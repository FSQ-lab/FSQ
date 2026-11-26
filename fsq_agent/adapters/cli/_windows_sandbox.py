# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import codecs
import ctypes
import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    WorkspaceRequest,
    require_initialized_workspace,
)
from fsq_agent.config import (
    WorkspaceRegistryEntry,
    inspect_registered_workspace,
    list_workspace_registry,
    load_user_provider_config,
    register_existing_workspace_root,
)
from fsq_agent.models import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

SandboxCommandKind = Literal["case_create", "case_test", "quick_test_run"]

_GUEST_USER_HOME = r"C:\Users\WDAGUtilityAccount"
_GUEST_USER_FSQ = rf"{_GUEST_USER_HOME}\.fsq"
_GUEST_QUICK_TEST_ROOT = rf"{_GUEST_USER_FSQ}\quick-test"
_GUEST_WORKSPACE_ROOT = rf"{_GUEST_USER_HOME}\fsq-workspace"
_GUEST_OUT_DIR = r"C:\fsq_sandbox"
_GUEST_PY_BASE = r"C:\fsq_pybase"
_GUEST_SITE_PACKAGES = r"C:\fsq_site"
_GUEST_PACKAGE_ROOT = r"C:\fsq_pkg"
_GUEST_HOST_FSQ_SNAPSHOT = r"C:\fsq_user_cfg"
_GUEST_CASE_FILE_DIR = rf"{_GUEST_OUT_DIR}\case_src"

_WSB_CONFIG_XML = "<Configuration><VGpu>Enable</VGpu><Networking>Enable</Networking><MemoryInMB>8192</MemoryInMB></Configuration>"

_REQUIRED_VC_RUNTIME_DLLS = (
    "mfc140u.dll",
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
_OPTIONAL_VC_RUNTIME_DLLS = (
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "concrt140.dll",
    "vccorlib140.dll",
)
_SANDBOX_CLIENT_PROCESS_NAMES = ("WindowsSandboxClient.exe", "WindowsSandboxRemoteSession.exe")
_HWND_BOTTOM = 1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _render_sitecustomize_source() -> str:
    guest_vc_dlls = rf"{_GUEST_OUT_DIR}\vc_dlls"
    guest_pywin32_sys32 = rf"{_GUEST_SITE_PACKAGES}\pywin32_system32"
    guest_pythonwin = rf"{_GUEST_SITE_PACKAGES}\Pythonwin"
    return f"""# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import pathlib
import site
import sys

if not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 0

if os.path.isdir({_GUEST_SITE_PACKAGES!r}):
    site.addsitedir({_GUEST_SITE_PACKAGES!r})

_SANDBOX_DLL_DIRS = (
    {guest_vc_dlls!r},
    {guest_pywin32_sys32!r},
    {guest_pythonwin!r},
    {_GUEST_PY_BASE!r},
)
_SANDBOX_DLL_HANDLES = []
if hasattr(os, "add_dll_directory"):
    for _dll_dir in _SANDBOX_DLL_DIRS:
        if os.path.isdir(_dll_dir):
            try:
                _SANDBOX_DLL_HANDLES.append(os.add_dll_directory(_dll_dir))
            except OSError:
                pass

_SANDBOX_MOUNT_CANONICAL = {{
    os.path.normcase({_GUEST_WORKSPACE_ROOT!r}): {_GUEST_WORKSPACE_ROOT!r},
    os.path.normcase({_GUEST_QUICK_TEST_ROOT!r}): {_GUEST_QUICK_TEST_ROOT!r},
}}
_orig_is_symlink = pathlib.Path.is_symlink
_orig_resolve = pathlib.Path.resolve


def _sandbox_is_symlink(self: pathlib.Path) -> bool:
    try:
        normalized = os.path.normcase(os.path.abspath(os.fspath(self)))
    except Exception:
        return _orig_is_symlink(self)
    if normalized in _SANDBOX_MOUNT_CANONICAL:
        return False
    return _orig_is_symlink(self)


def _sandbox_resolve(self: pathlib.Path, strict: bool = False) -> pathlib.Path:
    try:
        abs_str = os.path.abspath(os.fspath(self))
        norm_str = os.path.normcase(abs_str)
        for norm_root, canonical_root in _SANDBOX_MOUNT_CANONICAL.items():
            if norm_str == norm_root:
                if strict and not os.path.exists(abs_str):
                    raise FileNotFoundError(abs_str)
                return pathlib.Path(canonical_root)
            prefix = norm_root + "\\\\"
            if norm_str.startswith(prefix):
                rel = os.path.relpath(abs_str, canonical_root)
                if rel != ".." and not rel.startswith("..\\\\"):
                    candidate = canonical_root if rel == "." else canonical_root + "\\\\" + rel
                    if strict and not os.path.exists(candidate):
                        raise FileNotFoundError(candidate)
                    return pathlib.Path(candidate)
    except FileNotFoundError:
        raise
    except Exception:
        pass
    return _orig_resolve(self, strict=strict)


pathlib.Path.is_symlink = _sandbox_is_symlink  # type: ignore[method-assign]
pathlib.Path.resolve = _sandbox_resolve  # type: ignore[method-assign]

try:
    import comtypes.client
    import comtypes.gen
except Exception:
    comtypes = None
else:
    _cache_dir = pathlib.Path(os.environ.get("TEMP", r"C:\\Temp")) / "comtypes_cache"
    _cache_dir.mkdir(parents=True, exist_ok=True)
    comtypes.client.gen_dir = str(_cache_dir)

    if str(_cache_dir) not in comtypes.gen.__path__:
        comtypes.gen.__path__.insert(0, str(_cache_dir))
"""


def _render_guest_bootstrap_source() -> str:
    return f"""# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import pathlib
import shutil
import subprocess
import sys

OUT_DIR = pathlib.Path({_GUEST_OUT_DIR!r})
SNAPSHOT_FSQ = pathlib.Path({_GUEST_HOST_FSQ_SNAPSHOT!r})
GUEST_FSQ = pathlib.Path.home() / ".fsq"

def _sync_user_config() -> None:
    GUEST_FSQ.mkdir(parents=True, exist_ok=True)
    cfg = SNAPSHOT_FSQ / "config.yaml"
    if cfg.is_file():
        shutil.copy2(cfg, GUEST_FSQ / "config.yaml")
    src_auth = SNAPSHOT_FSQ / "auth"
    if src_auth.is_dir():
        dst_auth = GUEST_FSQ / "auth"
        dst_auth.mkdir(parents=True, exist_ok=True)
        for item in src_auth.iterdir():
            if item.is_file():
                shutil.copy2(item, dst_auth / item.name)


def main() -> int:
    spec = json.loads((OUT_DIR / "command.json").read_text(encoding="utf-8"))
    _sync_user_config()
    cmd = [sys.executable, "-m", "fsq_agent.cli", *spec["cli_args"]]
    stdout_path = OUT_DIR / "guest_stdout.jsonl"
    stderr_path = OUT_DIR / "guest_stderr.log"
    with stdout_path.open("w", encoding="utf-8", buffering=1) as out_stream, stderr_path.open("w", encoding="utf-8", buffering=1) as err_stream:
        proc = subprocess.Popen(cmd, cwd=spec["cwd"], stdout=out_stream, stderr=err_stream, text=True, encoding="utf-8")
        rc = proc.wait()
    (OUT_DIR / "exit_code.txt").write_text(str(rc), encoding="utf-8")
    (OUT_DIR / "done.marker").write_text("done\\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        (OUT_DIR / "bootstrap_error.txt").write_text(f"{{type(exc).__name__}}: {{exc}}\\n", encoding="utf-8")
        (OUT_DIR / "exit_code.txt").write_text("5", encoding="utf-8")
        (OUT_DIR / "done.marker").write_text("error\\n", encoding="utf-8")
        raise
"""


@dataclass(frozen=True)
class SandboxCommandSpec:
    kind: SandboxCommandKind
    current_directory: Path
    output_format: str
    non_interactive: bool
    goal: str | None = None
    target_id: str | None = None
    url: str | None = None
    case_name: str | None = None
    case_path: Path | None = None
    suggest: bool = False
    user_config_root: Path | None = None


@dataclass(frozen=True)
class SandboxExecutionOutcome:
    exit_code: int
    terminal_record: dict[str, object] | None
    stderr_text: str


def ensure_sandbox_platform_supported(*, sandbox: bool, platform: str, udid: str | None = None) -> None:
    if not sandbox:
        return
    if platform != "windows":
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_INVALID,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="--sandbox is supported only with --platform windows.",
            action="Omit --sandbox or select --platform windows.",
        )
    if udid is not None:
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_INVALID,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="--udid is supported only with --platform ios.",
            action="Omit --udid or select --platform ios.",
        )


def execute_in_windows_sandbox(
    spec: SandboxCommandSpec,
    *,
    event_callback: Callable[[dict[str, object]], None] | None = None,
    timeout_seconds: float = 1800.0,
) -> SandboxExecutionOutcome:
    if sys.platform != "win32":
        raise _sandbox_unavailable(
            "Windows Sandbox execution requires a Windows host.",
            "Run --sandbox on an unlocked Windows 11 host with Windows Sandbox enabled.",
        )

    wsb_executable = _resolve_wsb_executable()
    host_user_root = (spec.user_config_root or Path.home() / ".fsq").expanduser().resolve()

    if spec.kind in {"case_create", "case_test"}:
        workspace, workspace_name = _resolve_initialized_host_workspace(spec.current_directory, spec.user_config_root)
        host_mount_root = workspace
        guest_mount_root = _GUEST_WORKSPACE_ROOT
        quick_test_was_registered = True
    else:
        host_mount_root, quick_test_was_registered = _prepare_quick_test_host_root(host_user_root)
        workspace_name = "quick-test"
        guest_mount_root = _GUEST_QUICK_TEST_ROOT
        host_mount_root.mkdir(parents=True, exist_ok=True)

    _ensure_no_running_sandboxes(wsb_executable)

    python_base, site_packages, package_root = _discover_python_mounts()
    external_case_file, guest_cli_args = _build_guest_cli_args(spec, host_mount_root=host_mount_root, guest_mount_root=guest_mount_root)
    path_rebases: list[tuple[str, Path]] = [(guest_mount_root, host_mount_root)]
    if external_case_file is not None:
        path_rebases.append((rf"{_GUEST_CASE_FILE_DIR}\{external_case_file.name}", external_case_file))

    win_cfg_path = host_mount_root / ".fsq" / "config" / "config.windows.yaml"
    original_win_cfg_bytes = win_cfg_path.read_bytes() if win_cfg_path.is_file() else None
    rewrote_host_win_cfg = False

    sandbox_id: str | None = None
    connect_proc: subprocess.Popen[bytes] | None = None
    exec_proc: subprocess.Popen[bytes] | None = None

    staging_dir = Path(tempfile.mkdtemp(prefix="fsq-wsb-")).resolve()
    snapshot_fsq_dir = staging_dir / "user_cfg"
    restore_error: ApplicationError | None = None
    active_exception: BaseException | None = None
    try:
        _prepare_guest_user_config_snapshot(
            host_user_root=host_user_root,
            snapshot_dir=snapshot_fsq_dir,
            workspace_name=workspace_name,
            guest_mount_root=guest_mount_root,
            include_workspace_entry=(spec.kind != "quick_test_run" or quick_test_was_registered),
        )
        _stage_sandbox_files(
            staging_dir=staging_dir,
            guest_cwd=guest_mount_root if spec.kind != "quick_test_run" else _GUEST_OUT_DIR,
            guest_cli_args=guest_cli_args,
            include_package_root=package_root is not None,
            python_base=python_base,
            site_packages=site_packages,
            external_case_file=external_case_file,
        )
        if original_win_cfg_bytes is not None:
            _rewrite_workspace_config_root_path(win_cfg_path, guest_mount_root)
            rewrote_host_win_cfg = True

        sandbox_id = _start_sandbox_instance(wsb_executable)
        _share_sandbox_folders(
            wsb_executable,
            sandbox_id=sandbox_id,
            staging_dir=staging_dir,
            python_base=python_base,
            site_packages=site_packages,
            package_root=package_root,
            snapshot_fsq_dir=snapshot_fsq_dir,
            host_mount_root=host_mount_root,
            guest_mount_root=guest_mount_root,
        )

        connect_proc = _connect_and_background_sandbox_window(wsb_executable, sandbox_id=sandbox_id)
        _wait_for_existing_login_session(wsb_executable, sandbox_id=sandbox_id)
        _run_wsb_command(wsb_executable, ["exec", "--id", sandbox_id, "-r", "System", "-c", rf"{_GUEST_OUT_DIR}\_setup.cmd", "--raw"], timeout=30.0)

        exec_proc = subprocess.Popen(  # noqa: S603
            [
                str(wsb_executable),
                "exec",
                "--id",
                sandbox_id,
                "-r",
                "ExistingLogin",
                "-d",
                _GUEST_OUT_DIR,
                "-c",
                rf"{_GUEST_OUT_DIR}\_user_run.cmd",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        exit_code, terminal_record, stderr_text = _stream_and_collect_guest_result(
            staging_dir=staging_dir,
            exec_proc=exec_proc,
            host_mount_root=host_mount_root,
            guest_mount_root=guest_mount_root,
            extra_path_rebases=path_rebases[1:],
            event_callback=event_callback,
            timeout_seconds=timeout_seconds,
        )
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        _terminate_process_safely(exec_proc)
        _terminate_process_safely(connect_proc)
        if sandbox_id is not None:
            _stop_sandbox_instance(wsb_executable, sandbox_id)
        if rewrote_host_win_cfg or (original_win_cfg_bytes is None and win_cfg_path.is_file()):
            try:
                _restore_host_workspace_config(
                    win_cfg_path=win_cfg_path,
                    original_bytes=original_win_cfg_bytes,
                    host_mount_root=host_mount_root,
                )
            except ApplicationError as err:
                restore_error = err
        _cleanup_staging_dir_best_effort(staging_dir)
    if restore_error is not None and active_exception is None:
        raise restore_error
    if spec.kind == "quick_test_run" and not quick_test_was_registered and win_cfg_path.is_file():
        _register_quick_test_on_host_best_effort(host_mount_root, host_user_root)

    return SandboxExecutionOutcome(
        exit_code=exit_code,
        terminal_record=terminal_record,
        stderr_text=stderr_text,
    )


def _cleanup_staging_dir_best_effort(staging_dir: Path) -> None:
    for attempt in range(8):
        if not staging_dir.exists():
            return
        shutil.rmtree(staging_dir, ignore_errors=True)
        if not staging_dir.exists():
            return
        if attempt < 7:
            time.sleep(0.25)


def _sandbox_unavailable(message: str, action: str, *, details: dict[str, object] | None = None) -> ApplicationError:
    return ApplicationError(
        code=ApplicationErrorCode.ENVIRONMENT_UNAVAILABLE,
        category=ApplicationErrorCategory.UNAVAILABLE,
        message=message,
        action=action,
        details=details or {},
    )


def _resolve_wsb_executable() -> Path:
    found = shutil.which("wsb.exe") or shutil.which("wsb")
    if found:
        resolved = Path(found).resolve()
        if resolved.is_file():
            return resolved
    windir = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    candidate = Path(windir) / "System32" / "wsb.exe"
    if candidate.is_file():
        return candidate.resolve()
    raise _sandbox_unavailable(
        "Windows Sandbox CLI (wsb.exe) is unavailable on this host.",
        "Enable the Windows Sandbox optional feature on Windows 11 (24H2 or newer) and verify wsb.exe is installed.",
    )


def _run_wsb_command(wsb_executable: Path, args: Sequence[str], *, timeout: float = 60.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(  # noqa: S603
            [str(wsb_executable), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _sandbox_unavailable(
            "Windows Sandbox command timed out.",
            "Ensure the host desktop is unlocked, virtualization is healthy, and retry.",
            details={"stage": args[0] if args else "wsb"},
        ) from exc
    except OSError as exc:
        raise _sandbox_unavailable(
            "Unable to invoke Windows Sandbox CLI (wsb.exe).",
            "Verify Windows Sandbox is enabled and accessible from the current user session.",
            details={"stage": args[0] if args else "wsb"},
        ) from exc
    if check and completed.returncode != 0:
        raise _sandbox_unavailable(
            "Windows Sandbox operation failed.",
            "Ensure the Windows host session is unlocked, virtualization is enabled, and no conflicting Sandbox state exists.",
            details={"stage": args[0] if args else "wsb", "exit_code": completed.returncode},
        )
    return completed


def _terminate_process_safely(proc: subprocess.Popen[bytes] | None, *, wait_timeout: float = 5.0) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        else:
            proc.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _ensure_no_running_sandboxes(wsb_executable: Path) -> None:
    completed = _run_wsb_command(wsb_executable, ["list", "--raw"], timeout=15.0, check=True)
    raw = (completed.stdout or "").strip()
    if not raw:
        return
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise _sandbox_unavailable(
            "Windows Sandbox CLI returned an unreadable environment list.",
            "Verify wsb.exe health (`wsb list --raw`) and retry.",
            details={"stage": "list"},
        ) from exc
    if not isinstance(parsed, dict):
        raise _sandbox_unavailable(
            "Windows Sandbox CLI returned an unexpected environment list format.",
            "Verify wsb.exe health (`wsb list --raw`) and retry.",
            details={"stage": "list"},
        )
    environments = parsed.get("WindowsSandboxEnvironments")
    if isinstance(environments, list) and environments:
        raise _sandbox_unavailable(
            "Another Windows Sandbox instance is already running.",
            "Close or stop the active Windows Sandbox session (`wsb stop --id <ID>`) before running FSQ with --sandbox.",
            details={"running_count": len(environments)},
        )


def _resolve_initialized_host_workspace(current_directory: Path, user_config_root: Path | None) -> tuple[Path, str]:
    resolved = current_directory.expanduser().resolve()
    resolved_user_root = (user_config_root or Path.home() / ".fsq").expanduser().resolve()
    if user_config_root is None:
        resolved = require_initialized_workspace(WorkspaceRequest(current_directory=current_directory)).workspace.resolve()
    try:
        entries = list_workspace_registry(resolved_user_root)
    except ConfigurationError as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.CONFIGURATION,
            message="Local FSQ user configuration is invalid.",
            action="Repair ~/.fsq/config.yaml and retry.",
        ) from exc
    entry = next((item for item in entries if item.root_path.resolve() == resolved), None)
    if entry is None:
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The current directory is not an initialized FSQ Workspace.",
            action="Run 'fsq init' here or change to an initialized FSQ Workspace.",
            details={"current_directory": str(resolved), "workspace": str(resolved)},
        )
    if user_config_root is not None:
        try:
            status = inspect_registered_workspace(entry.name, resolved_user_root)
        except Exception as exc:
            raise ApplicationError(
                code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
                category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
                message="The registered FSQ Workspace is unavailable.",
                action="Repair it in Control Plane or initialize a valid workspace root.",
                details={"workspace": str(resolved)},
            ) from exc
        if status.status == "unavailable":
            raise ApplicationError(
                code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
                category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
                message="The registered FSQ Workspace has no valid platform configuration.",
                action="Add or repair a platform configuration.",
                details={"workspace": str(resolved)},
            )
    return resolved, entry.name


def _prepare_quick_test_host_root(user_config_root: Path) -> tuple[Path, bool]:
    if not user_config_root.is_absolute() or any(path.is_symlink() for path in (user_config_root, *user_config_root.parents)):
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_WORKSPACE_CONFLICT,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The dedicated Quick Test Workspace cannot be safely used.",
            action="Resolve the ~/.fsq/quick-test path conflict before retrying.",
            details={"reason": "unsafe_root"},
        )
    candidate_root = user_config_root / "quick-test"
    if candidate_root.is_symlink():
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_WORKSPACE_CONFLICT,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The dedicated Quick Test Workspace cannot be safely used.",
            action="Resolve the ~/.fsq/quick-test path conflict before retrying.",
            details={"reason": "unsafe_root"},
        )
    root = candidate_root.resolve()
    try:
        entries = list_workspace_registry(user_config_root)
    except ConfigurationError as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_PREREQUISITES,
            category=ApplicationErrorCategory.UNAVAILABLE,
            message="Quick Test execution prerequisites could not be validated.",
            action="Recheck Provider and local user configuration.",
        ) from exc
    matching = [entry for entry in entries if entry.name.casefold() == "quick-test" or entry.root_path.resolve() == root]
    if any(entry.name.casefold() != "quick-test" or entry.root_path.resolve() != root for entry in matching):
        raise ApplicationError(
            code=ApplicationErrorCode.QUICK_TEST_WORKSPACE_CONFLICT,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The dedicated Quick Test Workspace conflicts with an existing Workspace registration.",
            action="Resolve the conflicting Workspace entry in ~/.fsq/config.yaml before retrying.",
            details={"reason": "identity_conflict"},
        )
    return root, bool(matching)


def _discover_python_mounts() -> tuple[Path, Path, Path | None]:
    python_base = Path(sys.base_prefix).resolve()
    if not (python_base / "python.exe").is_file():
        raise _sandbox_unavailable(
            "Host base Python installation could not be located for Windows Sandbox mounting.",
            "Run FSQ from a standard Windows Python or virtual environment with a valid base python.exe.",
        )

    site_candidates = [Path(p).resolve() for p in site.getsitepackages() if Path(p).name.casefold() == "site-packages" and Path(p).is_dir()]
    if not site_candidates:
        venv_site = Path(sys.prefix).resolve() / "Lib" / "site-packages"
        if venv_site.is_dir():
            site_candidates.append(venv_site)
    if not site_candidates:
        raise _sandbox_unavailable(
            "Host Python site-packages directory could not be located for Windows Sandbox mounting.",
            "Verify the active Python environment contains a readable site-packages directory.",
        )
    site_packages = site_candidates[0]

    import fsq_agent

    fsq_pkg_parent = Path(fsq_agent.__file__).resolve().parent.parent
    package_root = None if _is_subpath(fsq_pkg_parent, site_packages) else fsq_pkg_parent
    return python_base, site_packages, package_root


def _build_guest_cli_args(
    spec: SandboxCommandSpec,
    *,
    host_mount_root: Path,
    guest_mount_root: str,
) -> tuple[Path | None, list[str]]:
    args: list[str] = ["--output", "jsonl"]
    if spec.non_interactive:
        args.append("--non-interactive")

    if spec.kind == "quick_test_run":
        args.extend(["quick-test", "run", "--platform", "windows", "--goal", spec.goal or ""])
        if spec.target_id is not None:
            args.extend(["--target-id", spec.target_id])
        if spec.url is not None:
            args.extend(["--url", spec.url])
        return None, args
    if spec.kind == "case_create":
        args.extend(["case", "create", "--platform", "windows", "--goal", spec.goal or ""])
        if spec.case_name is not None:
            args.extend(["--name", spec.case_name])
        return None, args

    if spec.case_path is None:
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_NOT_FOUND,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="Case not found.",
            action="Provide an existing *.fsq.yaml Case path.",
        )
    resolved_case = _resolve_host_case_path_for_sandbox(spec.case_path, workspace_root=host_mount_root, current_directory=spec.current_directory)
    if resolved_case is None:
        # Delegate missing-file reporting to guest/Application using relative/supplied spelling when safe, or raise canonical CASE_NOT_FOUND directly.
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_NOT_FOUND,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message=f"Case not found: {spec.case_path}",
            action="Provide an existing *.fsq.yaml Case path.",
        )
    if _is_subpath(resolved_case, host_mount_root):
        rel = resolved_case.relative_to(host_mount_root)
        guest_case_str = str(Path(guest_mount_root) / rel)
        external_case_file = None
    else:
        external_case_file = resolved_case
        guest_case_str = rf"{_GUEST_CASE_FILE_DIR}\{resolved_case.name}"

    args.extend(["case", "test", guest_case_str, "--platform", "windows"])
    if spec.suggest:
        args.append("--suggest")
    return external_case_file, args


def _resolve_host_case_path_for_sandbox(case_path: Path, *, workspace_root: Path, current_directory: Path) -> Path | None:
    cases_dir = workspace_root / "cases" / "windows"
    candidates = [case_path] if case_path.is_absolute() else [cases_dir / case_path, current_directory / case_path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _prepare_guest_user_config_snapshot(
    *,
    host_user_root: Path,
    snapshot_dir: Path,
    workspace_name: str,
    guest_mount_root: str,
    include_workspace_entry: bool,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    host_cfg_path = host_user_root / "config.yaml"
    try:
        user_cfg = load_user_provider_config(host_user_root)
    except ConfigurationError as exc:
        # Missing/corrupt provider credentials should not block provider-free strict `case test --sandbox`,
        # whereas a malformed/unreadable host `config.yaml` must fail fast.
        if host_cfg_path.is_file() and "provider" not in exc.context:
            raise ApplicationError(
                code=ApplicationErrorCode.CONFIGURATION_INVALID,
                category=ApplicationErrorCategory.CONFIGURATION,
                message="Local FSQ user configuration is invalid.",
                action="Repair ~/.fsq/config.yaml or reconfigure your Provider before retrying.",
            ) from exc
        user_cfg = None
    provider_dump = user_cfg.model_dump(mode="json").get("provider") if user_cfg is not None else None
    workspaces_dump = [{"name": workspace_name, "root_path": guest_mount_root}] if include_workspace_entry else []
    guest_doc = {
        "version": 3,
        "provider": provider_dump,
        "workspaces": workspaces_dump,
    }
    (snapshot_dir / "config.yaml").write_text(yaml.safe_dump(guest_doc, sort_keys=False), encoding="utf-8")

    host_auth = host_user_root / "auth"
    if host_auth.is_dir():
        dst_auth = snapshot_dir / "auth"
        dst_auth.mkdir(parents=True, exist_ok=True)
        for item in host_auth.iterdir():
            if item.is_file() and not item.is_symlink():
                shutil.copy2(item, dst_auth / item.name)


def _stage_runtime_dlls(
    *,
    vc_dll_dir: Path,
    python_base: Path | None,
    site_packages: Path | None,
) -> None:
    windir = Path(os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows")
    search_dirs: list[Path] = [windir / "System32"]
    if python_base is not None:
        search_dirs.append(python_base)
    if site_packages is not None:
        search_dirs.extend([site_packages / "Pythonwin", site_packages / "pywin32_system32"])

    def find_dll(dll_name: str) -> Path | None:
        for directory in search_dirs:
            candidate = directory / dll_name
            if candidate.is_file():
                return candidate
        return None

    missing_required: list[str] = []
    for dll_name in _REQUIRED_VC_RUNTIME_DLLS:
        source = find_dll(dll_name)
        if source is None:
            missing_required.append(dll_name)
        else:
            shutil.copy2(source, vc_dll_dir / dll_name)

    if missing_required:
        raise _sandbox_unavailable(
            "Required host Windows C++/MFC runtime DLLs for Sandbox execution could not be located.",
            "Install the Microsoft Visual C++ Redistributable (x64) and pywin32 on the host, then retry.",
            details={"stage": "runtime_dlls", "missing_dlls": missing_required},
        )

    for dll_name in _OPTIONAL_VC_RUNTIME_DLLS:
        source = find_dll(dll_name)
        if source is not None:
            shutil.copy2(source, vc_dll_dir / dll_name)

    if site_packages is not None:
        pywin32_sys32 = site_packages / "pywin32_system32"
        if pywin32_sys32.is_dir():
            for item in sorted(pywin32_sys32.glob("*.dll")):
                if item.is_file() and not item.is_symlink():
                    shutil.copy2(item, vc_dll_dir / item.name)


def _stage_sandbox_files(
    *,
    staging_dir: Path,
    guest_cwd: str,
    guest_cli_args: Sequence[str],
    include_package_root: bool,
    python_base: Path | None = None,
    site_packages: Path | None = None,
    external_case_file: Path | None = None,
) -> None:
    py_shim_dir = staging_dir / "py_shim"
    py_shim_dir.mkdir(parents=True, exist_ok=True)
    (py_shim_dir / "sitecustomize.py").write_text(_render_sitecustomize_source(), encoding="utf-8")
    (staging_dir / "guest_bootstrap.py").write_text(_render_guest_bootstrap_source(), encoding="utf-8")
    (staging_dir / "command.json").write_text(
        json.dumps({"cwd": guest_cwd, "cli_args": list(guest_cli_args)}, ensure_ascii=False),
        encoding="utf-8",
    )
    if external_case_file is not None:
        case_src_dir = staging_dir / "case_src"
        case_src_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(external_case_file, case_src_dir / external_case_file.name)
        except OSError as exc:
            raise ApplicationError(
                code=ApplicationErrorCode.CASE_NOT_FOUND,
                category=ApplicationErrorCategory.REQUEST_VALIDATION,
                message=f"Unable to read Case file for Sandbox staging: {external_case_file}",
                action="Verify the Case file exists and is readable, then retry.",
            ) from exc
    vc_dll_dir = staging_dir / "vc_dlls"
    vc_dll_dir.mkdir(parents=True, exist_ok=True)
    _stage_runtime_dlls(vc_dll_dir=vc_dll_dir, python_base=python_base, site_packages=site_packages)

    setup_lines = [
        "@echo off",
        rf'if exist "{_GUEST_OUT_DIR}\vc_dlls\*.dll" (copy /Y "{_GUEST_OUT_DIR}\vc_dlls\*.dll" C:\Windows\System32\ >nul || exit /b 1)',
    ]
    (staging_dir / "_setup.cmd").write_bytes(("\r\n".join(setup_lines) + "\r\n").encode("ascii"))
    py_paths = [rf"{_GUEST_OUT_DIR}\py_shim"]
    if include_package_root:
        py_paths.append(_GUEST_PACKAGE_ROOT)
    py_paths.extend(
        [
            _GUEST_SITE_PACKAGES,
            rf"{_GUEST_SITE_PACKAGES}\win32",
            rf"{_GUEST_SITE_PACKAGES}\win32\lib",
            rf"{_GUEST_SITE_PACKAGES}\Pythonwin",
        ]
    )
    path_entries = [
        rf"{_GUEST_OUT_DIR}\vc_dlls",
        _GUEST_PY_BASE,
        rf"{_GUEST_PY_BASE}\Scripts",
        rf"{_GUEST_SITE_PACKAGES}\pywin32_system32",
        rf"{_GUEST_SITE_PACKAGES}\win32",
        rf"{_GUEST_SITE_PACKAGES}\Pythonwin",
        "%PATH%",
    ]
    user_lines = [
        "@echo off",
        "setlocal",
        'set "PYTHONUTF8=1"',
        'set "PYTHONIOENCODING=utf-8"',
        f'set "PYTHONPATH={";".join(py_paths)}"',
        f'set "PATH={";".join(path_entries)}"',
        rf"{_GUEST_PY_BASE}\python.exe {_GUEST_OUT_DIR}\guest_bootstrap.py > {_GUEST_OUT_DIR}\boot_stdout.log 2>&1",
    ]
    (staging_dir / "_user_run.cmd").write_bytes(("\r\n".join(user_lines) + "\r\n").encode("ascii"))


def _rewrite_workspace_config_root_path(config_path: Path, new_root_path: str) -> None:
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="Host Windows Workspace configuration could not be parsed for Sandbox execution.",
            action="Inspect and repair .fsq/config/config.windows.yaml before retrying.",
            details={"config_path": str(config_path)},
        ) from exc
    if not isinstance(raw, dict):
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="Host Windows Workspace configuration must be a YAML mapping.",
            action="Inspect and repair .fsq/config/config.windows.yaml before retrying.",
            details={"config_path": str(config_path)},
        )
    raw["root_path"] = new_root_path
    try:
        _atomic_write_bytes(config_path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8"))
    except OSError as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.INTERNAL_ERROR,
            category=ApplicationErrorCategory.INTERNAL,
            message="Unable to update host Windows Workspace configuration for Sandbox execution.",
            action="Ensure .fsq/config/config.windows.yaml is writable and retry.",
            details={"config_path": str(config_path)},
        ) from exc


def _restore_host_workspace_config(*, win_cfg_path: Path, original_bytes: bytes | None, host_mount_root: Path) -> None:
    try:
        if original_bytes is not None:
            _atomic_write_bytes(win_cfg_path, original_bytes)
        elif win_cfg_path.is_file():
            _rewrite_workspace_config_root_path(win_cfg_path, str(host_mount_root.resolve()))
    except OSError as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.INTERNAL_ERROR,
            category=ApplicationErrorCategory.INTERNAL,
            message="Failed to restore host Windows Workspace configuration after Sandbox execution.",
            action=f"Verify root_path in {win_cfg_path} points to {host_mount_root.resolve()}.",
            details={"config_path": str(win_cfg_path), "expected_root_path": str(host_mount_root.resolve())},
        ) from exc


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _start_sandbox_instance(wsb_executable: Path) -> str:
    completed = _run_wsb_command(wsb_executable, ["start", "--config", _WSB_CONFIG_XML, "--raw"], timeout=90.0)
    try:
        parsed = json.loads(completed.stdout or "")
        sandbox_id = parsed.get("Id") if isinstance(parsed, dict) else None
    except ValueError as exc:
        raise _sandbox_unavailable(
            "Windows Sandbox returned an unreadable startup response.",
            "Verify wsb.exe health and retry.",
            details={"stage": "start"},
        ) from exc
    if not isinstance(sandbox_id, str) or not sandbox_id.strip():
        raise _sandbox_unavailable(
            "Windows Sandbox did not return a valid environment ID.",
            "Verify Windows Sandbox is enabled and retry.",
            details={"stage": "start"},
        )
    return sandbox_id.strip()


def _share_sandbox_folders(
    wsb_executable: Path,
    *,
    sandbox_id: str,
    staging_dir: Path,
    python_base: Path,
    site_packages: Path,
    package_root: Path | None,
    snapshot_fsq_dir: Path,
    host_mount_root: Path,
    guest_mount_root: str,
) -> None:
    shares: list[tuple[Path, str, bool]] = [
        (staging_dir, _GUEST_OUT_DIR, True),
        (python_base, _GUEST_PY_BASE, False),
        (site_packages, _GUEST_SITE_PACKAGES, False),
        (snapshot_fsq_dir, _GUEST_HOST_FSQ_SNAPSHOT, False),
        (host_mount_root, guest_mount_root, True),
    ]
    if package_root is not None:
        shares.append((package_root, _GUEST_PACKAGE_ROOT, False))

    for host_path, guest_path, writable in shares:
        cmd = ["share", "--id", sandbox_id, "-f", str(host_path), "-s", guest_path]
        if writable:
            cmd.append("--allow-write")
        cmd.append("--raw")
        _run_wsb_command(wsb_executable, cmd, timeout=30.0)


def _connect_and_background_sandbox_window(wsb_executable: Path, *, sandbox_id: str) -> subprocess.Popen[bytes]:
    saved_fg = _get_foreground_hwnd()
    try:
        proc = subprocess.Popen(  # noqa: S603
            [str(wsb_executable), "connect", "--id", sandbox_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise _sandbox_unavailable(
            "Failed to launch Windows Sandbox interactive session client.",
            "Ensure the Windows host has an unlocked interactive desktop session.",
            details={"stage": "connect"},
        ) from exc

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        hwnd = _find_sandbox_client_hwnd()
        if hwnd:
            _send_window_to_bottom_restore_foreground(hwnd, saved_fg)
            break
        if proc.poll() is not None and proc.returncode != 0:
            break
        time.sleep(0.25)
    return proc


def _wait_for_existing_login_session(wsb_executable: Path, *, sandbox_id: str) -> None:
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        completed = _run_wsb_command(
            wsb_executable,
            ["exec", "--id", sandbox_id, "-r", "ExistingLogin", "-c", "cmd.exe /c exit 0", "--raw"],
            timeout=15.0,
            check=False,
        )
        if completed.returncode == 0:
            return
        time.sleep(1.0)
    raise _sandbox_unavailable(
        "Windows Sandbox interactive user session (ExistingLogin) did not become ready.",
        "Ensure the host Windows desktop is unlocked (not on the Win+L lock screen or headless SSH) and retry.",
        details={"stage": "existing_login"},
    )


def _stop_sandbox_instance(wsb_executable: Path, sandbox_id: str) -> None:
    try:
        subprocess.run(  # noqa: S603
            [str(wsb_executable), "stop", "--id", sandbox_id, "--raw"],
            capture_output=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _extract_safe_exception_type(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    token = first_line.split(":", 1)[0].strip()
    if token and all(ch.isalnum() or ch in {"_", "."} for ch in token) and len(token) <= 128:
        return token
    return None


def _has_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _stream_and_collect_guest_result(
    *,
    staging_dir: Path,
    exec_proc: subprocess.Popen[bytes],
    host_mount_root: Path,
    guest_mount_root: str,
    extra_path_rebases: Sequence[tuple[str, Path]] = (),
    event_callback: Callable[[dict[str, object]], None] | None,
    timeout_seconds: float,
) -> tuple[int, dict[str, object] | None, str]:
    done_marker = staging_dir / "done.marker"
    stdout_path = staging_dir / "guest_stdout.jsonl"
    stderr_path = staging_dir / "guest_stderr.log"
    exit_code_path = staging_dir / "exit_code.txt"
    bootstrap_error_path = staging_dir / "bootstrap_error.txt"
    boot_stdout_path = staging_dir / "boot_stdout.log"

    read_offset = 0
    utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending_fragment = ""
    terminal_record: dict[str, object] | None = None
    deadline = time.monotonic() + timeout_seconds
    rebases: tuple[tuple[str, Path], ...] = ((guest_mount_root, host_mount_root), *extra_path_rebases)

    def process_line(line: str) -> None:
        nonlocal terminal_record
        stripped = line.strip()
        if not stripped:
            return
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return
        if not isinstance(parsed, dict):
            return
        rebased: object = parsed
        for guest_prefix, host_target in rebases:
            rebased = _rebase_guest_paths(rebased, guest_mount_root=guest_prefix, host_mount_root=host_target)
        if not isinstance(rebased, dict):
            return
        if rebased.get("type") == "event":
            event_payload = rebased.get("event")
            if event_callback is not None and isinstance(event_payload, dict):
                event_callback(event_payload)
        elif rebased.get("type") in {"result", "error"}:
            terminal_record = rebased

    def drain_lines(*, flush_partial: bool = False) -> None:
        nonlocal read_offset, pending_fragment
        if stdout_path.is_file():
            try:
                with stdout_path.open("rb") as handle:
                    handle.seek(read_offset)
                    chunk = handle.read()
                    read_offset += len(chunk)
            except OSError:
                chunk = b""
            if chunk:
                pending_fragment += utf8_decoder.decode(chunk, final=False)
                lines = pending_fragment.splitlines(keepends=True)
                if lines and not lines[-1].endswith("\n"):
                    pending_fragment = lines.pop()
                else:
                    pending_fragment = ""
                for line in lines:
                    process_line(line)
        if flush_partial:
            pending_fragment += utf8_decoder.decode(b"", final=True)
            if pending_fragment:
                trailing = pending_fragment
                pending_fragment = ""
                process_line(trailing)

    while not done_marker.is_file():
        if time.monotonic() > deadline:
            raise _sandbox_unavailable(
                "Windows Sandbox guest execution timed out before completion.",
                "Inspect the Workspace Run artifacts or retry with a narrower goal/Case.",
                details={"stage": "guest_timeout"},
            )
        drain_lines()
        if exec_proc.poll() is not None and not done_marker.is_file():
            time.sleep(0.5)
            if not done_marker.is_file():
                drain_lines(flush_partial=True)
                details: dict[str, object] = {
                    "exception_type": "SandboxGuestTerminatedError",
                    "has_boot_stdout": _has_nonempty_file(boot_stdout_path),
                    "has_guest_stderr": _has_nonempty_file(stderr_path),
                }
                bootstrap_exc_type = _extract_safe_exception_type(bootstrap_error_path)
                if bootstrap_exc_type:
                    details["bootstrap_exception_type"] = bootstrap_exc_type
                raise ApplicationError(
                    code=ApplicationErrorCode.INTERNAL_ERROR,
                    category=ApplicationErrorCategory.INTERNAL,
                    message="Windows Sandbox guest execution terminated unexpectedly before producing a completion marker.",
                    action="Inspect Workspace Run artifacts and retry.",
                    details=details,
                )
        time.sleep(0.25)

    drain_lines(flush_partial=True)
    stderr_text = ""
    if stderr_path.is_file():
        try:
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr_text = ""

    exit_code = 5
    if exit_code_path.is_file():
        try:
            exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            exit_code = 5

    return exit_code, terminal_record, stderr_text


def _rebase_guest_paths(value: object, *, guest_mount_root: str, host_mount_root: Path) -> object:
    if isinstance(value, str):
        return _rebase_path_string(value, guest_mount_root=guest_mount_root, host_mount_root=host_mount_root)
    if isinstance(value, list):
        return [_rebase_guest_paths(item, guest_mount_root=guest_mount_root, host_mount_root=host_mount_root) for item in value]
    if isinstance(value, dict):
        return {k: _rebase_guest_paths(v, guest_mount_root=guest_mount_root, host_mount_root=host_mount_root) for k, v in value.items()}
    return value


def _rebase_path_string(text: str, *, guest_mount_root: str, host_mount_root: Path) -> str:
    host_str = str(host_mount_root)
    guest_norm = guest_mount_root.rstrip("\\/")
    for sep, host_target in (("\\", host_str), ("/", host_str.replace("\\", "/"))):
        prefix = guest_norm.replace("\\", sep)
        low_text = text.casefold()
        low_prefix = prefix.casefold()
        if low_text == low_prefix:
            return host_target
        if low_text.startswith(low_prefix + sep):
            suffix = text[len(prefix) :]
            return host_target + suffix
    return text


def _register_quick_test_on_host_best_effort(host_mount_root: Path, host_user_root: Path) -> None:
    try:
        entries = list_workspace_registry(host_user_root)
        if any(item.name.casefold() == "quick-test" or item.root_path.resolve() == host_mount_root.resolve() for item in entries):
            return
        register_existing_workspace_root(WorkspaceRegistryEntry(name="quick-test", root_path=host_mount_root.resolve()), host_user_root)
    except Exception:  # noqa: BLE001, S110 - guest execution result remains authoritative if host registration already raced.
        pass


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    else:
        return True


def _get_foreground_hwnd() -> int:
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _send_window_to_bottom_restore_foreground(hwnd: int, saved_fg: int) -> None:
    if os.name != "nt" or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos(hwnd, _HWND_BOTTOM, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
        if saved_fg and saved_fg != hwnd:
            user32.SetForegroundWindow(saved_fg)
    except Exception:  # noqa: BLE001, S110
        pass


def _find_sandbox_client_hwnd() -> int:
    if os.name != "nt":
        return 0
    target_pids = _find_sandbox_client_pids()
    if not target_pids:
        return 0
    found_hwnd = 0
    try:
        user32 = ctypes.windll.user32
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _lparam: int) -> bool:
            nonlocal found_hwnd
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) in target_pids:
                found_hwnd = int(hwnd)
                return False
            return True

        user32.EnumWindows(enum_proc_type(callback), 0)
    except Exception:  # noqa: BLE001
        return 0
    return found_hwnd


def _find_sandbox_client_pids() -> set[int]:
    pids: set[int] = set()
    for image_name in _SANDBOX_CLIENT_PROCESS_NAMES:
        try:
            completed = subprocess.run(  # noqa: S603
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],  # noqa: S607
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in (completed.stdout or "").splitlines():
            parts = [part.strip().strip('"') for part in line.split(",")]
            if len(parts) >= 2 and parts[0].casefold() == image_name.casefold() and parts[1].isdigit():
                pids.add(int(parts[1]))
    return pids


__all__ = [
    "SandboxCommandSpec",
    "SandboxExecutionOutcome",
    "ensure_sandbox_platform_supported",
    "execute_in_windows_sandbox",
]
