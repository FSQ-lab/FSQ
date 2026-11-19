# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import PlatformRuntimeCheck


def _server(tmp_path: Path, *, host: str = "127.0.0.1") -> ControlPlaneServer:
    return ControlPlaneServer(
        ControlPlaneServerOptions(
            host=host,
            static_path=tmp_path / "static",
            user_config_root=tmp_path / "user",
            open_browser=False,
        )
    )


class _FakeDirectoryPicker:
    def __init__(self, result: dict[str, str]) -> None:
        self.result = result
        self.calls = 0
        self.shutdown_called = False

    def choose(self) -> dict[str, str]:
        self.calls += 1
        return self.result

    def shutdown(self) -> None:
        self.shutdown_called = True


def _create(
    server: ControlPlaneServer,
    parent: Path,
    *,
    name: str = "checkout",
    platform: str = "android",
    target: dict[str, object] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    return server.handle_post(
        "/api/control-plane/workspaces",
        {
            "name": name,
            "selectedPath": str(parent),
            "platforms": [
                {
                    "platform": platform,
                    "target": target or {"appId": "com.example.checkout"},
                    "env": env or {"TEST_PASSWORD": "private-value"},
                }
            ],
        },
        peer_host="127.0.0.1",
    )


def test_workspace_create_list_and_detail_keep_env_out_of_registry_projection(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)

    create_status, created = _create(server, parent)
    list_status, listed, headers = server.handle_get("/api/control-plane/workspaces", peer_host="127.0.0.1")
    detail_status, detail, _ = server.handle_get("/api/control-plane/workspaces/checkout", peer_host="127.0.0.1")
    platform_status, platform_detail, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/platforms/android",
        peer_host="127.0.0.1",
    )

    assert create_status == 201
    assert list_status == detail_status == platform_status == 200
    assert headers["Cache-Control"] == "no-store"
    assert listed == {
        "workspaces": [
            {
                "name": "checkout",
                "rootPath": str(parent.resolve()),
                "status": "available",
                "message": "Workspace is available.",
                "platforms": [
                    {
                        "platform": "android",
                        "configPath": str((parent / ".fsq" / "config" / "config.android.yaml").resolve()),
                        "status": "available",
                        "message": "Platform configuration is available.",
                    }
                ],
            }
        ]
    }
    assert "private-value" not in str(listed)
    assert created == detail
    assert detail["platforms"][0]["target"] == {"appId": "com.example.checkout"}
    assert detail["platforms"][0]["env"] == [{"name": "TEST_PASSWORD", "configured": True}]
    assert "private-value" not in str(detail)
    assert platform_detail["env"] == {"TEST_PASSWORD": "private-value"}
    assert str(platform_detail["revision"]).startswith("sha256:")


def test_workspace_parent_directory_picker_returns_selection_and_cancellation(tmp_path: Path) -> None:
    selected = _server(tmp_path / "selected")
    selected_picker = _FakeDirectoryPicker({"status": "selected", "selectedPath": str(tmp_path.resolve()), "isEmpty": True})
    selected._directory_picker = selected_picker  # type: ignore[attr-defined]
    cancelled = _server(tmp_path / "cancelled")
    cancelled_picker = _FakeDirectoryPicker({"status": "cancelled"})
    cancelled._directory_picker = cancelled_picker  # type: ignore[attr-defined]

    selected_status, selected_payload = selected.handle_post(
        "/api/control-plane/workspaces/pick-parent-directory",
        {},
        peer_host="127.0.0.1",
    )
    cancelled_status, cancelled_payload = cancelled.handle_post(
        "/api/control-plane/workspaces/pick-parent-directory",
        {},
        peer_host="127.0.0.1",
    )

    assert selected_status == cancelled_status == 200
    assert selected_payload == {"status": "selected", "selectedPath": str(tmp_path.resolve()), "isEmpty": True}
    assert cancelled_payload == {"status": "cancelled"}
    assert selected_picker.calls == cancelled_picker.calls == 1


def test_workspace_parent_directory_picker_rejects_fields_and_untrusted_access(tmp_path: Path) -> None:
    server = _server(tmp_path)
    picker = _FakeDirectoryPicker({"status": "cancelled"})
    server._directory_picker = picker  # type: ignore[attr-defined]

    field_status, field_error = server.handle_post(
        "/api/control-plane/workspaces/pick-parent-directory",
        {"selectedPath": str(tmp_path)},
        peer_host="127.0.0.1",
    )
    peer_status, peer_error = server.handle_post(
        "/api/control-plane/workspaces/pick-parent-directory",
        {},
        peer_host="192.0.2.5",
    )
    origin_status, origin_error = server.handle_post(
        "/api/control-plane/workspaces/pick-parent-directory",
        {},
        peer_host="127.0.0.1",
        origin="https://evil.example",
        host="127.0.0.1:8879",
    )

    assert field_status == 400
    assert field_error["code"] == "invalid_directory_picker_request"
    assert peer_status == origin_status == 403
    assert peer_error["code"] == "config_unavailable"
    assert origin_error["code"] == "cross_origin_forbidden"
    assert picker.calls == 0


def test_control_plane_stop_shuts_down_directory_picker(tmp_path: Path) -> None:
    server = _server(tmp_path)
    picker = _FakeDirectoryPicker({"status": "cancelled"})
    server._directory_picker = picker  # type: ignore[attr-defined]

    server.stop()

    assert picker.shutdown_called is True


def test_workspace_routes_use_case_insensitive_registry_identity(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)

    detail_status, detail, _ = server.handle_get("/api/control-plane/workspaces/CHECKOUT", peer_host="127.0.0.1")
    entries_status, entries, _ = server.handle_get(
        "/api/control-plane/workspaces/CHECKOUT/entries",
        peer_host="127.0.0.1",
    )

    assert detail_status == entries_status == 200
    assert detail["name"] == "checkout"
    assert [entry["path"] for entry in entries["entries"]] == ["cases", "knowledge"]


def test_devices_cases_require_and_resolve_registered_workspace_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    captured: dict[str, Path] = {}

    def capture_cases(settings):
        captured["workspace"] = settings.workspace.root_dir
        captured["cases"] = settings.cases.dir
        return {"platform": settings.harness.platform, "cases": [], "truncated": False}

    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.discover_cases", capture_cases)

    status, payload, _ = server.handle_get(
        "/api/control-plane/cases",
        {"workspace": ["CHECKOUT"], "platform": ["android"]},
    )
    missing_status, missing, _ = server.handle_get(
        "/api/control-plane/cases",
        {"platform": ["android"]},
    )

    assert status == 200
    assert payload == {"platform": "android", "cases": [], "truncated": False}
    assert captured == {
        "workspace": parent.resolve(),
        "cases": (parent / "cases" / "android").resolve(),
    }
    assert (missing_status, missing["code"]) == (400, "invalid_request")


def test_devices_readiness_keeps_workspace_ready_when_selected_platform_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._readiness.provider_readiness",
        lambda _settings: pytest.fail("provider readiness must not run for an absent platform"),
    )
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._readiness.target_readiness",
        lambda _settings: pytest.fail("target readiness must not run for an absent platform"),
    )

    status, payload, _ = server.handle_get(
        "/api/control-plane/readiness",
        {"workspace": ["CHECKOUT"], "platform": ["web"]},
    )

    assert status == 200
    assert payload["workspaceName"] == "checkout"
    assert payload["platformId"] == "web"
    assert payload["workspace"]["status"] == "ready"
    assert {payload[key]["status"] for key in ("platform", "provider", "target", "strict")} == {"unavailable"}


@pytest.mark.parametrize("platform", ["web", "windows", "macos"])
def test_workspace_create_projects_platform_discriminated_targets(tmp_path: Path, platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    executable = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe" if platform == "web" else tmp_path / "target.exe"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    targets: dict[str, dict[str, object]] = {
        "web": {"browserChannel": "chrome", "browserExecutablePath": str(executable)},
        "windows": {"appPath": str(executable), "windowTitleRe": ".*Checkout", "launchArgs": '--mode "test run"'},
        "macos": {"bundleId": "com.example.Checkout"},
    }
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: PlatformRuntimeCheck(platform=selected, status="ready", ready=True, message="ready"),
    )
    if platform == "web":
        monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [executable.resolve()])

    status, detail = _create(server, parent, platform=platform, target=targets[platform])

    assert status == 201
    assert detail["platforms"][0]["platform"] == platform
    assert detail["platforms"][0]["target"] == targets[platform]


def test_workspace_create_discovers_omitted_web_executable_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    executable = tmp_path / "Google Chrome" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(PlatformRuntimeService, "discover_web_executables", lambda self, channel: [executable.resolve()])
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: PlatformRuntimeCheck(platform=selected, status="ready", ready=True, message="ready"),
    )

    status, detail = _create(
        _server(tmp_path),
        parent,
        platform="web",
        target={"browserChannel": "chrome"},
    )

    assert status == 201
    assert detail["platforms"][0]["target"] == {
        "browserChannel": "chrome",
        "browserExecutablePath": str(executable),
    }


def test_workspace_create_rejects_web_executable_incompatible_with_preset_channel(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    firefox = tmp_path / "firefox.exe"
    firefox.write_text("", encoding="utf-8")
    firefox.chmod(0o755)
    server = _server(tmp_path)

    status, error = _create(
        server,
        parent,
        platform="web",
        target={"browserChannel": "chrome", "browserExecutablePath": str(firefox)},
    )

    assert status == 400
    assert error["code"] == "invalid_workspace"
    assert "selected channel" in error["message"]


def test_workspace_update_uses_revision_and_preserves_stale_draft(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    _, created, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/platforms/android",
        peer_host="127.0.0.1",
    )

    update_status, updated = server.handle_put(
        "/api/control-plane/workspaces/checkout/platforms/android",
        {
            "target": {"appId": "com.example.changed"},
            "env": {"TOKEN": "replacement"},
            "expectedRevision": created["revision"],
        },
        peer_host="127.0.0.1",
    )
    conflict_status, conflict = server.handle_put(
        "/api/control-plane/workspaces/checkout/platforms/android",
        {
            "target": {"appId": "com.example.unsaved"},
            "env": {"TOKEN": "unsaved"},
            "expectedRevision": created["revision"],
        },
        peer_host="127.0.0.1",
    )
    _, loaded, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/platforms/android",
        peer_host="127.0.0.1",
    )

    assert update_status == 200
    assert updated["platform"]["target"] == {"appId": "com.example.changed"}
    assert conflict_status == 409
    assert conflict["code"] == "workspace_conflict"
    assert "unsaved" not in str(conflict)
    assert loaded == updated["platform"]


def test_workspace_adds_platform_without_exposing_env_in_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: PlatformRuntimeCheck(platform=selected, status="ready", ready=True, message="ready"),
    )

    status, payload = server.handle_post(
        "/api/control-plane/workspaces/checkout/platforms",
        {
            "platform": "macos",
            "target": {"bundleId": "com.example.Checkout"},
            "env": {"MAC_TOKEN": "private-mac-token"},
        },
        peer_host="127.0.0.1",
    )

    assert status == 201, payload
    assert [item["platform"] for item in payload["workspace"]["platforms"]] == ["android", "macos"]
    assert "private-mac-token" not in str(payload["workspace"])
    assert payload["platform"]["env"] == {"MAC_TOKEN": "private-mac-token"}
    delete_status, _ = server.handle_delete(
        "/api/control-plane/workspaces/checkout/platforms/macos",
        peer_host="127.0.0.1",
    )
    assert delete_status == 404


def test_workspace_add_platform_maps_missing_registered_root_to_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    server = _server(tmp_path)
    _create(server, root)
    root.rename(tmp_path / "moved-projects")
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check",
        lambda self, selected: PlatformRuntimeCheck(platform=selected, status="ready", ready=True, message="ready"),
    )

    status, error = server.handle_post(
        "/api/control-plane/workspaces/checkout/platforms",
        {
            "platform": "macos",
            "target": {"bundleId": "com.example.Checkout"},
            "env": {},
        },
        peer_host="127.0.0.1",
    )

    assert status == 409, error
    assert error["code"] == "workspace_unavailable"


def test_workspace_list_retains_unavailable_registry_entry_without_env(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    config_path = Path(str(created["platforms"][0]["configPath"]))
    config_path.unlink()

    status, payload, _ = server.handle_get("/api/control-plane/workspaces", peer_host="127.0.0.1")

    assert status == 200
    assert payload["workspaces"][0]["status"] == "unavailable"
    assert payload["workspaces"][0]["name"] == "checkout"
    assert "private-value" not in str(payload)


def test_workspace_detail_maps_missing_registered_config_to_unavailable(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    Path(str(created["platforms"][0]["configPath"])).unlink()

    status, payload, _ = server.handle_get("/api/control-plane/workspaces/checkout", peer_host="127.0.0.1")

    assert status == 200
    assert payload["status"] == "unavailable"
    assert "private-value" not in str(payload)


@pytest.mark.parametrize(("field", "value"), [("target", None), ("target", []), ("env", None), ("env", ["TOKEN"])])
def test_workspace_update_rejects_malformed_target_and_env(tmp_path: Path, field: str, value: object) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    _, created, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/platforms/android",
        peer_host="127.0.0.1",
    )
    payload: dict[str, object] = {
        "target": {"appId": "com.example.changed"},
        "env": {"TOKEN": "replacement"},
        "expectedRevision": created["revision"],
    }
    payload[field] = value

    status, error = server.handle_put(
        "/api/control-plane/workspaces/checkout/platforms/android",
        payload,
        peer_host="127.0.0.1",
    )

    assert status == 400
    assert error["code"] == "invalid_workspace"
    assert "replacement" not in str(error)


def test_workspace_routes_require_loopback_and_same_origin(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    nonloopback_server = _server(tmp_path / "bind", host="0.0.0.0")  # noqa: S104 - verifies rejection of non-loopback binds.
    bind_status, bind_error, _ = nonloopback_server.handle_get("/api/control-plane/workspaces", peer_host="127.0.0.1")
    server = _server(tmp_path / "origin")
    origin_parent = tmp_path / "origin" / "projects"
    origin_parent.mkdir(parents=True)
    origin_status, origin_error = server.handle_post(
        "/api/control-plane/workspaces",
        {
            "name": "checkout",
            "selectedPath": str(origin_parent),
            "platforms": [
                {
                    "platform": "android",
                    "target": {"appId": "com.example.checkout"},
                    "env": {"TOKEN": "secret"},
                }
            ],
        },
        peer_host="127.0.0.1",
        origin="https://evil.example",
        host="127.0.0.1:8879",
    )

    assert bind_status == origin_status == 403
    assert bind_error["code"] == "config_unavailable"
    assert origin_error["code"] == "cross_origin_forbidden"
    assert "secret" not in str(origin_error)


def test_workspace_file_browser_exposes_only_managed_text_content(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    root = Path(str(created["rootPath"]))
    markdown = root / "knowledge" / "android" / "project.md"
    markdown.write_text("# Checkout\n\nProject notes.\n", encoding="utf-8")
    (root / "cases" / "android" / "sample.fsq.yaml").write_text("platform: android\n", encoding="utf-8")

    root_status, root_entries, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        peer_host="127.0.0.1",
    )
    entries_status, entries, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        {"path": ["knowledge/android"]},
        peer_host="127.0.0.1",
    )
    file_status, file_payload, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/file",
        {"path": ["knowledge/android/project.md"]},
        peer_host="127.0.0.1",
    )

    assert root_status == entries_status == file_status == 200
    assert [entry["path"] for entry in root_entries["entries"]] == ["cases", "knowledge"]
    assert entries["entries"][0]["path"] == "knowledge/android/project.md"
    assert file_payload["presentation"] == "markdown"
    assert file_payload["lineCount"] == 3
    assert file_payload["content"] == markdown.read_bytes().decode("utf-8")


def test_workspace_file_browser_keeps_deleted_cases_as_empty_virtual_root(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    cases_dir = Path(str(created["rootPath"])) / "cases"
    (cases_dir / "android").rmdir()
    cases_dir.rmdir()

    root_status, root_payload, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        peer_host="127.0.0.1",
    )
    cases_status, cases_payload, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        {"path": ["cases"]},
        peer_host="127.0.0.1",
    )

    assert root_status == cases_status == 200
    assert [entry["path"] for entry in root_payload["entries"]] == ["cases", "knowledge"]
    assert next(entry for entry in root_payload["entries"] if entry["path"] == "cases")["kind"] == "directory"
    assert cases_payload == {"path": "cases", "entries": [], "truncated": False}


def test_workspace_file_browser_rejects_private_traversal_binary_and_oversized_files(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    root = Path(str(created["rootPath"]))
    binary = root / "cases" / "android" / "binary.dat"
    binary.write_bytes(b"\x00\xff")
    oversized = root / "cases" / "android" / "large.txt"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))

    private_status, private, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/file",
        {"path": [".fsq/config.yaml"]},
        peer_host="127.0.0.1",
    )
    traversal_status, traversal, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/file",
        {"path": ["cases/../.fsq/config.yaml"]},
        peer_host="127.0.0.1",
    )
    binary_status, binary_error, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/file",
        {"path": ["cases/android/binary.dat"]},
        peer_host="127.0.0.1",
    )
    oversized_status, oversized_error, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/file",
        {"path": ["cases/android/large.txt"]},
        peer_host="127.0.0.1",
    )

    assert private_status == traversal_status == 400
    assert private["code"] == traversal["code"] == "invalid_workspace_path"
    assert binary_status == 415
    assert binary_error["code"] == "workspace_file_not_text"
    assert oversized_status == 413
    assert oversized_error["code"] == "workspace_file_too_large"


def test_workspace_file_browser_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    root = Path(str(created["rootPath"]))
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "cases" / "android" / "outside.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    status, error, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        {"path": ["cases/android"]},
        peer_host="127.0.0.1",
    )

    assert status == 400
    assert error["code"] == "invalid_workspace_path"
    assert "private" not in str(error)


def test_workspace_file_browser_rejects_symlinked_managed_root_when_supported(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _, created = _create(server, parent)
    root = Path(str(created["rootPath"]))
    cases_dir = root / "cases"
    (cases_dir / "android").rmdir()
    cases_dir.rmdir()
    outside = tmp_path / "outside-cases"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    try:
        cases_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    status, error, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/entries",
        peer_host="127.0.0.1",
    )

    assert status == 400
    assert error["code"] == "invalid_workspace_path"
    assert "private" not in str(error)


def test_control_plane_omits_cli_only_ios_platforms_and_runs_from_workspace_and_history_surfaces(
    tmp_path: Path,
) -> None:
    from fsq_agent.config import IOSWorkspaceTarget, add_workspace_platform

    parent = tmp_path / "projects"
    parent.mkdir()
    server = _server(tmp_path)
    _create(server, parent)
    add_workspace_platform(
        name="checkout",
        platform="ios",
        target=IOSWorkspaceTarget(bundle_id="com.apple.Preferences"),
        env={"IOS_SECRET": "do-not-expose"},
        user_config_root=tmp_path / "user",
    )
    ios_run_dir = parent / ".fsq" / "runs" / "ios" / "20261119T010000Z-iosrun"
    ios_run_dir.mkdir(parents=True)
    (ios_run_dir / "run.json").write_text(
        '{"schema_version": "fsq.run/v2", "run_id": "20261119T010000Z-iosrun", "platform": "ios", "mode": "strict", "status": "succeeded"}',
        encoding="utf-8",
    )

    list_status, listed, _ = server.handle_get("/api/control-plane/workspaces", peer_host="127.0.0.1")
    detail_status, detail, _ = server.handle_get("/api/control-plane/workspaces/checkout", peer_host="127.0.0.1")
    ios_detail_status, ios_detail_err, _ = server.handle_get(
        "/api/control-plane/workspaces/checkout/platforms/ios",
        peer_host="127.0.0.1",
    )
    ios_add_status, ios_add_err = server.handle_post(
        "/api/control-plane/workspaces/checkout/platforms",
        {"platform": "ios", "target": {"bundleId": "com.apple.Preferences"}, "env": {}},
        peer_host="127.0.0.1",
    )
    history_status, history, _ = server.handle_get(
        "/api/control-plane/history",
        {"workspace": ["checkout"]},
        peer_host="127.0.0.1",
    )
    ios_history_status, ios_history_err, _ = server.handle_get(
        "/api/control-plane/history",
        {"workspace": ["checkout"], "platform": ["ios"]},
        peer_host="127.0.0.1",
    )

    assert list_status == detail_status == history_status == 200
    assert [p["platform"] for p in listed["workspaces"][0]["platforms"]] == ["android"]
    assert [p["platform"] for p in detail["platforms"]] == ["android"]
    assert "do-not-expose" not in str(listed)
    assert "do-not-expose" not in str(detail)
    assert ios_detail_status == 400
    assert ios_detail_err["code"] == "invalid_workspace"
    assert ios_add_status == 400
    assert ios_add_err["code"] == "invalid_workspace"
    assert history["platforms"] == ["android"]
    assert all(entry["platform"] != "ios" for entry in history["runs"])
    assert ios_history_status == 400
    assert ios_history_err["code"] == "invalid_request"
