# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ValidationError

from fsq_agent.drivers._capabilities import (
    _capability_matches,
    _discover_driver_capability_definitions,
    _schema_from_capability_definition,
    _with_driver_metadata,
)
from fsq_agent.harnesses._common_tools import CommonPlatformTools
from fsq_agent.harnesses._resources import OwnedResources
from fsq_agent.harnesses._visual import _ScopedVisualTools
from fsq_agent.models import (
    AndroidVisualAssertionPolicy,
    AssertionBudget,
    CapabilityDefinition,
    ConfigurationError,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    IOSUiSnapshotParams,
    RuntimeSecretSettings,
    StepPhase,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, IOSDriverInterface


class IOSHarness:
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
        driver: IOSDriverInterface,
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
        self._visual = _ScopedVisualTools("ios", driver, artifact_store, ai_assertion_evaluator, visual_assertion_policy_resolver, visual_assertion_budget, visual_assertion_provider_available)
        self._owned_resources = OwnedResources(driver, ai_assertion_evaluator)
        self.common_tools = CommonPlatformTools(platform="ios")
        configure_redaction = getattr(self.driver, "configure_diagnostic_redaction", None)
        if runtime_secret_settings is not None and callable(configure_redaction):
            configure_redaction(tuple(runtime_secret_settings.private_values().values()))

    @property
    def available_scoped_visual_tools(self) -> tuple[str, ...]:
        return self._visual.available_tools

    def capture_scope(self, callback):
        return self.artifact_store.capture_scope(callback) if self.artifact_store is not None else nullcontext()

    def close(self) -> None:
        self._owned_resources.close()

    def get_context(self) -> HarnessContext:
        context = self.driver.context()
        return HarnessContext(
            platform="ios",
            session_id=self._optional_str(context.get("session_id")),
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
                error_message=f"Unsupported iOS action: {step.action_name}",
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
                    update={
                        "metadata": {
                            **step.metadata,
                            "prepared_artifacts": [prepared.model_dump(mode="json")],
                            "run_id": capture_context.metadata["capture_invocation"]["run_id"],
                        }
                    }
                )
            driver_method = getattr(self.driver, driver_method_name)
            self._prepare_driver_ai_assertion_tool_invocation(step, context)
            try:
                output = driver_method(params)
            except ConfigurationError as exc:
                return HarnessActionResult(
                    status="failed",
                    action_name=step.action_name,
                    failure_category="configuration_error",
                    error_message=str(exc),
                    metadata={"context": exc.context} if exc.context else {},
                )
            except ToolExecutionError as exc:
                return HarnessActionResult(
                    status="failed",
                    action_name=step.action_name,
                    failure_category="action_error",
                    error_message=str(exc),
                    metadata={"context": exc.context} if exc.context else {},
                )
            finally:
                self._clear_driver_ai_assertion_tool_invocation()
            return self._result_from_driver_output(step.action_name, output, step_id=step.step_id)
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="configuration_error",
            error_message=f"Unsupported iOS capability executor: {capability.executor_kind}",
        )

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        self._visual.discard_capture()

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        invocation = context.metadata.get("capture_invocation", {})
        capability = self._capability_for(invocation.get("action_name", ""))
        if kind == "screenshot" and capability is not None and capability.name == "assert_with_ai":
            return self._visual.capture_assertion(context, invocation, step_id, phase)
        if self.artifact_store is None:
            raise RuntimeError("Artifact capture requires an ArtifactStore.")
        if kind in {"screenshot", "ui_snapshot"} and self._session_not_started():
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="json",
                    step_id=step_id,
                    phase=phase,
                    name=f"{reason}-{kind}-unavailable",
                    payload={
                        "status": "unavailable",
                        "reason": "ios_session_not_started",
                        "message": "Appium XCUITest session is not started yet.",
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
                    data=self.driver.screenshot(),
                )
            )
        if kind == "ui_snapshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="ui_snapshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    payload=self.driver.ui_snapshot(IOSUiSnapshotParams()),
                )
            )
        raise RuntimeError(f"Unsupported iOS artifact kind: {kind}")

    def _session_not_started(self) -> bool:
        try:
            context = self.driver.context()
        except Exception:  # noqa: BLE001
            return False
        return self._optional_str(context.get("session_id")) is None

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
        updates: dict[str, object] = {"driver_class": type(self.driver).__name__}
        if isinstance(backend, str):
            updates["backend"] = backend
        definitions = _discover_driver_capability_definitions(self.driver, platform="ios", metadata=updates)
        return [_with_driver_metadata(definition, updates) for definition in definitions]

    def _schema_from_capability(self, definition: CapabilityDefinition) -> HarnessFunctionSchema:
        return _schema_from_capability_definition(definition, platform="ios")

    def _prepare_driver_ai_assertion_tool_invocation(self, step: ExecutableStep, context: HarnessContext) -> None:
        capability = self._capability_for(step.action_name)
        self._visual.prepare(step, context, capability.name if capability is not None else None, self.capture_artifact)

    def _clear_driver_ai_assertion_tool_invocation(self) -> None:
        self._visual.clear()

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        if isinstance(error, ConfigurationError):
            return "configuration_error"
        return "harness_error"

    def _validate_params(self, step: ExecutableStep, params_model: type[BaseModel]) -> BaseModel | HarnessActionResult:
        try:
            return params_model.model_validate(step.params)
        except ValidationError as exc:
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=f"Invalid iOS parameters for {step.action_name}.",
                metadata={"validation_errors": self._validation_errors(exc)},
            )

    def _validation_errors(self, error: ValidationError) -> list[dict[str, object]]:
        try:
            return error.errors(include_url=False, include_context=False, include_input=False)
        except TypeError:
            return [{key: value for key, value in item.items() if key != "input"} for item in error.errors()]

    def _result_from_driver_output(self, action_name: str, output: object, *, step_id: str = "step") -> HarnessActionResult:
        if isinstance(output, (bytes, bytearray)):
            artifact_refs: list[HarnessArtifactRef] = []
            if self.artifact_store is not None:
                artifact_refs.append(
                    self._to_harness_artifact_ref(
                        self.artifact_store.write_bytes(
                            kind="screenshot",
                            step_id=step_id,
                            phase="invoke",
                            name="take-screenshot",
                            data=bytes(output),
                        )
                    )
                )
            return HarnessActionResult(
                status="passed",
                action_name=action_name,
                output={"byte_length": len(output)},
                artifact_refs=artifact_refs,
            )
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

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _screen_size(value: object) -> tuple[int, int] | None:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return None
        width, height = value
        if not isinstance(width, int) or isinstance(width, bool) or not isinstance(height, int) or isinstance(height, bool):
            return None
        return (width, height)

    @staticmethod
    def _dict_value(value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}
