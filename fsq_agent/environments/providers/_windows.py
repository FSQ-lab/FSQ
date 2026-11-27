# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path

from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import PlatformPrerequisiteCheck

WINDOWS_RUNTIME_PROVIDER = RuntimeProvider(platform="windows", module="pywinauto", required_host="Windows")

_REQUIRED_SANDBOX_RUNTIME_DLLS = (
    "mfc140u.dll",
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
_WSB_LIST_TIMEOUT_SECONDS = 15.0
_MAX_WSB_LIST_STDOUT_BYTES = 256 * 1024


def _fact(
    identifier: str,
    status: str = "ready",
    message: str = "Requirement is ready.",
    *,
    code: str | None = None,
    action: str | None = None,
    commands: tuple[str, ...] = (),
) -> PlatformPrerequisiteCheck:
    return PlatformPrerequisiteCheck(
        identifier=identifier,
        status=status,
        message=message,
        code=code,
        action=action,
        commands=commands,
    )


def _blocked(identifier: str, dependency: str) -> PlatformPrerequisiteCheck:
    return _fact(
        identifier,
        "not_applicable",
        f"Check blocked by {dependency}.",
        code="windows.dependency_unavailable",
        action="Resolve the preceding Windows Sandbox requirement and recheck.",
    )


def _locate_wsb_executable() -> Path | None:
    found = shutil.which("wsb.exe") or shutil.which("wsb")
    if found:
        try:
            resolved = Path(found).resolve()
            if resolved.is_file():
                return resolved
        except OSError:
            pass
    windir = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    candidate = Path(windir) / "System32" / "wsb.exe"
    try:
        if candidate.is_file():
            return candidate.resolve()
    except OSError:
        return None
    return None


def _probe_wsb_list(wsb_executable: Path) -> tuple[PlatformPrerequisiteCheck, PlatformPrerequisiteCheck]:
    try:
        completed = subprocess.run(  # noqa: S603
            [str(wsb_executable), "list", "--raw"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_WSB_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        service = _fact(
            "windows_sandbox_service",
            "error",
            "Windows Sandbox status query timed out.",
            code="windows.sandbox_timeout",
            action="Ensure the Windows host desktop is unlocked, virtualization is healthy, and recheck.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")
    except OSError:
        service = _fact(
            "windows_sandbox_service",
            "unavailable",
            "Unable to invoke Windows Sandbox CLI (wsb.exe).",
            code="windows.sandbox_invoke_failed",
            action="Verify Windows Sandbox is enabled and accessible from the current interactive user session.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")

    if completed.returncode != 0:
        service = _fact(
            "windows_sandbox_service",
            "unavailable",
            "Windows Sandbox service check failed.",
            code="windows.sandbox_service_unavailable",
            action="Enable the Windows Sandbox optional feature and host virtualization, ensure the Windows session is unlocked, and recheck.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")

    raw_stdout = completed.stdout or ""
    if len(raw_stdout.encode("utf-8", errors="replace")) > _MAX_WSB_LIST_STDOUT_BYTES:
        service = _fact(
            "windows_sandbox_service",
            "error",
            "Windows Sandbox CLI returned an oversized environment list.",
            code="windows.sandbox_invalid_response",
            action="Verify wsb.exe health (`wsb list --raw`) and recheck.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")

    stripped = raw_stdout.strip()
    if not stripped:
        return (
            _fact("windows_sandbox_service", "ready", "Windows Sandbox service is responsive."),
            _fact("windows_sandbox_slot", "ready", "No conflicting Windows Sandbox session is active."),
        )

    try:
        parsed = json.loads(stripped)
    except ValueError:
        service = _fact(
            "windows_sandbox_service",
            "error",
            "Windows Sandbox CLI returned an unreadable environment list.",
            code="windows.sandbox_invalid_response",
            action="Verify wsb.exe health (`wsb list --raw`) and recheck.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")

    environments = parsed.get("WindowsSandboxEnvironments") if isinstance(parsed, dict) else None
    if not isinstance(environments, list):
        service = _fact(
            "windows_sandbox_service",
            "error",
            "Windows Sandbox CLI returned an unexpected environment list format.",
            code="windows.sandbox_invalid_response",
            action="Verify wsb.exe health (`wsb list --raw`) and recheck.",
        )
        return service, _blocked("windows_sandbox_slot", "windows_sandbox_service")

    service_ready = _fact("windows_sandbox_service", "ready", "Windows Sandbox service is responsive.")
    if environments:
        return (
            service_ready,
            _fact(
                "windows_sandbox_slot",
                "unavailable",
                "Another Windows Sandbox instance is already running.",
                code="windows.sandbox_busy",
                action="Close or stop the active Windows Sandbox session (`wsb list --raw` / `wsb stop --id <ID>`) before running FSQ with --sandbox wsb.",
            ),
        )
    return (
        service_ready,
        _fact("windows_sandbox_slot", "ready", "No conflicting Windows Sandbox session is active."),
    )


def _check_sandbox_runtime() -> PlatformPrerequisiteCheck:
    try:
        python_base = Path(sys.base_prefix).resolve()
    except OSError:
        python_base = None
    if python_base is None or not (python_base / "python.exe").is_file():
        return _fact(
            "windows_sandbox_runtime",
            "unavailable",
            "Host base Python installation could not be located for Windows Sandbox mounting.",
            code="windows.sandbox_python_missing",
            action="Run FSQ from a standard Windows Python or virtual environment with a valid base python.exe.",
        )

    site_candidates = [Path(p).resolve() for p in site.getsitepackages() if Path(p).name.casefold() == "site-packages" and Path(p).is_dir()]
    if not site_candidates:
        venv_site = Path(sys.prefix).resolve() / "Lib" / "site-packages"
        if venv_site.is_dir():
            site_candidates.append(venv_site)
    if not site_candidates:
        return _fact(
            "windows_sandbox_runtime",
            "unavailable",
            "Host Python site-packages directory could not be located for Windows Sandbox mounting.",
            code="windows.sandbox_site_packages_missing",
            action="Verify the active Python environment contains a readable site-packages directory.",
        )
    site_packages = site_candidates[0]

    windir = Path(os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows")
    search_dirs = [
        windir / "System32",
        python_base,
        site_packages / "Pythonwin",
        site_packages / "pywin32_system32",
    ]
    missing_dlls = [dll_name for dll_name in _REQUIRED_SANDBOX_RUNTIME_DLLS if not any((directory / dll_name).is_file() for directory in search_dirs)]
    if missing_dlls:
        missing_list = ", ".join(missing_dlls)
        return _fact(
            "windows_sandbox_runtime",
            "unavailable",
            f"Required host Windows C++/MFC runtime DLLs for Sandbox execution are missing ({missing_list}).",
            code="windows.sandbox_runtime_dlls_missing",
            action="Install the Microsoft Visual C++ Redistributable (x64) and pywin32 on the host, then recheck.",
        )

    return _fact("windows_sandbox_runtime", "ready", "Host Python and C++/MFC runtime DLLs for Windows Sandbox are ready.")


def windows_sandbox_prerequisites() -> tuple[PlatformPrerequisiteCheck, ...]:
    if sys.platform != "win32":
        host_unavailable = _fact(
            "windows_sandbox_cli",
            "unavailable",
            "Windows Sandbox is supported only on a Windows host.",
            code="windows.host_unsupported",
            action="Run Windows Sandbox diagnostics and tests on a Windows 11 host.",
        )
        return (
            host_unavailable,
            _blocked("windows_sandbox_service", "windows_sandbox_cli"),
            _blocked("windows_sandbox_slot", "windows_sandbox_cli"),
            _blocked("windows_sandbox_runtime", "windows_sandbox_cli"),
        )

    try:
        wsb_executable = _locate_wsb_executable()
    except Exception:  # noqa: BLE001 - isolate unexpected filesystem errors
        wsb_executable = None

    if wsb_executable is None:
        cli_fact = _fact(
            "windows_sandbox_cli",
            "unavailable",
            "Windows Sandbox CLI (wsb.exe) is unavailable on this host.",
            code="windows.sandbox_cli_missing",
            action="Enable the Windows Sandbox optional feature on Windows 11 (24H2 or newer) and verify wsb.exe is installed.",
        )
        service_fact = _blocked("windows_sandbox_service", "windows_sandbox_cli")
        slot_fact = _blocked("windows_sandbox_slot", "windows_sandbox_cli")
    else:
        cli_fact = _fact("windows_sandbox_cli", "ready", "Windows Sandbox CLI (wsb.exe) is available.")
        try:
            service_fact, slot_fact = _probe_wsb_list(wsb_executable)
        except Exception:  # noqa: BLE001 - isolate unexpected probe errors
            service_fact = _fact(
                "windows_sandbox_service",
                "error",
                "Windows Sandbox service check could not be completed safely.",
                code="windows.sandbox_probe_failed",
                action="Inspect Windows Sandbox (`wsb list --raw`) manually and recheck.",
            )
            slot_fact = _blocked("windows_sandbox_slot", "windows_sandbox_service")

    try:
        runtime_fact = _check_sandbox_runtime()
    except Exception:  # noqa: BLE001 - isolate unexpected runtime inspection errors
        runtime_fact = _fact(
            "windows_sandbox_runtime",
            "error",
            "Windows Sandbox host runtime inspection could not be completed safely.",
            code="windows.sandbox_runtime_probe_failed",
            action="Inspect the host Python environment and Visual C++ Redistributable installation.",
        )

    return (cli_fact, service_fact, slot_fact, runtime_fact)
