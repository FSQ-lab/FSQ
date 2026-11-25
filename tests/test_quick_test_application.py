# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import multiprocessing
import plistlib
from types import SimpleNamespace

import pytest

from fsq_agent.agent_engine import ModelResult
from fsq_agent.ai_services import QuickTestIntent, QuickTestIntentAnalyzer
from fsq_agent.application import ApplicationError, QuickTestContextRequest, QuickTestPrepareRequest, get_quick_test_context, prepare_quick_test
from fsq_agent.config import create_workspace, create_workspace_at_root, list_workspace_registry, load_registered_workspace, save_openai_provider, update_workspace_platform, workspace_revision
from fsq_agent.environments import AndroidDeviceDiscovery, PlatformRuntimeService
from fsq_agent.models import (
    AndroidDevice,
    AndroidDeviceDiscoveryResult,
    AndroidWorkspaceTarget,
    ApplicationCandidate,
    ApplicationInventoryResult,
    ApplicationLookupResult,
    PlatformPrerequisiteCheck,
    PlatformRuntimeCheck,
    WebWorkspaceTarget,
    WorkspacePlatformCreateInput,
)

_DEFAULT_ANDROID_CANDIDATE = ApplicationCandidate(
    candidate_id="c001",
    identity="com.calc",
    label="Calculator",
    app_id="com.calc",
    source="android_package",
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
    intent = QuickTestIntent(True, "sufficient", target_name="Calculator", candidate_id="c001")
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=lambda **kwargs: intent))
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(
            status="complete",
            candidates=(_DEFAULT_ANDROID_CANDIDATE,),
            from_cache=False,
            fingerprint="fp-calc-1",
        ),
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "lookup_application",
        lambda self, **kwargs: ApplicationLookupResult(status="complete", candidates=(_DEFAULT_ANDROID_CANDIDATE,)),
    )
    return root


def request(root, **updates):
    params = {"platform": "android", "target_id": "serial-1", "goal": "Calculator: 1+1=2", "user_config_root": root}
    params.update(updates)
    return QuickTestPrepareRequest(**params)


@pytest.fixture
def windows_catalogue(ready, monkeypatch):
    from fsq_agent.models import WindowsApplicationConfig

    executable = ready / "msedge.exe"
    executable.write_bytes(b"filesystem fixture only")
    missing = ready / "missing.exe"
    entries = (
        WindowsApplicationConfig(
            id="edge",
            name="Microsoft Edge",
            description="Web browser",
            path_candidates=[str(missing), str(executable)],
            window_title_re=".*Microsoft Edge.*",
        ),
    )
    monkeypatch.setattr("fsq_agent.application._quick_test.load_windows_application_catalogue", lambda: entries)
    return entries, executable


def test_windows_catalogue_candidate_hides_window_title_re_from_model_and_persists_it(ready, monkeypatch, windows_catalogue):
    from fsq_agent.config import load_workspace_platform_settings

    entries, executable = windows_catalogue
    candidate = ApplicationCandidate(
        candidate_id="c001",
        identity=str(executable),
        label="Microsoft Edge",
        aliases=("edge",),
        app_path=executable,
        window_title_re=entries[0].window_title_re,
        source="windows_catalogue",
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(candidate,), fingerprint="fp-win-1"),
    )
    requests = []
    payload = json.dumps(
        {
            "sufficient": True,
            "reason": "sufficient",
            "target_name": "Microsoft Edge",
            "candidate_id": "c001",
            "explicit_reference": None,
            "base_url": None,
            "candidate_ids": [],
            "explanation": None,
        }
    )

    class Session:
        def complete_sync(self, req):
            requests.append(req)
            return ModelResult(text=payload, parsed_output=req.output.parse(payload))

        def close_sync(self):
            pass

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: QuickTestIntentAnalyzer(Session()))
    monkeypatch.setattr("fsq_agent.core.HarnessFactory.create_harness", lambda *args, **kwargs: pytest.fail("No GUI/Driver construction"))

    result = prepare_quick_test(QuickTestPrepareRequest(platform="windows", goal="打开edge\uff0c访问www.google.com", user_config_root=ready))
    assert len(requests) == 1
    assert "window_title_re" not in requests[0].input
    assert "launch_args" not in requests[0].input
    assert result.target.app_path == executable
    assert result.target.window_title_re == entries[0].window_title_re
    assert result.application == "Microsoft Edge"
    persisted = load_registered_workspace("quick-test", "windows", ready)
    case_settings = load_workspace_platform_settings(result.root_path, "windows", ready)
    assert persisted.target == result.target
    assert case_settings.harness.windows.app_path == executable
    assert case_settings.harness.windows.window_title_re == entries[0].window_title_re
    assert "candidate_id" not in result.settings.workspace.config_path.read_text(encoding="utf-8")


def test_windows_unknown_model_candidate_id_is_inference_failure(ready, monkeypatch, windows_catalogue):
    _, executable = windows_catalogue
    candidate = ApplicationCandidate(
        candidate_id="c001",
        identity=str(executable),
        label="Microsoft Edge",
        app_path=executable,
        source="windows_catalogue",
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(candidate,), fingerprint="fp-win-1"),
    )

    class Session:
        def complete_sync(self, req):
            payload = json.dumps(
                {
                    "sufficient": True,
                    "reason": "sufficient",
                    "target_name": "edge",
                    "candidate_id": "c999",
                    "explicit_reference": None,
                    "base_url": None,
                    "candidate_ids": [],
                    "explanation": None,
                }
            )
            return ModelResult(text=payload, parsed_output=req.output.parse(payload))

        def close_sync(self):
            pass

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: QuickTestIntentAnalyzer(Session()))
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(QuickTestPrepareRequest(platform="windows", goal="打开edge\uff0c访问www.google.com", user_config_root=ready))
    assert error.value.code.value == "quick_test.inference_failed"
    assert not (ready / "quick-test").exists()


def test_cache_miss_refreshes_inventory_once_and_reinvokes_only_when_changed(ready, monkeypatch):
    cached_candidate = ApplicationCandidate(candidate_id="c001", identity="com.calc", label="Calculator", app_id="com.calc", source="android_package")
    fresh_candidate = ApplicationCandidate(candidate_id="c002", identity="com.tencent.mm", label="WeChat", app_id="com.tencent.mm", source="android_package")
    inventory_calls = []

    def list_apps(self, **kwargs):
        inventory_calls.append(kwargs["force_refresh"])
        if not kwargs["force_refresh"]:
            return ApplicationInventoryResult(status="complete", candidates=(cached_candidate,), from_cache=True, fingerprint="fp-old")
        return ApplicationInventoryResult(status="complete", candidates=(cached_candidate, fresh_candidate), from_cache=False, fingerprint="fp-new")

    analyze_calls = []

    def analyze(**kwargs):
        analyze_calls.append(tuple(item.candidate_id for item in kwargs["candidates"]))
        if len(analyze_calls) == 1:
            return QuickTestIntent(False, "application_not_found", target_name="WeChat", explanation="Not in cached inventory.")
        return QuickTestIntent(True, "sufficient", target_name="WeChat", candidate_id="c002")

    monkeypatch.setattr(PlatformRuntimeService, "list_applications", list_apps)
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))

    result = prepare_quick_test(request(ready, goal="打开微信发消息"))
    assert inventory_calls == [False, True]
    assert analyze_calls == [("c001",), ("c001", "c002")]
    assert result.target.app_id == "com.tencent.mm"
    assert result.application == "WeChat"


def test_cache_miss_skips_second_model_call_when_refreshed_inventory_is_unchanged(ready, monkeypatch):
    cached_candidate = ApplicationCandidate(candidate_id="c001", identity="com.calc", label="Calculator", app_id="com.calc", source="android_package")
    inventory_calls = []

    def list_apps(self, **kwargs):
        inventory_calls.append(kwargs["force_refresh"])
        return ApplicationInventoryResult(
            status="complete",
            candidates=(cached_candidate,),
            from_cache=not kwargs["force_refresh"],
            fingerprint="fp-same",
        )

    analyze_calls = []

    def analyze(**kwargs):
        analyze_calls.append(kwargs)
        return QuickTestIntent(False, "application_not_found", target_name="WeChat", explanation="WeChat is not installed.")

    monkeypatch.setattr(PlatformRuntimeService, "list_applications", list_apps)
    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))

    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready, goal="打开微信发消息"))
    assert error.value.code.value == "quick_test.application_not_found"
    assert error.value.details == {"target_name": "WeChat", "reason": "WeChat is not installed."}
    assert inventory_calls == [False, True]
    assert len(analyze_calls) == 1
    assert not (ready / "quick-test").exists()


def test_application_ambiguous_carries_structured_candidate_diagnostics(ready, monkeypatch):
    first = ApplicationCandidate(candidate_id="c001", identity="com.tencent.mm", label="WeChat", app_id="com.tencent.mm", source="android_package")
    second = ApplicationCandidate(candidate_id="c002", identity="com.tencent.wework", label="WeCom", app_id="com.tencent.wework", source="android_package")
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(first, second), from_cache=False, fingerprint="fp-2"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(
            analyze=lambda **kwargs: QuickTestIntent(
                False,
                "ambiguous_application",
                target_name="微信",
                candidate_ids=("c001", "c002"),
                explanation="Both WeChat and WeCom are installed.",
            )
        ),
    )
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready, goal="打开微信发消息"))
    assert error.value.code.value == "quick_test.application_ambiguous"
    assert error.value.details["target_name"] == "微信"
    assert error.value.details["reason"] == "Both WeChat and WeCom are installed."
    assert [item["candidate_id"] for item in error.value.details["candidates"]] == ["c001", "c002"]
    assert not (ready / "quick-test").exists()


def test_explicit_reference_fallback_resolves_via_lookup_application(ready, monkeypatch):
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(), from_cache=False, fingerprint="fp-empty"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", target_name="CustomCalc", explicit_reference="com.custom.calc")),
    )
    seen = []

    def lookup(self, **kwargs):
        seen.append(kwargs)
        return ApplicationLookupResult(
            status="complete",
            candidates=(ApplicationCandidate(identity="com.custom.calc", label="CustomCalc", app_id="com.custom.calc"),),
        )

    monkeypatch.setattr(PlatformRuntimeService, "lookup_application", lookup)
    result = prepare_quick_test(request(ready, goal="Open com.custom.calc and verify 1+1=2"))
    assert seen[0]["explicit_reference"] == "com.custom.calc"
    assert result.target.app_id == "com.custom.calc"


def test_context_needs_no_workspace_and_app_checks_do_not_deadlock(ready):
    result = get_quick_test_context(QuickTestContextRequest(platform="android", target_id="serial-1", user_config_root=ready))
    assert result.can_start
    assert any(check.id == "application" and check.status == "pending" for check in result.checks)
    assert not (ready / "quick-test").exists()


def test_context_requires_explicit_device_selection(ready):
    result = get_quick_test_context(QuickTestContextRequest(platform="android", user_config_root=ready))
    assert not result.can_start
    assert [item.id for item in result.choices] == ["serial-1"]


def test_prepare_existing_platform_preserves_saved_target_env_and_unrelated_files(ready, monkeypatch):
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
    before = config_path.read_bytes()
    keep = first.root_path / "cases/android/keep.txt"
    keep.write_text("unchanged")
    other = ApplicationCandidate(candidate_id="c001", identity="com.other", label="Other", app_id="com.other", source="android_package")
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(other,), from_cache=False, fingerprint="fp-other"),
    )
    second = prepare_quick_test(request(ready))
    current = load_registered_workspace("quick-test", "android", ready)
    assert current.target.app_id == "com.calc"
    assert config_path.read_bytes() == before
    assert current.env == {"PRIVATE": "secret-value"}
    assert keep.read_text() == "unchanged"
    assert first.settings.harness.android.app_id == "com.calc"
    assert second.settings.harness.android.app_id == "com.other"
    assert second.platform_revision == workspace_revision(config_path)


def _prepare_web_worker(root, channel, executable, url, gate, publication, results):
    from fsq_agent.config import _user_provider, _workspace, load_quick_test_workspace_settings

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="Ready."))
        patch.setattr(PlatformRuntimeService, "check_prerequisites", lambda *args, **kwargs: ())
        patch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, selected: [executable] if selected == channel else [])
        patch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda settings: SimpleNamespace(ready=True, message="Ready.", action=""))
        patch.setattr(
            "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
            lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", target_name="Fixture", base_url=url)),
        )
        if publication is not None:
            published, contender = publication
            if channel == "chrome":
                original_write = _workspace._atomic_write

                def hold_first_publication(path, data):
                    original_write(path, data)
                    if path.name == "config.web.yaml":
                        published.set()
                        assert contender.wait(timeout=15), "The other process did not attempt the initialization lock."

                patch.setattr(_workspace, "_atomic_write", hold_first_publication)
            else:
                acquire = _user_provider._acquire_process_lock

                def contend(lock_file):
                    if published.is_set():
                        contender.set()
                    acquire(lock_file)

                patch.setattr(_user_provider, "_acquire_process_lock", contend)

        def reach_configuration(*args, **kwargs):
            gate.wait(timeout=15)
            if publication is not None and channel != "chrome":
                assert published.wait(timeout=15), "The first process did not publish the initial config."
            return load_quick_test_workspace_settings(*args, **kwargs)

        patch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (True, "Ready.", ""))
        patch.setattr("fsq_agent.application._quick_test.load_quick_test_workspace_settings", reach_configuration)
        try:
            result = prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id=channel, goal="Open the fixture and read its heading.", url=url, user_config_root=root))
        except ApplicationError as error:
            results.put((channel, error.to_record()))
        else:
            web = result.settings.harness.web
            results.put((channel, result.workspace_name, result.root_path, result.platform_revision, result.target_id, result.target, web.channel, web.base_url, web.browser_executable_path))


@pytest.mark.parametrize("state", ["new_workspace", "new_platform", "existing_platform"])
def test_independent_processes_prepare_distinct_web_targets(ready, state):
    def executable(product, name):
        path = ready / product / name
        path.parent.mkdir()
        path.write_bytes(b"not launched")
        path.chmod(0o755)
        return path

    targets = {
        "chrome": (executable("Google Chrome", "chrome"), "https://first.example.test/path"),
        "msedge": (executable("Microsoft Edge", "msedge"), "https://second.example.test/?section=two"),
    }
    root = ready / "quick-test"
    config_path = root / ".fsq/config/config.web.yaml"
    if state == "existing_platform":
        default = WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=targets["chrome"][0])
        create_workspace_at_root(root_path=root, name="quick-test", platforms=[WorkspacePlatformCreateInput(platform="web", target=default, env={"KEEP": "unchanged"})], user_config_root=ready)
        before = config_path.read_bytes()
    elif state == "new_platform":
        prepare_quick_test(request(ready))

    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Barrier(2)
    results = ctx.Queue()
    publication = None if state == "existing_platform" else (ctx.Event(), ctx.Event())
    workers = [ctx.Process(target=_prepare_web_worker, args=(ready, channel, path, url, gate, publication, results)) for channel, (path, url) in targets.items()]
    try:
        for worker in workers:
            worker.start()
        observed = {row[0]: row[1:] for row in (results.get(timeout=20) for _ in workers)}
        for worker in workers:
            worker.join(timeout=5)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            worker.close()
        results.close()
        results.join_thread()

    assert all(isinstance(outcome[0], str) for outcome in observed.values()), observed
    assert [(entry.name, entry.root_path) for entry in list_workspace_registry(ready)] == [("quick-test", root)]
    revision = workspace_revision(config_path)
    assert observed == {
        channel: ("quick-test", root, revision, channel, WebWorkspaceTarget(browser_channel=channel, browser_executable_path=path), channel, url, path) for channel, (path, url) in targets.items()
    }
    stored = load_registered_workspace("quick-test", "web", ready)
    assert stored.target == WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=targets["chrome"][0])
    if state == "existing_platform":
        assert config_path.read_bytes() == before
    else:
        assert "example.test" not in config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("reason", ["insufficient", "missing_application", "application_not_found", "ambiguous_application"])
def test_insufficient_never_uses_old_target(ready, monkeypatch, reason):
    prepare_quick_test(request(ready))
    before = (ready / "quick-test/.fsq/config/config.android.yaml").read_bytes()
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(False, reason)),
    )
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
    assert error.value.details["reason"] == "identity_conflict"
    assert not (ready / "quick-test").exists()


@pytest.mark.parametrize("problem", ["unsafe_root", "identity_unavailable"])
def test_preparation_does_not_replace_an_unavailable_or_unsafe_root(ready, tmp_path, problem):
    root = ready / "quick-test"
    if problem == "unsafe_root":
        other = tmp_path / "other"
        other.mkdir()
        try:
            root.symlink_to(other, target_is_directory=True)
        except OSError:
            pytest.skip("Host does not support creating directory symlinks.")
    else:
        prepare_quick_test(request(ready))
        root.rename(ready / "moved")
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready))
    assert error.value.code.value == "quick_test.workspace_conflict"
    assert error.value.details["reason"] == problem
    if problem == "unsafe_root":
        assert root.is_symlink()
        assert not list(other.iterdir())
    else:
        assert not root.exists()


def test_cancel_after_inference_prevents_persistence(ready, monkeypatch):
    cancelled = False

    def analyze(**kwargs):
        nonlocal cancelled
        cancelled = True
        return QuickTestIntent(True, "sufficient", target_name="Calculator", candidate_id="c001")

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
    from fsq_agent.config import _loader

    first = prepare_quick_test(request(ready))
    config_path = first.settings.workspace.config_path
    update_workspace_platform(name="quick-test", platform="android", target=first.target, env={"PRIVATE": "before"}, expected_revision=first.platform_revision, user_config_root=ready)
    before = workspace_revision(config_path)
    compose = _loader._compose_workspace_platform_settings

    def edit_after_snapshot(*args, **kwargs):
        update_workspace_platform(name="quick-test", platform="android", target=AndroidWorkspaceTarget(app_id="com.other"), env={"PRIVATE": "after"}, expected_revision=before, user_config_root=ready)
        return compose(*args, **kwargs)

    monkeypatch.setattr(_loader, "_compose_workspace_platform_settings", edit_after_snapshot)
    prepared = prepare_quick_test(request(ready))
    assert prepared.settings.harness.android.app_id == "com.calc"
    current = load_registered_workspace("quick-test", "android", ready)
    assert current.target.app_id == "com.other"
    assert current.env == {"PRIVATE": "after"}
    assert prepared.settings.runtime_secrets.private_values() == {"PRIVATE": "before"}
    assert prepared.platform_revision == before
    assert prepared.platform_revision != workspace_revision(config_path)


@pytest.mark.parametrize("unsafe_runs", [False, True])
def test_stale_saved_browser_does_not_authorize_unsafe_workspace_paths(ready, unsafe_runs):
    from fsq_agent.config import load_quick_test_workspace_settings
    from fsq_agent.models import ConfigurationError

    chrome = ready / "Google Chrome" / "chrome"
    chrome.parent.mkdir()
    chrome.write_bytes(b"not launched")
    chrome.chmod(0o755)
    root = ready / "quick-test"
    default = WebWorkspaceTarget(browser_channel="chrome", browser_executable_path=chrome)
    create_workspace_at_root(root_path=root, name="quick-test", platforms=[WorkspacePlatformCreateInput(platform="web", target=default, env={})], user_config_root=ready)
    chrome.unlink()
    edge = ready / "Microsoft Edge" / "msedge"
    edge.parent.mkdir()
    edge.write_bytes(b"not launched")
    edge.chmod(0o755)
    target = WebWorkspaceTarget(browser_channel="msedge", browser_executable_path=edge)
    if not unsafe_runs:
        _, _, settings = load_quick_test_workspace_settings("web", target, ready)
        assert settings.output.runs_dir == root / ".fsq/runs/web"
        return

    outside = ready / "outside"
    outside.mkdir()
    runs_dir = root / ".fsq/runs/web"
    runs_dir.rmdir()
    try:
        runs_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not support creating directory symlinks.")
    with pytest.raises(ConfigurationError) as error:
        load_quick_test_workspace_settings("web", target, ready)
    assert error.value.context["workspace_failure"] == "configuration_conflict"
    assert not list(outside.iterdir())


def test_quick_test_shared_config_checks_saved_windows_launch_syntax(ready):
    from fsq_agent.config import load_quick_test_workspace_settings
    from fsq_agent.models import ConfigurationError, WindowsWorkspaceTarget

    default_app = ready / "default.exe"
    default_app.touch()
    default = WindowsWorkspaceTarget(app_path=default_app, launch_args='--title "unmatched')
    root = ready / "quick-test"
    create_workspace_at_root(root_path=root, name="quick-test", platforms=[WorkspacePlatformCreateInput(platform="windows", target=default, env={})], user_config_root=ready)
    new_app = ready / "new.exe"
    new_app.touch()
    with pytest.raises(ConfigurationError) as error:
        load_quick_test_workspace_settings("windows", WindowsWorkspaceTarget(app_path=new_app), ready)
    assert error.value.context["workspace_failure"] == "configuration_conflict"


@pytest.mark.parametrize("existing_workspace", [False, True])
def test_invalid_windows_seed_cannot_publish_platform_or_registry(ready, existing_workspace):
    from fsq_agent.config import load_quick_test_workspace_settings
    from fsq_agent.models import ConfigurationError, WindowsWorkspaceTarget

    root = ready / "quick-test"
    if existing_workspace:
        prepare_quick_test(request(ready))
    before = (ready / "config.yaml").read_bytes()
    exe = ready / "fixture.exe"
    exe.touch()
    target = WindowsWorkspaceTarget(app_path=exe, launch_args='--title "unmatched')
    with pytest.raises(ConfigurationError) as error:
        load_quick_test_workspace_settings("windows", target, ready)
    assert error.value.context["workspace_failure"] == "configuration_conflict"
    assert (ready / "config.yaml").read_bytes() == before
    assert not (root / ".fsq/config/config.windows.yaml").exists()
    assert root.exists() == existing_workspace


def test_quick_test_config_does_not_initialize_ios(tmp_path):
    from fsq_agent.config import load_quick_test_workspace_settings
    from fsq_agent.models import ConfigurationError, IOSWorkspaceTarget

    user_root = tmp_path / "absent"
    with pytest.raises(ConfigurationError):
        load_quick_test_workspace_settings("ios", IOSWorkspaceTarget(bundle_id="com.fixture"), user_root)
    assert not user_root.exists()


def test_web_multiple_navigation_addresses_with_explicit_start_are_valid(ready, monkeypatch):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    goal = "Start at http://example.test then navigate to https://example.test/login and verify the login form."
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", target_name="example.test", base_url="http://example.test")),
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
        return QuickTestIntent(True, "sufficient" if address else "web_search", target_name=subject, base_url=address)

    monkeypatch.setattr("fsq_agent.application._quick_test.build_quick_test_intent_analyzer", lambda settings: SimpleNamespace(analyze=analyze))
    result = prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=goal, user_config_root=ready))
    assert seen == [goal]
    assert result.application == (address or subject)
    assert result.settings.harness.web.base_url == address
    assert result.requires_web_search_evidence is (address is None)


@pytest.mark.parametrize("goal", ["搜索 FSQ github\uff0c并且帮我找到FSQ 的官网地址", "Goal: search FSQ on GitHub and find its official site"])
def test_web_search_clears_initial_and_frozen_url_and_keeps_subject(ready, monkeypatch, goal):
    from fsq_agent.config import load_platform_settings, load_quick_test_workspace_settings

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
        name, revision, settings = load_quick_test_workspace_settings(*args, **kwargs)
        settings.harness.web.base_url = "https://prior-workspace.test"
        frozen.append(settings)
        return name, revision, settings

    def analyze(**kwargs):
        analyzed.append(kwargs["goal"])
        return QuickTestIntent(True, "web_search", target_name="FSQ", base_url=None)

    monkeypatch.setattr("fsq_agent.application._quick_test.load_platform_settings", load)
    monkeypatch.setattr("fsq_agent.application._quick_test.load_quick_test_workspace_settings", load_frozen)
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
    "bad_url",
    ["https://user:pass@example.test", "javascript:alert(1)", "file:///tmp/test", "ftp://example.test", "https://example.test:0"],
)
def test_invalid_model_extracted_web_url_is_rejected_safely(ready, monkeypatch, bad_url):
    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("browser")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", target_name="site", base_url=bad_url)),
    )
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(QuickTestPrepareRequest(platform="web", target_id="chrome", goal=f"Open {bad_url} and verify", user_config_root=ready))
    assert error.value.code.value == "quick_test.invalid_web_address"
    assert "user:pass" not in str(error.value.to_record())
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
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "web_search", target_name="FSQ", base_url=None)),
    )
    seen = []
    execute = DynamicExecutionService.execute

    async def checked_execute(self, req):
        seen.append(req)
        return await execute(self, req)

    class Agent:
        async def run_in_context(self, task, context, **kwargs):
            assert task.description == task.planning_reference_text == goal
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Unsupported model claim"))

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


def test_targetless_context_ignores_removed_macos_appium_endpoint_env(ready, monkeypatch):
    observed = []
    monkeypatch.setenv("FSQ_MACOS_APPIUM_SERVER_URL", "http://127.0.0.1:4799")
    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", lambda self, settings, **kwargs: observed.append(settings.harness.macos.backend) or ())
    context = get_quick_test_context(QuickTestContextRequest(platform="macos", user_config_root=ready))
    assert context.can_start
    assert observed == ["appium_connect_agent"]


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
        monkeypatch.setattr(PlatformRuntimeService, "list_applications", lambda *args, **kwargs: ApplicationInventoryResult(status="failed", code="discovery_failed"))
    elif stage == "availability":
        monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: (False, "PRIVATE", "PRIVATE"))
    else:
        monkeypatch.setattr("fsq_agent.application._quick_test.load_quick_test_workspace_settings", fail)
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


@pytest.mark.parametrize("operation", ["create", "add", "registry"])
@pytest.mark.parametrize("failure", [PermissionError("PRIVATE permissions"), OSError(28, "PRIVATE disk full")])
def test_real_config_write_failures_are_persistence_errors_not_conflicts(ready, monkeypatch, operation, failure):
    from fsq_agent.config import _user_provider, _workspace

    root = ready / "quick-test"
    if operation == "add":
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
def test_desktop_prepare_freezes_new_targets_without_updating_saved_defaults(ready, monkeypatch, platform):
    from fsq_agent.models import MacOSWorkspaceTarget, WindowsWorkspaceTarget

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
    candidates = tuple(
        ApplicationCandidate(
            candidate_id=f"c00{index + 1}",
            identity=str(paths[index]),
            label="Calculator",
            app_path=paths[index],
            bundle_id=f"com.fixture.app{index}" if platform == "macos" else None,
            source="windows_app_paths" if platform == "windows" else "macos_bundle",
        )
        for index in range(2)
    )
    selected = [0]
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=candidates, fingerprint="fp-desk"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(analyze=lambda **kwargs: QuickTestIntent(True, "sufficient", target_name="Calculator", candidate_id=f"c00{selected[0] + 1}")),
    )

    req = QuickTestPrepareRequest(platform=platform, goal="Calculator: verify 1+1=2", user_config_root=ready)
    first = prepare_quick_test(req)
    assert isinstance(first.target, WindowsWorkspaceTarget if platform == "windows" else MacOSWorkspaceTarget)
    config_path = first.settings.workspace.config_path
    assert first.root_path == ready / "quick-test"
    assert first.platform_revision == workspace_revision(config_path)
    update_workspace_platform(name="quick-test", platform=platform, target=first.target, env={"KEEP": "private-value"}, expected_revision=first.platform_revision, user_config_root=ready)
    keep = first.root_path / "cases" / platform / "preserved.txt"
    keep.write_bytes(b"case fixture")
    before = config_path.read_bytes()
    selected[0] = 1
    second = prepare_quick_test(req)
    persisted = load_registered_workspace("quick-test", platform, ready)
    assert persisted.env == {"KEEP": "private-value"}
    assert persisted.target.app_path == paths[0]
    assert config_path.read_bytes() == before
    assert keep.read_bytes() == b"case fixture"
    assert second.platform_revision == workspace_revision(config_path)
    assert first.platform_revision != second.platform_revision  # The explicit env edit changed the snapshot.
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


def test_control_plane_failure_propagates_structured_diagnostic_details(ready, monkeypatch):
    import time

    from fsq_agent.adapters.control_plane import ControlPlaneServer, ControlPlaneServerOptions

    first = ApplicationCandidate(candidate_id="c001", identity="com.tencent.mm", label="WeChat", app_id="com.tencent.mm", source="android_package")
    second = ApplicationCandidate(candidate_id="c002", identity="com.tencent.wework", label="WeCom", app_id="com.tencent.wework", source="android_package")
    monkeypatch.setattr(
        PlatformRuntimeService,
        "list_applications",
        lambda self, **kwargs: ApplicationInventoryResult(status="complete", candidates=(first, second), from_cache=False, fingerprint="fp-cp"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(
            analyze=lambda **kwargs: QuickTestIntent(
                False,
                "ambiguous_application",
                target_name="微信",
                candidate_ids=("c001", "c002"),
                explanation="Multiple matching apps found.",
            )
        ),
    )
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=ready))
    status, accepted = server.handle_post("/api/control-plane/quick-test/runs", {"platform": "android", "targetId": "serial-1", "goal": "打开微信发消息"})
    assert status == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = server.state.snapshot(accepted["requestId"])
        if snapshot["terminal"]:
            break
        time.sleep(0.01)
    assert snapshot["terminal"]
    assert snapshot["error"]["code"] == "quick_test.application_ambiguous"
    assert snapshot["error"]["details"]["target_name"] == "微信"
    assert snapshot["error"]["details"]["reason"] == "Multiple matching apps found."
    assert [item["candidate_id"] for item in snapshot["error"]["details"]["candidates"]] == ["c001", "c002"]


def test_cli_human_and_json_output_include_structured_quick_test_diagnostics(ready, monkeypatch):
    from click.testing import CliRunner

    from fsq_agent.adapters.cli import main

    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: SimpleNamespace(
            analyze=lambda **kwargs: QuickTestIntent(
                False,
                "application_not_found",
                target_name="WeChat",
                explanation="WeChat is not installed on this device.",
            )
        ),
    )
    runner = CliRunner()
    human = runner.invoke(
        main,
        ["quick-test", "run", "--platform", "android", "--target-id", "serial-1", "--goal", "打开微信发消息"],
        env={"FSQ_USER_CONFIG_ROOT": str(ready)},
    )
    assert human.exit_code != 0
    assert "WeChat" in human.output
    assert "WeChat is not installed on this device." in human.output

    as_json = runner.invoke(
        main,
        ["--output", "json", "quick-test", "run", "--platform", "android", "--target-id", "serial-1", "--goal", "打开微信发消息"],
        env={"FSQ_USER_CONFIG_ROOT": str(ready)},
    )
    assert as_json.exit_code != 0
    record = json.loads(as_json.output)
    assert record["error"]["code"] == "quick_test.application_not_found"
    assert record["error"]["details"]["target_name"] == "WeChat"
    assert record["error"]["details"]["reason"] == "WeChat is not installed on this device."


def test_real_analyzer_invalid_extracted_web_url_raises_invalid_web_address(ready, monkeypatch):
    from fsq_agent.ai_services import QuickTestIntentAnalyzer

    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_bytes(b"fixture")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])

    class FakeSession:
        def complete_sync(self, req):
            raw = '{"sufficient": true, "reason": "sufficient", "target_name": "site", "candidate_id": null, "explicit_reference": null, "base_url": "javascript:alert(1)", "candidate_ids": [], "explanation": null}'
            return SimpleNamespace(parsed_output=req.output.parse(raw))

        def close_sync(self):
            pass

    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: QuickTestIntentAnalyzer(FakeSession()),
    )
    with pytest.raises(ApplicationError) as error:
        prepare_quick_test(request(ready, platform="web", target_id="chrome", goal="Open javascript:alert(1) and check"))
    assert error.value.code.value == "quick_test.invalid_web_address"
    assert error.value.details["reason"] == "invalid_extracted_web_url"


def test_web_bare_domain_goal_persists_completed_https_url(ready, monkeypatch):
    from fsq_agent.ai_services import QuickTestIntentAnalyzer

    browser = ready / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_bytes(b"fixture")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser] if channel == "chrome" else [])

    class FakeSession:
        def complete_sync(self, req):
            raw = '{"sufficient": true, "reason": "sufficient", "target_name": "x.com", "candidate_id": null, "explicit_reference": null, "base_url": "https://x.com", "candidate_ids": [], "explanation": null}'
            return SimpleNamespace(parsed_output=req.output.parse(raw))

        def close_sync(self):
            pass

    monkeypatch.setattr(
        "fsq_agent.application._quick_test.build_quick_test_intent_analyzer",
        lambda settings: QuickTestIntentAnalyzer(FakeSession()),
    )
    prepared = prepare_quick_test(request(ready, platform="web", target_id="chrome", goal="please open x.com and check trending posts"))
    assert prepared.application == "https://x.com"
    assert prepared.settings.harness.web.base_url == "https://x.com"
    assert not prepared.requires_web_search_evidence
