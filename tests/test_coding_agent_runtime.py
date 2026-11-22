# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fsq_agent.adapters.coding_agent import DefaultCodingAgentRuntime
from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent._pre_plan import build_pre_plan_input
from fsq_agent.agent._prompt import PromptModelBuilder, PromptRenderer
from fsq_agent.agent._verification_task import VerificationEvidenceBuilder
from fsq_agent.agent_engine import AgentEvent, AgentRequest, AgentResult, EngineError, ModelRequest, ModelResult, TokenUsage, ToolCall, ToolInputFailure, ToolOutputEntry
from fsq_agent.config import Settings
from fsq_agent.models import (
    AgentFinalOutput,
    AgentRuntimeSettings,
    AndroidSwipeParams,
    AndroidTapAtParams,
    GoalPrePlan,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    KnowledgeBundle,
    LocalToolOutputSettings,
    OutputSettings,
    RunnerStepResult,
    RuntimeSecretSettings,
    SkillBundle,
    StepPhase,
    StepResult,
    Task,
    WebClickOnParams,
)
from fsq_agent.providers import build_model_provider_session


class _EmptyToolFactory:
    def build_tools(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class _CapturingToolFactory:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def build_tools(self, *_args: Any, **kwargs: Any) -> list[Any]:
        self.kwargs = kwargs
        return []


class _FakeHarness:
    def close(self) -> None:
        return None

    def __init__(
        self,
        *,
        tool_name: str = "tap_on",
        driver_method: str = "tap_on",
        fsq_action_name: str = "tapOn",
        screen_size: tuple[int, int] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.driver_method = driver_method
        self.fsq_action_name = fsq_action_name
        self.screen_size = screen_size
        self.steps: list[Any] = []
        self.calls: list[str] = []

    def action_space(self) -> list[HarnessFunctionSchema]:
        params_models = {"tap_at": AndroidTapAtParams, "swipe": AndroidSwipeParams, "click_on": WebClickOnParams}
        params_model = params_models.get(self.driver_method)
        return [
            HarnessFunctionSchema(
                name=self.tool_name,
                description=f"Run {self.fsq_action_name}.",
                params_json_schema=params_model.model_json_schema() if params_model is not None else {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
                platform="android",
                driver_method=self.driver_method,
                fsq_action_name=self.fsq_action_name,
            )
        ]

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="android", session_id="session-1", screen_size=self.screen_size)

    def before_action(self, step: Any, context: HarnessContext) -> None:
        self.calls.append(f"before:{step.action_name}:{context.session_id}")

    def invoke_action(self, step: Any, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        self.steps.append(step)
        return HarnessActionResult(
            status="passed",
            action_name=step.action_name,
            output={"context_session_id": context.session_id, "params": step.params},
            metadata={"harness": "fake"},
        )

    def after_action(
        self,
        step: Any,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        status = action_result.status if action_result else "none"
        self.calls.append(f"after:{step.action_name}:{status}")

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}:{context.session_id}")
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{reason}-{kind}",
            kind=kind,
            path=Path(f"artifacts/{kind}/{step_id}-{phase}-{reason}.{kind}"),
        )

    def classify_error(self, _error: BaseException, _phase: StepPhase, _step: Any) -> str:
        return "unknown"


class _FailingHarness(_FakeHarness):
    def action_space(self) -> list[HarnessFunctionSchema]:
        raise RuntimeError("Harness action-space failed")


class _FailingCaptureHarness(_FakeHarness):
    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}:{context.session_id}")
        raise RuntimeError("capture failed")


class _DirectInvokeForbiddenHarness(_FakeHarness):
    def invoke_action(self, step: Any, context: HarnessContext) -> HarnessActionResult:
        raise AssertionError("adapter must not directly invoke the harness")


class _FakeWebHarness(_FakeHarness):
    def __init__(self) -> None:
        super().__init__(tool_name="click_on", driver_method="click_on", fsq_action_name="clickOn")

    def action_space(self) -> list[HarnessFunctionSchema]:
        schemas = super().action_space()
        return [schemas[0].model_copy(update={"platform": "web"})]

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="web", session_id="session-1")


def _fake_harness_factory(_run_id: str) -> _FakeHarness:
    return _FakeHarness()


class _FakeProviderSession:
    def get_model(self):
        return _FakeModel()

    async def close(self) -> None:
        return None


class _FakeModel:
    async def complete(self, request: ModelRequest) -> ModelResult:
        raise AssertionError("Agent runtime must not bypass the engine")


class _FakeEngine:
    requests: ClassVar[list[AgentRequest]] = []

    async def run(self, model, request: AgentRequest, *, on_event=None) -> AgentResult:
        self.requests.append(request)
        if request.output is not None and request.output.name == "GoalPrePlan":
            return AgentResult(final_output=GoalPrePlan(goal="Open the app.", verification_goal="The app is open."))
        return AgentResult(final_output=AgentFinalOutput(status="success", summary="Done."))


@pytest.mark.parametrize("outcome", ["success", "startup", "model", "cancelled"])
@pytest.mark.parametrize("cleanup_failures", [(), ("harness",), ("provider",), ("harness", "provider")])
async def test_owned_harness_and_provider_are_disposed_for_every_exit(tmp_path, monkeypatch, outcome, cleanup_failures):
    closed = []
    primary = asyncio.CancelledError("primary") if outcome == "cancelled" else EngineError("model", "primary")
    cleanup_errors = {resource: OSError(f"{resource} cleanup") for resource in cleanup_failures}

    class Harness(_FakeHarness):
        def action_space(self):
            if outcome == "startup":
                raise primary
            return super().action_space()

        def close(self):
            closed.append("harness")
            if "harness" in cleanup_errors:
                raise cleanup_errors["harness"]

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")
            if "provider" in cleanup_errors:
                raise cleanup_errors["provider"]

    class Engine(_FakeEngine):
        async def run(self, *args, **kwargs):
            if outcome in {"model", "cancelled"}:
                raise primary
            return await super().run(*args, **kwargs)

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), engine=Engine())
    operation = _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "owned")
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError) as raised:
            await operation
        assert raised.value is primary
        if cleanup_errors:
            _assert_cleanup_errors(raised.value.__cause__, list(cleanup_errors.values()))
    elif cleanup_errors:
        with pytest.raises(BaseExceptionGroup if outcome != "success" or len(cleanup_errors) > 1 else OSError) as raised:
            await operation
        cleanup_failure = raised.value
        if outcome != "success":
            execution_failure, cleanup_failure = raised.value.exceptions
            if outcome == "startup":
                assert execution_failure.__cause__ is primary
            else:
                assert execution_failure is primary
        _assert_cleanup_errors(cleanup_failure, list(cleanup_errors.values()))
    else:
        result = await operation
        assert result[-1].status == ("success" if outcome == "success" else "failed")
        if outcome != "success":
            assert result[-1].error == ("Harness action-space discovery failed." if outcome == "startup" else "primary")
    assert closed == ["harness", "provider"]


def _assert_cleanup_errors(error, expected):
    if len(expected) == 1:
        assert error is expected[0]
    else:
        assert isinstance(error, BaseExceptionGroup)
        assert error.exceptions == tuple(expected)


@pytest.mark.parametrize("operation", ["run_task", "run_pre_plan"])
@pytest.mark.parametrize("failure_phase", ["provider", "model"])
async def test_runtime_cancellation_during_failure_reporting_keeps_cleanup_visible(tmp_path, monkeypatch, operation, failure_phase):
    primary = EngineError("model", "Primary failure")
    cancellation = asyncio.CancelledError("Failure reporting cancelled")
    cleanup_error = OSError("Provider cleanup failed")

    class Session(_FakeProviderSession):
        def get_model(self):
            if failure_phase == "provider":
                raise primary
            return super().get_model()

        async def close(self):
            raise cleanup_error

    class Engine(_FakeEngine):
        async def run(self, *args, **kwargs):
            raise primary

    def event_sink(event):
        if event.type == "run_failed":
            raise cancellation

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=Engine())
    invocation = (
        runtime.run_pre_plan("Plan task", KnowledgeBundle(), [], "report-cancel", event_sink)
        if operation == "run_pre_plan"
        else _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "report-cancel", event_sink)
    )
    with pytest.raises(asyncio.CancelledError) as failure:
        await invocation
    assert failure.value is cancellation
    assert cancellation.__cause__ is cleanup_error


@pytest.mark.parametrize("operation", ["run_task", "run_pre_plan", "run_verification"])
@pytest.mark.parametrize("failed_resources", [("harness",), ("provider",), ("harness", "provider")])
async def test_runtime_retains_failed_cleanup_and_rejects_reuse(tmp_path, monkeypatch, operation, failed_resources):
    from fsq_agent.models import ConfigurationError

    constructed = []
    cleanup_errors = {resource: OSError(f"{resource} cleanup") for resource in failed_resources}

    class Harness(_FakeHarness):
        def close(self):
            if "harness" in cleanup_errors:
                raise cleanup_errors["harness"]

    class Session(_FakeProviderSession):
        async def close(self):
            if "provider" in cleanup_errors:
                raise cleanup_errors["provider"]

    harness, session = Harness(), Session()

    def build_harness(run_id):
        constructed.append("harness")
        return harness

    def build_session(settings):
        constructed.append("provider")
        return session

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", build_session)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), build_harness, engine=_FakeEngine())
    with pytest.raises(OSError if len(failed_resources) == 1 else ExceptionGroup):
        await _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "pending-cleanup")
    resources = {"harness": harness, "provider": session}
    assert runtime._pending_cleanup == [(resources[name], cleanup_errors[name]) for name in failed_resources]

    if operation == "run_pre_plan":
        invocation = runtime.run_pre_plan("Plan task", KnowledgeBundle(), [], "no-rebuild")
    elif operation == "run_verification":
        invocation = runtime.run_verification(Task(description="Test"), [], "no-rebuild")
    else:
        invocation = _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "no-rebuild")
    with pytest.raises(ConfigurationError, match="unresolved owned-resource cleanup") as failure:
        await invocation
    _assert_cleanup_errors(failure.value.__cause__, list(cleanup_errors.values()))
    assert constructed == ["provider", "harness"]


@pytest.mark.parametrize("interruption", ["timeout", "cancelled"])
async def test_late_harness_construction_is_disposed_in_worker(tmp_path, interruption):
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Harness(_FakeHarness):
        def close(self):
            closed.set()

    def build(run_id):
        started.set()
        assert release.wait(5)
        return Harness()

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    settings.agent.step_timeout_seconds = 0.02
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), build)
    operation = asyncio.create_task(runtime._build_harness_with_timeout("late"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if interruption == "cancelled":
            operation.cancel()
        with pytest.raises(TimeoutError if interruption == "timeout" else asyncio.CancelledError):
            await operation
    finally:
        release.set()
        assert await asyncio.to_thread(closed.wait, 2)


@pytest.mark.parametrize("provider_fails", [False, True])
async def test_runtime_preserves_grouped_harness_setup_cleanup_failure(tmp_path, monkeypatch, provider_fails):
    startup_error = ExceptionGroup("Harness setup and cleanup failed", [TimeoutError("Setup timed out"), OSError("Harness cleanup failed")])
    provider_error = OSError("Provider cleanup failed")
    closed = []

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")
            if provider_fails:
                raise provider_error

    async def fail_setup(run_id):
        raise startup_error

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=_FakeEngine())
    monkeypatch.setattr(runtime, "_build_harness_with_timeout", fail_setup)
    with pytest.raises(ExceptionGroup) as failure:
        await _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "grouped-setup")
    if provider_fails:
        assert failure.value.exceptions == (startup_error, provider_error)
    else:
        assert failure.value is startup_error
    assert closed == ["provider"]


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancellation_during_disposal_waits_for_owned_cleanup(tmp_path, monkeypatch, cleanup_fails):
    started, release = threading.Event(), threading.Event()
    closed = []
    cleanup_error = OSError("Harness cleanup failed")

    class Harness(_FakeHarness):
        def close(self):
            started.set()
            assert release.wait(5)
            closed.append("harness")
            if cleanup_fails:
                raise cleanup_error

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), engine=_FakeEngine())
    operation = asyncio.create_task(_run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "during-close"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as failure:
        await operation
    assert failure.value.__cause__ is (cleanup_error if cleanup_fails else None)
    assert closed == ["harness", "provider"]


def _patch_runtime_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    _FakeEngine.requests = []
    monkeypatch.setattr(runtime_module, "build_model_provider_session", lambda _settings: _FakeProviderSession())
    monkeypatch.setattr(runtime_module, "create_agent_engine_for_model", lambda model: _FakeEngine())


def _test_request(runtime: DefaultCodingAgentRuntime, run_id: str = "") -> AgentRequest:
    return runtime._build_request(name="test", instructions="instructions", model_input="test", tools=[], output_type=AgentFinalOutput, run_id=run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://Example.TEST:443/report?q=%2f&next=%2Fdocs#part!",
        "https://[::1]:8443/a(b)?#",
        "https://例子.test/路径\uff08说明\uff09?q=中文\uff0c值#片段\uff01",
        "https://example.test/report?token=runtime-private-start#part",
    ],
)
async def test_runtime_supplies_exact_starting_url_to_planning_and_navigation(tmp_path, monkeypatch, url):
    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.drivers.web._playwright import PlaywrightWebDriver
    from fsq_agent.harnesses._web import WebHarness

    _patch_runtime_engine(monkeypatch)
    goal = "Verify the heading; then visit https://later.test and check its form."
    task = Task(id="exact-start", name=goal, description=goal, planning_reference_text=goal)
    original_task = task.model_dump()
    navigations = []
    requests = []
    events = []

    class Page:
        url = "about:blank"
        viewport_size: ClassVar[dict[str, int]] = {"width": 800, "height": 600}

        def goto(self, target, **kwargs):
            navigations.append(target)
            self.url = target

        def close(self):
            return None

        def screenshot(self, **kwargs):
            return b"controlled screenshot"

        def aria_snapshot(self, **kwargs):
            return '- heading "Example Domain" [level=1]'

    class Engine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            requests.append(request)
            if request.output.name == "GoalPrePlan":
                return await super().run(model, request, on_event=on_event)
            context = json.loads(request.input.rsplit("Authoritative Web starting-address context:\n", 1)[1])
            navigate = next(tool for tool in request.tools if tool.name == "navigate_to")
            arguments = {"url": context["initial_url"], "waitUntil": None}
            assert arguments["url"] == url
            await on_event(AgentEvent(kind="tool_called", tool_name=navigate.name, arguments=arguments, call_id="start-navigation"))
            output = json.loads(await navigate.invoke(ToolCall(name=navigate.name, arguments=arguments, call_id="start-navigation")))
            assert output["status"] == "passed", output
            await on_event(AgentEvent(kind="tool_output", tool_name=navigate.name, output=json.dumps(output), call_id="start-navigation"))
            return AgentResult(final_output=AgentFinalOutput(status="success", summary="Heading checked."))

    settings = Settings(harness={"platform": "web", "web": {"base_url": url}}, agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    settings.execution.post_action_delay_seconds = 0
    settings.runtime_secrets.set_values({"START_TOKEN": "runtime-private-start"})
    browser = tmp_path / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("Controlled browser executable fixture")
    browser.chmod(0o755)
    settings.harness.web.browser_executable_path = browser
    driver = PlaywrightWebDriver(base_url=url, page=Page())
    runtime = DefaultCodingAgentRuntime(
        settings, _EmptyToolFactory(), lambda _: WebHarness(driver, artifact_store=ArtifactStore(run_dir=tmp_path / "exact-start-run", secret_values=("runtime-private-start",))), engine=Engine()
    )
    await runtime.run_pre_plan(goal, KnowledgeBundle(), [], "exact-start-run", event_sink=events.append)
    assert url in requests[0].input
    result = await _run_in_context(runtime, task, KnowledgeBundle(), [], "exact-start-run", event_sink=events.append)

    assert len(requests) == 2
    for request in requests:
        source, context_text = request.input.rsplit("Authoritative Web starting-address context:\n", 1)
        context = json.loads(context_text)
        assert context["initial_url"] == url
        instruction = context["navigation_instruction"]
        assert "first navigation" in instruction
        assert "exact absolute" in instruction
        assert "path, query, and fragment" in instruction
        assert "relative" in instruction
        assert goal in source
    assert json.loads(requests[0].input.split("\n\nAuthoritative Web starting-address context:\n", 1)[0])["reference_text"] == goal
    assert task.model_dump() == original_task
    assert navigations == [url]
    assert result[-1].status == "success", result[-1].model_dump()
    assert "runtime-private-start" not in json.dumps([event.model_dump(mode="json") for event in events])


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,url", [("web", None), ("android", "https://ignored.test/path?q=one#part")])
async def test_runtime_omits_starting_address_context_without_web_target(tmp_path, monkeypatch, platform, url):
    _patch_runtime_engine(monkeypatch)
    settings = Settings(harness={"platform": platform, "web": {"base_url": url}}, agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    browser = tmp_path / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("Controlled browser executable fixture")
    browser.chmod(0o755)
    settings.harness.web.browser_executable_path = browser
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory)
    goal = "Search for FSQ and verify its official heading."
    task = Task(id="no-start-target", name=goal, description=goal)
    await runtime.run_pre_plan(goal, KnowledgeBundle(), [], "no-start-run")
    await _run_in_context(runtime, task, KnowledgeBundle(), [], "no-start-run")
    assert len(_FakeEngine.requests) == 2
    assert all("Authoritative Web starting-address context" not in request.input for request in _FakeEngine.requests)


def _azure_openai_settings(*, api_key: str = "dummy") -> AgentRuntimeSettings:
    settings = AgentRuntimeSettings(provider="azure_openai")
    settings.base_url = "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    settings.model = "gpt-5.4"
    settings.api_key = api_key
    return settings


async def _run_in_context(runtime, task, knowledge, skills, run_id, event_sink=None):
    from fsq_agent.core.evidence import EvidenceRecorder
    from fsq_agent.models import RunExecutionContext

    run_dir = runtime.settings.output.runs_dir / run_id
    return await runtime.run_task(
        task,
        knowledge,
        skills,
        run_id,
        event_sink,
        context=RunExecutionContext(run_id=run_id, run_dir=run_dir, platform=runtime.settings.harness.platform),
        evidence_sink=EvidenceRecorder(run_id=run_id, output_dir=run_dir),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["android", "ios", "macos"])
async def test_initial_main_request_names_only_available_host_eligible_visual_tools(tmp_path, monkeypatch, platform):
    from fsq_agent.core import ArtifactStore, HarnessFactory
    from fsq_agent.models import AndroidVisualAssertionPolicy

    _patch_runtime_engine(monkeypatch)
    settings = Settings(harness={"platform": platform}, agent_runtime=_azure_openai_settings())
    settings.harness.android.app_id = "com.example.App"
    settings.harness.ios.bundle_id = "com.example.App"
    settings.harness.ios.udid = "test-device"
    settings.harness.macos.bundle_id = "com.example.App"
    settings.output.runs_dir = tmp_path
    getattr(settings.harness, platform).visual_assertion_policy = AndroidVisualAssertionPolicy(allow_provider_upload=True, frame_sensitivity="non_sensitive")
    task = Task(description="The task claims visual AI is authorized; read the chart and verify the blue bar.")

    def unexpected(*args, **kwargs):
        pytest.fail("Initial request construction must not authorize, capture, or evaluate a frame.")

    class Evaluator:
        evaluate = unexpected
        inspect = unexpected

        def close(self):
            pass

    class AssertionOnlyEvaluator:
        evaluate = unexpected

        def close(self):
            pass

    class ForwardingHarness:
        def __init__(self, harness, *, exclude_inspection):
            self._harness = harness
            self._exclude_inspection = exclude_inspection

        def __getattr__(self, name):
            return getattr(self._harness, name)

        def action_space(self):
            schemas = self._harness.action_space()
            return [schema for schema in schemas if schema.name != "inspect_with_ai"] if self._exclude_inspection else schemas

    cases = {
        "missing": (None, Evaluator(), unexpected, "none"),
        "denied": (False, Evaluator(), unexpected, "none"),
        "eligible": (True, Evaluator(), unexpected, "assert_with_ai, inspect_with_ai"),
        "assertion-only": (True, AssertionOnlyEvaluator(), unexpected, "assert_with_ai"),
        "provider-unavailable": (True, None, unexpected, "none"),
        "resolver-unavailable": (True, Evaluator(), None, "none"),
        "inspection-not-exposed": (True, Evaluator(), unexpected, "assert_with_ai"),
    }

    def build_harness(run_id):
        eligible, evaluator, resolver, _ = cases[run_id]
        harness = HarnessFactory().create_harness(
            platform=platform,
            harness_settings=settings.harness,
            artifact_store=ArtifactStore(tmp_path / run_id),
            ai_assertion_evaluator=evaluator,
            visual_assertion_policy_resolver=resolver,
            visual_ai_eligible=eligible,
        )
        monkeypatch.setattr(harness.driver, "context", unexpected)
        monkeypatch.setattr(harness.driver, "screenshot", unexpected)
        return ForwardingHarness(harness, exclude_inspection=run_id == "inspection-not-exposed")

    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), build_harness)
    await runtime.run_pre_plan(task.description, KnowledgeBundle(), [], "pre-plan")
    for case in cases:
        await _run_in_context(runtime, task, KnowledgeBundle(), [], case)
    pre_plan, *main_requests = _FakeEngine.requests
    prefix = "Scoped AI visual tools available and host-confirmed eligible for this Run: "
    assert prefix not in pre_plan.input
    assert len(main_requests) == len(cases)
    baseline = main_requests[0]
    schema_view = [(tool.name, tool.description, tool.parameters_schema, tool.strict) for tool in baseline.tools]
    assert {"assert_with_ai", "inspect_with_ai"} <= {tool.name for tool in baseline.tools}
    assert "do not probe" in baseline.instructions
    assert "privacy_blocked or provider_unavailable" in baseline.instructions
    assert "authoritative readback, not visual AI estimation" in baseline.instructions
    for request, (case, (_, _, _, expected)) in zip(main_requests, cases.items(), strict=True):
        assert request.input == baseline.input.replace(f"{prefix}none.", f"{prefix}{expected}.")
        assert f"{prefix}{expected}." in request.input
        assert request.instructions == baseline.instructions
        expected_schema_view = [tool for tool in schema_view if tool[0] != "inspect_with_ai"] if case == "inspection-not-exposed" else schema_view
        assert [(tool.name, tool.description, tool.parameters_schema, tool.strict) for tool in request.tools] == expected_schema_view
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.asyncio
async def test_main_closing_policy_preserves_answer_before_cleanup_and_read_only_verifier(tmp_path, monkeypatch):
    from fsq_agent.agent import CodingAgentPolicy

    requests, order = [], []
    main_output = AgentFinalOutput(status="inconclusive", summary="6510", errors=["Coverage remains bounded."])
    policy = CodingAgentPolicy()

    class Harness(_FakeHarness):
        def close(self):
            order.append("harness.close")

    class Session(_FakeProviderSession):
        async def close(self):
            order.append("provider.close")

    class Engine:
        async def run(self, model, request, *, on_event=None):
            requests.append(request)
            if request.output.name == "GoalPrePlan":
                order.append("preplan")
                assert request.final_turn_instructions is None
                return AgentResult(final_output=GoalPrePlan(goal="Read the distance.", verification_goal="Observed distance is delivered."))
            if request.name.endswith(" verifier"):
                order.append("verifier")
                assert order[-3:-1] == ["harness.close", "provider.close"]
                assert request.tools == ()
                assert request.final_turn_instructions is None
                return AgentResult(final_output=AgentFinalOutput(status="inconclusive", summary="Independent coverage assessment."))
            order.append("main")
            assert "harness.close" not in order
            assert request.final_turn_instructions == policy.final_turn_instructions
            assert request.final_turn_instructions.strip()
            assert "max_turns: 2" in request.input
            action = next(tool for tool in request.tools if tool.name == "tap_on")
            await action.invoke(ToolCall(name="tap_on", arguments={"target": "distance"}, call_id="read-distance"))
            return AgentResult(final_output=main_output, usage=TokenUsage(6, 4, 10, 2))

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.agent_runtime.max_turns = 2
    settings.execution.post_action_delay_seconds = 0
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), policy=policy, engine=Engine())
    task = Task(id="answer-close", description="How many meters?", verification_goal="The observed distance is delivered.")
    await runtime.run_pre_plan(task.description, KnowledgeBundle(), [], "answer-close")
    results = await _run_in_context(runtime, task, KnowledgeBundle(), [], "answer-close")
    runner = next(step for step in results if step.tool_name == "agent_runtime.runner")
    assert AgentFinalOutput.model_validate(runner.tool_output) == main_output
    assert runner.actual_outcome == policy.serialize_agent_final_output(main_output)
    verification = await runtime.run_verification(task, results, "answer-close")
    assert AgentFinalOutput.model_validate(verification[-1].tool_output).summary == "Independent coverage assessment."
    assert AgentFinalOutput.model_validate(runner.tool_output).summary == "6510"
    assert len(requests) == 3
    assert all(request.max_turns == 2 for request in requests)
    assert order == ["preplan", "provider.close", "main", "harness.close", "provider.close", "verifier", "provider.close"]


@pytest.mark.asyncio
async def test_runtime_failure_returns_failed_step() -> None:
    settings = Settings(agent_runtime=_azure_openai_settings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _run_id: _FailingHarness())
    task = Task(
        id="runtime-failure",
        name="Runtime Failure",
        description="Trigger harness failure.",
        acceptance_criteria=["A failed step is returned."],
    )

    results = await _run_in_context(runtime, task, KnowledgeBundle(), [], "runtime-failure-2026-05-09_00-00-00")

    assert results[0].status == "failed"
    assert results[0].tool_name == "agent_runtime.runner"
    assert results[0].tool_output["failure_category"] == "agent_runtime_error"
    assert results[0].tool_output["failure_reason"] == "agent_runtime_error"
    assert results[0].actual_outcome == "Agent runtime execution failed before producing structured verification output."
    assert "Harness action-space discovery failed" in str(results[0].error)


@pytest.mark.asyncio
async def test_runtime_classifies_durable_evidence_failure_separately_from_model_failure() -> None:
    class EvidenceFailureEngine:
        async def run(self, model, request, *, on_event=None):
            raise EngineError("evidence", "Durable execution evidence failed.")

    runtime = DefaultCodingAgentRuntime(
        Settings(agent_runtime=_azure_openai_settings()),
        _EmptyToolFactory(),
        _fake_harness_factory,
        engine=EvidenceFailureEngine(),
    )

    results = await _run_in_context(runtime, Task(id="evidence-failure", description="Fail evidence."), KnowledgeBundle(), [], "evidence-failure-run")

    assert results[0].status == "failed"
    assert results[0].tool_output["failure_category"] == "artifact_error"
    assert results[0].tool_output["failure_reason"] == "evidence_persistence"
    assert results[0].actual_outcome == "Agent runtime stopped because durable execution evidence failed."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,reason,status_code",
    [
        ("unavailable", "connection_error", None),
        ("unavailable", "read_error", None),
        ("unavailable", "remote_protocol_error", None),
        ("unavailable", "incomplete_read", None),
        ("timeout", "timeout", None),
        ("rate_limit", "rate_limit", 429),
        ("unavailable", "service_unavailable", 503),
        ("unavailable", "stream_error", 100),
        ("unavailable", "stream_incomplete", 599),
    ],
)
async def test_exhausted_stream_failure_preserves_safe_fields_in_failed_runner_and_event(tmp_path, monkeypatch, category, reason, status_code):
    canary = "private-transport-credential-and-response-fragment"
    primary = EngineError(category, "Model streaming operation failed.", reason=reason, status_code=status_code)
    cause = RuntimeError(canary)
    primary.context = {"failure_category": canary, "request": canary}
    requests, closed, events = [], [], []

    class Harness(_FakeHarness):
        def close(self):
            closed.append("harness")

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            requests.append(request)
            raise primary from cause

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.agent_runtime.max_turns = 1
    settings.output.runs_dir = tmp_path
    harness = Harness()
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: harness, engine=FailedEngine())
    results = await _run_in_context(runtime, Task(id="stream-failure", description="Complete the goal."), KnowledgeBundle(), [], "stream-failure-run", events.append)

    assert len(results) == 1
    runner = results[0]
    assert runner.status == "failed"
    assert runner.tool_name == "agent_runtime.runner"
    assert runner.error == "Model streaming operation failed."
    expected = {
        "failure_category": "agent_runtime_error",
        "failure_reason": "agent_runtime_error",
        "failure_summary": "Agent runtime execution failed before producing structured verification output.",
        "engine_category": category,
        "engine_reason": reason,
    }
    if status_code is not None:
        expected["status_code"] = status_code
    assert runner.tool_output == expected
    assert runner.actual_outcome == expected["failure_summary"]
    failures = [json.loads(event.model_dump_json()) for event in events if event.type == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["payload"] == expected
    assert failures[0]["run_id"] == "stream-failure-run"
    assert failures[0]["task_id"] == "stream-failure"
    assert canary not in runner.model_dump_json() + json.dumps([event.model_dump(mode="json") for event in events])
    assert not any("termination_reason" in event.payload for event in events)
    assert len(requests) == 1
    assert harness.steps == []
    assert closed == ["harness", "provider"]
    assert primary.__cause__ is cause


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,reason,status_code,expected",
    [
        ("unknown-credential-canary", "unknown-credential-canary", "unknown-credential-canary", {}),
        (["unknown-credential-canary"], {"secret": "unknown-credential-canary"}, {"secret": "unknown-credential-canary"}, {}),
        (None, False, None, {}),
        ("unavailable", "read_error", True, {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unavailable", "read_error", False, {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unavailable", "read_error", 99, {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unavailable", "read_error", 600, {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unavailable", "read_error", 503.0, {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unavailable", "read_error", "503", {"engine_category": "unavailable", "engine_reason": "read_error"}),
        ("unknown-credential-canary", "read_error", 503, {"engine_reason": "read_error", "status_code": 503}),
        ("timeout", "unknown-credential-canary", None, {"engine_category": "timeout"}),
    ],
)
async def test_runtime_omits_unknown_or_malformed_engine_fields_without_leaking_causes(tmp_path, monkeypatch, category, reason, status_code, expected):
    canary = "unknown-credential-canary"
    primary = EngineError(category, "Model operation failed.", reason=reason, status_code=status_code)
    primary.context = {"engine_category": canary, "engine_reason": canary, "status_code": canary}
    primary.raw_response = {"body": canary}

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            raise primary from RuntimeError(canary)

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: _FakeProviderSession())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=FailedEngine())
    events = []
    results = await _run_in_context(runtime, Task(description="Fail safely."), KnowledgeBundle(), [], "malformed-engine-run", events.append)

    runner = results[0]
    assert runner.status == "failed"
    assert runner.tool_name == "agent_runtime.runner"
    assert runner.tool_output["failure_category"] == runner.tool_output["failure_reason"] == "agent_runtime_error"
    assert {key: value for key, value in runner.tool_output.items() if key in {"engine_category", "engine_reason", "status_code"}} == expected
    failure = json.loads(events[-1].model_dump_json())
    assert failure["type"] == "run_failed"
    assert failure["payload"] == runner.tool_output
    assert canary not in runner.model_dump_json() + json.dumps([event.model_dump(mode="json") for event in events])


@pytest.mark.asyncio
async def test_runtime_does_not_accept_engine_fields_from_other_exceptions(tmp_path, monkeypatch):
    primary = RuntimeError("Runtime operation failed.")
    primary.category, primary.reason, primary.status_code = "unavailable", "read_error", 503

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            raise primary

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: _FakeProviderSession())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=FailedEngine())
    events = []
    results = await _run_in_context(runtime, Task(description="Fail runtime."), KnowledgeBundle(), [], "non-engine-run", events.append)

    assert results[0].status == "failed"
    assert not {"engine_category", "engine_reason", "status_code"}.intersection(results[0].tool_output)
    assert json.loads(events[-1].model_dump_json())["payload"] == results[0].tool_output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,reason,failure_category,failure_reason,summary",
    [
        ("evidence", "read_error", "artifact_error", "evidence_persistence", "Agent runtime stopped because durable execution evidence failed."),
        (
            "incomplete",
            "content_filter",
            "provider_content_filter",
            "content_filter",
            "Agent runtime execution ended with an incomplete provider response due to content filtering.",
        ),
        ("incomplete", "incomplete", "provider_response_incomplete", "incomplete", "Agent runtime execution ended with an incomplete provider response."),
    ],
)
async def test_safe_engine_fields_do_not_replace_existing_failure_classification(tmp_path, monkeypatch, category, reason, failure_category, failure_reason, summary):
    canary = "private-failure-context-canary"
    primary = EngineError(category, "Engine operation failed.", reason=reason, status_code=503)
    primary.context = {"failure_category": canary, "failure_reason": canary, "failure_summary": canary}

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            raise primary from RuntimeError(canary)

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: _FakeProviderSession())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=FailedEngine())
    events = []
    results = await _run_in_context(runtime, Task(description="Keep failure classification."), KnowledgeBundle(), [], "classified-engine-run", events.append)

    runner = results[0]
    assert runner.status == "failed"
    assert runner.tool_output["failure_category"] == failure_category
    assert runner.tool_output["failure_reason"] == failure_reason
    assert runner.tool_output["failure_summary"] == runner.actual_outcome == summary
    assert runner.tool_output["engine_category"] == category
    assert runner.tool_output["status_code"] == 503
    if reason == "read_error":
        assert runner.tool_output["engine_reason"] == reason
    else:
        assert "engine_reason" not in runner.tool_output
    assert json.loads(events[-1].model_dump_json())["payload"] == runner.tool_output
    assert canary not in runner.model_dump_json() + json.dumps([event.model_dump(mode="json") for event in events])


@pytest.mark.asyncio
async def test_main_turn_limit_returns_complete_inconclusive_task_result(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fsq_agent.agent import FsqAgent
    from fsq_agent.core import ArtifactStore
    from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, load_run_metadata

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.agent_runtime.max_turns = 100
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / "runs"
    settings.agent_runtime.user_config_root = tmp_path / "user"
    requests, closed = [], []
    events = []

    def event_sink(event):
        if event.payload.get("termination_reason") == "max_turns":
            assert closed == ["harness", "provider"]
        events.append(event)

    class Harness(_FakeHarness):
        def close(self):
            closed.append("harness")

        def capture_artifact(self, kind, reason, context, step_id, phase):
            store = ArtifactStore(self.run_dir)
            if kind == "ui_snapshot":
                return store.write_json(kind=kind, step_id=step_id, phase=phase, name=reason, payload={"xml": "<hierarchy/>"})
            return store.write_bytes(kind=kind, step_id=step_id, phase=phase, name=reason, data=b"controlled-observation")

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")

    class BudgetEngine:
        async def run(self, model, request, *, on_event=None):
            requests.append(request)
            action = next(tool for tool in request.tools if tool.name == "tap_on")
            await action.invoke(ToolCall(name="tap_on", arguments={"target": "controlled-target"}, call_id="before-limit"))
            raise EngineError("max_turns", "Main execution exhausted its turn budget.")

    def harness_factory(run_id):
        harness = Harness()
        harness.run_dir = settings.output.runs_dir / run_id
        return harness

    monkeypatch.setattr("fsq_agent.agent._core.refresh_model_provider_session", lambda _: SimpleNamespace(close_sync=lambda: None))
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), harness_factory, engine=BudgetEngine())
    agent = FsqAgent.from_settings(settings, runtime_factory=lambda *_args, **_kwargs: runtime)
    task = Task(id="turn-limit", description="Complete the controlled goal.", key_actions=["Inspect the goal state"], verification_goal="The controlled goal is complete.")
    result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings, event_sink=event_sink))
    task_result = result.task_result
    directory = settings.output.runs_dir / task_result.report.run_id
    frozen = json.loads((directory / "execution-result.json").read_text())
    runners = [step for step in task_result.steps if step.tool_name == "agent_runtime.runner"]
    assert len(runners) == 1
    assert runners[0].status == "success"
    output = AgentFinalOutput.model_validate(runners[0].tool_output)
    assert output.status == task_result.status == task_result.verification.status == frozen["outcome"] == "inconclusive"
    assert output.summary == task_result.verification.summary
    assert output.summary.strip()
    assert output.satisfied_criteria == output.evidence == []
    assert any("max_turns" in error for error in output.errors)
    assert len(requests) == 1
    assert requests[0].max_turns == 100
    assert closed == ["harness", "provider"]
    assert frozen["counts"]["total"] == frozen["counts"]["passed"] == 1
    assert load_run_metadata(directory).status == "inconclusive"
    assert task_result.report.path.is_file()
    (tmp_path / "task-result.json").write_text(task_result.model_dump_json(), encoding="utf-8")
    assert any(event.payload.get("termination_reason") == "max_turns" for event in events)
    assert runtime._turn_limit_result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["harness", "provider"])
async def test_main_turn_limit_does_not_hide_pending_cleanup(tmp_path, monkeypatch, resource):
    primary = EngineError("max_turns", "Main execution exhausted its turn budget.")
    cleanup = OSError("Owned resource remains pending.")
    closed = []
    events = []

    class Harness(_FakeHarness):
        def close(self):
            closed.append("harness")
            if resource == "harness":
                raise cleanup

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")
            if resource == "provider":
                raise cleanup

    class BudgetEngine:
        async def run(self, model, request, *, on_event=None):
            raise primary

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), engine=BudgetEngine())
    with pytest.raises(ExceptionGroup) as failure:
        await _run_in_context(runtime, Task(description="Exhaust turns"), KnowledgeBundle(), [], "pending-limit", events.append)
    assert failure.value.exceptions == (primary, cleanup)
    assert closed == ["harness", "provider"]
    assert not any(event.payload.get("termination_reason") == "max_turns" for event in events)
    assert runtime._turn_limit_result is None


@pytest.mark.asyncio
async def test_runtime_emits_startup_events_before_main_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="startup", name="Startup", description="Run startup.")
    events: list[Any] = []

    results = await _run_in_context(runtime, task, KnowledgeBundle(), [], "startup-run", event_sink=events.append)

    assert results[-1].status == "success"
    titles = [event.title for event in events]
    expected_titles = [
        "Runtime startup started",
        "Provider setup started",
        "Provider setup completed",
        "Harness setup started",
        "Harness setup completed",
        "Tool setup started",
        "Tool setup completed",
        "Agent runtime ready",
        "Planning started",
    ]
    for title in expected_titles:
        assert title in titles
    assert [titles.index(title) for title in expected_titles] == sorted(titles.index(title) for title in expected_titles)
    harness_started = events[titles.index("Harness setup started")]
    assert harness_started.payload["timeout_seconds"] == 60
    assert harness_started.payload["app_id_configured"] is False
    assert harness_started.payload["serial_selected"] is False
    assert "serial_configured" not in harness_started.payload
    harness_completed = events[titles.index("Harness setup completed")]
    assert harness_completed.payload["harness_class"] == "_FakeHarness"
    assert "driver_class" not in harness_completed.payload


@pytest.mark.asyncio
async def test_runtime_routes_three_agent_flows_through_neutral_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="explicit-model-settings", name="Model Settings", description="Run with stable runtime settings.")

    await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "explicit-model-settings-run")
    await _run_in_context(runtime, task, KnowledgeBundle(), [], "explicit-model-settings-run")
    await runtime.run_verification(task, [], "explicit-model-settings-run", None)

    assert [request.name for request in _FakeEngine.requests] == [
        "fsq-agent pre-planner",
        "fsq-agent",
        "fsq-agent verifier",
    ]
    assert [request.output.name for request in _FakeEngine.requests] == ["GoalPrePlan", "AgentFinalOutput", "AgentFinalOutput"]
    assert all(request.stream for request in _FakeEngine.requests)
    assert len(_FakeEngine.requests[0].tools) == 2
    assert _FakeEngine.requests[-1].tools == ()


@pytest.mark.asyncio
async def test_runtime_emits_one_dynamic_agent_token_usage_event_from_engine_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class UsageEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            result = await super().run(model, request, on_event=on_event)
            if request.output is not None and request.output.name == "AgentFinalOutput":
                return AgentResult(
                    final_output=result.final_output,
                    usage=TokenUsage(1200, 80, 1280, requests=3, cached_input_tokens=900, reasoning_tokens=25),
                )
            return result

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(
        Settings(agent_runtime=_azure_openai_settings()),
        _EmptyToolFactory(),
        _fake_harness_factory,
        engine=UsageEngine(),
    )
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="usage", description="Measure usage."), KnowledgeBundle(), [], "usage-run", events.append)

    assert results[-1].status == "success"
    usage_events = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert len(usage_events) == 1
    assert usage_events[0].payload == {
        "provider": "azure_openai",
        "model": "gpt-5.4",
        "requests": 3,
        "input_tokens": 1200,
        "output_tokens": 80,
        "total_tokens": 1280,
        "cached_input_tokens": 900,
        "reasoning_tokens": 25,
    }


@pytest.mark.asyncio
async def test_runtime_does_not_emit_token_usage_without_engine_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    events: list[Any] = []

    await _run_in_context(runtime, Task(id="no-usage", description="No usage."), KnowledgeBundle(), [], "no-usage-run", events.append)

    assert all(event.type != "dynamic_agent_token_usage" for event in events)


@pytest.mark.asyncio
async def test_runtime_does_not_fabricate_usage_when_engine_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FailingEngine:
        async def run(self, model, request, *, on_event=None):
            calls.append(request)
            raise EngineError("incomplete", "Model stream failed.")

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=FailingEngine())
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="failed-usage", description="Fail after usage."), KnowledgeBundle(), [], "failed-usage-run", events.append)

    assert results[0].status == "failed"
    usage_events = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert usage_events == []
    assert len(calls) == 1
    assert events[-1].type == "run_failed"


@pytest.mark.asyncio
async def test_runtime_does_not_retry_usage_event_when_sink_fails_after_receiving_it(monkeypatch: pytest.MonkeyPatch) -> None:
    class UsageEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            result = await super().run(model, request, on_event=on_event)
            return AgentResult(final_output=result.final_output, usage=TokenUsage(100, 10, 110))

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(
        Settings(agent_runtime=_azure_openai_settings()),
        _EmptyToolFactory(),
        _fake_harness_factory,
        engine=UsageEngine(),
    )
    events: list[Any] = []

    def failing_usage_sink(event: Any) -> None:
        events.append(event)
        if event.type == "dynamic_agent_token_usage":
            raise RuntimeError("downstream sink failed after persistence")

    results = await _run_in_context(
        runtime,
        Task(id="usage-sink-failure", description="Fail the usage sink."),
        KnowledgeBundle(),
        [],
        "usage-sink-failure-run",
        failing_usage_sink,
    )

    assert results[0].status == "failed"
    assert len([event for event in events if event.type == "dynamic_agent_token_usage"]) == 1
    assert events[-1].type == "run_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "high"])
async def test_runtime_preserves_effort_with_durable_context(monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    _patch_runtime_engine(monkeypatch)
    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.agent_runtime.reasoning_effort = effort
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="explicit-model-settings", name="Model Settings", description="Run with configured effort.")

    await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "explicit-model-settings-run")
    await _run_in_context(runtime, task, KnowledgeBundle(), [], "explicit-model-settings-run")
    await runtime.run_verification(task, [], "explicit-model-settings-run", None)

    assert [request.name for request in _FakeEngine.requests] == [
        "fsq-agent pre-planner",
        "fsq-agent",
        "fsq-agent verifier",
    ]
    assert all(request.reasoning_effort == effort for request in _FakeEngine.requests)


@pytest.mark.asyncio
async def test_runtime_harness_construction_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)

    def fail_harness(_run_id: str) -> _FakeHarness:
        raise RuntimeError("device connect failed")

    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), fail_harness)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="failure", description="Fail startup."), KnowledgeBundle(), [], "failure-run", events.append)

    assert results[0].status == "failed"
    assert "device connect failed" in str(results[0].error)
    titles = [event.title for event in events]
    assert "Harness setup started" in titles
    assert "Harness setup completed" not in titles
    assert titles[-1] == "Agent run failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("category,reason", [("incomplete", "content_filter"), ("unavailable", "read_error"), ("timeout", "timeout"), ("rate_limit", "rate_limit")])
async def test_runtime_cleanup_failure_preserves_original_failed_result(monkeypatch: pytest.MonkeyPatch, category: str, reason: str) -> None:
    primary = EngineError(category, "Primary model failure", reason=reason)
    cleanup_error = EngineError("cleanup", "Secondary cleanup failure")

    class FailedCloseSession(_FakeProviderSession):
        async def close(self) -> None:
            raise cleanup_error

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            raise primary

    _patch_runtime_engine(monkeypatch)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda settings: FailedCloseSession())
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=FailedEngine())

    with pytest.raises(ExceptionGroup) as failure:
        await _run_in_context(runtime, Task(id="failure", description="Fail run"), KnowledgeBundle(), [], "failed-cleanup-run")

    assert failure.value.exceptions == (primary, cleanup_error)
    assert primary.category == category
    assert primary.reason == reason


@pytest.mark.parametrize("operation", ["run_task", "run_pre_plan", "run_verification"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "success"])
@pytest.mark.parametrize("cleanup_outcome", ["success", "failure", "cancelled"])
async def test_runtime_cleanup_preserves_primary_outcome_with_real_session(monkeypatch: pytest.MonkeyPatch, operation: str, outcome: str, cleanup_outcome: str) -> None:
    from fsq_agent.models import PlanningError

    primary = asyncio.CancelledError("Primary cancellation") if outcome == "cancelled" else EngineError("incomplete", "Primary model failure", reason="content_filter")
    cleanup_error = asyncio.CancelledError("Secondary cleanup cancellation") if cleanup_outcome == "cancelled" else EngineError("cleanup", "Secondary cleanup failure")
    closed = []

    class ControlledProvider:
        def get_model(self, model_name: str):
            return _FakeModel()

        async def aclose(self) -> None:
            closed.append(True)
            if cleanup_outcome != "success":
                raise cleanup_error

    class ControlledEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            if outcome != "success":
                raise primary
            return await super().run(model, request, on_event=on_event)

    settings = Settings(agent_runtime=_azure_openai_settings())
    monkeypatch.setattr("fsq_agent.providers._session.create_model_provider", lambda **kwargs: ControlledProvider())
    session = build_model_provider_session(settings)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda configured: session)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=ControlledEngine())
    task = Task(id="failure-precedence", description="Verify failure precedence")

    async def invoke_operation():
        if operation == "run_pre_plan":
            return await runtime.run_pre_plan("Plan task", KnowledgeBundle(), [], "precedence-run")
        if operation == "run_verification":
            return await runtime.run_verification(task, [], "precedence-run", None)
        return await _run_in_context(runtime, task, KnowledgeBundle(), [], "precedence-run")

    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError) as failure:
            await invoke_operation()
        assert failure.value is primary
        assert primary.__cause__ is (None if cleanup_outcome == "success" else cleanup_error)
    elif cleanup_outcome != "success":
        if outcome == "success":
            with pytest.raises(type(cleanup_error)) as failure:
                await invoke_operation()
            assert failure.value is cleanup_error
        else:
            with pytest.raises(BaseExceptionGroup) as failure:
                await invoke_operation()
            execution_error, secondary_error = failure.value.exceptions
            assert secondary_error is cleanup_error
            if operation == "run_pre_plan":
                assert isinstance(execution_error, PlanningError)
                assert execution_error.__cause__ is primary
            else:
                assert execution_error is primary
    elif outcome == "failure" and operation == "run_pre_plan":
        with pytest.raises(PlanningError) as failure:
            await invoke_operation()
        assert failure.value.__cause__ is primary
    else:
        results = await invoke_operation()
        if operation == "run_pre_plan":
            assert isinstance(results, GoalPrePlan)
        else:
            assert results[0].status == ("failed" if outcome == "failure" else "success")
            if outcome == "failure":
                assert results[0].error == "Primary model failure"
    assert closed == [True]


@pytest.mark.parametrize("operation", ["run_task", "run_pre_plan", "run_verification"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_runtime_cancellation_during_provider_cleanup_preserves_all_failures(tmp_path, monkeypatch, operation, outcome, cleanup_fails):
    from fsq_agent.models import PlanningError

    started, release = asyncio.Event(), asyncio.Event()
    closed = []
    primary = asyncio.CancelledError("Primary cancellation") if outcome == "cancelled" else EngineError("model", "Primary failure")
    cleanup_error = OSError("Provider cleanup failed")

    class Session(_FakeProviderSession):
        async def close(self):
            started.set()
            await release.wait()
            closed.append("provider")
            if cleanup_fails:
                raise cleanup_error

    class Engine(_FakeEngine):
        async def run(self, *args, **kwargs):
            if outcome != "success":
                raise primary
            return await super().run(*args, **kwargs)

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=Engine())

    async def invoke_operation():
        if operation == "run_pre_plan":
            return await runtime.run_pre_plan("Plan task", KnowledgeBundle(), [], "cancel-cleanup")
        if operation == "run_verification":
            return await runtime.run_verification(Task(description="Test"), [], "cancel-cleanup")
        return await _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "cancel-cleanup")

    invocation = asyncio.create_task(invoke_operation())
    try:
        await asyncio.wait_for(started.wait(), 2)
        invocation.cancel("During cleanup")
        await asyncio.sleep(0)
        invocation.cancel("Repeated cancellation")
        await asyncio.sleep(0)
        assert not invocation.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as failure:
        await invocation
    assert closed == ["provider"]
    cancellation = failure.value
    if outcome == "cancelled":
        assert cancellation is primary
        assert cancellation.__cause__ is (cleanup_error if cleanup_fails else None)
    else:
        assert cancellation.args == ("During cleanup",)
        cause = cancellation.__cause__
        if outcome == "failure":
            if cleanup_fails:
                assert isinstance(cause, BaseExceptionGroup)
                execution_error, secondary_error = cause.exceptions
                assert secondary_error is cleanup_error
            else:
                execution_error = cause
            if operation == "run_pre_plan":
                assert isinstance(execution_error, PlanningError)
                assert execution_error.__cause__ is primary
            else:
                assert execution_error is primary
        else:
            assert cause is (cleanup_error if cleanup_fails else None)


@pytest.mark.asyncio
async def test_runtime_harness_construction_timeout_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)

    def slow_harness(_run_id: str) -> _FakeHarness:
        time.sleep(2)
        return _FakeHarness()

    settings = Settings(agent={"step_timeout_seconds": 1}, agent_runtime=_azure_openai_settings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), slow_harness)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="timeout", description="Timeout startup."), KnowledgeBundle(), [], "timeout-run", events.append)

    assert results[0].status == "failed"
    assert "Harness setup timed out after 1 seconds" in str(results[0].error)
    titles = [event.title for event in events]
    assert "Harness setup started" in titles
    assert "Harness setup completed" not in titles
    assert titles[-1] == "Agent run failed"


def test_runtime_harness_timeout_does_not_wait_for_worker_shutdown() -> None:
    def slow_harness(_run_id: str) -> _FakeHarness:
        time.sleep(3)
        return _FakeHarness()

    settings = Settings(agent={"step_timeout_seconds": 1}, agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), slow_harness)

    async def run_timeout() -> None:
        with pytest.raises(TimeoutError, match="Harness setup timed out after 1 seconds"):
            await runtime._build_harness_with_timeout("shutdown-run")

    started = time.perf_counter()
    asyncio.run(run_timeout())

    assert time.perf_counter() - started < 1.8


@pytest.mark.asyncio
async def test_runtime_classifies_engine_content_filter_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ContentFilterEngine:
        async def run(self, model, request, *, on_event=None):
            raise EngineError("incomplete", "Model response incomplete.", reason="content_filter")

    _patch_runtime_engine(monkeypatch)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.create_agent_engine_for_model", lambda model: _ContentFilterEngine())
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="content-filter", description="Trigger content filter."), KnowledgeBundle(), [], "content-filter-run", events.append)

    assert results[0].status == "failed"
    assert results[0].actual_outcome == "Agent runtime execution ended with an incomplete provider response due to content filtering."
    assert results[0].tool_output["failure_category"] == "provider_content_filter"
    assert events[-1].type == "run_failed"
    assert events[-1].payload["failure_category"] == "provider_content_filter"
    assert events[-1].payload["failure_reason"] == "content_filter"


def test_runtime_builds_configured_web_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}

    class _FakeWebDriver:
        def __init__(self, **kwargs: Any) -> None:
            calls["driver"] = kwargs

    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    monkeypatch.setattr("fsq_agent.drivers._factory.PlaywrightWebDriver", _FakeWebDriver)
    monkeypatch.setattr(runtime_module, "build_ai_assertion_evaluator", lambda _settings: "ai-evaluator")
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_text("", encoding="utf-8")
    settings = Settings(
        harness={
            "platform": "web",
            "web": {
                "backend": "playwright",
                "channel": "chrome",
                "headless": False,
                "base_url": "https://example.test",
                "viewport_width": 390,
                "viewport_height": 844,
            },
        },
        output={"root_dir": tmp_path / "output"},
        agent_runtime=AgentRuntimeSettings(),
    )
    settings.harness.web.browser_executable_path = chrome_path
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    payload = runtime._harness_setup_payload()
    harness = runtime._build_harness("web-run")
    completed_payload = runtime._harness_setup_payload(harness)

    assert calls["driver"] == {
        "channel": "chrome",
        "executable_path": chrome_path,
        "headless": False,
        "base_url": "https://example.test",
        "viewport": (390, 844),
        "attach": False,
        "attach_endpoint": "http://127.0.0.1:9222",
    }
    assert payload == {
        "platform": "web",
        "timeout_seconds": 60,
        "backend": "playwright",
        "channel": "chrome",
        "browser_executable_configured": True,
        "headless": False,
        "base_url_configured": True,
        "viewport_configured": True,
    }
    assert completed_payload["harness_class"] == "WebHarness"
    assert completed_payload["driver_class"] == "_FakeWebDriver"


def test_runtime_builds_configured_macos_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}

    class _FakeMacOSDriver:
        def __init__(self, **kwargs: Any) -> None:
            calls["driver"] = kwargs

    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    monkeypatch.setattr("fsq_agent.drivers._factory.AppiumConnectMacOSDriver", _FakeMacOSDriver)
    monkeypatch.setattr(runtime_module, "build_ai_assertion_evaluator", lambda _settings: "ai-evaluator")
    settings = Settings(
        harness={
            "platform": "macos",
            "macos": {
                "backend": "appium_connect_agent",
                "page_source_max_depth": 7,
                "action_timeout_seconds": 11,
                "new_command_timeout_seconds": 420,
                "skip_app_kill": False,
            },
        },
        output={"root_dir": tmp_path / "output"},
        agent_runtime=AgentRuntimeSettings(),
    )
    settings.harness.macos.bundle_id = "com.example.MacApp"
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    payload = runtime._harness_setup_payload()
    harness = runtime._build_harness("mac-run")
    completed_payload = runtime._harness_setup_payload(harness)

    assert calls["driver"] == {
        "bundle_id": "com.example.MacApp",
        "app_path": None,
        "page_source_max_depth": 7,
        "action_timeout_seconds": 11,
        "new_command_timeout_seconds": 420,
        "skip_app_kill": False,
    }
    assert payload == {
        "platform": "macos",
        "timeout_seconds": 60,
        "backend": "appium_connect_agent",
        "bundle_id_configured": True,
        "app_path_configured": False,
        "action_timeout_seconds": 11,
        "new_command_timeout_seconds": 420,
        "skip_app_kill": False,
        "configured_skill_names": [],
    }
    assert completed_payload["harness_class"] == "MacOSHarness"
    assert completed_payload["driver_class"] == "_FakeMacOSDriver"


def test_runtime_builds_step_results_from_structured_pre_plan() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    final_output = """
{
    "status": "failed",
    "summary": "Could not finish.",
    "pre_plan": [
        {
            "step_id": 1,
            "action": "Open browser",
            "success_criteria": ["Browser is open"],
            "status": "success"
        },
        {
            "step_id": 2,
            "action": "Add page to favorites",
            "success_criteria": ["Page is favorited"],
            "status": "adjusted"
        }
    ],
    "plan_updates": ["Used keyboard shortcut after toolbar button was unavailable."],
    "satisfied_criteria": ["Browser is open"],
    "unmet_criteria": ["Page is favorited"],
    "evidence": [],
    "errors": []
}
"""

    steps = runtime._build_pre_plan_step_results(final_output)

    assert [step.step_id for step in steps] == [1, 2]
    assert [step.status for step in steps] == ["success", "adjusted"]
    assert [step.duration_ms for step in steps] == [0, 0]
    assert steps[0].tool_name == "pre_plan"
    assert "Browser is open" in steps[0].actual_outcome
    assert "Used keyboard shortcut" in steps[1].actual_outcome


def test_runtime_task_input_includes_runtime_secret_names_and_warnings() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_EMAIL": "user@example.com"})
    runtime_secrets.allowed_env_names.append("TEST_ACCOUNT_PASSWORD")
    settings = Settings(
        agent_runtime=AgentRuntimeSettings(),
        runtime_secrets=runtime_secrets,
    )
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    task = Task(id="login", name="Login", description="Sign in.")

    task_input = runtime._build_task_input(task)

    assert '"runtime_secret_names": [\n    "TEST_ACCOUNT_EMAIL"\n  ]' in task_input
    assert "Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set." in task_input
    assert "user@example.com" not in task_input


def test_pre_plan_input_includes_available_platform_tools() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Open downloads.",
            KnowledgeBundle(),
            [],
            available_platform_tools=[
                {
                    "name": "tap_on",
                    "alias": "tapOn",
                    "aliases": [],
                    "executor_kind": "driver",
                    "step_kind": "action",
                    "platform": "android",
                }
            ],
        )
    )

    assert payload["available_platform_tools"] == [
        {
            "name": "tap_on",
            "alias": "tapOn",
            "aliases": [],
            "executor_kind": "driver",
            "step_kind": "action",
            "platform": "android",
        }
    ]


def test_pre_plan_input_includes_runtime_secret_names_without_values() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Sign in.",
            KnowledgeBundle(),
            [],
            runtime_secret_names=["TEST_ACCOUNT_EMAIL"],
            runtime_secret_warnings=["Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set."],
        )
    )

    assert payload["runtime_secret_names"] == ["TEST_ACCOUNT_EMAIL"]
    assert payload["runtime_secret_warnings"] == ["Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set."]


def test_pre_plan_input_includes_loaded_skills() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Open downloads.",
            KnowledgeBundle(),
            [SkillBundle(name="automation-basics", description="Use semantic actions.", kind="markdown", instructions="Prefer semantic actions.")],
        )
    )

    assert payload["skills"] == [
        {
            "name": "automation-basics",
            "description": "Use semantic actions.",
            "instructions": "Prefer semantic actions.",
        }
    ]


def test_runtime_pre_plan_tool_summary_uses_active_platform_registry() -> None:
    settings = Settings(harness={"platform": "android"}, agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    tools = runtime._pre_plan_tool_summary()

    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["tap_on"]["alias"] == "tapOn"
    assert by_name["tap_at"]["alias"] == "tapAt"
    assert by_name["ui_snapshot"]["alias"] == "uiTree"
    assert by_name["assert_visible"]["step_kind"] == "assertion"
    assert "get_runtime_secret" not in by_name


def test_runtime_instructions_exclude_loader_diagnostics() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"project.md": "Use Edge account guidance."}, warnings=["missing optional knowledge"])
    skills = [SkillBundle(name="automation-basics", kind="markdown", instructions="Use semantic actions.")]

    instructions = runtime._build_instructions(knowledge, skills)

    assert "Custom operator instructions:" not in instructions
    assert "Knowledge warnings:" not in instructions
    assert "Skill warnings:" not in instructions
    assert "missing optional knowledge" not in instructions
    assert "Use Edge account guidance." in instructions
    assert "Use semantic actions." in instructions
    assert "Final output JSON Schema:" not in instructions
    assert "AgentFinalOutput structured output required by the agent runtime" in instructions


def test_runtime_instructions_use_configured_prompt_templates(tmp_path: Path) -> None:
    agent_template = tmp_path / "agent.j2"
    task_template = tmp_path / "task.j2"
    agent_template.write_text(
        "Configured base instruction.\nConfigured knowledge:\n{% for item in private_knowledge %}- {{ item.key }}={{ item.value }}\n{% endfor %}",
        encoding="utf-8",
    )
    task_template.write_text(
        "Task {{ task.id }}: {{ task.description }}\n{% if task.acceptance_criteria %}{{ task.acceptance_criteria | join(', ') }}{% else %}Configured no criteria text.{% endif %}\n",
        encoding="utf-8",
    )
    settings = Settings(
        agent_runtime=AgentRuntimeSettings(
            prompt={
                "agent_template_path": agent_template,
                "task_template_path": task_template,
            },
        )
    )
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"k": "v"})

    instructions = runtime._build_instructions(knowledge, [])
    task_input = runtime._build_task_input(Task(id="t1", description="Do it."))

    assert instructions.startswith("Configured base instruction.")
    assert "Configured knowledge:" in instructions
    assert task_input == "Task t1: Do it.\nConfigured no criteria text."


def test_runtime_instructions_include_knowledge_index_content() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"project.md": "Use Other ways to sign in, then choose password sign-in."})

    instructions = runtime._build_instructions(knowledge, [])

    assert "Private knowledge:" in instructions
    assert "project.md" in instructions
    assert "choose password sign-in" in instructions


def test_agent_runtime_settings_rejects_obsolete_custom_instruction_fields(tmp_path: Path) -> None:
    custom_instructions = tmp_path / "custom-instructions.md"

    with pytest.raises(ValueError, match="custom_instructions"):
        AgentRuntimeSettings(prompt={"custom_instructions": ["Custom."]})

    with pytest.raises(ValueError, match="custom_instructions_path"):
        AgentRuntimeSettings(prompt={"custom_instructions_path": custom_instructions})


def test_prompt_renderer_injects_model_into_configured_jinja_templates(tmp_path: Path) -> None:
    agent_template = tmp_path / "agent.j2"
    task_template = tmp_path / "task.j2"
    agent_template.write_text("{{ variables.prefix }}{% for skill in skills %} {{ skill.name }}={{ skill.instructions }}{% endfor %}", encoding="utf-8")
    task_template.write_text("Task {{ task.id }} {{ task.variables.prefix }}", encoding="utf-8")
    settings = AgentRuntimeSettings(
        prompt={
            "agent_template_path": agent_template,
            "task_template_path": task_template,
            "variables": {"prefix": "Base."},
        },
    ).prompt
    builder = PromptModelBuilder(settings)
    renderer = PromptRenderer(settings)

    agent_model = builder.build_agent_prompt(KnowledgeBundle(), [SkillBundle(name="s", kind="markdown", instructions="Skill.")])
    task_model = builder.build_task_prompt(Task(id="task-1", description="Do it.", acceptance_criteria=["Done."]))

    assert renderer.render_agent_prompt(agent_model) == "Base. s=Skill."
    assert renderer.render_task_prompt(task_model) == "Task task-1 Base."


@pytest.mark.asyncio
async def test_harness_tool_adapter_preserves_fatal_evidence_failure_category(monkeypatch: pytest.MonkeyPatch) -> None:
    import fsq_agent.adapters.coding_agent._harness_tools as harness_tools_module

    class _FatalEvidenceStepRunner:
        def __init__(self, harness: Any, **_: Any) -> None:
            pass

        def run_step(self, run_id: str, step: Any) -> RunnerStepResult:
            error = ValueError("private evidence mismatch")
            error.fsq_evidence_fatal = True
            raise error

    monkeypatch.setattr(harness_tools_module, "StepRunner", _FatalEvidenceStepRunner)
    adapter = HarnessToolAdapter(_DirectInvokeForbiddenHarness(), run_id="run-1")
    tool = adapter.build_tools()[0]

    with pytest.raises(EngineError) as failure:
        await tool.invoke(ToolCall(name=tool.name, arguments={"target": "Downloads"}, call_id="call-1"))

    assert failure.value.category == "evidence"
    assert str(failure.value) == "Durable execution evidence failed."


@pytest.mark.asyncio
async def test_harness_tool_adapter_applies_evidence_policy_to_mutating_action() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert tools[0].name == "tap_on"
    assert payload["tool_origin"] == "platform"
    assert payload["status"] == "passed"
    assert payload["driver_method"] == "tap_on"
    assert payload["fsq_action_name"] == "tapOn"
    assert payload["result"]["output"]["params"] == {"target": "Downloads"}
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot"]
    assert payload["result"]["artifact_refs"] == payload["artifact_refs"]
    assert payload["runner_result"]["phase_reports"][0]["phase"] == "prepare"
    assert payload["runner_result"]["phase_reports"][-1]["phase"] == "finalize"
    assert harness.steps[0].action_name == "tap_on"
    assert harness.steps[0].metadata["authored_action_name"] == "tapOn"
    assert harness.steps[0].kind == "action"
    assert harness.steps[0].evidence_policy.capture_before is False
    assert harness.steps[0].evidence_policy.capture_after is True
    assert harness.steps[0].evidence_policy.capture_on_failure is False
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:after-action:agent-tap_on-1:finalize:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_outputs_tap_at_safe_replay_params() -> None:
    harness = _FakeHarness(tool_name="tap_at", driver_method="tap_at", fsq_action_name="tapAt", screen_size=(1080, 2400))
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"point": {"x": 100, "y": 200}}, call_id="call-1"))

    payload = json.loads(output)
    expected = {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}}
    assert payload["tool_name"] == "tap_at"
    assert payload["safe_replay_params"] == expected
    assert payload["runner_result"]["phase_reports"][1]["metadata"]["safe_replay_params"] == expected


def test_harness_tool_adapter_uses_default_strict_schema_for_capability_tools() -> None:
    harness = _FakeHarness(tool_name="perform_actions", driver_method="perform_actions", fsq_action_name="performActions")
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()

    assert tools[0].name == "perform_actions"
    assert tools[0].strict is True


def test_harness_tool_adapter_builds_macos_bindings_with_strict_schema_requirement() -> None:
    from fsq_agent.core.harness._appium_connect_macos_driver import AppiumConnectMacOSDriver
    from fsq_agent.core.harness._macos import MacOSHarness

    class SchemaOnlyEvaluator:
        def evaluate(self, request: Any, *, cancellation_check=None) -> Any:
            raise AssertionError(f"Schema construction must not invoke the evaluator: {request!r}")

    harness = MacOSHarness(
        driver=AppiumConnectMacOSDriver(bundle_id="com.microsoft.edgemac"),
        ai_assertion_evaluator=SchemaOnlyEvaluator(),
    )
    adapter = HarnessToolAdapter(harness, run_id="macos-schema-run", platform="macos")

    tools = adapter.build_tools()

    assert {tool.name for tool in tools} == {schema.name for schema in adapter.schemas}
    assert "assert_with_ai" in {tool.name for tool in tools}
    assert all(tool.strict for tool in tools)


def test_harness_tool_adapter_preserves_capability_parameter_schema() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="schema-run", platform="android")
    schema = adapter.schemas[0].params_json_schema
    schema["description"] = "Tool parameter description."
    schema["properties"]["target"]["description"] = "Target description."

    [tool] = adapter.build_tools()

    assert tool.parameters_schema is schema
    assert tool.parameters_schema["description"] == "Tool parameter description."
    assert tool.parameters_schema["properties"]["target"]["description"] == "Target description."
    assert tool.strict is True


async def test_invalid_harness_input_keeps_failure_provenance_without_actions() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")
    tool = adapter.build_tools()[0]
    output = await tool.on_invalid_input(ToolInputFailure(name=tool.name, call_id="invalid-action", message="Tool arguments must be a JSON object."))
    payload = json.loads(output)
    assert payload["status"] == "failed"
    assert payload["capability_name"] == "tap_on"
    assert payload["platform"] == "android"
    assert payload["artifact_refs"] == []
    assert payload["error_message"] == "Tool arguments must be a JSON object."
    assert harness.calls == []


@pytest.mark.asyncio
async def test_harness_tool_adapter_keeps_default_evidence_policy_for_assertion_actions() -> None:
    harness = _FakeHarness(
        tool_name="assert_visible",
        driver_method="assert_visible",
        fsq_action_name="assertVisible",
    )
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot"]
    assert payload["result"]["artifact_refs"] == payload["artifact_refs"]
    assert harness.steps[0].action_name == "assert_visible"
    assert harness.steps[0].metadata["authored_action_name"] == "assertVisible"
    assert harness.steps[0].kind == "assertion"
    assert harness.steps[0].evidence_policy.capture_before is True
    assert harness.steps[0].evidence_policy.capture_after is False
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:before-action:agent-assert_visible-1:prepare:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_uses_step_kind_for_effective_evidence_policy() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["fsq_action_name"] == "tapOn"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot"]
    assert harness.steps[0].action_name == "tap_on"
    assert harness.steps[0].metadata["authored_action_name"] == "tapOn"
    assert harness.steps[0].evidence_policy.capture_before is False
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:after-action:agent-tap_on-1:finalize:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_uses_web_platform_registry_for_evidence_policy() -> None:
    harness = _FakeWebHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1", platform="web")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"locator": {"role": "button", "name": "Search"}}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["fsq_action_name"] == "clickOn"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot"]
    assert harness.steps[0].action_name == "click_on"
    assert harness.steps[0].metadata["authored_action_name"] == "clickOn"
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot"]


@pytest.mark.asyncio
async def test_harness_tool_adapter_surfaces_artifact_capture_failure() -> None:
    harness = _FailingCaptureHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == "artifact_error"
    assert payload["result"]["failure_category"] == "artifact_error"
    assert payload["runner_result"]["status"] == "failed"
    assert payload["runner_result"]["failure_category"] == "artifact_error"
    assert "capture failed" in payload["error_message"]


def test_runtime_tool_origin_recognizes_platform_tools() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory(), _fake_harness_factory)
    runtime._agent_tool_names = {"read_file"}
    runtime._harness_tool_names = {"tap_on"}

    assert runtime._tool_origin("tap_on") == "platform"
    assert runtime._tool_origin("read_file") == "agent_tool"
    assert runtime._tool_origin("read_knowledge_index") == "runtime"
    assert runtime._tool_origin("unexpected_tool") == "unknown"


def test_runtime_tool_output_payload_preserves_runner_evidence_fields() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    output = json.dumps(
        {
            "tool_name": "tap_on",
            "tool_origin": "harness",
            "status": "passed",
            "replay": {"kind": "fsq_command", "alias": "tapAt"},
            "safe_replay_params": {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}},
            "runner_step_id": "agent-tap_on-1",
            "runner_result": {"step_id": "agent-tap_on-1", "status": "passed"},
            "artifact_refs": [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}],
            "result": {
                "artifact_refs": [{"kind": "ui_tree", "path": "artifacts/ui-trees/after.json"}],
            },
        }
    )

    payload = runtime._tool_output_payload(output)

    assert payload["runner_step_id"] == "agent-tap_on-1"
    assert payload["replay"] == {"kind": "fsq_command", "alias": "tapAt"}
    assert payload["safe_replay_params"] == {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}}
    assert payload["runner_result"] == {"step_id": "agent-tap_on-1", "status": "passed"}
    assert payload["artifact_refs"] == [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}]


def test_runtime_tool_output_payload_adds_agent_tool_fields() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    runtime._agent_tool_names = {"search_artifact"}
    output = json.dumps(
        {
            "tool_name": "search_artifact",
            "model_output": "full",
            "artifact": {"path": None, "content_chars": None},
            "status": "passed",
            "result": {
                "tool_name": "search_artifact",
                "status": "success",
                "output": {"matches": []},
                "duration_ms": 12,
            },
        }
    )

    payload = runtime._tool_output_payload(output)

    assert payload["tool_name"] == "search_artifact"
    assert payload["tool_origin"] == "agent_tool"
    assert payload["executor_kind"] == "agent_tool"
    assert payload["status"] == "passed"
    assert payload["duration_ms"] == 12


@pytest.mark.asyncio
async def test_runtime_uses_engine_events_for_agent_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    tool_factory = _CapturingToolFactory()
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), tool_factory, _fake_harness_factory)
    task = Task(id="agent-tools", name="Agent Tools", description="Run with AgentTools.")

    await _run_in_context(runtime, task, KnowledgeBundle(), [], "agent-tools-run", event_sink=lambda _event: None)

    assert tool_factory.kwargs is not None
    assert tool_factory.kwargs["event_sink"] is None
    assert callable(tool_factory.kwargs["runner_invoker"])


def test_runtime_stream_tool_output_preserves_tool_name_from_started_event() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    started = AgentEvent(kind="tool_called", tool_name="read_knowledge_page", call_id="call-1", arguments={"page_id": "edge_android_new_tab_page", "file": None})
    completed = AgentEvent(kind="tool_output", call_id="call-1", output='{"ok":true,"page_id":"edge_android_new_tab_page","duration_ms":123}')

    start_event = runtime._map_stream_event(started, "run-1", "pre-plan")
    completed_event = runtime._map_stream_event(completed, "run-1", "pre-plan")

    assert start_event is not None
    assert completed_event is not None
    assert completed_event.tool_name == "read_knowledge_page"
    assert completed_event.tool_call_id == "call-1"
    assert completed_event.duration_ms == 123


def test_runtime_stream_message_output_uses_text_not_engine_object_repr() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    event = AgentEvent(kind="message", text='{"schema_version":"task_run_v1","status":"success"}')

    run_event = runtime._map_stream_event(event, "run-1", "task")

    assert run_event is not None
    assert run_event.message == '{"schema_version":"task_run_v1","status":"success"}'


def test_runtime_stream_omits_empty_reasoning_summary() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    event = AgentEvent(kind="reasoning_summary")

    assert runtime._map_stream_event(event, "run-1", "task") is None


def test_verification_evidence_builder_uses_text_only_after_runner_visual_assertion(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    task = Task(
        id="visual",
        description="Verify the page visually.",
        verification_goal="Verify the logo is visible.",
    )
    results = [
        StepResult(
            step_id=1,
            status="success",
            actual_outcome=json.dumps(
                {
                    "schema_version": "task_run_v1",
                    "status": "success",
                    "summary": "Visual assertion passed.",
                    "pre_plan": [],
                    "plan_updates": [],
                    "satisfied_criteria": ["Key action 1: assertWithAI Verify the logo is visible."],
                    "unmet_criteria": [],
                    "evidence": [f"Runner inspected submitted screenshot {screenshot_path} and verified the logo."],
                    "errors": [],
                }
            ),
            tool_name="agent_runtime.runner",
        )
    ]

    model_input = VerificationEvidenceBuilder().build_model_input(task, results, image_root=output_root)

    assert isinstance(model_input, str)
    evidence = json.loads(model_input)
    assert evidence["verification_goal"] == "Verify the logo is visible."
    assert "verification_mode" not in evidence
    assert "blocking_criteria" not in evidence
    assert "visual_artifacts" not in evidence
    assert evidence["agent_claims"]["status"] == "success"
    assert "Runner inspected submitted screenshot" in evidence["agent_claims"]["evidence"][0]
    assert "input_image" not in model_input


def test_verification_evidence_builder_does_not_attach_images_from_paths(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    screenshot_path = outside_root / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    task = Task(id="visual", description="Verify the page visually.")
    results = [
        StepResult(
            step_id=1,
            status="success",
            actual_outcome=f"Screenshot outside output root: {screenshot_path}",
        )
    ]

    model_input = VerificationEvidenceBuilder().build_model_input(task, results, image_root=output_root)

    assert isinstance(model_input, str)
    assert "input_image" not in model_input
    assert "visual_artifacts" not in model_input


def test_runtime_builds_neutral_request_with_one_tool_output_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    request = _test_request(runtime)

    assert request.tracing_enabled is False
    input_filter = request.tool_output_filter
    assert input_filter.max_output_chars == 60000
    assert input_filter.max_total_inline_chars == 180000
    assert not hasattr(request, "trimming")


def test_runtime_builds_run_config_enables_sdk_tracing_with_openai_export_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "trace-key")
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    assert _test_request(runtime).tracing_enabled is True


def test_runtime_builds_run_config_respects_explicit_tracing_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "trace-key")
    settings = Settings(agent_runtime=AgentRuntimeSettings(tracing_enabled=False))
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    assert _test_request(runtime).tracing_enabled is False


def test_provider_session_preserves_azure_connection_values_for_neutral_access() -> None:
    settings = Settings(agent_runtime=_azure_openai_settings(api_key="azure-key"))

    session = build_model_provider_session(settings)
    assert session.client_config.api_key == "azure-key"
    assert session.client_config.base_url == "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    assert session.client_config.default_headers == {}
    assert not hasattr(session, "create_agents_provider")


def test_runtime_tool_count_filter_keeps_thirty_small_recent_outputs(tmp_path: Path) -> None:
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = tuple(ToolOutputEntry(index, str(index), "read_file", f"output {index}") for index in range(1, 32))

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical read_file output stored as artifact.")
    assert "output 1" not in filtered[1]
    assert all(entry.entry_id not in filtered for entry in entries[1:])


def test_runtime_tool_count_filter_enforces_total_inline_budget(tmp_path: Path) -> None:
    runtime_settings = AgentRuntimeSettings()
    runtime_settings.local_tool_output = LocalToolOutputSettings(recent_inline_output_count=3, total_inline_output_max_chars=60000)
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=runtime_settings, output=output_settings), _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = tuple(ToolOutputEntry(index, str(index), "ui_snapshot", character * 25000) for index, character in enumerate("abc", start=1))

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical ui_snapshot output stored as artifact.")
    assert 2 not in filtered
    assert 3 not in filtered


@pytest.mark.parametrize(("tool_name", "artifact_label"), [("harness_source", "harness_source"), ("", "runtime_tool")])
def test_runtime_tool_count_filter_writes_artifact_for_trimmed_history(tmp_path: Path, tool_name: str, artifact_label: str) -> None:
    runtime_settings = AgentRuntimeSettings()
    runtime_settings.local_tool_output = LocalToolOutputSettings(recent_inline_output_count=0)
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=runtime_settings, output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = (ToolOutputEntry(1, "1", tool_name, "<node>" * 7000),)

    filtered = input_filter(entries)

    assert "Artifact path:" in filtered[1]
    artifacts = list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))
    assert [artifact.name for artifact in artifacts] == [f"000001-{artifact_label}.json"]
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["tool_name"] == artifact_label
    assert payload["content"] == entries[0].output


async def test_file_helper_context_filter_preserves_complete_artifact(tmp_path: Path) -> None:
    from fsq_agent.adapters.coding_agent import create_coding_agent_runtime
    from fsq_agent.agent_engine import ToolCall

    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / "runs"
    settings.agent_context.knowledge.root_dir = tmp_path / "knowledge"
    settings.agent_context.knowledge.root_dir.mkdir()
    content = "x" * 40000 + "TAIL"
    (settings.agent_context.knowledge.root_dir / "large.txt").write_text(content, encoding="utf-8")
    runtime = create_coding_agent_runtime(settings)
    settings.agent_runtime.local_tool_output.recent_inline_output_count = 3
    settings.agent_runtime.local_tool_output.full_output_max_chars = 30000
    tools = runtime.tool_factory.build_tools(run_id="run-1", task_id="task-1")
    read_tool = next(tool for tool in tools if tool.name == "read_file")
    search_tool = next(tool for tool in tools if tool.name == "search_artifact")
    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "knowledge/large.txt"}, call_id="large-read"))
    payload = json.loads(output)
    artifact_path = Path(payload["artifact"]["path"])
    original_artifact = await asyncio.to_thread(artifact_path.read_text, encoding="utf-8")
    assert payload["model_output"] == "artifact_reference"
    assert json.loads(json.loads(original_artifact)["content"])["output"] == content
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter

    entries = (
        ToolOutputEntry(1, "large-read", "read_file", output),
        ToolOutputEntry(2, "recent-1", "read_file", "recent 1"),
        ToolOutputEntry(3, "recent-2", "read_file", "recent 2"),
        ToolOutputEntry(4, "recent-3", "read_file", "recent 3"),
    )
    filtered = input_filter(entries)

    assert str(artifact_path) in filtered[1]
    assert await asyncio.to_thread(artifact_path.read_text, encoding="utf-8") == original_artifact
    recovery = json.loads(await search_tool.invoke(ToolCall(name="search_artifact", arguments={"artifact_path": str(artifact_path), "query": "TAIL", "max_matches": 1}, call_id="recover-tail")))
    assert recovery["result"]["output"]["matches"]
    assert "TAIL" in recovery["result"]["output"]["matches"][0]["preview"]


def test_runtime_input_filter_trims_recent_large_ui_snapshot_to_artifact(tmp_path: Path) -> None:
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    snapshot_output = json.dumps(
        {
            "tool_name": "ui_snapshot",
            "status": "passed",
            "result": {"output": {"xml": '<node password="false">' + ("visible text " * 5000) + "</node>"}},
        }
    )
    entries = (ToolOutputEntry(1, "snapshot", "ui_snapshot", snapshot_output),)

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical ui_snapshot output stored as artifact.")
    assert "Artifact path:" in filtered[1]
    assert len(filtered[1]) < len(snapshot_output)
    assert list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))


def test_runtime_preview_redacts_wrapped_sensitive_tool_output() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    output = json.dumps(
        {
            "tool_name": "secret_debug_tool",
            "model_output": "full",
            "result": {
                "tool_name": "secret_debug_tool",
                "status": "success",
                "output": {
                    "type": "runtime_secret",
                    "name": "TEST_ACCOUNT_PASSWORD",
                    "value": "super-secret",
                    "sensitive": True,
                },
                "sensitive": True,
            },
        }
    )

    preview = runtime._preview(output)

    assert "super-secret" not in preview
    assert '"value": "***"' in preview


def test_runtime_redacts_configured_secret_values_from_final_output() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": "super-secret"})
    runtime = DefaultCodingAgentRuntime(
        Settings(
            agent_runtime=AgentRuntimeSettings(),
            runtime_secrets=runtime_secrets,
        ),
        _EmptyToolFactory(),
    )
    final_output = AgentFinalOutput(
        status="success",
        summary="Logged in with super-secret.",
        evidence=["The password super-secret was entered."],
    )

    redacted = runtime._redact_runtime_secrets(final_output)

    assert isinstance(redacted, AgentFinalOutput)
    assert redacted.summary == "Logged in with ***."
    assert redacted.evidence == ["The password *** was entered."]


def test_runtime_redacts_configured_secret_values_from_tool_arguments() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": "super-secret"})
    runtime = DefaultCodingAgentRuntime(
        Settings(
            agent_runtime=AgentRuntimeSettings(),
            runtime_secrets=runtime_secrets,
        ),
        _EmptyToolFactory(),
    )
    event = AgentEvent(kind="tool_called", tool_name="text_type", call_id="call-1", arguments={"text": "super-secret", "target": "Password"})

    run_event = runtime._map_stream_event(event, "run-1", "task-1")

    assert run_event.tool_arguments == {"text": "***", "target": "Password"}


def test_runtime_preserves_keyboard_key_arguments_for_recording() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(), _EmptyToolFactory())
    event = AgentEvent(kind="tool_called", tool_name="press_key", call_id="keyboard-1", arguments={"key": "Enter", "modifiers": ["COMMAND"]})
    recorded = runtime._map_stream_event(event, "run-1", "task-1")
    assert recorded.tool_arguments == {"key": "Enter", "modifiers": ["COMMAND"]}
    assert runtime._redact({"apiKey": "credential-value", "private_key": "private-value", "authorization": "bearer-value", "key": "Escape"}) == {
        "apiKey": "***",
        "private_key": "***",
        "authorization": "***",
        "key": "Escape",
    }


def test_runtime_input_filter_leaves_plain_screenshot_outputs_text_only(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = (ToolOutputEntry(1, "img", "harness_screenshot", f"Screenshot saved successfully to: {screenshot_path}"),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_runtime_input_filter_does_not_attach_submitted_visual_assertion_image(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    output = json.dumps(
        {
            "type": "visual_assertion_submission",
            "assertion_id": "key-action-7",
            "prompt": "Verify the logo is visible.",
            "screenshot_path": str(screenshot_path),
        }
    )
    entries = (ToolOutputEntry(1, "visual", "submit_visual_assertion", output),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_runtime_input_filter_rejects_screenshot_images_outside_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    screenshot_path = outside_root / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    output = json.dumps({"type": "visual_assertion_submission", "assertion_id": "key-action-7", "prompt": "Verify the logo is visible.", "screenshot_path": str(screenshot_path)})
    entries = (ToolOutputEntry(1, "visual", "submit_visual_assertion", output),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_coding_agent_adapter_does_not_import_agent_private_modules() -> None:
    adapter_root = Path(__file__).parents[1] / "fsq_agent" / "adapters" / "coding_agent"
    private_imports: list[str] = []
    for source_path in adapter_root.glob("*.py"):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fsq_agent.agent._"):
                private_imports.append(f"{source_path.name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                private_imports.extend(f"{source_path.name}:{node.lineno}:{alias.name}" for alias in node.names if alias.name.startswith("fsq_agent.agent._"))
    assert private_imports == []


@pytest.mark.asyncio
async def test_cancelled_main_engine_does_not_fabricate_unreturned_usage(monkeypatch):
    class CancelledEngine:
        async def run(self, model, request, *, on_event=None):
            raise asyncio.CancelledError()

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=CancelledEngine())
    events = []
    with pytest.raises(asyncio.CancelledError):
        await _run_in_context(runtime, Task(id="cancel-usage", description="cancel"), KnowledgeBundle(), [], "cancel-usage", events.append)
    usage = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert usage == []


@pytest.mark.parametrize("as_json", [False, True])
def test_neutral_argument_redaction_preserves_shape_and_removes_credentials(as_json):
    runtime = DefaultCodingAgentRuntime(Settings(), _EmptyToolFactory(), _fake_harness_factory)
    arguments = {"password": "CANARY_PASSWORD", "authorization": "Bearer CANARY_AUTH", "nested": {"token": "CANARY_TOKEN"}, "key": "Enter", "textType": "runtimeSecret", "text": "PASSWORD_REFERENCE"}
    value = json.dumps(arguments) if as_json else arguments
    redacted = runtime._redact(value)
    parsed = json.loads(redacted) if as_json else redacted
    assert "CANARY" not in json.dumps(parsed)
    assert parsed["key"] == "Enter"
    assert parsed["text"] == "PASSWORD_REFERENCE"


@pytest.mark.parametrize("reference", [None, {"width": 1080, "height": 2209}])
@pytest.mark.parametrize(
    ("name", "alias", "arguments"),
    [
        ("tap_at", "tapAt", {"point": {"x": 100, "y": 200}}),
        ("swipe", "swipe", {"start": {"x": 100, "y": 200}, "end": {"x": 200, "y": 200}}),
    ],
)
async def test_online_coordinate_callbacks_reject_replay_reference_before_any_action(name, alias, arguments, reference):
    harness = _FakeHarness(tool_name=name, driver_method=name, fsq_action_name=alias, screen_size=(1080, 2400))
    [tool] = HarnessToolAdapter(harness, run_id="reference-rejected").build_tools()
    arguments = {**arguments, "reference_screen_size": reference}
    result = json.loads(await tool.invoke(ToolCall(name=name, arguments=arguments, call_id="hidden-reference")))
    assert result["status"] == "failed"
    assert result["capability_name"] == name
    assert result["artifact_refs"] == []
    assert harness.calls == []
    assert harness.steps == []


@pytest.mark.parametrize("shape", ["ref", "all_of", "all_of_ref"])
async def test_harness_callback_accepts_properties_from_supported_schema_composition(shape: str) -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="composed-schema")
    parameters = {"type": "object", "properties": {"target": {"type": "string"}}, "additionalProperties": False}
    if shape == "ref":
        adapter.schemas[0].params_json_schema = {"$defs": {"Arguments": parameters}, "$ref": "#/$defs/Arguments"}
    elif shape == "all_of":
        adapter.schemas[0].params_json_schema = {"type": "object", "additionalProperties": False, "allOf": [parameters]}
    else:
        adapter.schemas[0].params_json_schema = {"$defs": {"Arguments": parameters}, "allOf": [{"$ref": "#/$defs/Arguments"}]}
    [tool] = adapter.build_tools()

    result = json.loads(await tool.invoke(ToolCall(name="tap_on", arguments={"target": "Save"}, call_id="composed-save")))

    assert result["status"] == "passed"
    assert harness.steps[0].params == {"target": "Save"}
    assert "invoke:tap_on:session-1" in harness.calls


@pytest.mark.parametrize("additional", [None, True])
async def test_harness_callback_preserves_permitted_additional_properties(additional: bool | None) -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="permissive-schema")
    if additional is not None:
        adapter.schemas[0].params_json_schema["additionalProperties"] = additional
    [tool] = adapter.build_tools()
    arguments = {"target": "Save", "locator": {"resourceId": "save"}}

    result = json.loads(await tool.invoke(ToolCall(name="tap_on", arguments=arguments, call_id="permissive-save")))

    assert result["status"] == "passed"
    assert harness.steps[0].params == arguments
    assert "invoke:tap_on:session-1" in harness.calls


@pytest.mark.asyncio
async def test_model_projection_preserves_business_result_and_raw_runner_evidence(tmp_path: Path) -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")
    tool = adapter.build_tools()[0]
    raw = await tool.invoke(ToolCall(name=tool.name, arguments={"target": "Downloads"}, call_id="tap"))
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entry = ToolOutputEntry(1, "tap", tool.name, raw)

    projected = json.loads(input_filter((entry,))[1])
    original = json.loads(raw)

    assert projected["output"] == original["result"]["output"]
    assert projected["status"] == "passed"
    assert projected["runner_step_id"] == original["runner_step_id"]
    assert projected["artifact_refs"] == original["artifact_refs"]
    assert "runner_result" not in projected
    assert "result" not in projected
    assert entry.output == raw
    assert original["runner_result"]["phase_reports"][0]["phase"] == "prepare"
    assert not list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))


def test_model_projection_budgets_business_output_not_duplicate_metadata(tmp_path: Path) -> None:
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    raw = json.dumps(
        {
            "tool_name": "ui_snapshot",
            "status": "passed",
            "tool_origin": "platform",
            "result": {"output": {"xml": "<hierarchy/>"}, "metadata": {"schema_metadata": {"description": "x" * 40000}}},
            "runner_result": {"status": "passed", "phase_reports": []},
        }
    )

    replacements = input_filter((ToolOutputEntry(1, "snapshot", "ui_snapshot", raw),))

    assert json.loads(replacements[1])["output"] == {"xml": "<hierarchy/>"}
    assert not list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))
    input_filter.max_output_chars = 10
    reference = input_filter((ToolOutputEntry(1, "snapshot", "ui_snapshot", raw),))[1]
    assert "Artifact path:" in reference
    artifacts = list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))
    assert json.loads(artifacts[0].read_text())["content"] == raw


def test_model_projection_preserves_incomplete_assertion_and_evidence_failure(tmp_path: Path) -> None:
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    input_filter = _test_request(DefaultCodingAgentRuntime(settings, _EmptyToolFactory()), run_id="run-1").tool_output_filter
    unavailable = {"kind": "screenshot", "availability": "failed", "unavailable_reason": "capture_failed", "path": None}
    output = {"outcome": "inconclusive", "reason": "privacy_blocked", "deterministic_conflict": False, "capture": {"pointer_mapping": "unverified"}}
    raw = json.dumps(
        {
            "tool_name": "assert_with_ai",
            "tool_origin": "platform",
            "status": "incomplete",
            "result": {"output": output, "metadata": {"harness_metadata": {"outcome": "inconclusive", "reason": "privacy_blocked"}}},
            "artifact_refs": [unavailable],
            "runner_result": {
                "action_status": "incomplete",
                "evidence_errors": [{"message": "capture_failed"}],
                "phase_reports": [{"phase": "prepare", "status": "failed", "failure_category": "artifact_error", "error_message": "capture_failed"}],
            },
        }
    )

    projected = json.loads(input_filter((ToolOutputEntry(1, "assert", "assert_with_ai", raw),))[1])

    assert projected["status"] == "incomplete"
    assert projected["output"] == output
    assert projected["artifact_refs"] == [unavailable]
    assert projected["evidence_errors"] == [{"message": "capture_failed"}]
    assert projected["phase_errors"][0]["failure_category"] == "artifact_error"
    assert projected["diagnostics"]["harness_metadata"]["reason"] == "privacy_blocked"


@pytest.mark.parametrize("raw", ["not JSON", "[1,2]", '{"tool_name":"business","status":"passed","result":{"answer":42}}'])
def test_model_projection_leaves_unrecognized_content_unchanged(raw: str) -> None:
    input_filter = _test_request(DefaultCodingAgentRuntime(Settings(), _EmptyToolFactory())).tool_output_filter
    assert input_filter((ToolOutputEntry(1, "unrelated", "read_file", raw),)) == {}


@pytest.mark.asyncio
async def test_verifier_receives_original_observation_when_compact_history_stays_inline(tmp_path: Path) -> None:
    marker = "VERIFIED_VALUE_BEYOND_PROGRESS_PREVIEW"
    observed_text = "x" * 2000 + marker + "y" * 14000

    class FullObservationHarness(_FakeHarness):
        def invoke_action(self, step: Any, context: HarnessContext) -> HarnessActionResult:
            return HarnessActionResult(status="passed", action_name=step.action_name, output={"text": observed_text})

    harness = FullObservationHarness(tool_name="inspect_element", driver_method="inspect_element", fsq_action_name="inspectElement")
    adapter = HarnessToolAdapter(harness, run_id="run-1")
    tool = next(binding for binding in adapter.build_tools() if binding.name == "inspect_element")
    raw = await tool.invoke(ToolCall(name=tool.name, arguments={"target": "editor"}, call_id="read-full"))
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    settings.agent_runtime.local_tool_output.full_output_max_chars = 30000
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    projected = input_filter((ToolOutputEntry(1, "read-full", tool.name, raw),))[1]
    assert len(raw) > input_filter.max_output_chars
    assert json.loads(projected)["output"]["text"] == observed_text
    assert not list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))

    event = runtime._map_stream_event(AgentEvent(kind="tool_output", call_id="read-full", tool_name=tool.name, output=raw), "run-1", "verify")
    assert marker not in event.tool_output_preview
    events_path = tmp_path / "runs" / "run-1" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    task = Task(id="verify", description="Inspect the editor.", verification_goal=f"The editor contains {marker}.")
    bundle = VerificationEvidenceBuilder().build(task, [], events_path=events_path)
    execution = bundle.tool_calls[0]["runner_result"]
    invoked = next(phase for phase in execution["phase_reports"] if phase["phase"] == "invoke")
    assert invoked["metadata"]["harness_output"]["text"] == observed_text
    assert execution["status"] == "passed"
    assert json.loads(events_path.read_text(encoding="utf-8"))["payload"]["runner_result"] == execution


class _BudgetStatusHarness(_FakeHarness):
    def __init__(self, outcome="passed", *, output_chars=0):
        super().__init__()
        self.outcome = outcome
        self.output_chars = output_chars
        self.used = 2 if outcome == "denied" else 0
        self.receipts = {}
        self.budget_queries = []

    def action_space(self):
        from fsq_agent.harnesses._common_tools import CommonPlatformTools
        from fsq_agent.models import AndroidInputTextParams, AndroidTapOnParams, AndroidUiTreeParams

        if self.outcome == "wait":
            return CommonPlatformTools().common_action_space()
        schema = super().action_space()[0]
        if self.outcome == "observation":
            return [schema.model_copy(update={"name": "ui_snapshot", "driver_method": "ui_snapshot", "fsq_action_name": "uiTree", "params_json_schema": AndroidUiTreeParams.model_json_schema()})]
        if self.outcome == "secret":
            return [schema.model_copy(update={"name": "input_text", "driver_method": "input_text", "fsq_action_name": "inputText", "params_json_schema": AndroidInputTextParams.model_json_schema()})]
        return [schema.model_copy(update={"params_json_schema": AndroidTapOnParams.model_json_schema()})]

    def before_action(self, step, context):
        super().before_action(step, context)
        if self.outcome == "prepare":
            raise RuntimeError("Preparation failed before dispatch.")

    def invoke_action(self, step, context):
        from fsq_agent.harnesses._common_tools import CommonPlatformTools

        charged = int(self.outcome not in {"denied", "observation", "wait"})
        self.used += charged
        self.receipts[step.step_id] = {"max": 2, "used": self.used, "remaining": 2 - self.used, "charged": charged}
        # Another consumer may change the shared gate before formatting this call.
        self.used = min(2, self.used + 1)
        if self.outcome == "wait":
            self.steps.append(step)
            return CommonPlatformTools().invoke_common_tool(step)
        result = super().invoke_action(step, context)
        if self.outcome == "raised":
            raise RuntimeError("Native dispatch failed.")
        if self.outcome in {"failed", "denied"}:
            return result.model_copy(update={"status": "failed", "failure_category": "action_error", "error_message": "Write denied." if self.outcome == "denied" else "Native write failed."})
        if self.output_chars:
            return result.model_copy(update={"output": {"xml": "x" * self.output_chars}})
        return result

    def write_budget_status(self, step_id=None):
        self.budget_queries.append(step_id)
        if step_id is not None:
            return dict(self.receipts[step_id])
        return {"max": 2, "used": self.used, "remaining": 2 - self.used, "charged": 0}


@pytest.mark.parametrize("outcome", ["passed", "failed", "raised", "denied", "observation", "wait", "validation", "extra_argument", "prepare", "secret"])
async def test_write_budget_receipt_reaches_main_model_request_without_balance_differencing(tmp_path, monkeypatch, outcome):
    from fsq_agent.core.evidence import EvidenceRecorder

    class DistinctIdentityRecorder(EvidenceRecorder):
        def allocate_step_identity(self, step):
            return super().allocate_step_identity(step.model_copy(update={"step_execution_id": f"execution-{step.step_id}"}))

    monkeypatch.setattr("fsq_agent.core.evidence.EvidenceRecorder", DistinctIdentityRecorder)
    harness = _BudgetStatusHarness(outcome)
    model_outputs = []

    class Engine:
        async def run(self, model, request, *, on_event=None):
            tool = request.tools[0]
            arguments = {} if outcome == "observation" else {"duration_ms": 1} if outcome == "wait" else {"target": "" if outcome == "validation" else "Row"}
            if outcome == "secret":
                arguments.update({"text": "BUDGET_MISSING_SECRET", "textType": "runtimeSecret"})
            if outcome == "extra_argument":
                arguments["undeclared"] = "reject before dispatch"
            raw = await tool.invoke(ToolCall(name=tool.name, arguments=arguments, call_id="budget-call"))
            entry = ToolOutputEntry(1, "budget-call", tool.name, raw)
            projected = request.tool_output_filter((entry,)).get(1, raw)
            model_outputs.append((json.loads(raw), json.loads(projected)))
            return AgentResult(final_output=AgentFinalOutput(status="inconclusive", summary="Budget receipt inspected."))

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: _FakeProviderSession())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: harness, engine=Engine())
    await _run_in_context(runtime, Task(description="Inspect budget receipts."), KnowledgeBundle(), [], "budget-run")

    assert len(model_outputs) == 1
    raw, projected = model_outputs[0]
    if outcome in {"validation", "extra_argument", "prepare", "secret"}:
        expected = {"max": 2, "used": 0, "remaining": 2, "charged": 0}
        assert not harness.steps
        assert harness.budget_queries == [None]
    else:
        expected = next(iter(harness.receipts.values()))
        assert harness.budget_queries == [harness.steps[0].step_id]
        assert harness.steps[0].step_id != harness.steps[0].source_step_id
    assert raw["write_budget"] == projected["write_budget"] == expected
    if outcome in {"failed", "raised", "denied", "validation", "extra_argument", "prepare", "secret"}:
        assert projected["status"] == "failed"
    if outcome in {"failed", "raised"}:
        assert projected["write_budget"]["charged"] == 1
        assert projected["write_budget"]["used"] == 1
    if outcome in {"denied", "observation", "wait"}:
        assert projected["write_budget"]["charged"] == 0
    if outcome == "wait":
        assert projected["status"] == "passed"
        assert harness.steps[0].action_name == "wait_ms"
    if outcome == "secret":
        assert raw["runner_result"]["action_status"] == "failed"


@pytest.mark.parametrize("artifact_available", [False, True])
@pytest.mark.parametrize("externalization", ["oversized_latest", "total_budget", "historical"])
async def test_write_budget_survives_actual_filter_externalization_without_ui_body(tmp_path, artifact_available, externalization):
    harness = _BudgetStatusHarness("observation", output_chars=4000)
    adapter = HarnessToolAdapter(harness, run_id="budget-filter")
    tool = adapter.build_tools()[0]
    raw = await tool.invoke(ToolCall(name=tool.name, arguments={}, call_id="large-snapshot"))
    settings = Settings()
    settings.output.runs_dir = tmp_path
    output_filter = _test_request(DefaultCodingAgentRuntime(settings, _EmptyToolFactory()), run_id="budget-filter").tool_output_filter
    if not artifact_available:
        output_filter.artifact_store = None
    if externalization == "oversized_latest":
        output_filter.max_output_chars = 100
    elif externalization == "total_budget":
        output_filter.max_total_inline_chars = 100
    else:
        output_filter.recent_inline_outputs = 0
    entry = ToolOutputEntry(1, "large-snapshot", tool.name, raw)

    replacement = output_filter((entry,))[1]
    projected = json.loads(replacement)

    assert projected["write_budget"] == json.loads(raw)["write_budget"] == {"max": 2, "used": 0, "remaining": 2, "charged": 0}
    assert "x" * 100 not in replacement
    assert entry.output == raw
    assert output_filter((entry,))[1] == replacement
    if artifact_available:
        artifacts = list(tmp_path.rglob("*.json"))
        assert len(artifacts) == 1
        assert Path(projected["artifact"]["path"]) == artifacts[0]
        assert json.loads(artifacts[0].read_text())["content"] == raw
    else:
        assert "omitted" in replacement
        assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize(
    "bad_receipt",
    [
        None,
        {"max": True, "used": 0, "remaining": 1, "charged": 0},
        {"max": 2, "used": -1, "remaining": 3, "charged": 0},
        {"max": 2, "used": 1, "remaining": 0, "charged": 1},
        {"max": 2, "used": 1, "remaining": 1, "charged": 2},
        {"max": 2, "used": 1, "remaining": 1, "charged": 1, "private": "budget-secret-canary"},
        "raise",
    ],
)
async def test_invalid_budget_hook_never_masks_primary_failure_or_leaks_values(bad_receipt):
    class Harness(_BudgetStatusHarness):
        def write_budget_status(self, step_id=None):
            if bad_receipt == "raise":
                raise RuntimeError("budget-secret-canary")
            return bad_receipt

    adapter = HarnessToolAdapter(Harness("failed"), run_id="invalid-budget")
    tool = adapter.build_tools()[0]
    raw = await tool.invoke(ToolCall(name=tool.name, arguments={"target": "Row"}, call_id="bad-budget"))
    result = json.loads(raw)

    assert result["status"] == "failed"
    assert result["error_message"] == "Native write failed."
    assert "write_budget" not in result
    assert result["write_budget_unavailable"] in {"invalid_receipt", "hook_failed"}
    assert "budget-secret-canary" not in raw


@pytest.mark.parametrize("outcome", ["validation", "prepare"])
async def test_current_budget_query_rejects_nonzero_charge_without_native_dispatch(outcome):
    class InvalidCurrentReceiptHarness(_BudgetStatusHarness):
        def write_budget_status(self, step_id=None):
            self.budget_queries.append(step_id)
            return {"max": 2, "used": 1, "remaining": 1, "charged": 1}

    harness = InvalidCurrentReceiptHarness(outcome)
    harness.used = 1
    adapter = HarnessToolAdapter(harness, run_id="invalid-current-receipt")
    tool = adapter.build_tools()[0]
    raw = await tool.invoke(ToolCall(name=tool.name, arguments={"target": "" if outcome == "validation" else "Row"}, call_id="undispatched"))
    result = json.loads(raw)

    assert result["status"] == "failed"
    assert result["error_message"]
    assert not harness.steps
    assert harness.budget_queries == [None]
    assert "write_budget" not in result
    assert result["write_budget_unavailable"] == "invalid_receipt"
