# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import plistlib
from types import SimpleNamespace

import pytest

from fsq_agent.agent_engine import ModelResult
from fsq_agent.ai_services import QuickTestIntent, QuickTestIntentAnalyzer
from fsq_agent.application import ApplicationError, QuickTestContextRequest, QuickTestPrepareRequest, get_quick_test_context, prepare_quick_test
from fsq_agent.config import create_workspace, load_registered_workspace, save_openai_provider, update_workspace_platform, workspace_revision
from fsq_agent.environments import AndroidDeviceDiscovery, PlatformRuntimeService
from fsq_agent.models import (
    AndroidDevice,
    AndroidDeviceDiscoveryResult,
    AndroidWorkspaceTarget,
    ApplicationCandidate,
    ApplicationLookupResult,
    PlatformPrerequisiteCheck,
    PlatformRuntimeCheck,
    WorkspacePlatformCreateInput,
)


@pytest.fixture
def ready(monkeypatch, tmp_path):
    root = tmp_path / "user"
    save_openai_provider(model="gpt-4.1", api_key="sk-fsq-unit-test-not-a-real-key", user_config_root=root)
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="Ready."))

    def prerequisites(self, settings, **kwargs):
        if settings.harness.platform == "android":
            return (PlatformPrerequisiteCheck(identifier="device_selection", status="ready" if settings.harness.android.serial else "unavailable", message="Device selection fixture."),)
        return ()

    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", prerequisites)
    monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (True, "Ready.", ""))
    monkeypatch.setattr(AndroidDeviceDiscovery, "discover", lambda self: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="serial-1", state="device")]))
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda settings: SimpleNamespace(ready=True, message="Ready.", action=""))
    intent = QuickTestIntent(True, "sufficient", "Calculator", None)
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    monkeypatch.setattr(
        PlatformRuntimeService,
        "lookup_application",
        lambda self, **kwargs: ApplicationLookupResult(status="complete", candidates=(ApplicationCandidate(identity="com.calc", label="Calculator", app_id="com.calc"),)),
    )
    return root


def request(root, **updates):
    return QuickTestPrepareRequest(platform="android", target_id="serial-1", goal="Calculator: 1+1=2", user_config_root=root, **updates)


def test_context_needs_no_workspace_and_app_checks_do_not_deadlock(ready):
    result = get_quick_test_context(QuickTestContextRequest(platform="android", target_id="serial-1", user_config_root=ready))
    assert result.can_start
    assert any(check.id == "application" and check.status == "pending" for check in result.checks)
    assert not (ready / "quick-test").exists()


def test_context_requires_explicit_device_selection(ready):
    result = get_quick_test_context(QuickTestContextRequest(platform="android", user_config_root=ready))
    assert not result.can_start
    assert [item.id for item in result.choices] == ["serial-1"]


def test_prepare_first_use_and_update_preserve_env_and_unrelated_files(ready, monkeypatch):
    first = prepare_quick_test(request(ready))
    assert first.workspace_name == "quick-test"
    assert first.root_path == ready / "quick-test"
    assert first.settings.harness.android.serial == "serial-1"
    config_path = first.settings.workspace.config_path
    update_workspace_platform(
        name="quick-test",
        platform="android",
        target=AndroidWorkspaceTarget(app_id="com.calc"),
        env={"PRIVATE": "secret-value"},
        expected_revision=workspace_revision(config_path),
        user_config_root=ready,
    )
    keep = first.root_path / "cases/android/keep.txt"
    keep.write_text("unchanged")
    monkeypatch.setattr(
        PlatformRuntimeService,
        "lookup_application",
        lambda self, **kwargs: ApplicationLookupResult(status="complete", candidates=(ApplicationCandidate(identity="com.other", label="Other", app_id="com.other"),)),
    )
    second = prepare_quick_test(request(ready))
    current = load_registered_workspace("quick-test", "android", ready)
    assert current.target.app_id == "com.other"
    assert current.env == {"PRIVATE": "secret-value"}
    assert keep.read_text() == "unchanged"
    assert first.settings.harness.android.app_id == "com.calc"
    assert second.settings.harness.android.app_id == "com.other"
    assert second.platform_revision == workspace_revision(config_path)


@pytest.mark.parametrize(("count", "code"), [(0, "quick_test.application_not_found"), (2, "quick_test.application_ambiguous")])
def test_zero_multiple_fail_before_workspace_creation(ready, monkeypatch, count, code):
    candidates = tuple(ApplicationCandidate(identity=f"com.app{index}", label="Calculator", app_id=f"com.app{index}") for index in range(count))
    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lambda self, **kwargs: ApplicationLookupResult(status="complete", candidates=candidates))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == code
    assert not (ready / "quick-test").exists()


@pytest.mark.parametrize("reason", ["insufficient", "missing_application", "ambiguous_application"])
def test_insufficient_never_uses_old_target(ready, monkeypatch, reason):
    prepare_quick_test(request(ready))
    before = (ready / "quick-test/.fsq/config/config.android.yaml").read_bytes()
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(False, reason, None, None)))
    with pytest.raises(ApplicationError):
        prepare_quick_test(request(ready))
    assert (ready / "quick-test/.fsq/config/config.android.yaml").read_bytes() == before


def test_unrelated_nonempty_root_is_not_adopted_or_nested(ready):
    fixed = ready / "quick-test"
    fixed.mkdir()
    (fixed / "unrelated").write_text("keep")
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"
    assert list(fixed.iterdir()) == [fixed / "unrelated"]


def test_registered_name_collision_does_not_fallback(ready, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    create_workspace(
        selected_path=other, name="QUICK-TEST", platforms=[WorkspacePlatformCreateInput(platform="android", target=AndroidWorkspaceTarget(app_id="com.other"), env={})], user_config_root=ready
    )
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"
    assert not (ready / "quick-test").exists()


def test_cancel_after_inference_prevents_discovery_and_persistence(ready, monkeypatch):
    cancelled = False

    def analyze(**kwargs):
        nonlocal cancelled
        cancelled = True
        return QuickTestIntent(True, "sufficient", "Calculator", None)

    def check():
        if cancelled:
            raise InterruptedError

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))
    with pytest.raises(InterruptedError):
        prepare_quick_test(request(ready), check_cancelled=check)
    assert not (ready / "quick-test").exists()


def test_config_exact_root_never_uses_nested_fallback(tmp_path):
    from fsq_agent.config import create_workspace_at_root
    from fsq_agent.models import ConfigurationError

    root = tmp_path / "exact"
    platform = WorkspacePlatformCreateInput(platform="android", target=AndroidWorkspaceTarget(app_id="com.calc"), env={})
    first = create_workspace_at_root(root_path=root, name="first", platforms=[platform], user_config_root=tmp_path / "user")
    assert first.root_path == root
    with pytest.raises(ConfigurationError):
        create_workspace_at_root(root_path=root, name="second", platforms=[platform], user_config_root=tmp_path / "user")
    assert not (root / "second").exists()


def test_concurrent_platform_edit_cannot_retarget_a_prepared_run(ready, monkeypatch):
    import fsq_agent.application._quick_test as implementation

    prepare_quick_test(request(ready))
    original = implementation.load_workspace_platform_settings

    def racing_load(*args, **kwargs):
        config_path = ready / "quick-test/.fsq/config/config.android.yaml"
        update_workspace_platform(
            name="quick-test", platform="android", target=AndroidWorkspaceTarget(app_id="com.other"), env={}, expected_revision=workspace_revision(config_path), user_config_root=ready
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(implementation, "load_workspace_platform_settings", racing_load)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"


def test_web_multiple_navigation_addresses_with_explicit_start_are_valid(ready, monkeypatch):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    goal = "Start at http://example.test then navigate to https://example.test/login and verify the login form."
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", None, "http://example.test")),
    )
    result = prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=ready))
    assert result.application == "http://example.test"
    assert result.settings.harness.web.base_url == "http://example.test"
    assert result.target_id == "chrome"
    assert not result.requires_web_search_evidence


@pytest.mark.parametrize("subject", ["Node.js", "Acme.SDK"])
@pytest.mark.parametrize("address", [None, "https://github.com"])
def test_dotted_search_subjects_are_not_supplied_navigation_addresses(ready, monkeypatch, subject, address):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    goal = f"Open {address} and search for {subject}; find its official website." if address else f"Search {subject} on GitHub and find its official website."
    seen = []

    def analyze(**kwargs):
        seen.append(kwargs["goal"])
        return QuickTestIntent(True, "sufficient" if address else "web_search", subject, address)

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))
    result = prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=ready))
    assert seen == [goal]
    assert result.application == (address or subject)
    assert result.settings.harness.web.base_url == address
    assert result.requires_web_search_evidence is (address is None)


@pytest.mark.parametrize("goal", ["搜索 FSQ github\uff0c并且帮我找到FSQ 的官网地址", "Goal: search FSQ on GitHub and find its official site"])
def test_web_search_clears_initial_and_frozen_url_and_keeps_subject(ready, monkeypatch, goal):
    from fsq_agent.config import load_platform_settings, load_workspace_platform_settings

    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    analyzed = []
    initial = []
    frozen = []

    def load(*args, **kwargs):
        settings = load_platform_settings(*args, **kwargs)
        settings.harness.web.base_url = "https://prior-preset.test"
        initial.append(settings)
        return settings

    def load_frozen(*args, **kwargs):
        settings = load_workspace_platform_settings(*args, **kwargs)
        settings.harness.web.base_url = "https://prior-workspace.test"
        frozen.append(settings)
        return settings

    def analyze(**kwargs):
        analyzed.append(kwargs["goal"])
        return QuickTestIntent(True, "web_search", "FSQ", None)

    monkeypatch.setattr("fsq_agent.application._quick_test.load_platform_settings", load)
    monkeypatch.setattr("fsq_agent.application._quick_test.load_workspace_platform_settings", load_frozen)
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))
    result = prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=ready))
    assert analyzed == [goal]
    assert result.requires_web_search_evidence
    assert result.application == "FSQ"
    assert result.settings.harness.web.base_url is None
    assert initial[0].harness.web.base_url is None
    assert frozen[0].harness.web.base_url is None
    assert result.settings.harness.web.browser_executable_path == browser
    assert result.workspace_name == "quick-test"


@pytest.mark.parametrize(
    "address", ["https:/", "https://", "https:/example.test", "https://user:pass@example.test", "javascript:alert(1)", "file:///tmp/test", "ftp://example.test", "custom://example.test"]
)
def test_invalid_supplied_address_cannot_be_laundered_into_search(ready, monkeypatch, address):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: pytest.fail("Invalid navigation address reached inference"))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=f"Search FSQ {address} and find its official site", user_config_root=ready))
    assert error.value.code.value == "quick_test.invalid_web_address"
    assert not (ready / "quick-test").exists()


def test_search_whole_prepare_execution_handoff_and_terminal_evidence_result(ready, monkeypatch):
    import time

    from fsq_agent.adapters.control_plane import ControlPlaneServer, ControlPlaneServerOptions
    from fsq_agent.execution import DynamicExecutionService
    from fsq_agent.models import DynamicAgentOutcome, VerificationResult

    goal = "搜索 FSQ github\uff0c并且帮我找到FSQ 的官网地址"
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "web_search", "FSQ", None))
    )
    seen = []
    execute = DynamicExecutionService.execute

    async def checked_execute(self, request):
        seen.append(request)
        return await execute(self, request)

    class Agent:
        async def run_in_context(self, task, context, **kwargs):
            assert task.description == task.planning_reference_text == goal
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Unsupported model claim"))

    # The service passes the event sink positionally, just as in normal execution.
    async def run(task, context, event_sink=None, **kwargs):
        return await Agent().run_in_context(task, context, **kwargs)

    monkeypatch.setattr(DynamicExecutionService, "execute", checked_execute)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings", lambda *args: SimpleNamespace(run_in_context=run))
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=ready))
    status, accepted = server.handle_post("/api/control-plane/quick-test/runs", {"platform": "web", "targetId": "chrome", "goal": goal})
    assert status == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = server.state.snapshot(accepted["requestId"])
        if snapshot["terminal"]:
            break
        time.sleep(0.01)
    assert snapshot["terminal"]
    assert snapshot["status"] == snapshot["result"]["status"] == "inconclusive"
    assert snapshot["application"] == "FSQ"
    assert snapshot["reportAvailable"]
    assert "Unsupported model claim" not in snapshot["summary"]
    assert len(seen) == 1
    assert seen[0].requires_web_search_evidence
    assert seen[0].settings.harness.web.base_url is None


def test_missing_provider_prevents_inference_and_preserves_runtime_checks(monkeypatch, ready):
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda settings: SimpleNamespace(ready=False, message="Provider unavailable.", action="Configure Provider."))
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: pytest.fail("Inference before Provider readiness"))
    context = get_quick_test_context(QuickTestContextRequest(platform="android", target_id="serial-1", user_config_root=ready))
    assert not context.can_start
    assert next(check for check in context.checks if check.id == "runtime").status == "ready"
    with pytest.raises(ApplicationError):
        prepare_quick_test(request(ready))


def test_targetless_context_honors_operator_appium_endpoint(ready, monkeypatch):
    observed = []
    monkeypatch.setenv("FSQ_MACOS_APPIUM_SERVER_URL", "http://127.0.0.1:4799")
    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", lambda self, settings, **kwargs: observed.append(settings.harness.macos.appium_server_url) or ())
    context = get_quick_test_context(QuickTestContextRequest(platform="macos", user_config_root=ready))
    assert context.can_start
    assert observed == ["http://127.0.0.1:4799"]


def test_context_probe_errors_preserve_independent_provider_facts(ready, monkeypatch):
    def fail(*args):
        raise OSError("private host details")

    monkeypatch.setattr(PlatformRuntimeService, "check", fail)
    context = get_quick_test_context(QuickTestContextRequest(platform="android", target_id="serial-1", user_config_root=ready))
    assert not context.can_start
    assert next(check for check in context.checks if check.id == "provider").status == "ready"
    assert next(check for check in context.checks if check.id == "runtime").status == "error"
    assert "private host details" not in context.model_dump_json()


def test_add_android_preserves_existing_web_platform_and_environment(ready):
    from fsq_agent.config import create_workspace_at_root
    from fsq_agent.models import WebWorkspaceTarget

    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("fixture")
    browser.chmod(0o755)
    root = ready / "quick-test"
    create_workspace_at_root(
        root_path=root,
        name="quick-test",
        platforms=[WorkspacePlatformCreateInput(platform="web", target=WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=browser), env={"WEB_PRIVATE": "preserved"})],
        user_config_root=ready,
    )
    web_path = root / ".fsq/config/config.web.yaml"
    before = web_path.read_bytes()
    prepared = prepare_quick_test(request(ready))
    assert prepared.settings.harness.platform == "android"
    assert web_path.read_bytes() == before
    assert load_registered_workspace("quick-test", "web", ready).env == {"WEB_PRIVATE": "preserved"}


@pytest.mark.parametrize(("stage", "code"), [("inference", "inference_failed"), ("discovery", "discovery_failed"), ("availability", "prerequisites"), ("persistence", "persistence_failed")])
def test_preparation_stage_failures_are_safe_and_do_not_allocate_workspace(ready, monkeypatch, stage, code):
    def fail(*args, **kwargs):
        raise OSError("PRIVATE provider or host output")

    if stage == "inference":
        monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", fail)
    elif stage == "discovery":
        monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lambda *args, **kwargs: ApplicationLookupResult(status="failed", code="discovery_failed"))
    elif stage == "availability":
        monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (False, "PRIVATE", "PRIVATE"))
    else:
        monkeypatch.setattr("fsq_agent.application._quick_test.create_workspace_at_root", fail)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == f"quick_test.{code}"
    assert "PRIVATE" not in str(error.value.to_record())
    assert not (ready / "quick-test").exists()


def test_symlinked_dedicated_root_is_rejected_without_touching_destination(ready, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ready / "quick-test").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"
    assert not list(outside.iterdir())


def test_unavailable_registered_identity_is_not_reinitialized(ready):
    result = prepare_quick_test(request(ready))
    result.settings.workspace.config_path.unlink()
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"
    assert not result.settings.workspace.config_path.exists()


def test_workspace_resource_exports_canonical_quick_test_contracts():
    from fsq_agent.application import contracts, workspace

    for name in ("QuickTestCheck", "QuickTestChoice", "QuickTestContextRequest", "QuickTestContextResult", "QuickTestPrepareRequest", "QuickTestPrepareResult"):
        assert getattr(workspace, name) is getattr(contracts, name)


def test_inferred_hint_reaches_discovery_without_replacing_grounded_name(ready, monkeypatch):
    from fsq_agent.models import ApplicationLookupHint

    hint = ApplicationLookupHint(kind="app_id", value="com.calc")
    intent = QuickTestIntent(True, "sufficient", "Calculator", None, inferred_hint=hint)
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    seen = []

    def lookup(self, **kwargs):
        seen.append(kwargs)
        return ApplicationLookupResult(status="complete", candidates=(ApplicationCandidate(identity="com.calc", label="Calculator", app_id="com.calc"),))

    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lookup)
    prepare_quick_test(request(ready))
    assert seen[0]["name"] == "Calculator"
    assert seen[0]["explicit_reference"] is None
    assert seen[0]["inferred_hint"] == hint


def _use_native_lookup(monkeypatch):
    from fsq_agent.environments._applications import lookup_application

    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lambda self, **kwargs: lookup_application(**kwargs))


@pytest.mark.parametrize("name", ["微信", "WeChat"])
def test_named_goal_prepares_installed_inferred_package_without_labels(ready, monkeypatch, name):
    payload = json.dumps(
        {
            "sufficient": True,
            "reason": "sufficient",
            "application_name": name,
            "explicit_reference": None,
            "inferred_hint": {"kind": "app_id", "value": "com.tencent.mm"},
        }
    )
    session = SimpleNamespace(
        complete_sync=lambda request: ModelResult(text=payload, parsed_output=request.output.parse(payload)),
        close_sync=lambda: None,
    )
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: QuickTestIntentAnalyzer(session))
    _use_native_lookup(monkeypatch)
    monkeypatch.setattr(
        "fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="serial-1", state="device"), AndroidDevice(serial="other-device", state="device")]
    )
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("Concrete reference must not enumerate names"))
    queries = []

    def package_status(serial, app_id, **kwargs):
        queries.append((serial, app_id))
        return "installed" if (serial, app_id) == ("serial-1", "com.tencent.mm") else "absent"

    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", package_status, raising=False)
    result = prepare_quick_test(QuickTestPrepareRequest(platform="android", target_id="serial-1", goal=f"用{name}搜索联系人张三\uff0c确认出现匹配结果", user_config_root=ready))
    assert result.target.app_id == "com.tencent.mm"
    assert result.settings.harness.android.app_id == "com.tencent.mm"
    assert result.settings.harness.android.serial == "serial-1"
    assert queries == [("serial-1", "com.tencent.mm")]
    assert load_registered_workspace("quick-test", "android", ready).target.app_id == "com.tencent.mm"


def test_missing_inferred_package_never_uses_name_match_or_previous_target(ready, monkeypatch):
    from fsq_agent.models import ApplicationLookupHint

    previous = prepare_quick_test(request(ready))
    before = previous.settings.workspace.config_path.read_bytes()
    intent = QuickTestIntent(True, "sufficient", "Calculator", None, ApplicationLookupHint(kind="app_id", value="com.missing.calculator"))
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    _use_native_lookup(monkeypatch)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="serial-1", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: "absent", raising=False)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("No name fallback after a missing concrete target"))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.application_not_found"
    assert previous.settings.workspace.config_path.read_bytes() == before
    assert not list((previous.root_path / ".fsq/runs/android").iterdir())


def test_scoped_multiple_bundle_installations_fail_before_workspace_or_execution(ready, monkeypatch, tmp_path):
    from fsq_agent.models import ApplicationLookupHint

    applications = tmp_path / "applications"
    for index in range(2):
        metadata = applications / f"Installation{index}.app/Contents/Info.plist"
        metadata.parent.mkdir(parents=True)
        metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.tencent.xinWeChat", "CFBundleName": f"Local label {index}"}))
    intent = QuickTestIntent(True, "sufficient", "WeChat", None, ApplicationLookupHint(kind="bundle_id", value="com.tencent.xinWeChat"))
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    _use_native_lookup(monkeypatch)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (applications,))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(QuickTestPrepareRequest(platform="macos", goal="Use WeChat to search and verify the result", user_config_root=ready))
    assert error.value.code.value == "quick_test.application_ambiguous"
    assert not (ready / "quick-test").exists()


@pytest.mark.parametrize(("reason", "code"), [("insufficient", "quick_test.insufficient"), ("ambiguous_application", "quick_test.application_ambiguous")])
def test_concrete_hint_does_not_override_negative_intent(ready, monkeypatch, reason, code):
    from fsq_agent.models import ApplicationLookupHint

    intent = QuickTestIntent(False, reason, "Calculator", None, ApplicationLookupHint(kind="app_id", value="com.android.calculator2"))
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lambda *args, **kwargs: pytest.fail("Negative intent must fail before concrete lookup"))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == code
    assert not (ready / "quick-test").exists()


@pytest.mark.parametrize("operation", ["create", "add", "update", "registry"])
@pytest.mark.parametrize("failure", [PermissionError("PRIVATE permissions"), OSError(28, "PRIVATE disk full")])
def test_real_config_write_failures_are_persistence_errors_not_conflicts(ready, monkeypatch, operation, failure):
    from fsq_agent.config import _user_provider, _workspace, create_workspace_at_root
    from fsq_agent.models import WebWorkspaceTarget

    root = ready / "quick-test"
    if operation == "update":
        prepare_quick_test(request(ready))
        monkeypatch.setattr(
            PlatformRuntimeService,
            "lookup_application",
            lambda *args, **kwargs: ApplicationLookupResult(status="complete", candidates=(ApplicationCandidate(identity="com.updated", label="Updated", app_id="com.updated"),)),
        )
    elif operation == "add":
        browser = ready / "Google Chrome" / "chrome"
        browser.parent.mkdir()
        browser.write_bytes(b"fixture")
        browser.chmod(0o755)
        create_workspace_at_root(
            root_path=root,
            name="quick-test",
            platforms=[WorkspacePlatformCreateInput(platform="web", target=WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=browser), env={"KEEP": "private-value"})],
            user_config_root=ready,
        )
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
    owner = _user_provider if operation == "registry" else _workspace
    original = owner._atomic_write

    def fail_write(path, payload):
        if path == ready / "config.yaml" if operation == "registry" else path.name == "config.android.yaml":
            raise failure
        return original(path, payload)

    monkeypatch.setattr(owner, "_atomic_write", fail_write)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.persistence_failed"
    assert error.value.details == {"stage": "workspace_persistence", "reason": "io_error"}
    assert "PRIVATE" not in str(error.value.to_record())
    assert str(ready) not in str(error.value.to_record())
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} if root.exists() else {}
    assert after == before


def test_configuration_exception_type_alone_is_not_workspace_conflict_evidence(ready, monkeypatch):
    from fsq_agent.config import _workspace
    from fsq_agent.models import ConfigurationError

    def opaque_write_failure(*args):
        raise ConfigurationError("PRIVATE storage adapter failure")

    monkeypatch.setattr(_workspace, "_atomic_write", opaque_write_failure)
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.persistence_failed"
    assert error.value.details == {"stage": "workspace_persistence", "reason": "storage_error"}
    assert "PRIVATE" not in str(error.value.to_record())


@pytest.mark.parametrize("platform", ["windows", "macos"])
def test_desktop_successful_prepare_create_update_preserves_env_and_frozen_settings(ready, monkeypatch, platform):
    from fsq_agent.models import ApplicationLookupHint, MacOSWorkspaceTarget, WindowsWorkspaceTarget

    monkeypatch.setattr("fsq_agent.execution.allocate_run", lambda *args, **kwargs: pytest.fail("Preparation must not allocate a Run"))
    monkeypatch.setattr("fsq_agent.core.HarnessFactory.create_harness", lambda *args, **kwargs: pytest.fail("Preparation must not create a Driver"))
    paths = []
    for index in range(2):
        path = ready / (f"Application{index}.exe" if platform == "windows" else f"Application{index}.app")
        if platform == "windows":
            path.write_bytes(b"not a real executable; filesystem fixture")
        else:
            metadata = path / "Contents/Info.plist"
            metadata.parent.mkdir(parents=True)
            metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": f"com.fixture.app{index}", "CFBundleName": "Calculator"}))
        paths.append(path)
    selected = [0]

    def analyzer(settings):
        hint = ApplicationLookupHint(kind="app_path", value=str(paths[selected[0]]))
        return SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", "Calculator", None, hint))

    def lookup(self, **kwargs):
        index = selected[0]
        assert kwargs["inferred_hint"].value == str(paths[index])
        return ApplicationLookupResult(
            status="complete",
            candidates=(ApplicationCandidate(identity=str(paths[index]), label="Calculator", app_path=paths[index], bundle_id=f"com.fixture.app{index}" if platform == "macos" else None),),
        )

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", analyzer)
    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lookup)
    req = QuickTestPrepareRequest(platform=platform, goal="Calculator: verify 1+1=2", user_config_root=ready)
    first = prepare_quick_test(req)
    assert isinstance(first.target, WindowsWorkspaceTarget if platform == "windows" else MacOSWorkspaceTarget)
    config_path = first.settings.workspace.config_path
    assert first.root_path == ready / "quick-test"
    assert first.platform_revision == workspace_revision(config_path)
    update_workspace_platform(name="quick-test", platform=platform, target=first.target, env={"KEEP": "private-value"}, expected_revision=first.platform_revision, user_config_root=ready)
    keep = first.root_path / "cases" / platform / "preserved.txt"
    keep.write_bytes(b"case fixture")
    selected[0] = 1
    second = prepare_quick_test(req)
    persisted = load_registered_workspace("quick-test", platform, ready)
    assert persisted.env == {"KEEP": "private-value"}
    assert persisted.target.app_path == paths[1]
    assert keep.read_bytes() == b"case fixture"
    assert second.platform_revision == workspace_revision(config_path)
    assert first.platform_revision != second.platform_revision
    assert getattr(first.settings.harness, platform).app_path == paths[0]
    assert getattr(second.settings.harness, platform).app_path == paths[1]
    assert first.settings.runtime_secrets.private_values() == {}
    assert second.settings.runtime_secrets.private_values() == {"KEEP": "private-value"}
    if platform == "macos":
        assert first.settings.harness.macos.bundle_id == "com.fixture.app0"
        assert second.settings.harness.macos.bundle_id == "com.fixture.app1"
    update_workspace_platform(name="quick-test", platform=platform, target=second.target, env={"KEEP": "later-value"}, expected_revision=second.platform_revision, user_config_root=ready)
    assert second.settings.runtime_secrets.private_values() == {"KEEP": "private-value"}
    assert second.platform_revision != workspace_revision(config_path)
    before_failure = config_path.read_bytes()
    selected[0] = 0
    monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (False, "Unavailable fixture target.", "Repair target."))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(req)
    assert error.value.code.value == "quick_test.prerequisites"
    assert config_path.read_bytes() == before_failure
    assert not list((first.root_path / ".fsq/runs" / platform).iterdir())


@pytest.mark.parametrize("goal", ["Check example.com opens", "Check ftp://example.com opens", "Check https://a.test and https://b.test open"])
def test_web_requires_explicit_single_site_before_persistence(ready, monkeypatch, goal):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda *args: [browser])
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=ready))
    assert error.value.code.value == "quick_test.invalid_web_address"
    assert not (ready / "quick-test").exists()


@pytest.fixture
def web_boundary(ready, monkeypatch):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("controlled browser fixture")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    monkeypatch.setattr("fsq_agent.core.HarnessFactory.create_harness", lambda *args, **kwargs: pytest.fail("No real browser or UI actions"))
    sessions = []

    def reply(reference, *, search=False):
        requests = []
        payload = json.dumps({"sufficient": True, "reason": "web_search" if search else "sufficient", "application_name": "FSQ", "explicit_reference": reference, "inferred_hint": None})
        session = SimpleNamespace(requests=requests, closed=False)

        def complete(request):
            requests.append(request)
            return ModelResult(text=payload, parsed_output=request.output.parse(payload))

        session.complete_sync = complete
        session.close_sync = lambda: setattr(session, "closed", True)
        sessions.append(session)
        monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: QuickTestIntentAnalyzer(session))
        return session

    return ready, reply, sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("goal", "reference"),
    [
        ("打开https://example.test/docs\uff0c确认标题", "https://example.test/docs"),
        ("Open “HTTPS://Example.TEST:443/a(b(c))?q=%2f#片段!” and verify", "HTTPS://Example.TEST:443/a(b(c))?q=%2f#片段!"),
        ("Open `https://example.test/路径?q=值\uff0c。` and verify", "https://example.test/路径?q=值\uff0c。"),
        ("Open <https://[2001:db8::1]:8443/a(b)?#> and verify", "https://[2001:db8::1]:8443/a(b)?#"),
        ("Open [site](https://example.test/report. 'title') and verify", "https://example.test/report."),
        ("Open https://example.test? and verify", "https://example.test?"),
        ("Open https://example.test/?q=one; and verify", "https://example.test/?q=one;"),
        ("Open https://example.test/#part!, and verify", "https://example.test/#part!,"),
        ("Open <https://example.test/a'b\"c?x=%2F> and verify", "https://example.test/a'b\"c?x=%2F"),
        ("Open <https://outer.test/?next=//☃.test&redirect=//😀.test> and verify", "https://outer.test/?next=//☃.test&redirect=//😀.test"),
        ("First https://first.test then https://second.test/path and verify", "https://second.test/path"),
        ("Open https://example.test/a\nVerify the heading", "https://example.test/a"),
        ("Open https://example.test/a\tVerify the heading", "https://example.test/a"),
        ("Open (https://[::1]:8443/a(b)) and verify", "https://[::1]:8443/a(b)"),
        (r'Open [https://label.test](<HTTPS://Example.TEST/a%2Fb?!> "say \"hello\" https://title.test") and verify', "HTTPS://Example.TEST/a%2Fb?!"),
    ],
)
async def test_exact_selected_address_reaches_shared_execution(web_boundary, monkeypatch, goal, reference):
    from fsq_agent.application import run_quick_test
    from fsq_agent.config import load_platform_settings
    from fsq_agent.execution import DynamicExecutionService
    from fsq_agent.models import DynamicAgentOutcome, VerificationResult

    root, reply, _ = web_boundary
    session = reply(reference)
    seen = []
    initial = []
    original_execute = DynamicExecutionService.execute

    def load(*args, **kwargs):
        settings = load_platform_settings(*args, **kwargs)
        initial.append(settings)
        return settings

    async def execute(self, request):
        seen.append(request)
        return await original_execute(self, request)

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            assert task.description == task.planning_reference_text == goal
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="inconclusive", summary="Controlled execution boundary"))

    monkeypatch.setattr(DynamicExecutionService, "execute", execute)
    monkeypatch.setattr("fsq_agent.application._quick_test.load_platform_settings", load)
    result = await run_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=root), agent_factory=lambda settings: Agent())
    assert len(seen) == 1
    assert seen[0].settings.harness.web.base_url == reference
    assert initial[0].harness.web.base_url == reference
    assert seen[0].task.description == seen[0].task.planning_reference_text == goal
    assert not seen[0].requires_web_search_evidence
    assert result.application == reference
    assert len(session.requests) == 1
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "https://example.test/a(b",
        "https://example.test:0/a",
        "https://[::1]extra/a",
        "https://example.test/%GG",
        "https://user@example.test/a",
        "https://example.test/a\\b",
        '"https://example.test/a\tb"',
        '"https://example.test/a\nb"',
        "https://example.test/a\u200bb",
        "https://[::1]]?x=1",
        "https://example.test/a)?x=1",
        "https://example.test/a]#part",
        *[
            template.format(f"//{host}")
            for host in ["example.test", "例子.test", "☃.test", "😀.test", "\u0301.test", "_.test", "%41.test", ").test", ">.test"]
            for template in ["{}", '"{}"', "<{}>", "`{}`", "[site]({})"]
        ],
        "https://example.test/path,then",
        '"https://example.test/a',
        "prefixhttps://example.test",
        "ftp://example.test",
    ],
)
async def test_invalid_syntax_stops_before_model_persistence_or_execution(web_boundary, monkeypatch, address):
    from fsq_agent.application import run_quick_test
    from fsq_agent.execution import DynamicExecutionService

    root, reply, _ = web_boundary
    session = reply(None, search=True)
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(DynamicExecutionService, "execute", lambda *args: pytest.fail("Invalid input reached execution"))
    with pytest.raises(ApplicationError) as error:
        await run_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=f"Search FSQ {address} and verify", user_config_root=root), agent_factory=lambda settings: object())
    assert error.value.code.value == "quick_test.invalid_web_address"
    assert session.requests == []
    assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_nul_in_address_fails_at_public_request_boundary(web_boundary):
    from pydantic import ValidationError

    root, reply, _ = web_boundary
    session = reply(None, search=True)
    with pytest.raises(ValidationError, match="A current goal is required"):
        prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal="Open https://example.test/a\x00b and verify", user_config_root=root))
    assert session.requests == []
    assert not (root / "quick-test").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize(
    "reference",
    [
        None,
        "https://Example.TEST:443/a%2Fb?x=1#part",
        "https://example.test:443/a%2Fb?x=1#part!",
        "https://Example.TEST:443/a%2fb?x=1#part!",
        "https://Example.TEST/a%2Fb?x=1#part!",
        "https://Example.TEST:443/a%2Fb?x=1",
        "https://Example.TEST",
        "https://inner.test",
        "\nhttps://Example.TEST:443/a%2Fb?x=1#part!\n",
    ],
)
async def test_invalid_model_provenance_stops_before_persistence_or_execution(web_boundary, monkeypatch, reference, multiple):
    from fsq_agent.application import run_quick_test
    from fsq_agent.execution import DynamicExecutionService

    root, reply, _ = web_boundary
    session = reply(reference)
    monkeypatch.setattr(DynamicExecutionService, "execute", lambda *args: pytest.fail("Invalid provenance reached execution"))
    goal = "Open <https://Example.TEST:443/a%2Fb?x=1#part!>"
    if multiple:
        goal += " then <https://outer.test/?next=https://inner.test>"
    goal += " and verify"
    with pytest.raises(ApplicationError) as error:
        await run_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=root), agent_factory=lambda settings: object())
    assert error.value.code.value == "quick_test.inference_failed"
    assert len(session.requests) == 1
    assert session.closed
    assert not (root / "quick-test").exists()
