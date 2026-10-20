# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fsq_agent.agent_engine import EngineError, ModelResult
from fsq_agent.ai_services import QuickTestIntent, QuickTestIntentAnalyzer, QuickTestWebAddressParser
from fsq_agent.application import ApplicationError, QuickTestPrepareRequest, prepare_quick_test, run_quick_test
from fsq_agent.config import save_openai_provider
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import DynamicAgentOutcome, ExecutableStep, PlatformRuntimeCheck, ReportArtifact, RunnerEvent, RunnerStepResult, TaskResult, VerificationResult


class Session:
    def __init__(self, **updates):
        self.payload = {"sufficient": True, "reason": "sufficient", "application_name": None, "explicit_reference": None, "inferred_hint": None, **updates}
        self.requests = []
        self.closed = False

    def complete_sync(self, request):
        self.requests.append(request)
        result = ModelResult(text=json.dumps(self.payload))
        try:
            return replace(result, parsed_output=request.output.parse(result.text))
        except ValueError:
            raise EngineError("invalid_output", "Invalid output.") from None

    def close_sync(self):
        self.closed = True


URLS = [
    "HTTPS://Example.TEST:443/a(b(c))?q=%2f&next=%2Fdocs#part!",
    "https://[::1]:8443/a(b)?#",
    "https://example.test/report.",
    "https://example.test/?q=one;",
    "https://例子.test/路径\uff08说明\uff09?q=中文\uff0c值#片段\uff01",
]
INVALID_URLS = [
    "",
    " ",
    " https://example.test",
    "https://example.test ",
    "Open https://example.test",
    "<https://example.test>",
    "ftp://example.test",
    "https://user:password@example.test",
    "https://example.test/%GG",
    "https://example.test:0",
    "https://[::1",
    "https://example.test/a" + chr(0),
    "https://example.test/\\file",
    "https://example.test/a b",
]


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("echo", [None, "https://other.test/", "https://example.test"])
def test_explicit_target_uses_caller_value_without_model_echo_or_goal_extraction(url, echo):
    goal = "Verify the heading, then navigate to https://later.test and check its form."
    session = Session(explicit_reference=echo)
    intent = QuickTestIntentAnalyzer(session).analyze(platform="web", goal=goal, url=url)
    assert intent.sufficient
    assert intent.explicit_reference == url
    assert len(session.requests) == 1
    context = json.loads(session.requests[0].input.splitlines()[-1])
    assert context == {"platform": "web", "goal": goal, "url": url}
    assert "actions and a checkable outcome" in session.requests[0].input
    assert session.closed


@pytest.mark.parametrize("reason", ["sufficient", "web_search"])
def test_model_search_label_cannot_enable_search_or_replace_explicit_start(reason):
    session = Session(reason=reason, application_name="other site")
    result = QuickTestIntentAnalyzer(session).analyze(platform="web", goal="Search for the support link and verify its heading", url=URLS[0])
    assert result.sufficient
    assert result.reason == "sufficient"
    assert result.explicit_reference == URLS[0]


@pytest.mark.parametrize("url", INVALID_URLS)
def test_analyzer_rejects_invalid_complete_url_before_inference(url):
    session = Session()
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="web", goal="Verify heading", url=url)
    assert error.value.category == "invalid_output"
    assert session.requests == []
    assert session.closed


@pytest.mark.parametrize("platform", ["android", "macos", "windows"])
def test_explicit_url_is_web_only_in_request_and_analyzer(platform):
    options = {"target_id": "device"} if platform == "android" else {}
    with pytest.raises(ValidationError):
        QuickTestPrepareRequest(platform=platform, goal="Verify heading", url="https://example.test", **options)
    session = Session()
    with pytest.raises(EngineError):
        QuickTestIntentAnalyzer(session).analyze(platform=platform, goal="Verify heading", url="https://example.test")
    assert session.requests == []
    assert session.closed


@pytest.mark.parametrize("goal", ["", " ", "\n\t"])
def test_url_never_supplies_missing_required_goal(goal):
    with pytest.raises(ValidationError):
        QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, url="https://example.test")


def test_request_keeps_explicit_url_whitespace_for_complete_validation():
    url = " https://example.test "
    value = QuickTestPrepareRequest(platform="web", target_id="chrome", goal="Verify heading", url=url)
    assert value.url == url


@pytest.mark.parametrize("url", [42, False, ["https://example.test"]])
def test_request_does_not_coerce_non_string_url(url):
    with pytest.raises(ValidationError):
        QuickTestPrepareRequest(platform="web", target_id="chrome", goal="Verify heading", url=url)


@pytest.fixture
def web(monkeypatch, tmp_path):
    root = tmp_path / "user"
    save_openai_provider(model="gpt-4.1", api_key="sk-unit-secret", user_config_root=root)
    browser = tmp_path / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser fixture")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="Ready."))
    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", lambda *args, **kwargs: ())
    monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (True, "Ready.", ""))
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda settings: SimpleNamespace(ready=True, message="Ready.", action=""))
    return root


def install_session(monkeypatch, session):
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: QuickTestIntentAnalyzer(session))


def request(root, url, *, goal="Verify the heading; then visit https://later.test and check the form."):
    return QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, url=url, user_config_root=root)


@pytest.mark.parametrize("url", URLS)
def test_preparation_freezes_complete_url_without_search_or_goal_substitution(web, monkeypatch, url):
    session = Session(explicit_reference="https://other.test")
    install_session(monkeypatch, session)
    result = prepare_quick_test(request(web, url))
    assert result.application == url
    assert result.settings.harness.web.base_url == url
    assert not result.requires_web_search_evidence
    assert result.root_path == web / "quick-test"
    assert len(session.requests) == 1


def test_explicit_start_never_extracts_goal_urls_in_either_layer(web, monkeypatch):
    monkeypatch.setattr(QuickTestWebAddressParser, "extract", lambda *args: pytest.fail("Explicit starting URL must not be extracted from goal"))
    install_session(monkeypatch, Session())
    goal = "Verify heading, then test the link to https://later.test under its stated conditions."
    result = prepare_quick_test(request(web, URLS[0], goal=goal))
    assert result.settings.harness.web.base_url == URLS[0]


@pytest.mark.parametrize(
    "goal",
    [
        "在 Chrome 中打开精确网址 https://example.com\uff08不得替换为其他站点\uff09\uff0c检查页面标题\uff0c并验证标题是否恰为 Example Domain\uff1b保留可支持结论的页面证据。",
        "打开网址:**https://example.com**\uff08不得替换站点\uff09\uff0c验证页面标题",
        "打开【https://example.com】\uff0c检查标题",
    ],
)
def test_goal_only_chinese_boundaries_keep_original_goal_and_exact_reference(web, monkeypatch, goal):
    session = Session(explicit_reference="https://example.com")
    install_session(monkeypatch, session)
    value = QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=web)
    result = prepare_quick_test(value)
    assert value.url is None
    assert value.goal == goal
    assert result.settings.harness.web.base_url == "https://example.com"
    assert not result.requires_web_search_evidence
    assert json.loads(session.requests[0].input.splitlines()[-1]) == {"platform": "web", "goal": goal}


def test_normalized_model_echo_cannot_replace_explicit_url(web, monkeypatch):
    install_session(monkeypatch, Session(explicit_reference=URLS[0].lower()))
    result = prepare_quick_test(request(web, URLS[0]))
    assert result.settings.harness.web.base_url == URLS[0]


def test_invalid_model_output_fails_safely_without_retry_or_persistence(web, monkeypatch):
    session = Session(sufficient="true", application_name="sk-unit-secret")
    install_session(monkeypatch, session)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(web, URLS[0]))
    assert error.value.code.value == "quick_test.inference_failed"
    assert "sk-unit-secret" not in str(error.value)
    assert len(session.requests) == 1
    assert session.closed
    assert not (web / "quick-test").exists()


def test_application_freezes_caller_url_even_when_analyzer_returns_an_alternate(web, monkeypatch):
    seen = []

    def analyze(**kwargs):
        seen.append(kwargs)
        return QuickTestIntent(True, "web_search", "alternate", "https://other.test")

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))
    value = request(web, URLS[0])
    result = prepare_quick_test(value)
    assert seen == [{"platform": "web", "goal": value.goal, "url": URLS[0]}]
    assert result.application == URLS[0]
    assert result.settings.harness.web.base_url == URLS[0]
    assert not result.requires_web_search_evidence


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_explicit_url_never_infers_or_persists(web, monkeypatch, url):
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda *args: pytest.fail("Invalid URL reached inference"))
    try:
        value = request(web, url)
    except ValidationError:
        pass
    else:
        with pytest.raises(ApplicationError) as error:
            prepare_quick_test(value)
        assert error.value.code.value == "quick_test.invalid_web_address"
    assert not (web / "quick-test").exists()


def test_invalid_url_and_insufficient_goal_preserve_existing_workspace(web, monkeypatch):
    install_session(monkeypatch, Session())
    prepare_quick_test(request(web, URLS[0]))
    before = {path.relative_to(web): path.read_bytes() for path in web.rglob("*") if path.is_file()}
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda *args: pytest.fail("Invalid URL reached inference"))
    with pytest.raises(ApplicationError):
        prepare_quick_test(request(web, "https://user:password@example.test"))
    install_session(monkeypatch, Session(sufficient=False, reason="insufficient"))
    with pytest.raises(ApplicationError):
        prepare_quick_test(request(web, "https://other.test", goal="Website"))
    assert {path.relative_to(web): path.read_bytes() for path in web.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("goal", ["FSQ", "Test the website", "https://example.test"])
def test_explicit_url_does_not_make_actionless_model_decision_sufficient(web, monkeypatch, goal):
    session = Session(sufficient=False, reason="insufficient")
    install_session(monkeypatch, session)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(web, "https://example.test", goal=goal))
    assert error.value.code.value == "quick_test.insufficient"
    assert len(session.requests) == 1
    assert not (web / "quick-test").exists()


@pytest.mark.asyncio
async def test_shared_execution_receives_exact_url_and_unchanged_goal(web, monkeypatch):
    url = URLS[0]
    goal = "Verify the heading\uff08不得替换站点\uff09; then navigate to https://later.test and check its form."
    install_session(monkeypatch, Session())
    seen = []

    async def execute(self, value):
        seen.append(value)
        assert value.settings.harness.web.base_url == url
        assert value.task.description == goal
        assert value.task.planning_reference_text == goal
        assert not value.requires_web_search_evidence
        assert value.publication_directory is None
        return SimpleNamespace(
            task_result=TaskResult(
                task_id=value.task.id,
                status="success",
                steps=[],
                verification=VerificationResult(status="success", summary="Heading verified."),
                report=ReportArtifact(run_id="exact-run", path=web / "absent-report.md"),
            ),
            recording=None,
        )

    monkeypatch.setattr("fsq_agent.application._quick_test_execution.DynamicExecutionService.execute", execute)
    result = await run_quick_test(request(web, url, goal=goal), agent_factory=lambda settings: object())
    assert len(seen) == 1
    assert result.root_path == web / "quick-test"
    assert result.run_id == "exact-run"
    assert result.application == url
    assert "sk-unit-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_original_chinese_goal_runs_through_real_shared_execution(web, monkeypatch):
    from fsq_agent.execution import DynamicExecutionService

    goal = "在 Chrome 中打开精确网址 https://example.com\uff08不得替换为其他站点\uff09\uff0c检查页面标题\uff0c并验证标题是否恰为 Example Domain\uff1b保留可支持结论的页面证据。"
    install_session(monkeypatch, Session(explicit_reference="https://example.com"))
    requests = []
    contexts = []
    tasks = []
    settings_seen = []
    execute = DynamicExecutionService.execute

    async def capture_execute(self, value):
        requests.append(value)
        return await execute(self, value)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            tasks.append(task)
            contexts.append(context)
            recorder = kwargs["evidence_sink"]
            step = recorder.allocate_step_identity(ExecutableStep(step_id="observe-title", action_name="observe", kind="observation", params={}))
            recorder.record_event(
                RunnerEvent(
                    run_id=context.run_id,
                    event_type="step_start",
                    step_id=step.step_id,
                    source_step_id=step.source_step_id,
                    step_execution_id=step.step_execution_id,
                    invocation_path=step.invocation_path,
                )
            )
            recorder.record_step_result(
                RunnerStepResult(step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path, status="passed")
            )
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Controlled title observation recorded."))

    def agent_factory(settings):
        settings_seen.append(settings)
        return Agent()

    monkeypatch.setattr(DynamicExecutionService, "execute", capture_execute)
    value = QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=web)
    result = await run_quick_test(value, agent_factory=agent_factory)

    assert value.url is None
    assert len(requests) == len(contexts) == len(settings_seen) == len(tasks) == 1
    execution = requests[0]
    assert execution.settings is settings_seen[0]
    assert execution.settings.harness.web.base_url == "https://example.com"
    for task in [execution.task, tasks[0]]:
        assert task.name == goal
        assert task.description == goal
        assert task.planning_reference_text == goal
        assert task.planning_reference_kind == "goal"
    assert execution.requires_web_search_evidence is False
    assert execution.publication_directory is None
    assert result.status == "success"
    assert result.run_id == contexts[0].run_id
    run_dir = contexts[0].run_dir
    assert run_dir == execution.settings.output.runs_dir / result.run_id
    assert (run_dir / "run.json").is_file()
    assert result.execution_result_path == run_dir / "execution-result.json"
    assert result.execution_result_path.is_file()
    assert result.evidence_manifest_path == run_dir / "evidence-manifest.json"
    manifest = json.loads(result.evidence_manifest_path.read_text())
    assert manifest["steps"]
    assert not list(execution.settings.cases.dir.rglob("*.fsq.yaml"))
