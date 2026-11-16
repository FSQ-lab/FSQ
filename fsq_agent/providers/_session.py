# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Literal, TypeVar

from fsq_agent.agent_engine import EngineError, Model, ModelProvider, ModelRequest, ModelResult, create_google_gemini_model_provider, create_model_provider
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig

_OutputT = TypeVar("_OutputT")


class ModelProviderSession:
    def __init__(self, client_config: ProviderClientConfig, *, provider_factory: Callable[..., ModelProvider] | None = None) -> None:
        self.client_config = client_config
        self.provider = client_config.provider
        self.model = client_config.model
        self.metadata = dict(client_config.metadata)
        self._provider_factory = provider_factory or (create_google_gemini_model_provider if client_config.backend == "google_interactions" else create_model_provider)
        self._model_provider: ModelProvider | None = None

    def get_model(self) -> Model:
        if self._model_provider is None:
            self._model_provider = self._new_provider()
        return self._model_provider.get_model(self.model)

    async def complete(self, request: ModelRequest[_OutputT]) -> ModelResult[_OutputT]:
        return await self.get_model().complete(request)

    def complete_sync(
        self,
        request: ModelRequest[_OutputT],
        *,
        deadline_monotonic: float | None = None,
        cancellation_check: Callable[[], None] | None = None,
        on_request_state: Callable[[Literal["not_sent", "response_unknown", "response_received"]], None] | None = None,
    ) -> ModelResult[_OutputT]:
        """Optionally observe neutral call/response facts, not transport delivery, without persisting the callback."""
        return _run_async_sync(self._complete_once(request, deadline_monotonic=deadline_monotonic, cancellation_check=cancellation_check, on_request_state=on_request_state))

    async def close(self) -> None:
        provider = self._model_provider
        if provider is not None and await _close_provider(provider, suppress_errors=sys.exception() is not None):
            if self._model_provider is provider:
                self._model_provider = None

    def close_sync(self) -> None:
        if self._model_provider is not None:
            _run_async_sync(self.close())

    async def _complete_once(
        self,
        request: ModelRequest[_OutputT],
        *,
        deadline_monotonic: float | None = None,
        cancellation_check: Callable[[], None] | None = None,
        on_request_state: Callable[[Literal["not_sent", "response_unknown", "response_received"]], None] | None = None,
    ) -> ModelResult[_OutputT]:
        if on_request_state is not None:
            on_request_state("not_sent")
        _check_call_control(deadline_monotonic, cancellation_check, sent=False)
        provider = self._new_provider()
        sent = False

        async def invoke() -> ModelResult[_OutputT]:
            nonlocal sent
            _check_call_control(deadline_monotonic, cancellation_check, sent=False)
            model = provider.get_model(self.model)
            # Entering the neutral model is not proof that its transport sent pixels.
            if on_request_state is not None:
                on_request_state("response_unknown")
            sent = True
            return await model.complete(request)

        failed = False
        try:
            if deadline_monotonic is None and cancellation_check is None:
                result = await invoke()
            else:
                _check_call_control(deadline_monotonic, cancellation_check, sent=False)

                invocation = asyncio.create_task(invoke())
                try:
                    while not invocation.done():
                        _check_call_control(deadline_monotonic, cancellation_check, sent=sent)
                        remaining = max(0.0, deadline_monotonic - time.monotonic()) if deadline_monotonic is not None else 0.01
                        await asyncio.wait((invocation,), timeout=min(0.01, remaining))
                    _check_call_control(deadline_monotonic, cancellation_check, sent=sent)
                    result = invocation.result()
                except BaseException:
                    # A cancelled coroutine may suppress cancellation and return a late value.
                    # Settle it on its owning loop, but never accept that value or retry it.
                    invocation.cancel()
                    with suppress(BaseException):
                        await invocation
                    raise
            if on_request_state is not None:
                on_request_state("response_received")
        except BaseException:
            failed = True
            raise
        finally:
            await _close_provider(provider, suppress_errors=failed)
        _check_call_control(deadline_monotonic, cancellation_check, sent=True)
        return result

    def _new_provider(self) -> ModelProvider:
        try:
            if self.client_config.backend == "google_interactions":
                return self._provider_factory(api_key=self.client_config.api_key, base_url=self.client_config.base_url)
            options = {"model_name_is_deployment": True} if self.client_config.model_name_is_deployment else {}
            if self.client_config.backend == "kimi_responses":
                options["responses_profile"] = "kimi"
            return self._provider_factory(api_key=self.client_config.api_key, base_url=self.client_config.base_url, headers=self.client_config.default_headers or None, **options)
        except EngineError as error:
            if error.category == "configuration":
                raise ConfigurationError(str(error)) from None
            raise


async def _close_provider(provider: ModelProvider, *, suppress_errors: bool) -> bool:
    try:
        await provider.aclose()
    except BaseException:
        if not suppress_errors:
            raise
        return False
    return True


def _check_call_control(deadline_monotonic: float | None, cancellation_check: Callable[[], None] | None, *, sent: bool) -> None:
    if cancellation_check is not None:
        cancellation_check()
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise EngineError("timeout", "Model call deadline expired.", reason="response_unknown" if sent else "not_sent")


def _run_async_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
