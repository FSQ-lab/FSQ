# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.application import QuickTestContextRequest, get_quick_test_context
from fsq_agent.config import Settings
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult, PlatformPrerequisiteCheck, PlatformRuntimeCheck


@pytest.fixture
def external_readiness(monkeypatch):
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="Runtime fact."))
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda settings: SimpleNamespace(ready=True, message="Provider fact.", action=""))
    monkeypatch.setattr("fsq_agent.application.doctor.check_case_suggestion_readiness", lambda settings: (True, "Suggestion fact.", ""))
    monkeypatch.setattr("fsq_agent.application.doctor.check_dynamic_agent_readiness", lambda settings: pytest.fail("Unresolved target must not run app-dependent dynamic readiness"))
    monkeypatch.setattr(PlatformRuntimeService, "check_target_configuration", lambda *args: pytest.fail("No dummy runnable native target"))
    monkeypatch.setattr(PlatformRuntimeService, "check_target_availability", lambda *args: pytest.fail("No unresolved application probe"))
    monkeypatch.setattr("fsq_agent.core.HarnessFactory.create_harness", lambda *args, **kwargs: pytest.fail("Diagnosis must not create a Driver"))


def context():
    from fsq_agent.application import PlatformDiagnosticContext

    return PlatformDiagnosticContext(platform="macos", application_pending=True, workspace_pending=True, require_explicit_selection=True)


def pending_facts(*, failing=False):
    return (
        PlatformPrerequisiteCheck(
            identifier="appium_cli",
            status="unavailable" if failing else "ready",
            code="fixture.appium",
            message="Shared Appium fact.",
            action="Shared repair." if failing else None,
            commands=("npm install -g appium",) if failing else (),
        ),
        PlatformPrerequisiteCheck(
            identifier="appium_mac2_driver",
            status="not_applicable",
            code="fixture.not_applicable",
            message="Original not-applicable fact.",
            action="Original action.",
            commands=("appium driver doctor mac2",),
        ),
        PlatformPrerequisiteCheck(identifier="application_path", status="pending", code="environment.application_pending", message="Awaiting application identity."),
        PlatformPrerequisiteCheck(identifier="bundle_identifier", status="pending", code="environment.application_pending", message="Awaiting application identity."),
    )


def test_shared_doctor_preserves_independent_facts_and_only_defers_dependencies(external_readiness, monkeypatch):
    from fsq_agent.application import diagnose_platform_settings

    facts = pending_facts(failing=True)
    observed = []

    def prerequisites(self, settings, **kwargs):
        observed.append(kwargs)
        return facts

    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", prerequisites)
    settings = Settings(harness={"platform": "macos"})
    result = diagnose_platform_settings(settings, context=context())
    assert result.checks.runtime.message == "Runtime fact."
    assert result.checks.provider.message == "Provider fact."
    assert result.checks.configuration.status == "pending"
    assert result.checks.target_configuration.status == "pending"
    assert result.commands.case_create.status == "unavailable"
    assert result.preparation.status == "unavailable"
    assert [item.model_dump() for item in result.prerequisites] == [item.model_dump(exclude={"target_id"}) for item in facts]
    assert observed == [{"application_pending": True, "require_explicit_selection": True}]
    assert settings.harness.macos.app_path is None
    assert datetime.fromisoformat(result.checked_at).tzinfo is not None


def test_shared_pending_commands_do_not_deadlock_identification(external_readiness, monkeypatch):
    from fsq_agent.application import diagnose_platform_settings

    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", lambda *args, **kwargs: pending_facts())
    monkeypatch.setattr("fsq_agent.application.doctor.check_case_suggestion_readiness", lambda settings: (False, "Suggestion unavailable independently.", "Repair suggestion."))
    result = diagnose_platform_settings(Settings(harness={"platform": "macos"}), context=context())
    assert result.preparation.status == "ready"
    assert result.commands.case_create.status == "pending"
    assert result.commands.case_test.status == "pending"
    assert result.commands.case_test_suggest.status == "unavailable"
    assert result.checks.suggestion_analyzer.status == "unavailable"
    assert result.prerequisites[1].status == "not_applicable"


def test_quick_context_and_http_projection_use_shared_doctor_metadata(external_readiness, monkeypatch, tmp_path):
    from fsq_agent.application import doctor
    from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions

    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", lambda *args, **kwargs: pending_facts())
    original = doctor.diagnose_platform_settings
    observed = []

    def diagnose(settings, **kwargs):
        result = original(settings, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr("fsq_agent.application._quick_test.diagnose_platform_settings", diagnose, raising=False)
    result = get_quick_test_context(QuickTestContextRequest(platform="macos", user_config_root=tmp_path))
    assert result.can_start
    assert result.diagnosis is observed[-1]
    assert result.diagnosis.prerequisites[1].status == "not_applicable"
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    status, payload, _ = server.handle_get("/api/control-plane/quick-test/context", {"platform": ["macos"]})
    assert status == 200
    diagnostics = payload["diagnostics"]
    assert diagnostics["workspaceName"] is None
    assert diagnostics["workspace"]["status"] == "pending"
    assert diagnostics["prerequisites"] == [item.model_dump(mode="json") for item in observed[-1].prerequisites]
    assert diagnostics["commands"]["caseCreate"]["status"] == "pending"
    assert diagnostics["commands"]["caseTestSuggest"]["status"] == "pending"
    assert diagnostics["commands"]["prepare"]["status"] == "ready"
    assert diagnostics["checkedAt"] == observed[-1].checked_at
    assert diagnostics["checks"] == observed[-1].checks.model_dump(mode="json")
    assert not (tmp_path / "quick-test").exists()


def test_android_unresolved_prerequisites_use_original_selection_and_skip_only_app(monkeypatch):
    from fsq_agent.environments.providers import _android

    monkeypatch.setattr(_android.shutil, "which", lambda name: "/fixture/adb")
    monkeypatch.setattr(_android, "ANDROID_RUNTIME_PROVIDER", SimpleNamespace(check=lambda: PlatformRuntimeCheck(platform="android", status="ready", ready=True, message="Ready.")))
    monkeypatch.setattr(_android, "discover_android_devices", lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="selected", state="device")]))
    monkeypatch.setattr(_android, "_installation", lambda *args: pytest.fail("No app installation query while identity is unresolved"))
    settings = Settings(harness={"platform": "android"})
    settings.harness.android.app_id = "com.previous"
    facts = PlatformRuntimeService().check_prerequisites(settings, application_pending=True, require_explicit_selection=True)
    assert next(item for item in facts if item.identifier == "device_selection").status == "unavailable"
    assert next(item for item in facts if item.identifier == "application_identifier").status == "pending"
    settings.harness.android.serial = "selected"
    selected = PlatformRuntimeService().check_prerequisites(settings, application_pending=True, require_explicit_selection=True)
    assert next(item for item in selected if item.identifier == "device_selection").status == "ready"
    assert next(item for item in selected if item.identifier == "application_installation").status == "pending"


def test_macos_unresolved_prerequisites_preserve_blocked_facts_and_commands(monkeypatch):
    from fsq_agent.environments.providers import _macos

    monkeypatch.setattr(_macos.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_macos, "_active_developer_directory", lambda: Path("/Applications/Xcode.app/Contents/Developer"))
    monkeypatch.setattr(_macos, "_developer_directory_is_full_xcode", lambda path: True)
    monkeypatch.setattr(_macos.shutil, "which", lambda name: None)
    monkeypatch.setattr(_macos, "_endpoint_available", lambda endpoint: False)
    monkeypatch.setattr(_macos, "_macos_application_path_available", lambda path: pytest.fail("No previous app path probe"))
    monkeypatch.setattr(_macos, "_macos_bundle_matches_target", lambda *args: pytest.fail("No previous bundle probe"))
    settings = Settings(harness={"platform": "macos"})
    settings.harness.macos.app_path = Path("/Previous.app")
    settings.harness.macos.bundle_id = "com.previous"
    settings.harness.macos.appium_server_url = "http://127.0.0.1:4723"
    facts = PlatformRuntimeService().check_prerequisites(settings, application_pending=True)
    by_id = {item.identifier: item for item in facts}
    assert by_id["appium_cli"].status == "unavailable"
    assert by_id["appium_mac2_driver"].status == "not_applicable"
    assert by_id["appium_mac2_driver"].commands == ("appium driver install mac2", "appium driver doctor mac2")
    assert by_id["appium_endpoint"].status == "unavailable"
    assert by_id["appium_endpoint"].commands == ("appium --address 127.0.0.1 --port 4723",)
    assert by_id["application_path"].status == "pending"
    assert by_id["bundle_identifier"].status == "pending"


@pytest.fixture
def failed_android_settings(monkeypatch, tmp_path):
    from fsq_agent.models import ConfigurationError

    root = tmp_path / "isolated-user"
    root.mkdir()

    def load_failure(*args, **kwargs):
        raise ConfigurationError("PRIVATE invalid configuration at /private/not-for-response")

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration-error diagnosis must not infer, inspect a device, construct a Driver or mutate a Workspace")

    monkeypatch.setattr("fsq_agent.application._quick_test.load_platform_settings", load_failure)
    for name in ("build_quick_test_intent_analyzer", "create_workspace_at_root", "add_workspace_platform", "update_workspace_platform"):
        monkeypatch.setattr(f"fsq_agent.application._quick_test.{name}", forbidden)
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", forbidden)
    monkeypatch.setattr("fsq_agent.environments.AndroidDeviceDiscovery.discover", forbidden)
    monkeypatch.setattr(PlatformRuntimeService, "check", forbidden)
    monkeypatch.setattr(PlatformRuntimeService, "check_prerequisites", forbidden)
    monkeypatch.setattr("fsq_agent.core.HarnessFactory.create_harness", forbidden)
    monkeypatch.setattr("fsq_agent.execution.allocate_run", forbidden)
    return root


@pytest.mark.parametrize("serial", ["selected-device", None])
def test_android_configuration_error_preserves_requested_scope_in_shared_diagnosis(failed_android_settings, serial):
    result = get_quick_test_context(QuickTestContextRequest(platform="android", target_id=serial, user_config_root=failed_android_settings))
    assert result.target_id == serial
    assert result.diagnosis.target_id == serial
    assert result.can_start is False
    assert result.choices == ()
    assert result.diagnosis.checks.configuration.status == "error"
    assert result.diagnosis.checks.configuration.code == "doctor.configuration_invalid"
    assert result.diagnosis.preparation.status == "error"
    assert result.diagnosis.prerequisites == ()
    assert not list(failed_android_settings.iterdir())


@pytest.mark.parametrize("serial", ["bad serial", "bad/serial", "", "a" * 257, 42])
def test_shared_diagnostic_context_rejects_invalid_android_scope(serial):
    from pydantic import ValidationError

    from fsq_agent.application import PlatformDiagnosticContext

    with pytest.raises(ValidationError):
        PlatformDiagnosticContext(platform="android", target_id=serial)


def test_shared_diagnostic_context_does_not_accept_browser_as_android_scope():
    from pydantic import ValidationError

    from fsq_agent.application import PlatformDiagnosticContext

    with pytest.raises(ValidationError):
        PlatformDiagnosticContext(platform="web", target_id="chrome")


def test_actual_http_android_configuration_error_is_scope_consistent(failed_android_settings, tmp_path):
    from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("test fixture", encoding="utf-8")
    server = ControlPlaneServer(ControlPlaneServerOptions(port=0, open_browser=False, static_path=static, user_config_root=failed_android_settings))
    server.start()
    connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
    try:
        connection.request("GET", "/api/control-plane/quick-test/context?platform=android&targetId=selected-device")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        payload = json.loads(body)
        assert payload["platform"] == payload["diagnostics"]["platformId"] == "android"
        assert payload["targetId"] == payload["diagnostics"]["targetId"] == "selected-device"
        assert payload["canStart"] is False
        assert payload["diagnostics"]["checks"]["configuration"]["code"] == "doctor.configuration_invalid"
        assert payload["diagnostics"]["checks"]["configuration"]["status"] == "error"
        assert payload["diagnostics"]["commands"]["prepare"]["status"] == "error"
        assert not any(record["status"] == "ready" for record in payload["diagnostics"]["checks"].values())
        assert payload["diagnostics"]["prerequisites"] == []
        assert "PRIVATE" not in body
        assert "/private/not-for-response" not in body
        assert not list(failed_android_settings.iterdir())
        assert not server.state.bootstrap()["busy"]
        print("QUICK_TEST_ANDROID_CONFIG_ERROR=" + body)
    finally:
        connection.close()
        server.stop()
