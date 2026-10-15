# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest
from pydantic import ValidationError

from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCode,
    WorkspaceInitializeRequest,
    WorkspaceRequest,
    WorkspaceResult,
    initialize_workspace,
    require_initialized_workspace,
)
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import PlatformRuntimeCheck, WorkspaceRegistryEntry, WorkspaceStatus


def test_require_initialized_workspace_returns_exact_registered_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.application.workspace.list_workspace_registry", lambda: [type("Entry", (), {"name": "project", "root_path": tmp_path})()])
    monkeypatch.setattr("fsq_agent.application.workspace.inspect_registered_workspace", lambda _name: type("Status", (), {"status": "available"})())

    result = require_initialized_workspace(WorkspaceRequest(current_directory=tmp_path))

    assert result == WorkspaceResult(current_directory=tmp_path.resolve(), workspace=tmp_path.resolve())


def test_require_initialized_workspace_does_not_search_parent_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.application.workspace.list_workspace_registry", lambda: [type("Entry", (), {"name": "project", "root_path": tmp_path})()])
    child = tmp_path / "project"
    child.mkdir()

    with pytest.raises(ApplicationError) as error:
        require_initialized_workspace(WorkspaceRequest(current_directory=child))

    assert error.value.code == ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED
    assert error.value.details["current_directory"] == str(child.resolve())
    assert error.value.details["workspace"] == str(child.resolve())
    assert error.value.action == "Run 'fsq init' here or change to an initialized FSQ Workspace."


def test_require_initialized_workspace_rejects_legacy_marker_only(tmp_path: Path) -> None:
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("legacy", encoding="utf-8")
    with pytest.raises(ApplicationError) as error:
        require_initialized_workspace(WorkspaceRequest(current_directory=tmp_path))

    assert error.value.code.value == "workspace.not_initialized"
    assert error.value.category.value == "workspace_configuration"


def test_workspace_contracts_are_transport_neutral_and_json_serializable(tmp_path: Path) -> None:
    request = WorkspaceRequest(current_directory=tmp_path)
    result = WorkspaceResult(current_directory=tmp_path.resolve(), workspace=tmp_path.resolve())

    assert request.model_dump(mode="json") == {"current_directory": str(tmp_path)}
    assert result.model_dump(mode="json") == {
        "current_directory": str(tmp_path.resolve()),
        "workspace": str(tmp_path.resolve()),
    }


def test_initialize_workspace_resolves_web_browser_before_config_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    browser = tmp_path / "Google Chrome" / "chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    calls: list[object] = []
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"))
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser])
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: calls.append(kwargs) or type("Result", (), {"name": "project", "root_path": tmp_path.resolve()})(),
    )

    result = initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform="web", browser_channel="chrome"))

    assert result.root_path == tmp_path.resolve()
    assert calls[0]["platforms"][0].target.browser_executable_path == browser.resolve()


def test_initialize_workspace_delegates_nonempty_selected_directory_to_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "projects"
    selected.mkdir()
    (selected / "existing.txt").write_text("owned", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"))
    monkeypatch.setattr("fsq_agent.application._workspace_init.list_workspace_registry", lambda root: [])
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: captured.update(kwargs) or WorkspaceStatus(name="custom", root_path=selected / "custom", status="available", message="available"),
    )

    result = initialize_workspace(WorkspaceInitializeRequest(current_directory=selected, platform="android", name="custom", app_id="com.example.app", user_config_root=tmp_path / "user"))

    assert captured["selected_path"] == selected.resolve()
    assert captured["name"] == "custom"
    assert result.root_path == selected / "custom"


def test_initialize_workspace_implicit_collision_uses_suffix_without_inspecting_conflicting_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "project-a" / "fsq_workspace"
    selected.mkdir(parents=True)
    conflicting = tmp_path / "project-b" / "fsq_workspace"
    conflicting.mkdir(parents=True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.list_workspace_registry",
        lambda root: [WorkspaceRegistryEntry(name="FSQ_WORKSPACE", root_path=conflicting)],
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.inspect_registered_workspace",
        lambda *args: pytest.fail("the conflicting workspace must not be inspected"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.load_registered_workspace",
        lambda *args: pytest.fail("the conflicting workspace must not be loaded"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace_platform",
        lambda **kwargs: pytest.fail("the conflicting workspace must not receive a platform"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace_platform_update",
        lambda **kwargs: pytest.fail("the conflicting workspace must not be updated"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: captured.update(kwargs) or WorkspaceStatus(name="fsq_workspace-2", root_path=selected, status="available", message="available"),
    )

    result = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=selected,
            platform="android",
            app_id="com.example.app",
            user_config_root=tmp_path / "user",
        )
    )

    assert captured["selected_path"] == selected.resolve()
    assert captured["name"] == "fsq_workspace-2"
    assert result.name == "fsq_workspace-2"
    assert result.root_path == selected.resolve()


def test_initialize_workspace_implicit_collision_uses_smallest_available_case_insensitive_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "project" / "workspace"
    selected.mkdir(parents=True)
    (selected / "existing.txt").write_text("owned", encoding="utf-8")
    entries = [
        WorkspaceRegistryEntry(name="WORKSPACE", root_path=tmp_path / "other-1"),
        WorkspaceRegistryEntry(name="Workspace-2", root_path=tmp_path / "other-2"),
        WorkspaceRegistryEntry(name="workspace-4", root_path=tmp_path / "other-4"),
    ]
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"),
    )
    monkeypatch.setattr("fsq_agent.application._workspace_init.list_workspace_registry", lambda root: entries)
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: captured.update(kwargs) or WorkspaceStatus(name="workspace-3", root_path=selected / "workspace-3", status="available", message="available"),
    )

    result = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=selected,
            platform="android",
            app_id="com.example.app",
            user_config_root=tmp_path / "user",
        )
    )

    assert captured["selected_path"] == selected.resolve()
    assert captured["name"] == "workspace-3"
    assert result.root_path == selected / "workspace-3"


def test_initialize_workspace_implicit_exact_root_reuses_actual_registered_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "workspace"
    selected.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.list_workspace_registry",
        lambda root: [WorkspaceRegistryEntry(name="Registered-Project", root_path=selected)],
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.inspect_registered_workspace",
        lambda name, root: WorkspaceStatus(name=name, root_path=selected, status="available", message="available"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace_platform",
        lambda **kwargs: captured.update(kwargs) or type("Config", (), {"name": "Registered-Project", "root_path": selected, "platform": "android"})(),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: pytest.fail("an exact registered root must not be recreated"),
    )

    result = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=selected,
            platform="android",
            app_id="com.example.app",
            user_config_root=tmp_path / "user",
        )
    )

    assert captured["name"] == "Registered-Project"
    assert result.name == "Registered-Project"
    assert result.root_path == selected.resolve()


def test_initialize_workspace_uses_registered_root_independently_of_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = tmp_path / "elsewhere"
    selected.mkdir()
    registered = tmp_path / "registered-root"
    registered.mkdir()
    config_path = registered / ".fsq" / "config" / "config.android.yaml"
    captured: dict[str, object] = {}
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"))
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.list_workspace_registry",
        lambda root: [WorkspaceRegistryEntry(name="Custom", root_path=registered)],
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.inspect_registered_workspace",
        lambda name, root: WorkspaceStatus(name="Custom", root_path=registered, status="available", message="available"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace_platform",
        lambda **kwargs: captured.update(kwargs) or type("Config", (), {"name": "Custom", "root_path": registered, "platform": "android"})(),
    )

    result = initialize_workspace(WorkspaceInitializeRequest(current_directory=selected, platform="android", name="custom", app_id="com.example.app", user_config_root=tmp_path / "user"))

    assert not config_path.exists()
    assert captured["name"] == "Custom"
    assert result.status == "platform_added"
    assert result.root_path == registered


def test_initialize_workspace_does_not_mutate_when_driver_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    browser = tmp_path / "Google Chrome"
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser])
    monkeypatch.setattr(PlatformRuntimeService, "web_executable_matches_channel", lambda self, channel, path: True)
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, platform: PlatformRuntimeCheck(platform=platform, status="missing", ready=False, message="missing", action="Reinstall or repair fsq-agent."),
    )
    monkeypatch.setattr("fsq_agent.application._workspace_init.persist_workspace", lambda **kwargs: pytest.fail("config mutation must not run"))

    with pytest.raises(ApplicationError, match="missing"):
        initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform="web", browser_channel="chrome"))


def test_initialize_workspace_request_rejects_install_driver(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkspaceInitializeRequest.model_validate({"current_directory": tmp_path, "platform": "android", "app_id": "example", "install_driver": True})


def test_initialize_workspace_validates_target_before_readiness_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="missing", ready=False, message="missing"))

    with pytest.raises(ApplicationError) as error:
        initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform="android", app_id="example", browser_channel="chrome"))

    assert error.value.code == ApplicationErrorCode.CONFIGURATION_INVALID


def test_initialize_workspace_rejects_unsupported_runtime_without_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "app.exe"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, platform: PlatformRuntimeCheck(platform=platform, status="unsupported", ready=False, message="unsupported"),
    )
    monkeypatch.setattr("fsq_agent.application._workspace_init.persist_workspace", lambda **kwargs: pytest.fail("config mutation must not run"))

    with pytest.raises(ApplicationError, match="unsupported"):
        initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform="windows", app_path=executable))


def test_initialize_workspace_persists_normalized_windows_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "bin" / "app.exe"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"))
    monkeypatch.chdir(tmp_path)

    target = __import__("fsq_agent.application", fromlist=["resolve_workspace_target"]).resolve_workspace_target(
        WorkspaceInitializeRequest(current_directory=tmp_path, platform="windows", app_path=Path("bin/app.exe"))
    )

    assert target.app_path == executable.resolve()


@pytest.mark.parametrize(
    ("platform", "request_values"),
    [
        ("android", {"app_id": "com.example.app"}),
        ("web", {"browser_channel": "chrome"}),
        ("windows", {"app_path": Path("application.exe")}),
        ("macos", {"bundle_id": "com.example.app"}),
    ],
)
def test_initialize_workspace_checks_readiness_before_persistence_for_every_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, request_values: dict[str, object]) -> None:
    browser = tmp_path / "Google Chrome" / "chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    application = tmp_path / "application.exe"
    application.write_text("", encoding="utf-8")
    application.chmod(0o755)
    if platform == "windows":
        request_values = {**request_values, "app_path": application}

    calls: list[str] = []
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser])
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: calls.append(f"check:{selected}") or PlatformRuntimeCheck(platform=selected, status="ready", ready=True, message="ready"),
    )
    monkeypatch.setattr(
        "fsq_agent.application._workspace_init.persist_workspace",
        lambda **kwargs: calls.append("persist") or type("Result", (), {"name": "project", "root_path": tmp_path.resolve()})(),
    )

    result = initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform=platform, **request_values))

    assert calls == [f"check:{platform}", "persist"]
    assert result.platform == platform
    assert result.driver_status == "ready"


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_initialize_workspace_readiness_failure_never_persists_for_any_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    browser = tmp_path / "Google Chrome" / "chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    application = tmp_path / "application.exe"
    application.write_text("", encoding="utf-8")
    application.chmod(0o755)
    request_values = {
        "android": {"app_id": "com.example.app"},
        "web": {"browser_channel": "chrome"},
        "windows": {"app_path": application},
        "macos": {"bundle_id": "com.example.app"},
    }[platform]
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [browser])
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: PlatformRuntimeCheck(platform=selected, status="missing", ready=False, message="runtime missing", action="Reinstall or repair fsq-agent."),
    )
    monkeypatch.setattr("fsq_agent.application._workspace_init.persist_workspace", lambda **kwargs: pytest.fail("workspace must not persist"))

    with pytest.raises(ApplicationError) as error:
        initialize_workspace(WorkspaceInitializeRequest(current_directory=tmp_path, platform=platform, **request_values))

    assert error.value.code == ApplicationErrorCode.ENVIRONMENT_UNAVAILABLE
    assert error.value.details == {"platform": platform}
