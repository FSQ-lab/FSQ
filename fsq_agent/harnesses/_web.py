# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from contextlib import nullcontext
from typing import ClassVar

from pydantic import BaseModel, ValidationError

from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, WebDriverInterface
from fsq_agent.drivers._capabilities import _capability_matches, _discover_driver_capability_definitions, _schema_from_capability_definition, _with_driver_metadata
from fsq_agent.harnesses._common_tools import CommonPlatformTools
from fsq_agent.harnesses._host_visual import _capture_host_visual, _HostVisualAssertionCapture, _HostVisualAssertionInvocation
from fsq_agent.harnesses._resources import OwnedResources
from fsq_agent.harnesses._visual import _available_scoped_visual_tools, _resolve_scoped_visual_policy
from fsq_agent.models import (
    AndroidVisualAssertionPolicy,
    AssertionBudget,
    CapabilityDefinition,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    RuntimeSecretSettings,
    StepPhase,
    WebAssertWithAIParams,
    WebTakeScreenshotParams,
    WebUiSnapshotParams,
)


class WebHarness:
    _RUNNER_STATUSES: ClassVar[set[str]] = {"passed", "failed", "skipped", "cancelled", "incomplete"}
    _FAILURE_CATEGORIES: ClassVar[set[str]] = {
        "configuration_error",
        "context_error",
        "target_resolution_error",
        "action_error",
        "assertion_error",
        "timeout_error",
        "observation_error",
        "artifact_error",
        "harness_error",
        "cancelled",
        "unknown",
    }

    def __init__(
        self,
        driver: WebDriverInterface,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: AIAssertionEvaluatorProtocol | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
        visual_assertion_policy_resolver: Callable[[HarnessContext, int], AndroidVisualAssertionPolicy | None] | None = None,
        visual_assertion_budget: AssertionBudget | None = None,
        visual_assertion_provider_available: bool | None = None,
        visual_ai_eligible: bool | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_store = artifact_store
        self.ai_assertion_evaluator = ai_assertion_evaluator
        self.visual_ai_eligible = visual_ai_eligible
        self._visual_assertion_policy_resolver = visual_assertion_policy_resolver
        self._visual_assertion_budget = visual_assertion_budget or AssertionBudget()
        self._visual_assertion_provider_available = visual_assertion_provider_available
        self._visual_assertion_capture: _HostVisualAssertionCapture | None = None
        self._owned_resources = OwnedResources(driver, ai_assertion_evaluator)
        self.common_tools = CommonPlatformTools(
            platform="web",
        )
        self._configure_driver_ai_assertion_tool()

    @property
    def available_scoped_visual_tools(self) -> tuple[str, ...]:
        return _available_scoped_visual_tools(self.artifact_store, self.ai_assertion_evaluator, self._visual_assertion_policy_resolver, self._visual_assertion_provider_available)

    def capture_scope(self, callback):
        return self.artifact_store.capture_scope(callback) if self.artifact_store is not None else nullcontext()

    def close(self) -> None:
        self._owned_resources.close()

    def get_context(self) -> HarnessContext:
        context = self.driver.context()
        return HarnessContext(
            platform="web",
            session_id=self._optional_str(context.get("session_id")),
            current_url=self._optional_str(context.get("current_url")),
            screen_size=self._screen_size(context.get("screen_size")),
            capabilities=self._dict_value(context.get("capabilities")),
            metadata=self._dict_value(context.get("metadata")),
        )

    def action_space(self) -> list[HarnessFunctionSchema]:
        definitions = self._capability_definitions()
        return [*self.common_tools.common_action_space(), *[self._schema_from_capability(definition) for definition in definitions]]

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        return None

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        if self.common_tools.common_capability_for(step.action_name) is not None:
            return self.common_tools.invoke_common_tool(step)
        capability = self._capability_for(step.action_name)
        if capability is None:
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=f"Unsupported Web action: {step.action_name}",
            )
        if capability.executor_kind == "driver":
            params = self._validate_params(step, capability.params_model)
            if isinstance(params, HarnessActionResult):
                return params
            driver_method_name = str(capability.metadata.get("driver_method") or capability.name)
            if capability.name == "assert_with_ai" and "prepared_artifacts" not in step.metadata:
                capture_context = context.model_copy(
                    update={
                        "metadata": {
                            **context.metadata,
                            "capture_invocation": {
                                "run_id": step.metadata.get("run_id") or (self.artifact_store.run_dir.name if self.artifact_store else "unallocated"),
                                "step_execution_id": step.step_execution_id or step.step_id,
                                "action_name": capability.name,
                                "params": step.params,
                            },
                        }
                    }
                )
                prepared = self.capture_artifact("screenshot", "assert-with-ai", capture_context, step.step_id, "invoke")
                step = step.model_copy(
                    update={"metadata": {**step.metadata, "prepared_artifacts": [prepared.model_dump(mode="json")], "run_id": capture_context.metadata["capture_invocation"]["run_id"]}}
                )
            driver_method = getattr(self.driver, driver_method_name)
            self._prepare_driver_ai_assertion_tool_invocation(step, context)
            try:
                output = driver_method(params)
            finally:
                self._clear_driver_ai_assertion_tool_invocation()
            return self._result_from_driver_output(step.action_name, output, capability=capability, step_id=step.step_id)
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="configuration_error",
            error_message=f"Unsupported Web capability executor: {capability.executor_kind}",
        )

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        self._visual_assertion_capture = None

    def screenshot(self, params: WebTakeScreenshotParams | None = None) -> bytes:
        return self.driver.screenshot(params or WebTakeScreenshotParams())

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        invocation = context.metadata.get("capture_invocation", {})
        capability = self._capability_for(invocation.get("action_name", "")) if isinstance(invocation, dict) else None
        if kind == "screenshot" and capability is not None and capability.name == "assert_with_ai":
            self._visual_assertion_capture = None
            check = context.cancellation_check
            if check is not None:
                check()
            run_id = invocation.get("run_id") or (self.artifact_store.run_dir.name if self.artifact_store else "unallocated")
            execution_id = invocation.get("step_execution_id") or step_id
            capture_context = context.model_copy(
                update={"metadata": {**context.metadata, "capture_invocation": {**invocation, "run_id": run_id, "step_id": step_id, "step_execution_id": execution_id}}}
            )
            current = _HostVisualAssertionInvocation(
                run_id=run_id,
                step_id=step_id,
                step_execution_id=execution_id,
                capture_sequence=self.artifact_store.allocate_capture_sequence() if self.artifact_store is not None else 1,
                params=WebAssertWithAIParams.model_validate(invocation["params"]),
                context=capture_context,
            )
            policy = self._frame_policy(current.context, current.capture_sequence)
            artifact = _capture_host_visual(
                lambda: self.driver.screenshot(WebTakeScreenshotParams(fullPage=current.params.region is None)),
                self.artifact_store,
                current,
                policy,
                callable(getattr(self.ai_assertion_evaluator, "evaluate", None)),
                phase,
                inactive_reason="browser_not_started" if self._browser_not_started(current.context) else None,
            )
            self._visual_assertion_capture = _HostVisualAssertionCapture(current, policy, artifact)
            return artifact
        if self.artifact_store is None:
            raise RuntimeError("Artifact capture requires an ArtifactStore.")
        if self._browser_not_started(context):
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="json",
                    step_id=step_id,
                    phase=phase,
                    name=f"{reason}-{kind}-unavailable",
                    payload={
                        "status": "unavailable",
                        "reason": "browser_not_started",
                        "message": "Browser is not started. Call startBrowser before Web page actions.",
                        "requested_artifact_kind": kind,
                    },
                )
            )
        if kind == "screenshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_bytes(
                    kind="screenshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    data=self.screenshot(WebTakeScreenshotParams()),
                )
            )
        if kind == "ui_snapshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="ui_snapshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    payload=self.driver.ui_snapshot(WebUiSnapshotParams()),
                )
            )
        raise RuntimeError(f"Unsupported Web artifact kind: {kind}")

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "harness_error"

    def _to_harness_artifact_ref(self, ref: object) -> HarnessArtifactRef:
        if isinstance(ref, HarnessArtifactRef):
            return ref
        data = ref if isinstance(ref, dict) else ref.model_dump()  # type: ignore[attr-defined]
        return HarnessArtifactRef(
            artifact_id=data["artifact_id"],
            kind=data["kind"],
            path=data["path"],
            mime_type=data.get("mime_type"),
            size_bytes=data.get("size_bytes"),
            sha256=data.get("sha256"),
            availability=data.get("availability", "available"),
            unavailable_reason=data.get("unavailable_reason"),
            capture_occurrence=data.get("capture_occurrence"),
            created_at=data["created_at"],
            metadata=dict(data.get("metadata") or {}),
        )

    def _capability_for(self, name_or_alias: str) -> CapabilityDefinition | None:
        for capability in self._capability_definitions():
            if _capability_matches(capability, name_or_alias):
                return capability
        return None

    def _capability_definitions(self) -> list[CapabilityDefinition]:
        backend = getattr(self.driver, "backend", None)
        driver_class = type(self.driver).__name__
        updates = {"driver_class": driver_class}
        if isinstance(backend, str):
            updates["backend"] = backend
        definitions = _discover_driver_capability_definitions(
            self.driver,
            platform="web",
            metadata=updates,
        )
        return [_with_driver_metadata(definition, updates) for definition in definitions]

    def _configure_driver_ai_assertion_tool(self) -> None:
        configure = getattr(self.driver, "configure_ai_assertion_tool", None)
        if callable(configure):
            configure(
                platform="web",
                artifact_store=self.artifact_store,
                ai_assertion_evaluator=self.ai_assertion_evaluator,
            )

    def _frame_policy(self, context: HarnessContext, sequence: int) -> AndroidVisualAssertionPolicy:
        return _resolve_scoped_visual_policy(context, sequence, self._visual_assertion_policy_resolver, self._visual_assertion_budget, self._visual_assertion_provider_available)

    def _prepare_driver_ai_assertion_tool_invocation(self, step: ExecutableStep, context: HarnessContext) -> None:
        binding, self._visual_assertion_capture = self._visual_assertion_capture, None
        policy = None
        capability = self._capability_for(step.action_name)
        if capability is not None and capability.name == "inspect_with_ai":
            sequence = self.artifact_store.allocate_capture_sequence() if self.artifact_store is not None else 1
            identity = {
                "run_id": step.metadata.get("run_id") or (self.artifact_store.run_dir.name if self.artifact_store else "unallocated"),
                "step_id": step.step_id,
                "step_execution_id": step.step_execution_id or step.step_id,
                "action_name": capability.name,
                "params": step.params,
                "capture_sequence": sequence,
            }
            context = context.model_copy(update={"metadata": {**context.metadata, "capture_invocation": identity}})
            policy = self._frame_policy(context, sequence)
        elif capability is not None and capability.name == "assert_with_ai" and binding is not None:
            current = binding.invocation
            refs = step.metadata.get("prepared_artifacts", [])
            screenshot = next((ref for ref in refs if ref.get("kind") == "screenshot"), None)
            if (
                current.run_id == step.metadata.get("run_id")
                and current.step_id == step.step_id
                and current.step_execution_id == (step.step_execution_id or step.step_id)
                and current.params == WebAssertWithAIParams.model_validate(step.params)
                and screenshot is not None
                and screenshot.get("artifact_id") == binding.artifact.artifact_id
                and screenshot.get("metadata", {}).get("capture") == binding.artifact.metadata.get("capture")
            ):
                policy = binding.policy
        prepare = getattr(self.driver, "prepare_ai_assertion_tool_invocation", None)
        if callable(prepare):
            prepare(
                context=context,
                step_id=step.step_id,
                action_name=step.action_name,
                metadata=step.metadata,
                capture_artifact=self.capture_artifact,
                visual_assertion_policy=policy,
            )

    def _clear_driver_ai_assertion_tool_invocation(self) -> None:
        clear = getattr(self.driver, "clear_ai_assertion_tool_invocation", None)
        self._visual_assertion_capture = None
        if callable(clear):
            clear()

    def _schema_from_capability(self, definition: CapabilityDefinition) -> HarnessFunctionSchema:
        return _schema_from_capability_definition(definition, platform="web")

    def _validate_params(self, step: ExecutableStep, params_model: type[BaseModel]) -> BaseModel | HarnessActionResult:
        try:
            return params_model.model_validate(step.params)
        except ValidationError as exc:
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=f"Invalid Web parameters for {step.action_name}.",
                metadata={"validation_errors": self._validation_errors(exc)},
            )

    def _validation_errors(self, error: ValidationError) -> list[dict[str, object]]:
        try:
            return error.errors(include_url=False, include_context=False, include_input=False)
        except TypeError:
            return [{key: value for key, value in item.items() if key != "input"} for item in error.errors()]

    def _result_from_driver_output(self, action_name: str, output: object, *, capability: CapabilityDefinition | None = None, step_id: str = "step") -> HarnessActionResult:
        persist_kind = capability.metadata.get("persist_artifact_kind") if capability is not None else None
        if isinstance(persist_kind, str) and isinstance(output, dict) and "status" not in output:
            artifact_refs: list[HarnessArtifactRef] = []
            if self.artifact_store is not None:
                try:
                    written = self.artifact_store.write_json(
                        kind=persist_kind,
                        step_id=step_id,
                        phase="invoke",
                        name=persist_kind.replace("_", "-"),
                        payload=output,
                    )
                except Exception as exc:
                    if getattr(exc, "fsq_evidence_fatal", False):
                        raise
                    written = self.artifact_store.capture_failure(
                        kind=persist_kind,
                        step_id=step_id,
                        phase="invoke",
                        name=persist_kind.replace("_", "-"),
                        error=exc,
                    )
                artifact_refs.append(self._to_harness_artifact_ref(written))
            return HarnessActionResult(status="passed", action_name=action_name, output=output, artifact_refs=artifact_refs)
        if not isinstance(output, dict) or "status" not in output:
            return HarnessActionResult(status="passed", action_name=action_name, output=output)
        status_value = output.get("status")
        status = status_value if isinstance(status_value, str) and status_value in self._RUNNER_STATUSES else "passed"
        failure_category_value = output.get("failure_category")
        failure_category = failure_category_value if isinstance(failure_category_value, str) and failure_category_value in self._FAILURE_CATEGORIES else None
        error_message_value = output.get("error_message")
        metadata_value = output.get("metadata")
        artifact_refs_value = output.get("artifact_refs")
        artifact_refs = [self._to_harness_artifact_ref(ref) for ref in artifact_refs_value] if isinstance(artifact_refs_value, list) else []
        return HarnessActionResult(
            status=status,
            action_name=action_name,
            output=output.get("output"),
            artifact_refs=artifact_refs,
            error_message=error_message_value if isinstance(error_message_value, str) else None,
            failure_category=failure_category,
            metadata=metadata_value if isinstance(metadata_value, dict) else {},
        )

    def _optional_str(self, value: object) -> str | None:
        return value if isinstance(value, str) else None

    def _screen_size(self, value: object) -> tuple[int, int] | None:
        if not isinstance(value, tuple) or len(value) != 2:
            return None
        width, height = value
        if not isinstance(width, int) or not isinstance(height, int):
            return None
        return (width, height)

    def _dict_value(self, value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    def _browser_not_started(self, context: HarnessContext) -> bool:
        return context.metadata.get("browser_started") is False

    def _metadata_str(self, metadata: dict[str, object], key: str) -> str | None:
        value = metadata.get(key)
        return value if isinstance(value, str) else None
