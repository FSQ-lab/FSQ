# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.adapters.cli._windows_sandbox import (
    SandboxCommandSpec,
    SandboxExecutionOutcome,
    _rebase_guest_paths,
    execute_in_windows_sandbox,
)
from fsq_agent.application import ApplicationError
from fsq_agent.config import (
    WindowsWorkspaceTarget,
    WorkspacePlatformCreateInput,
    create_workspace_at_root,
    list_workspace_registry,
    load_user_provider_config,
    save_openai_provider,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.mark.parametrize(
    ("command_args", "expected_operation"),
    [
        (["case", "create", "--platform", "web", "--goal", "Verify heading", "--sandbox"], "case.create"),
        (["case", "create", "--platform", "ios", "--goal", "Verify heading", "--sandbox"], "case.create"),
        (["case", "test", "sample.fsq.yaml", "--platform", "android", "--sandbox"], "case.test"),
        (["quick-test", "run", "--platform", "macos", "--goal", "Verify Calculator", "--sandbox"], "quick-test.run"),
        (["quick-test", "run", "--platform", "web", "--target-id", "chrome", "--goal", "Verify heading", "--sandbox"], "quick-test.run"),
    ],
)
def test_sandbox_flag_rejects_non_windows_platforms_with_exit_2(monkeypatch: pytest.MonkeyPatch, command_args: list[str], expected_operation: str) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.execute_in_windows_sandbox", lambda *a, **k: pytest.fail("Non-windows sandbox must fail static validation"))
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", *command_args])
    assert result.exit_code == 2, result.output
    record = json.loads(result.output)
    assert record["type"] == "error"
    assert record["operation"] == expected_operation
    assert record["error"]["category"] == "request_validation"
    assert "--platform windows" in record["error"]["message"]


def test_sandbox_flag_rejects_ios_udid_on_windows_case_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.execute_in_windows_sandbox", lambda *a, **k: pytest.fail("Should reject --udid before sandbox start"))
    for subcmd in (
        ["case", "create", "--platform", "windows", "--goal", "Test", "--udid", "sim-1", "--sandbox"],
        ["case", "test", "a.fsq.yaml", "--platform", "windows", "--udid", "sim-1", "--sandbox"],
    ):
        result = CliRunner().invoke(main, ["--output", "json", *subcmd])
        assert result.exit_code == 2
        record = json.loads(result.output)
        assert record["type"] == "error"
        assert "--udid" in record["error"]["message"]


def test_cli_delegates_windows_commands_to_sandbox_and_emits_single_terminal_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen_specs: list[SandboxCommandSpec] = []

    def fake_execute(spec: SandboxCommandSpec, *, event_callback=None, timeout_seconds: float = 1800.0) -> SandboxExecutionOutcome:
        seen_specs.append(spec)
        if event_callback is not None:
            event_callback({"phase": "running", "message": "Executing inside Sandbox"})
        report_path = str(tmp_path / ".fsq" / "runs" / "windows" / "run-1" / "report.html")
        if spec.kind == "case_test":
            payload = {
                "type": "result",
                "status": "success",
                "result": {
                    "status": "success",
                    "summary": "Notepad verified.",
                    "report_path": report_path,
                    "suggestion_path": None,
                    "candidate_case_path": None,
                    "warnings": [],
                },
            }
        else:
            payload = {
                "type": "result",
                "status": "success",
                "result": {
                    "status": "success",
                    "run_id": "run-1",
                    "report_path": report_path,
                    "warnings": [],
                },
            }
        return SandboxExecutionOutcome(exit_code=0, terminal_record=payload, stderr_text="")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.execute_in_windows_sandbox", fake_execute)

    qt_res = CliRunner().invoke(
        main,
        ["--output", "jsonl", "--non-interactive", "quick-test", "run", "--platform", "windows", "--goal", "Open Notepad", "--sandbox"],
    )
    assert qt_res.exit_code == 0, qt_res.output
    qt_records = [json.loads(line) for line in qt_res.output.splitlines()]
    assert [r["type"] for r in qt_records] == ["event", "result"]
    assert {r["operation"] for r in qt_records} == {"quick-test.run"}
    assert seen_specs[-1].kind == "quick_test_run"
    assert seen_specs[-1].goal == "Open Notepad"

    cc_res = CliRunner().invoke(
        main,
        ["--output", "jsonl", "case", "create", "--platform", "windows", "--goal", "Verify Notepad", "--name", "notepad-smoke", "--sandbox"],
    )
    assert cc_res.exit_code == 0, cc_res.output
    cc_records = [json.loads(line) for line in cc_res.output.splitlines()]
    assert [r["type"] for r in cc_records] == ["event", "result"]
    assert {r["operation"] for r in cc_records} == {"case.create"}
    assert seen_specs[-1].kind == "case_create"
    assert seen_specs[-1].case_name == "notepad-smoke"

    ct_res = CliRunner().invoke(
        main,
        ["--output", "human", "case", "test", "notepad-smoke.fsq.yaml", "--platform", "windows", "--suggest", "--sandbox"],
    )
    assert ct_res.exit_code == 0, ct_res.output
    assert "Case success: Notepad verified." in ct_res.output
    assert "Source Case was not modified." in ct_res.output
    assert seen_specs[-1].kind == "case_test"
    assert seen_specs[-1].suggest is True


def test_rebase_guest_paths_rewrites_workspace_and_quick_test_prefixes(tmp_path: Path) -> None:
    host_ws = tmp_path / "my-workspace"
    payload = {
        "type": "result",
        "result": {
            "root_path": r"C:\fsq_workspace",
            "report_path": r"C:\fsq_workspace\.fsq\runs\windows\20261125\report.html",
            "slash_path": "C:/fsq_workspace/cases/windows/notepad.fsq.yaml",
            "untouched": r"C:\Windows\System32\notepad.exe",
        },
    }
    rebased = _rebase_guest_paths(payload, guest_mount_root=r"C:\fsq_workspace", host_mount_root=host_ws)
    assert isinstance(rebased, dict)
    res = rebased["result"]
    assert res["root_path"] == str(host_ws)
    assert res["report_path"] == str(host_ws) + r"\.fsq\runs\windows\20261125\report.html"
    assert res["slash_path"] == str(host_ws).replace("\\", "/") + "/cases/windows/notepad.fsq.yaml"
    assert res["untouched"] == r"C:\Windows\System32\notepad.exe"


def test_execute_in_windows_sandbox_rejects_non_win32_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.sys.platform", "darwin")
    with pytest.raises(ApplicationError) as exc_info:
        execute_in_windows_sandbox(
            SandboxCommandSpec(
                kind="quick_test_run",
                current_directory=tmp_path,
                output_format="json",
                non_interactive=True,
                goal="Open Notepad",
            )
        )
    assert exc_info.value.category.value == "unavailable"


def _create_fake_python_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    fake_windir = tmp_path / "EmptyWindows"
    (fake_windir / "System32").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SYSTEMROOT", str(fake_windir))
    monkeypatch.setenv("WINDIR", str(fake_windir))

    fake_py_base = tmp_path / "pybase"
    fake_py_base.mkdir(exist_ok=True)
    (fake_py_base / "python.exe").write_text("stub", encoding="utf-8")
    for dll_name in ("mfc140u.dll", "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (fake_py_base / dll_name).write_bytes(b"stub-dll")
    fake_site = tmp_path / "site-packages"
    fake_site.mkdir(exist_ok=True)
    return fake_py_base, fake_site


def test_execute_in_windows_sandbox_orchestrates_mounts_rewrites_and_restores_workspace_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user_root = tmp_path / "host_user_fsq"
    save_openai_provider(api_key="sk-real-secret-test-key", model="gpt-4.1", user_config_root=user_root)

    fake_app = tmp_path / "notepad.exe"
    fake_app.write_text("stub", encoding="utf-8")

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    create_workspace_at_root(
        name="demo-win",
        root_path=ws_root,
        platforms=[
            WorkspacePlatformCreateInput(
                platform="windows",
                target=WindowsWorkspaceTarget(app_path=str(fake_app), window_title_re=".*Notepad.*"),
            )
        ],
        user_config_root=user_root,
    )
    case_file = ws_root / "cases" / "windows" / "smoke.fsq.yaml"
    case_file.parent.mkdir(parents=True, exist_ok=True)
    case_file.write_text("name: smoke\nplatform: windows\nsteps: []\n", encoding="utf-8")

    win_cfg_path = ws_root / ".fsq" / "config" / "config.windows.yaml"
    original_cfg_bytes = win_cfg_path.read_bytes()

    fake_wsb = tmp_path / "wsb.exe"
    fake_wsb.write_text("stub", encoding="utf-8")
    fake_py_base, fake_site = _create_fake_python_env(tmp_path, monkeypatch)
    wsb_calls: list[list[str]] = []
    observed_cfg_root_during_share: list[str] = []
    observed_snapshot_provider: list[object] = []

    def fake_run_wsb(wsb_executable: Path, args: Sequence[str], *, timeout: float = 60.0, check: bool = True) -> subprocess.CompletedProcess[str]:
        wsb_calls.append(list(args))
        verb = args[0]
        if verb == "list":
            return subprocess.CompletedProcess(args, 0, stdout='{"WindowsSandboxEnvironments": []}', stderr="")
        if verb == "start":
            return subprocess.CompletedProcess(args, 0, stdout='{"Id": "sandbox-guid-123"}', stderr="")
        if verb == "share":
            if "-s" in args and args[args.index("-s") + 1] == r"C:\Users\WDAGUtilityAccount\fsq-workspace":
                loaded = yaml.safe_load(win_cfg_path.read_text(encoding="utf-8"))
                observed_cfg_root_during_share.append(loaded["root_path"])
            if "-s" in args and args[args.index("-s") + 1] == r"C:\fsq_user_cfg":
                snap_dir = Path(args[args.index("-f") + 1])
                snap_doc = yaml.safe_load((snap_dir / "config.yaml").read_text(encoding="utf-8"))
                observed_snapshot_provider.append(snap_doc)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class DummyProc:
        def __init__(self, args: Sequence[str]) -> None:
            self.args = list(args)
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode or 0

    def fake_popen(args: Sequence[str], **kwargs):
        if "exec" in args and "ExistingLogin" in args:
            # Locate staging dir from recorded share call
            share_call = next(c for c in wsb_calls if c[0] == "share" and c[c.index("-s") + 1] == r"C:\fsq_sandbox")
            staging = Path(share_call[share_call.index("-f") + 1])
            lines = [
                json.dumps({"type": "event", "event": {"phase": "running", "path": r"C:\Users\WDAGUtilityAccount\fsq-workspace\cases\windows\smoke.fsq.yaml"}}),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "result": {
                            "status": "success",
                            "summary": "Passed",
                            "report_path": r"C:\Users\WDAGUtilityAccount\fsq-workspace\.fsq\runs\windows\r1\report.html",
                        },
                    }
                ),
            ]
            (staging / "guest_stdout.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            (staging / "exit_code.txt").write_text("0", encoding="utf-8")
            (staging / "done.marker").write_text("done\n", encoding="utf-8")
        return DummyProc(args)

    stopped_ids: list[str] = []
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.sys.platform", "win32")
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._resolve_wsb_executable", lambda: fake_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._discover_python_mounts", lambda: (fake_py_base, fake_site, tmp_path / "pkg"))
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._run_wsb_command", fake_run_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._find_sandbox_client_hwnd", lambda: 12345)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._send_window_to_bottom_restore_foreground", lambda hwnd, fg: None)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._stop_sandbox_instance", lambda wsb, sid: stopped_ids.append(sid))

    events: list[dict[str, object]] = []
    outcome = execute_in_windows_sandbox(
        SandboxCommandSpec(
            kind="case_test",
            current_directory=ws_root,
            output_format="jsonl",
            non_interactive=True,
            case_path=Path("smoke.fsq.yaml"),
            user_config_root=user_root,
        ),
        event_callback=events.append,
    )

    assert outcome.exit_code == 0
    assert observed_cfg_root_during_share == [r"C:\Users\WDAGUtilityAccount\fsq-workspace"]
    assert win_cfg_path.read_bytes() == original_cfg_bytes
    assert stopped_ids == ["sandbox-guid-123"]
    assert len(events) == 1
    assert events[0]["path"] == str(ws_root) + r"\cases\windows\smoke.fsq.yaml"
    assert outcome.terminal_record is not None
    assert outcome.terminal_record["result"]["report_path"] == str(ws_root) + r"\.fsq\runs\windows\r1\report.html"
    assert observed_snapshot_provider[0]["workspaces"] == [{"name": "demo-win", "root_path": r"C:\Users\WDAGUtilityAccount\fsq-workspace"}]
    # Host user config remains untouched
    assert [e.name for e in list_workspace_registry(user_root)] == ["demo-win"]
    assert load_user_provider_config(user_root).provider.model == "gpt-4.1"


def test_quick_test_sandbox_registers_quick_test_workspace_on_host_and_rewrites_root_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user_root = tmp_path / "host_user_fsq"
    save_openai_provider(api_key="sk-real-secret-test-key", model="gpt-4.1", user_config_root=user_root)

    fake_wsb = tmp_path / "wsb.exe"
    fake_wsb.write_text("stub", encoding="utf-8")
    fake_py_base, fake_site = _create_fake_python_env(tmp_path, monkeypatch)
    wsb_calls: list[list[str]] = []

    def fake_run_wsb(wsb_executable: Path, args: Sequence[str], *, timeout: float = 60.0, check: bool = True) -> subprocess.CompletedProcess[str]:
        wsb_calls.append(list(args))
        if args[0] == "list":
            return subprocess.CompletedProcess(args, 0, stdout='{"WindowsSandboxEnvironments": []}', stderr="")
        if args[0] == "start":
            return subprocess.CompletedProcess(args, 0, stdout='{"Id": "sandbox-qt-1"}', stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class DummyProc:
        def poll(self) -> int | None:
            return 0

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(args: Sequence[str], **kwargs):
        if "exec" in args and "ExistingLogin" in args:
            share_call = next(c for c in wsb_calls if c[0] == "share" and c[c.index("-s") + 1] == r"C:\fsq_sandbox")
            staging = Path(share_call[share_call.index("-f") + 1])
            qt_host_root = user_root / "quick-test"
            win_cfg = qt_host_root / ".fsq" / "config" / "config.windows.yaml"
            win_cfg.parent.mkdir(parents=True, exist_ok=True)
            win_cfg.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "platform": "windows",
                        "root_path": r"C:\Users\WDAGUtilityAccount\.fsq\quick-test",
                        "target": {"app_path": r"C:\Windows\System32\notepad.exe", "window_title_re": ".*Notepad.*"},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (staging / "guest_stdout.jsonl").write_text(
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "result": {
                            "status": "success",
                            "root_path": r"C:\Users\WDAGUtilityAccount\.fsq\quick-test",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (staging / "exit_code.txt").write_text("0", encoding="utf-8")
            (staging / "done.marker").write_text("done\n", encoding="utf-8")
        return DummyProc()

    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.sys.platform", "win32")
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._resolve_wsb_executable", lambda: fake_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._discover_python_mounts", lambda: (fake_py_base, fake_site, None))
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._run_wsb_command", fake_run_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._find_sandbox_client_hwnd", lambda: 999)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._send_window_to_bottom_restore_foreground", lambda hwnd, fg: None)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._stop_sandbox_instance", lambda wsb, sid: None)

    outcome = execute_in_windows_sandbox(
        SandboxCommandSpec(
            kind="quick_test_run",
            current_directory=tmp_path,
            output_format="json",
            non_interactive=True,
            goal="Open Notepad",
            user_config_root=user_root,
        )
    )

    assert outcome.exit_code == 0
    qt_host_root = (user_root / "quick-test").resolve()
    assert outcome.terminal_record["result"]["root_path"] == str(qt_host_root)
    saved_win_cfg = yaml.safe_load((qt_host_root / ".fsq" / "config" / "config.windows.yaml").read_text(encoding="utf-8"))
    assert saved_win_cfg["root_path"] == str(qt_host_root)
    entries = list_workspace_registry(user_root)
    assert [(e.name, e.root_path.resolve()) for e in entries] == [("quick-test", qt_host_root)]


def test_ensure_no_running_sandboxes_rejects_non_json_and_active_instances(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _ensure_no_running_sandboxes

    fake_wsb = tmp_path / "wsb.exe"
    fake_wsb.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(
        "fsq_agent.adapters.cli._windows_sandbox._run_wsb_command",
        lambda *a, **k: subprocess.CompletedProcess(a[1], 0, stdout="not-json-output", stderr=""),
    )
    with pytest.raises(ApplicationError) as bad_json_exc:
        _ensure_no_running_sandboxes(fake_wsb)
    assert bad_json_exc.value.category.value == "unavailable"
    assert bad_json_exc.value.details.get("stage") == "list"

    monkeypatch.setattr(
        "fsq_agent.adapters.cli._windows_sandbox._run_wsb_command",
        lambda *a, **k: subprocess.CompletedProcess(a[1], 0, stdout='{"WindowsSandboxEnvironments": [{"Id": "abc"}]}', stderr=""),
    )
    with pytest.raises(ApplicationError) as running_exc:
        _ensure_no_running_sandboxes(fake_wsb)
    assert running_exc.value.category.value == "unavailable"
    assert running_exc.value.details.get("running_count") == 1


def test_rewrite_workspace_config_raises_application_error_on_malformed_yaml(tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _rewrite_workspace_config_root_path

    cfg = tmp_path / "config.windows.yaml"
    cfg.write_text("version: [unclosed\n", encoding="utf-8")
    with pytest.raises(ApplicationError) as exc_info:
        _rewrite_workspace_config_root_path(cfg, r"C:\Users\WDAGUtilityAccount\fsq-workspace")
    assert exc_info.value.code.value == "configuration.invalid"
    assert exc_info.value.category.value == "workspace_configuration"

    cfg.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ApplicationError) as non_dict_exc:
        _rewrite_workspace_config_root_path(cfg, r"C:\Users\WDAGUtilityAccount\fsq-workspace")
    assert non_dict_exc.value.code.value == "configuration.invalid"
    assert non_dict_exc.value.category.value == "workspace_configuration"


def test_restore_host_workspace_config_raises_application_error_on_os_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _restore_host_workspace_config

    cfg = tmp_path / "config.windows.yaml"
    cfg.write_text("root_path: guest\n", encoding="utf-8")

    def boom(*_args, **_kwargs) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._atomic_write_bytes", boom)
    with pytest.raises(ApplicationError) as exc_info:
        _restore_host_workspace_config(win_cfg_path=cfg, original_bytes=b"root_path: host\n", host_mount_root=tmp_path)
    assert exc_info.value.category.value == "internal"
    assert exc_info.value.details.get("config_path") == str(cfg)


def test_prepare_guest_user_config_snapshot_preserves_malformed_host_config(tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _prepare_guest_user_config_snapshot

    host_user_root = tmp_path / "host_fsq"
    host_user_root.mkdir()
    bad_yaml = "provider: [unclosed\n"
    (host_user_root / "config.yaml").write_text(bad_yaml, encoding="utf-8")

    snapshot_dir = tmp_path / "snap"
    with pytest.raises(ApplicationError) as exc_info:
        _prepare_guest_user_config_snapshot(
            host_user_root=host_user_root,
            snapshot_dir=snapshot_dir,
            workspace_name="demo",
            guest_mount_root=r"C:\Users\WDAGUtilityAccount\fsq-workspace",
            include_workspace_entry=True,
        )
    assert exc_info.value.code.value == "configuration.invalid"
    assert exc_info.value.category.value == "configuration"


def test_external_case_copies_single_file_and_rebases_guest_case_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user_root = tmp_path / "host_user_fsq"
    save_openai_provider(api_key="sk-real-secret-test-key", model="gpt-4.1", user_config_root=user_root)

    fake_app = tmp_path / "notepad.exe"
    fake_app.write_text("stub", encoding="utf-8")

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    create_workspace_at_root(
        name="demo-win",
        root_path=ws_root,
        platforms=[
            WorkspacePlatformCreateInput(
                platform="windows",
                target=WindowsWorkspaceTarget(app_path=str(fake_app), window_title_re=".*Notepad.*"),
            )
        ],
        user_config_root=user_root,
    )

    external_dir = tmp_path / "external_dir"
    external_dir.mkdir()
    external_case = external_dir / "ext.fsq.yaml"
    external_case.write_text("name: ext\nplatform: windows\nsteps: []\n", encoding="utf-8")
    unrelated_secret = external_dir / "secret.txt"
    unrelated_secret.write_text("do-not-mount-parent", encoding="utf-8")

    fake_wsb = tmp_path / "wsb.exe"
    fake_wsb.write_text("stub", encoding="utf-8")
    fake_py_base, fake_site = _create_fake_python_env(tmp_path, monkeypatch)
    wsb_calls: list[list[str]] = []
    shared_host_paths: list[str] = []

    def fake_run_wsb(wsb_executable: Path, args: Sequence[str], *, timeout: float = 60.0, check: bool = True) -> subprocess.CompletedProcess[str]:
        wsb_calls.append(list(args))
        if args[0] == "list":
            return subprocess.CompletedProcess(args, 0, stdout='{"WindowsSandboxEnvironments": []}', stderr="")
        if args[0] == "start":
            return subprocess.CompletedProcess(args, 0, stdout='{"Id": "sandbox-ext-1"}', stderr="")
        if args[0] == "share":
            shared_host_paths.append(args[args.index("-f") + 1])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class DummyProc:
        def __init__(self) -> None:
            self.waited = False

        def poll(self) -> int | None:
            return 0

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            self.waited = True
            return 0

    def fake_popen(args: Sequence[str], **kwargs):
        if "exec" in args and "ExistingLogin" in args:
            share_call = next(c for c in wsb_calls if c[0] == "share" and c[c.index("-s") + 1] == r"C:\fsq_sandbox")
            staging = Path(share_call[share_call.index("-f") + 1])
            assert (staging / "case_src" / "ext.fsq.yaml").is_file()
            assert not (staging / "case_src" / "secret.txt").exists()
            (staging / "guest_stdout.jsonl").write_text(
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "result": {
                            "status": "success",
                            "case_path": r"C:\fsq_sandbox\case_src\ext.fsq.yaml",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (staging / "exit_code.txt").write_text("0", encoding="utf-8")
            (staging / "done.marker").write_text("done\n", encoding="utf-8")
        return DummyProc()

    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.sys.platform", "win32")
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._resolve_wsb_executable", lambda: fake_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._discover_python_mounts", lambda: (fake_py_base, fake_site, None))
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._run_wsb_command", fake_run_wsb)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._find_sandbox_client_hwnd", lambda: 123)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._send_window_to_bottom_restore_foreground", lambda hwnd, fg: None)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr("fsq_agent.adapters.cli._windows_sandbox._stop_sandbox_instance", lambda wsb, sid: None)

    outcome = execute_in_windows_sandbox(
        SandboxCommandSpec(
            kind="case_test",
            current_directory=ws_root,
            output_format="json",
            non_interactive=True,
            case_path=external_case,
            user_config_root=user_root,
        )
    )

    assert str(external_dir.resolve()) not in shared_host_paths
    assert outcome.terminal_record is not None
    assert outcome.terminal_record["result"]["case_path"] == str(external_case.resolve())


def test_stream_and_collect_incremental_offset_and_bootstrap_diagnostics(tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _stream_and_collect_guest_result

    staging = tmp_path / "staging"
    staging.mkdir()
    stdout_path = staging / "guest_stdout.jsonl"
    done_marker = staging / "done.marker"
    exit_code_path = staging / "exit_code.txt"

    # Test partial line followed by completion across incremental polls
    class SteppingProc:
        def __init__(self) -> None:
            self.polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            if self.polls == 1:
                stdout_path.write_text('{"type": "event", "event": {"phase": "p1"}}\n{"type": "res', encoding="utf-8")
            elif self.polls == 2:
                with stdout_path.open("a", encoding="utf-8") as handle:
                    handle.write('ult", "status": "success", "result": {"status": "success"}}\n')
                exit_code_path.write_text("0", encoding="utf-8")
                done_marker.write_text("done\n", encoding="utf-8")
                return 0
            return None

    seen_events: list[dict[str, object]] = []
    code, record, _stderr = _stream_and_collect_guest_result(
        staging_dir=staging,
        exec_proc=SteppingProc(),  # type: ignore[arg-type]
        host_mount_root=tmp_path,
        guest_mount_root=r"C:\Users\WDAGUtilityAccount\fsq-workspace",
        event_callback=seen_events.append,
        timeout_seconds=10.0,
    )
    assert code == 0
    assert seen_events == [{"phase": "p1"}]
    assert record is not None
    assert record["type"] == "result"

    # Test abnormal termination emits safe exception type & presence flags without raw exception text/stderr
    staging_err = tmp_path / "staging_err"
    staging_err.mkdir()
    (staging_err / "bootstrap_error.txt").write_text("RuntimeError: secret-token-should-not-leak\n", encoding="utf-8")
    (staging_err / "guest_stderr.log").write_text("Traceback: raw stderr\n", encoding="utf-8")

    class DeadProc:
        def poll(self) -> int | None:
            return 1

    with pytest.raises(ApplicationError) as exc_info:
        _stream_and_collect_guest_result(
            staging_dir=staging_err,
            exec_proc=DeadProc(),  # type: ignore[arg-type]
            host_mount_root=tmp_path,
            guest_mount_root=r"C:\Users\WDAGUtilityAccount\fsq-workspace",
            event_callback=None,
            timeout_seconds=10.0,
        )
    assert exc_info.value.details.get("bootstrap_exception_type") == "RuntimeError"
    assert exc_info.value.details.get("has_guest_stderr") is True
    assert "secret-token-should-not-leak" not in json.dumps(exc_info.value.details)
    assert "raw stderr" not in json.dumps(exc_info.value.details)


def test_emit_sandbox_outcome_preserves_guest_error_exit_code_and_rejects_malformed_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.execute_in_windows_sandbox",
        lambda *a, **k: SandboxExecutionOutcome(
            exit_code=130,
            terminal_record={
                "type": "error",
                "error": {
                    "code": "internal.error",
                    "category": "internal",
                    "message": "Interrupted in guest.",
                },
            },
            stderr_text="guest trace",
        ),
    )
    res = CliRunner().invoke(
        main,
        ["--output", "json", "quick-test", "run", "--platform", "windows", "--goal", "Open Notepad", "--sandbox"],
    )
    assert res.exit_code == 130, res.output
    payload = json.loads(res.output)
    assert payload["type"] == "error"
    assert payload["error"]["message"] == "Interrupted in guest."

    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.execute_in_windows_sandbox",
        lambda *a, **k: SandboxExecutionOutcome(
            exit_code=0,
            terminal_record={"type": "unknown", "result": None},
            stderr_text="",
        ),
    )
    bad_res = CliRunner().invoke(
        main,
        ["--output", "json", "quick-test", "run", "--platform", "windows", "--goal", "Open Notepad", "--sandbox"],
    )
    assert bad_res.exit_code == 5, bad_res.output
    bad_payload = json.loads(bad_res.output)
    assert bad_payload["type"] == "error"


def test_sitecustomize_source_guards_comtypes_and_uses_shared_mount_constants() -> None:
    from fsq_agent.adapters.cli._windows_sandbox import (
        _GUEST_OUT_DIR,
        _GUEST_QUICK_TEST_ROOT,
        _GUEST_SITE_PACKAGES,
        _GUEST_WORKSPACE_ROOT,
        _render_guest_bootstrap_source,
        _render_sitecustomize_source,
    )

    source = _render_sitecustomize_source()
    assert repr(_GUEST_WORKSPACE_ROOT) in source
    assert repr(_GUEST_QUICK_TEST_ROOT) in source
    assert repr(_GUEST_SITE_PACKAGES) in source
    assert "site.addsitedir(" in source
    assert "os.add_dll_directory(" in source
    assert "try:\n    import comtypes.client" in source
    compile(source, "sitecustomize.py", "exec")

    bootstrap = _render_guest_bootstrap_source()
    assert repr(_GUEST_OUT_DIR) in bootstrap
    compile(bootstrap, "guest_bootstrap.py", "exec")


def test_stage_runtime_dlls_uses_fallback_dirs_and_includes_pywin32_and_optional_dlls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _stage_sandbox_files

    fake_windir = tmp_path / "Windows"
    sys32 = fake_windir / "System32"
    sys32.mkdir(parents=True)
    (sys32 / "msvcp140.dll").write_bytes(b"msvcp")
    (sys32 / "concrt140.dll").write_bytes(b"concrt")

    py_base = tmp_path / "pybase"
    py_base.mkdir()
    (py_base / "vcruntime140.dll").write_bytes(b"vcr")
    (py_base / "vcruntime140_1.dll").write_bytes(b"vcr1")

    site_pkg = tmp_path / "site-packages"
    (site_pkg / "Pythonwin").mkdir(parents=True)
    (site_pkg / "Pythonwin" / "mfc140u.dll").write_bytes(b"mfc")
    (site_pkg / "pywin32_system32").mkdir(parents=True)
    (site_pkg / "pywin32_system32" / "pythoncom312.dll").write_bytes(b"pycom")
    (site_pkg / "pywin32_system32" / "pywintypes312.dll").write_bytes(b"pywin")

    monkeypatch.setenv("SYSTEMROOT", str(fake_windir))
    staging = tmp_path / "staging"
    staging.mkdir()

    _stage_sandbox_files(
        staging_dir=staging,
        guest_cwd=r"C:\fsq_sandbox",
        guest_cli_args=["--output", "jsonl"],
        include_package_root=False,
        python_base=py_base,
        site_packages=site_pkg,
    )

    staged_names = {p.name.lower() for p in (staging / "vc_dlls").glob("*.dll")}
    assert {"mfc140u.dll", "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "pythoncom312.dll", "pywintypes312.dll"} <= staged_names
    setup_cmd = (staging / "_setup.cmd").read_text(encoding="ascii")
    assert "if exist" in setup_cmd.lower()


def test_stage_runtime_dlls_raises_unavailable_when_required_dll_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fsq_agent.adapters.cli._windows_sandbox import _stage_sandbox_files

    fake_windir = tmp_path / "EmptyWindows"
    (fake_windir / "System32").mkdir(parents=True)
    monkeypatch.setenv("SYSTEMROOT", str(fake_windir))
    monkeypatch.setenv("WINDIR", str(fake_windir))

    py_base = tmp_path / "pybase"
    py_base.mkdir()
    site_pkg = tmp_path / "site-packages"
    site_pkg.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(ApplicationError) as exc_info:
        _stage_sandbox_files(
            staging_dir=staging,
            guest_cwd=r"C:\fsq_sandbox",
            guest_cli_args=["--output", "jsonl"],
            include_package_root=False,
            python_base=py_base,
            site_packages=site_pkg,
        )
    assert exc_info.value.category.value == "unavailable"
    assert exc_info.value.details.get("stage") == "runtime_dlls"
    missing = exc_info.value.details.get("missing_dlls")
    assert isinstance(missing, list)
    assert "mfc140u.dll" in missing
