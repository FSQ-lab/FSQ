# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli._main import main
from fsq_agent.application import (
    ApplicationError,
    CaseCreateRequest,
    CaseTestRequest,
    RegisteredPlatformDoctorRequest,
    WorkspaceInitializeRequest,
    create_case,
    diagnose_registered_platform,
    initialize_workspace,
)
from fsq_agent.application import (
    test_case as run_case_test,
)
from fsq_agent.config import (
    create_workspace,
    load_registered_workspace,
    load_workspace_platform_settings,
    update_workspace_platform,
    workspace_revision,
)
from fsq_agent.models import (
    ConfigurationError,
    IOSDevice,
    IOSDeviceDiscoveryResult,
    IOSWorkspaceTarget,
    WorkspacePlatformCreateInput,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_fake_ios_app_bundle(app_dir: Path) -> Path:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "Info.plist").write_bytes(b"<?xml version='1.0' encoding='UTF-8'?><plist version='1.0'><dict/></plist>")
    return app_dir


def test_ios_workspace_init_and_signing_persistence_without_persisting_udid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_dir = tmp_path / "ios-ws"
    ws_dir.mkdir()
    user_root = tmp_path / "user"
    app_bundle = _write_fake_ios_app_bundle(tmp_path / "Sample.app")
    monkeypatch.setattr(
        "fsq_agent.environments.providers._runtime.platform.system",
        lambda: "Darwin",
    )
    res = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=ws_dir,
            platform="ios",
            bundle_id="com.example.sample",
            app_path=app_bundle,
            xcode_org_id="TEAM123456",
            xcode_signing_id="Apple Development",
            updated_wda_bundle_id="com.example.wda.runner",
            user_config_root=user_root,
        )
    )
    assert res.status == "initialized"
    assert res.platform == "ios"

    config_file = ws_dir / ".fsq" / "config" / "config.ios.yaml"
    config_text = config_file.read_text(encoding="utf-8")
    assert "udid" not in config_text

    loaded = load_registered_workspace(res.name, "ios", user_root)
    assert isinstance(loaded.target, IOSWorkspaceTarget)
    assert loaded.target.bundle_id == "com.example.sample"
    assert loaded.target.app_path == app_bundle.resolve()
    assert loaded.target.xcode_org_id == "TEAM123456"
    assert loaded.target.xcode_signing_id == "Apple Development"
    assert loaded.target.updated_wda_bundle_id == "com.example.wda.runner"

    settings = load_workspace_platform_settings(ws_dir, "ios", user_root)
    assert settings.harness.platform == "ios"
    assert settings.harness.ios.bundle_id == "com.example.sample"
    assert settings.harness.ios.app_path == app_bundle.resolve()
    assert settings.harness.ios.xcode_org_id == "TEAM123456"
    assert settings.harness.ios.udid is None


def test_ios_load_for_repair_preserves_revision_when_app_bundle_removed(tmp_path: Path) -> None:
    selected = tmp_path / "workspace"
    selected.mkdir()
    user = tmp_path / "user"
    old_app = _write_fake_ios_app_bundle(tmp_path / "Old.app")

    create_workspace(
        selected_path=selected,
        name="ios-repair",
        platforms=[WorkspacePlatformCreateInput(platform="ios", target={"app_path": old_app}, env={})],
        user_config_root=user,
    )
    (old_app / "Info.plist").unlink()
    old_app.rmdir()

    with pytest.raises(ConfigurationError):
        load_registered_workspace("ios-repair", "ios", user)

    current = load_registered_workspace("ios-repair", "ios", user, allow_unavailable_target=True)
    assert current.target.app_path == old_app

    config_path = selected / ".fsq" / "config" / "config.ios.yaml"
    revision = workspace_revision(config_path)

    with pytest.raises(ConfigurationError):
        update_workspace_platform(
            name="ios-repair",
            platform="ios",
            target={"app_path": tmp_path / "Missing.app"},
            env={},
            expected_revision=revision,
            user_config_root=user,
        )
    assert workspace_revision(config_path) == revision

    new_app = _write_fake_ios_app_bundle(tmp_path / "New.app")
    updated = update_workspace_platform(
        name="ios-repair",
        platform="ios",
        target={"app_path": new_app, "bundle_id": "com.example.new"},
        env={},
        expected_revision=revision,
        user_config_root=user,
    )
    assert updated.target.app_path == new_app
    assert updated.target.bundle_id == "com.example.new"


def test_ios_doctor_registered_platform_respects_target_id_without_invalid_target_id_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_dir = tmp_path / "ios-doc-ws"
    ws_dir.mkdir()
    user_root = tmp_path / "user"
    monkeypatch.setattr(
        "fsq_agent.environments.providers._runtime.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "fsq_agent.environments._service.platform.system",
        lambda: "Darwin",
    )

    init_res = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=ws_dir,
            platform="ios",
            bundle_id="com.apple.Preferences",
            user_config_root=user_root,
        )
    )

    from fsq_agent.models import PlatformPrerequisiteCheck

    monkeypatch.setattr(
        "fsq_agent.environments._service.ios_prerequisites",
        lambda ios_settings, **_kwargs: (
            PlatformPrerequisiteCheck(identifier="xcode_installation", status="ready", message="Xcode ready."),
            PlatformPrerequisiteCheck(identifier="xcode_developer_directory", status="ready", message="Developer dir ready."),
            PlatformPrerequisiteCheck(identifier="appium_connect_runtime", status="ready", message="Managed XCUITest ready."),
            PlatformPrerequisiteCheck(identifier="device_connection", status="ready", message="2 targets connected."),
            PlatformPrerequisiteCheck(
                identifier="device_selection",
                status="ready" if ios_settings.udid == "SIM-UDID-1" else "unavailable",
                code=None if ios_settings.udid == "SIM-UDID-1" else "ios.multiple_devices",
                message="Selected." if ios_settings.udid == "SIM-UDID-1" else "Multiple targets active.",
                target_id=ios_settings.udid,
            ),
            PlatformPrerequisiteCheck(identifier="application_identifier", status="ready", message="Bundle ID valid."),
            PlatformPrerequisiteCheck(identifier="application_path", status="not_applicable", message="Bundle ID mode."),
            PlatformPrerequisiteCheck(
                identifier="application_installation",
                status="ready" if ios_settings.udid == "SIM-UDID-1" else "not_applicable",
                message="Installed.",
            ),
        ),
    )

    ambiguous = diagnose_registered_platform(
        RegisteredPlatformDoctorRequest(
            workspace_name=init_res.name,
            platform="ios",
            user_config_root=user_root,
        )
    )
    assert ambiguous.platforms[0].commands.case_test.status == "unavailable"

    selected = diagnose_registered_platform(
        RegisteredPlatformDoctorRequest(
            workspace_name=init_res.name,
            platform="ios",
            target_id="SIM-UDID-1",
            user_config_root=user_root,
        )
    )
    assert selected.platforms[0].target_id == "SIM-UDID-1"
    assert selected.platforms[0].commands.case_test.status == "ready"


def test_ios_workspace_init_rejects_non_darwin_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_dir = tmp_path / "ios-linux-ws"
    ws_dir.mkdir()
    monkeypatch.setattr(
        "fsq_agent.environments.providers._runtime.platform.system",
        lambda: "Linux",
    )
    with pytest.raises(ApplicationError, match="ios platform runtime is unsupported on this host"):
        initialize_workspace(
            WorkspaceInitializeRequest(
                current_directory=ws_dir,
                platform="ios",
                bundle_id="com.apple.Preferences",
                user_config_root=tmp_path / "user",
            )
        )


@pytest.mark.asyncio
async def test_case_create_and_test_reject_udid_on_non_ios_and_validate_explicit_udid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ApplicationError) as non_ios_create:
        await create_case(
            CaseCreateRequest(
                current_directory=tmp_path,
                platform="android",
                goal="Open Settings",
                udid="SIM-UDID-1",
            ),
            agent_factory=lambda _s: None,  # type: ignore[return-value]
        )
    assert non_ios_create.value.code.value == "case.invalid"

    with pytest.raises(ApplicationError) as non_ios_test:
        run_case_test(
            CaseTestRequest(
                current_directory=tmp_path,
                platform="macos",
                case_path=tmp_path / "dummy.fsq.yaml",
                udid="SIM-UDID-1",
            )
        )
    assert non_ios_test.value.code.value == "case.invalid"

    # Explicit UDID disambiguates multiple active iOS devices
    monkeypatch.setattr(
        "fsq_agent.application._case_test.IOSDeviceDiscovery.discover",
        lambda _self: IOSDeviceDiscoveryResult(
            devices=[
                IOSDevice(udid="REAL-01", name="iPhone", kind="device", state="connected", platform_version="26.2"),
                IOSDevice(udid="SIM-02", name="iPhone 17 Pro", kind="simulator", state="booted", platform_version="26.2"),
            ]
        ),
    )
    from fsq_agent.application._case_test import _resolve_ios_run_udid
    from fsq_agent.config import Settings

    s = Settings(harness={"platform": "ios"})
    assert _resolve_ios_run_udid(s, "SIM-02") == "SIM-02"

    with pytest.raises(ApplicationError) as missing_udid:
        _resolve_ios_run_udid(s, "UNKNOWN-99")
    assert missing_udid.value.code.value == "environment.unavailable"


def test_cli_accepts_ios_for_init_and_case_commands() -> None:
    runner = CliRunner()

    init_help = runner.invoke(main, ["init", "--help"])
    assert init_help.exit_code == 0
    assert "ios" in init_help.output
    assert "--xcode-org-id" in init_help.output
    assert "--updated-wda-bundle-id" in init_help.output

    case_test_help = runner.invoke(main, ["case", "test", "--help"])
    assert case_test_help.exit_code == 0
    assert "--udid" in case_test_help.output
