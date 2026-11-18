# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
import logging
import math
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from time import time
from typing import TYPE_CHECKING

import httpx
from openai import APIConnectionError, APIResponseValidationError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import ResponseStreamEvent

from ._backend import BackendModel, BackendTurn
from ._contracts import AgentResult, EngineError, ToolOutputEntry
from ._conversion import _item_text, check_response, engine_error, model_input, model_result, response_items, response_parameters, semantic_event, token_usage
from ._runner import _close_stream, _complete_cleanup, run_agent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ._contracts import AgentEventSink, AgentRequest, Model, ModelRequest, ModelResult, OutputT, ResponsesCompatibilityProfile

_safe_model_logging: ContextVar[bool] = ContextVar("agent_engine_safe_model_logging", default=False)


class _SafeModelLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != __name__ and not _safe_model_logging.get():
            return True
        record.msg = "Model backend diagnostic (%s)."
        record.args = (record.levelname,)
        record.exc_info = None
        record.exc_text = None
        return True


_logger = logging.getLogger(__name__)
_logger.addFilter(_SafeModelLogFilter())
logging.getLogger("openai._base_client").addFilter(_SafeModelLogFilter())

_EXECUTION_PHASE = (
    "Protocol phase: tool execution. Use available tools when the task requires actions or evidence not supplied in context. "
    "Preserve all task constraints, including restrictions on tool use. When supplied context is sufficient, "
    "a no-action handoff is valid; do not invoke unnecessary tools. Do not substitute a plan or a final JSON report for required tool calls. "
    "Final structured formatting happens in a separate phase. When execution is complete or blocked, "
    "provide a brief factual handoff, preserving failures, uncertainty, and any work not performed."
)
_FINALIZATION_PHASE = (
    "Protocol phase: structured finalization only. No tools are available and execution cannot resume. "
    "Return the required structured output using the task and retained conversation. "
    "Treat the execution handoff as unverified claims; use actual tool results as evidence of actions. "
    "Do not describe planned or unperformed actions as completed. Preserve failures and uncertainty, "
    "and do not invent evidence or turn missing execution evidence into success."
)


class OpenAIModelProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        headers: Mapping[str, str] | None = None,
        model_name_is_deployment: bool = False,
        responses_profile: ResponsesCompatibilityProfile = "openai",
    ) -> None:
        if not base_url.strip() or not api_key.strip():
            raise EngineError("configuration", "Model endpoint and credential are required.")
        if responses_profile not in {"openai", "kimi"}:
            raise EngineError("configuration", "Responses compatibility profile is unsupported.")
        self._base_url = base_url
        self._api_key = api_key
        self._headers = dict(headers or {})
        self._model_name_is_deployment = model_name_is_deployment
        self._responses_profile = responses_profile
        self._client: AsyncOpenAI | None = None
        self._models: dict[str, _OpenAIModel] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = False
        self._closed = False

    def get_model(self, model_name: str) -> Model:
        self._check_open()
        if not model_name.strip():
            raise EngineError("configuration", "An explicit model name is required.")
        if model_name not in self._models:
            self._models[model_name] = _OpenAIModel(self, model_name)
        return self._models[model_name]

    def _check_open(self) -> None:
        if self._closed:
            raise EngineError("closed", "Model provider is closed.")

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EngineError("lifecycle", "Model provider belongs to another event loop.")
        self._loop = loop

    @asynccontextmanager
    async def _use_client(self) -> AsyncIterator[AsyncOpenAI]:
        self._check_open()
        self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Model provider is already in use.")
        self._busy = True
        log_scope = _safe_model_logging.set(True)
        try:
            if self._client is None:
                self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, default_headers=self._headers or None)
            yield self._client
        finally:
            _safe_model_logging.reset(log_scope)
            self._busy = False

    async def aclose(self) -> None:
        if self._closed and self._client is None:
            return
        if self._loop is not None:
            self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Cannot close a model provider while it is in use.")
        self._closed = True
        self._models.clear()
        self._api_key = ""
        self._headers.clear()
        self._busy = True
        try:
            if self._client is not None:
                await self._client.close()
                self._client = None
        except Exception:
            _logger.exception("Model provider cleanup failed.")
            raise EngineError("cleanup", "Model provider cleanup failed.") from None
        finally:
            self._busy = False


class _OpenAIModel(BackendModel):
    def __init__(self, provider: OpenAIModelProvider, name: str) -> None:
        self._provider = provider
        self._name = name

    def create_engine(self):
        return OpenAIAgentEngine()

    async def complete(self, request: ModelRequest[OutputT]) -> ModelResult[OutputT]:
        try:
            parameters = response_parameters(
                self._name,
                model_input(request.input),
                request.instructions,
                output=request.output,
                reasoning_effort=request.reasoning_effort,
                model_name_is_deployment=self._provider._model_name_is_deployment,
                responses_profile=self._provider._responses_profile,
            )
            async with self._provider._use_client() as client:
                response = await client.responses.create(**parameters)
                return model_result(response, request.output)
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Model operation failed.")
            raise engine_error(error) from None


class OpenAIAgentEngine:
    def __init__(self) -> None:
        self._busy = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self, model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None) -> AgentResult:
        if not isinstance(model, _OpenAIModel):
            raise EngineError("incompatible_model", "The selected model is incompatible with this agent backend.")
        if self._busy:
            raise EngineError("lifecycle", "Agent engine is already running.")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EngineError("lifecycle", "Agent engine belongs to another event loop.")
        self._loop = loop
        if request.max_turns < 1 or not request.name.strip():
            raise EngineError("configuration", "Agent name and positive turn limit are required.")
        if len({tool.name for tool in request.tools}) != len(request.tools):
            raise EngineError("configuration", "Agent tool names must be unique.")
        self._busy = True
        try:
            async with model._provider._use_client() as client:
                return await run_agent(
                    OpenAIConversation(
                        client,
                        model._name,
                        request,
                        model_name_is_deployment=model._provider._model_name_is_deployment,
                        responses_profile=model._provider._responses_profile,
                    ),
                    request,
                    on_event,
                )
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Agent operation failed.")
            raise engine_error(error) from None
        finally:
            self._busy = False


class OpenAIConversation:
    def __init__(
        self,
        client: AsyncOpenAI,
        model_name: str,
        request: AgentRequest,
        *,
        model_name_is_deployment: bool = False,
        responses_profile: ResponsesCompatibilityProfile = "openai",
    ) -> None:
        self.client = client
        self.history = model_input(request.input)
        self.stream = request.stream
        self.parameters = response_parameters(
            model_name,
            self.history,
            request.instructions,
            tools=request.tools,
            output=request.output,
            agent=True,
            reasoning_effort=request.reasoning_effort,
            model_name_is_deployment=model_name_is_deployment,
            responses_profile=responses_profile,
        )
        self._finalization_parameters = None
        self._finalizing = False
        if responses_profile == "kimi" and request.tools and request.output is not None:
            # Build and validate the schema before any inference, but advertise it only after tool execution.
            self._finalization_parameters = {
                **self.parameters,
                "instructions": f"{request.instructions}\n\n{_FINALIZATION_PHASE}",
            }
            self._finalization_parameters.pop("tools", None)
            self.parameters = {**self.parameters, "instructions": f"{request.instructions}\n\n{_EXECUTION_PHASE}"}
            self.parameters.pop("text", None)

    def tool_outputs(self) -> tuple[ToolOutputEntry, ...]:
        names = {item["call_id"]: item.get("name") for item in self.history if item.get("type") == "function_call"}
        return tuple(
            ToolOutputEntry(entry_id=index, call_id=item.get("call_id"), tool_name=names.get(item.get("call_id")), output=item["output"])
            for index, item in enumerate(self.history)
            if item.get("type") == "function_call_output"
        )

    def filtered_input(self, replacements: Mapping[int, str]) -> list[dict]:
        return [{**item, "output": replacements[index]} if index in replacements else item for index, item in enumerate(self.history)]

    async def request(self, replacements: Mapping[int, str], *, final_turn_instructions: str | None = None) -> BackendTurn:
        finalization = self._finalizing or final_turn_instructions is not None
        phase_parameters = self._finalization_parameters if finalization and self._finalization_parameters is not None else self.parameters
        parameters = {**phase_parameters, "input": self.filtered_input(replacements)}
        if final_turn_instructions is not None:
            parameters.pop("tools", None)
            parameters["instructions"] = f"{parameters['instructions']}\n\n{final_turn_instructions}"
        response = await _read_response(self.client, parameters, stream=self.stream)
        items = response_items(response)
        self.history.extend(items)
        messages = [item for item in items if item["type"] == "message"]
        text = _item_text(messages[-1]) if messages else ""
        calls = tuple(item for item in items if item["type"] == "function_call")
        execution_phase = self._finalization_parameters is not None and not finalization
        if execution_phase and not calls and text:
            self._finalizing = True
        return BackendTurn(
            text=text,
            calls=calls,
            events=tuple(event for item in items if not (execution_phase and item["type"] == "message") and (event := semantic_event(item)) is not None),
            usage=token_usage(response.usage),
            requires_continuation=execution_phase,
            finalization=finalization,
        )

    def add_tool_output(self, call: dict, output: str) -> None:
        self.history.append({"type": "function_call_output", "call_id": call["call_id"], "output": output})


async def _read_response(client: AsyncOpenAI, parameters: dict, *, stream: bool):
    if not stream:
        response = await client.responses.create(**parameters)
        check_response(response)
        return response
    # This borrowed clone shares the provider's HTTP client; only streamed Agent attempts disable SDK retries.
    client = client.with_options(max_retries=0)
    for attempt in range(4):
        response_stream = None
        response = None
        failure = None
        retryable = False
        retry_after = None
        owned_response = None
        body_read = False
        try:
            response_stream = await client.responses.create(**parameters, stream=True)
            owned_response = response_stream.response
            content = await owned_response.aread()
            body_read = True
            response = _decode_stream_response(client, response_stream, content)
        except Exception as error:  # noqa: BLE001 - Normalize supplier failures before owned stream cleanup.
            owned_response = owned_response if owned_response is not None else _failure_response(error)
            if response_stream is not None and not body_read and owned_response.is_closed:
                failure = EngineError("cleanup", "Agent stream cleanup failed.")
            else:
                failure, retryable = _stream_failure(error, owned_response)
            if owned_response is not None:
                retry_after = _retry_after(owned_response.headers.get("retry-after"))
        except BaseException as error:  # noqa: BLE001 - Preserve cancellation and process interruption through cleanup.
            failure = error
            owned_response = owned_response if owned_response is not None else _failure_response(error)

        settled = True
        if response_stream is not None or owned_response is not None:
            cleanup_failure = None
            try:
                if owned_response.is_closed and not body_read and (response_stream is not None or isinstance(failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))):
                    # HTTPX marks a response closed before awaiting disposal. A cancelled/failed implicit
                    # close must still be joined through the underlying stream, not its now-no-op response.
                    await _complete_cleanup(owned_response.stream.aclose(), None)
                elif response_stream is not None:
                    await _close_stream(response_stream, None)
                else:
                    await _complete_cleanup(owned_response.aclose(), None)
            except BaseException as error:  # noqa: BLE001 - Settle stream disposal before propagating the primary failure.
                cleanup_failure = EngineError("cleanup", "Agent stream cleanup failed.") if isinstance(error, Exception) else error
            if cleanup_failure is not None:
                settled = False
                if failure is None or (
                    isinstance(cleanup_failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) and not isinstance(failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
                ):
                    failure = cleanup_failure
                else:
                    failure.add_note("Agent stream cleanup failed; no further transport attempt was started.")

        # Raise only after leaving SDK/HTTP exception handlers: even suppressed __context__ must stay safe.
        if failure is None:
            return response
        if not retryable or not settled or attempt == 3 or isinstance(failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise failure
        await asyncio.sleep(max(2**attempt, retry_after or 0))


def _safe_stream_error(category: str, reason: str, identifier: str, status_code: int | None = None) -> EngineError:
    error = EngineError(category, "Model streaming operation failed.", reason=reason, status_code=status_code)
    error.__cause__ = RuntimeError(f"Model transport diagnostic ({identifier}).")
    return error


def _failure_chain(error: BaseException):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(link for link in (current.__cause__, current.__context__) if link is not None)


def _failure_response(error: BaseException) -> httpx.Response | None:
    for current in _failure_chain(error):
        if isinstance(current, (APIStatusError, APIResponseValidationError, httpx.HTTPStatusError)):
            return current.response
    return None


def _stream_failure(error: Exception, response: httpx.Response | None) -> tuple[EngineError, bool]:
    if isinstance(error, EngineError):
        return error, error.category == "unavailable" and error.reason in {"stream_error", "stream_incomplete"}
    status = response.status_code if response is not None else None
    status = status if type(status) is int and 100 <= status <= 599 else None
    if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
        if status == 429:
            return _safe_stream_error("rate_limit", "rate_limit", "APIStatusError", status), True
        if status is not None and status >= 500:
            return _safe_stream_error("unavailable", "service_unavailable", "APIStatusError", status), True
        return engine_error(error), False
    # SDK connection errors also wrap arbitrary local exceptions: only recognized transport causes qualify.
    chain = [error]
    current = error
    while isinstance(current, APIConnectionError):
        cause = current.__cause__ or current.__context__
        if cause is None or any(cause is known for known in chain):
            break
        chain.append(cause)
        current = cause
    for current in chain:
        if isinstance(current, httpx.TimeoutException):
            identifier = "ReadTimeout" if isinstance(current, httpx.ReadTimeout) else "ConnectTimeout" if isinstance(current, httpx.ConnectTimeout) else "TimeoutException"
            return _safe_stream_error("timeout", "timeout", identifier, status), True
        if isinstance(current, (TimeoutError, APITimeoutError)):
            if isinstance(current, APITimeoutError) and any(isinstance(cause, httpx.TimeoutException) for cause in chain):
                continue
            identifier = "APITimeoutError" if isinstance(current, APITimeoutError) else "TimeoutError"
            return _safe_stream_error("timeout", "timeout", identifier, status), True
        if isinstance(current, httpx.RemoteProtocolError):
            return _safe_stream_error("unavailable", "remote_protocol_error", "RemoteProtocolError", status), True
        if isinstance(current, IncompleteRead):
            return _safe_stream_error("unavailable", "incomplete_read", "IncompleteRead", status), True
        if isinstance(current, httpx.ReadError):
            return _safe_stream_error("unavailable", "read_error", "ReadError", status), True
        if isinstance(current, httpx.ConnectError):
            return _safe_stream_error("unavailable", "connection_error", "ConnectError", status), True
        if isinstance(current, ConnectionError):
            identifier = "ConnectionResetError" if isinstance(current, ConnectionResetError) else "ConnectionError"
            return _safe_stream_error("unavailable", "connection_error", identifier, status), True
    if isinstance(error, APIConnectionError) and len(chain) == 1:
        return _safe_stream_error("unavailable", "connection_error", "APIConnectionError", status), True
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError, APIResponseValidationError)):
        return EngineError("invalid_output", "Model provider returned malformed streaming protocol data.", status_code=status), False
    return engine_error(error), False


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        if value.strip().isdigit():
            delay = float(value)
        else:
            date = parsedate_to_datetime(value)
            delay = date.replace(tzinfo=UTC).timestamp() - time() if date.tzinfo is None else date.timestamp() - time()
    except (ValueError, TypeError, OverflowError):
        return None
    return min(delay, 4) if math.isfinite(delay) and delay > 0 else None


def _sse_rejection(data: dict) -> EngineError | None:
    detail = data.get("error") if isinstance(data.get("error"), dict) else data
    identifiers = {value for key in ("code", "type") if isinstance(value := detail.get(key), str)}
    if identifiers & {"invalid_api_key", "authentication_error", "authentication_failed", "unauthorized", "permission_denied", "forbidden"}:
        return EngineError("authentication", "Model provider authentication failed.")
    if identifiers & {"invalid_request_error", "invalid_request", "invalid_json_schema", "invalid_schema", "schema_validation_error", "unsupported_parameter", "invalid_parameter", "bad_request"}:
        return EngineError("invalid_output", "Model provider rejected the request or output schema.")
    if identifiers & {"content_filter", "content_filtered", "content_policy_violation"}:
        return EngineError("incomplete", "Model provider returned an incomplete response.", reason="content_filter")
    if identifiers & {"refusal", "refused"}:
        return EngineError("refusal", "Model provider refused the request.")
    return None


def _decode_stream_response(client: AsyncOpenAI, response_stream, content: bytes):
    completed_response = None
    failed_response = None
    stream_error = False
    rejection = None
    for sse in response_stream._decoder.iter_bytes(iter((content,))):
        if sse.data.startswith("[DONE]"):
            break
        data = sse.json()
        if not isinstance(data, dict):
            raise EngineError("invalid_output", "Model provider returned malformed streaming protocol data.")
        if data.get("type") in {"response.refusal.delta", "response.refusal.done"}:
            rejection = rejection or EngineError("refusal", "Model provider refused the request.")
            continue
        if data.get("error") or data.get("type") in {"error", "response.error"}:
            rejection = rejection or _sse_rejection(data)
            stream_error = True
            continue
        if isinstance(data.get("response"), dict) and isinstance(data["response"].get("error"), dict):
            rejection = rejection or _sse_rejection(data["response"])
        event = client._process_response_data(data=data, cast_to=ResponseStreamEvent, response=response_stream.response)
        if event.type == "response.completed":
            completed_response = event.response
        elif event.type in {"response.failed", "response.incomplete"}:
            failed_response = event.response
    if rejection is not None:
        raise rejection
    if failed_response is not None:
        check_response(failed_response)
        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.")
    if completed_response is not None:
        check_response(completed_response)
    if stream_error or completed_response is None:
        reason = "stream_error" if stream_error else "stream_incomplete"
        raise _safe_stream_error("unavailable", reason, "SSEError" if stream_error else "StreamIncomplete")
    return completed_response
