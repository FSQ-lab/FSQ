# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from fsq_agent.environments.providers._android import (
    ALLOWED_MANAGED_ROOT_ENTRIES,
    MANAGED_APPIUM_VERSION,
    MANAGED_CLEANUP_PYTHON_COMMAND,
    MANAGED_INSTALL_PYTHON_COMMAND,
    MAX_MANAGED_METADATA_BYTES,
    _check_safe_owned_dir,
    _managed_root,
    _read_owned_json,
    _resolve_first_host_executable,
    _validate_managed_marker_and_subdirs,
    _validate_managed_records,
)
from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import PlatformPrerequisiteCheck

if TYPE_CHECKING:
    from collections.abc import Callable

_ProbeValue = TypeVar("_ProbeValue")
MACOS_RUNTIME_PROVIDER = RuntimeProvider(platform="macos", module="appium_connect_agent", required_host="Darwin")
MANAGED_ENSURE_MAC2_PYTHON_COMMAND = (
    "python -c \"from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import Runtime; Runtime(LocalSettings()).ensure_managed_driver('mac2')\""
)


def _fact(
    identifier: str,
    status: str = "ready",
    message: str = "Requirement is ready.",
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


def _prerequisite(identifier: str, ready: bool, message: str, action: str | None = None) -> PlatformPrerequisiteCheck:
    return PlatformPrerequisiteCheck(
        identifier=identifier,
        status="ready" if ready else "unavailable",
        message=message,
        action=None if ready else action,
    )


def _validate_managed_mac2_packages(root: Path, *, node_executable: Path | None = None) -> None:
    appium_pkg_dir = root / "appium" / "node_modules" / "appium"
    _check_safe_owned_dir(appium_pkg_dir)
    appium_pkg = _read_owned_json(appium_pkg_dir / "package.json", max_bytes=MAX_MANAGED_METADATA_BYTES)
    entry_js = appium_pkg_dir / "index.js"
    entry_info = entry_js.lstat()
    if (
        not stat.S_ISREG(entry_info.st_mode)
        or stat.S_ISLNK(entry_info.st_mode)
        or not isinstance(appium_pkg, dict)
        or appium_pkg.get("name") != "appium"
        or appium_pkg.get("version") != MANAGED_APPIUM_VERSION
    ):
        raise ValueError("appium_component_mismatch")

    driver_pkg_dir = root / "driver-home" / "node_modules" / "appium-mac2-driver"
    if not driver_pkg_dir.exists() and not driver_pkg_dir.is_symlink():
        raise FileNotFoundError("mac2_driver_missing")
    _check_safe_owned_dir(driver_pkg_dir)
    driver_pkg = _read_owned_json(driver_pkg_dir / "package.json", max_bytes=MAX_MANAGED_METADATA_BYTES)
    appium_meta = driver_pkg.get("appium") if isinstance(driver_pkg, dict) else None
    version = driver_pkg.get("version") if isinstance(driver_pkg, dict) else None
    peers = driver_pkg.get("peerDependencies") if isinstance(driver_pkg, dict) else None
    peer_appium = peers.get("appium") if isinstance(peers, dict) else None
    if (
        not isinstance(driver_pkg, dict)
        or driver_pkg.get("name") != "appium-mac2-driver"
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(appium_meta, dict)
        or appium_meta.get("driverName") != "mac2"
        or appium_meta.get("automationName") != "Mac2"
        or not isinstance(peer_appium, str)
        or not peer_appium.strip()
    ):
        raise ValueError("mac2_driver_damaged")

    _validate_mac2_peer_compatibility_via_runtime(
        appium_entry_js=entry_js,
        driver_home=root / "driver-home",
        node_executable=node_executable,
    )


def _validate_mac2_peer_compatibility_via_runtime(
    *,
    appium_entry_js: Path,
    driver_home: Path,
    node_executable: Path | None,
) -> None:
    from appium_connect_agent.contracts import LocalSettings
    from appium_connect_agent.runtime import Runtime

    report = Runtime(
        LocalSettings(
            appium_executable=appium_entry_js,
            appium_home=driver_home,
            node_executable=node_executable,
            npm_executable=None,
        )
    ).diagnose(target_driver="mac2")
    checks = {check.check_id: check for check in report.checks}
    appium_check = checks.get("appium_installation")
    if appium_check is None or appium_check.status != "pass":
        raise ValueError("appium_component_mismatch")
    driver_check = checks.get("driver_installation")
    if driver_check is None or driver_check.status != "pass":
        raise ValueError("mac2_driver_damaged")
    compat_check = checks.get("declared_compatibility")
    if compat_check is None:
        raise ValueError("mac2_driver_damaged")
    if any(item.startswith("driver.appium=unsupported_constraint;") for item in compat_check.evidence):
        raise ValueError("mac2_driver_damaged")
    if compat_check.status == "fail":
        raise ValueError("mac2_driver_incompatible")


def _inspect_macos_managed_root(root: Path, *, node_executable: Path | None = None) -> PlatformPrerequisiteCheck:
    if not root.exists() and not root.is_symlink():
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "macos.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 runtime and official Mac2 driver using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND),
        )
    try:
        _check_safe_owned_dir(root, exact_700=True)
        entries = {entry.name for entry in root.iterdir()}
    except (OSError, ValueError):
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root (~/.appium-connect-agent) has unsafe permissions or ownership.",
            "macos.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent permissions/ownership manually before running managed installation.",
        )
    if entries - ALLOWED_MANAGED_ROOT_ENTRIES:
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root contains unrecognized entries.",
            "macos.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually and keep only appium-connect-agent owned artifacts.",
        )
    marker_path = root / "managed.json"
    if not marker_path.exists() and not marker_path.is_symlink():
        if entries:
            return _fact(
                "appium_connect_runtime",
                "error",
                "~/.appium-connect-agent is non-empty without a valid managed ownership marker.",
                "macos.managed_runtime_conflict",
                "Inspect ~/.appium-connect-agent manually before running managed installation.",
            )
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "macos.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 runtime and official Mac2 driver using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND),
        )
    try:
        _validate_managed_marker_and_subdirs(root, marker_path, entries)
    except (OSError, ValueError, UnicodeDecodeError):
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime ownership marker or directory structure is invalid.",
            "macos.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually or repair managed installation.",
        )

    records_dir = root / "records"
    if not records_dir.exists():
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed Appium installation records are incomplete and require recovery.",
            "macos.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND),
        )
    try:
        _validate_managed_records(records_dir)
    except (OSError, ValueError, UnicodeDecodeError):
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime requires cleanup or installation completion before starting a session.",
            "macos.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND),
        )

    try:
        _validate_managed_mac2_packages(root, node_executable=node_executable)
    except FileNotFoundError:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The official Appium Mac2 driver is not installed in ~/.appium-connect-agent.",
            "macos.mac2_driver_missing",
            "Install the managed Mac2 driver using the Python API, then recheck.",
            commands=(MANAGED_ENSURE_MAC2_PYTHON_COMMAND,),
        )
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        if str(exc) == "appium_component_mismatch":
            return _fact(
                "appium_connect_runtime",
                "error",
                "Managed Appium 3.7.0 package under ~/.appium-connect-agent is missing or damaged.",
                "macos.managed_runtime_damaged",
                "Repair the managed Appium runtime via the Python API, then recheck.",
                commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND),
            )
        if str(exc) == "mac2_driver_incompatible":
            return _fact(
                "appium_connect_runtime",
                "error",
                "Installed Appium Mac2 driver under ~/.appium-connect-agent is incompatible with managed Appium 3.7.0.",
                "macos.mac2_driver_incompatible",
                "Reinstall a compatible managed Mac2 driver via the Python API, then recheck.",
                commands=(MANAGED_ENSURE_MAC2_PYTHON_COMMAND,),
            )
        return _fact(
            "appium_connect_runtime",
            "error",
            "Managed Mac2 driver metadata under ~/.appium-connect-agent is missing or damaged.",
            "macos.managed_runtime_damaged",
            "Repair the managed Mac2 driver via the Python API, then recheck.",
            commands=(MANAGED_ENSURE_MAC2_PYTHON_COMMAND,),
        )

    return _fact(
        "appium_connect_runtime",
        message="Managed Appium 3.7.0 + Mac2 runtime and Node.js host prerequisites are ready.",
    )


def _check_macos_appium_connect_runtime() -> PlatformPrerequisiteCheck:
    runtime = MACOS_RUNTIME_PROVIDER.check()
    if not runtime.ready:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The appium-connect-agent dependency is missing from FSQ.",
            "macos.runtime_missing",
            "Reinstall or repair FSQ using the same Python environment.",
        )
    if platform.system() != "Darwin":
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed appium-connect-agent runtime requires a macOS host.",
            "macos.unsupported_host",
            "Run macOS automation on a macOS host.",
        )
    node_executable = _resolve_first_host_executable("node")
    if node_executable is None:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Node.js executable was not found on PATH for managed Appium startup.",
            "macos.node_missing",
            "Install Node.js (^20.19.0 || ^22.12.0 || >=24.0.0) and make node available on PATH.",
        )
    return _inspect_macos_managed_root(_managed_root(), node_executable=node_executable)


def _macos_prerequisites(settings: Any, *, application_pending: bool = False) -> tuple[PlatformPrerequisiteCheck, ...]:
    if platform.system() != "Darwin":
        return tuple(
            PlatformPrerequisiteCheck(
                identifier=name,
                status="not_applicable",
                message="macOS prerequisite checks require a macOS host.",
                action="Run macOS platform tests on a macOS host.",
            )
            for name in (
                "xcode_installation",
                "xcode_developer_directory",
                "appium_connect_runtime",
                "application_path",
                "bundle_identifier",
            )
        )
    errors: dict[str, PlatformPrerequisiteCheck] = {}

    def probe(identifier: str, operation: Callable[[], _ProbeValue]) -> _ProbeValue | None:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - isolate each host probe without exposing its exception.
            errors[identifier] = PlatformPrerequisiteCheck(
                identifier=identifier,
                status="error",
                message=f"The {identifier.replace('_', ' ')} check could not be completed safely.",
                action="Inspect this prerequisite using the macOS platform preparation guide.",
            )
            return None

    developer = probe("xcode_developer_directory", _active_developer_directory)
    developer_ready = bool(developer and probe("xcode_developer_directory", lambda: _developer_directory_is_full_xcode(developer)))
    xcode = developer.parent.parent if developer is not None and developer_ready else probe("xcode_installation", _full_xcode_path)
    runtime_check = probe("appium_connect_runtime", _check_macos_appium_connect_runtime)
    app_path = Path(settings.app_path) if settings.app_path is not None and not application_pending else None
    bundle_id = (settings.bundle_id or "").strip() if not application_pending else ""
    app_ready = bool(app_path and probe("application_path", lambda: _macos_application_path_available(app_path)))
    bundle_ready = bool(bundle_id and probe("bundle_identifier", lambda: _macos_bundle_matches_target(bundle_id, app_path)))

    checks: list[PlatformPrerequisiteCheck] = [
        _prerequisite(
            "xcode_installation",
            xcode is not None,
            "Full Xcode is installed." if xcode else "Full Xcode is unavailable; Command Line Tools alone are insufficient for Appium Mac2.",
            "Install full Xcode from the Mac App Store.",
        ),
        _prerequisite(
            "xcode_developer_directory",
            developer_ready,
            "The active developer directory uses full Xcode." if developer_ready else "The active developer directory uses Command Line Tools or is unavailable.",
            "Run 'sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer' after installing Xcode.",
        ),
        runtime_check
        if runtime_check is not None
        else PlatformPrerequisiteCheck(
            identifier="appium_connect_runtime",
            status="error",
            message="The appium connect runtime check could not be completed safely.",
            action="Inspect this prerequisite using the macOS platform preparation guide.",
        ),
        _prerequisite(
            "application_path",
            app_ready,
            "The configured macOS application path is available." if app_ready else "The configured macOS application path is unavailable or is not an application bundle or executable.",
            "Configure an existing .app bundle or executable path.",
        )
        if app_path is not None
        else PlatformPrerequisiteCheck(
            identifier="application_path",
            status="not_applicable",
            message="No macOS application path is configured; bundle identifier discovery is used.",
        ),
        _prerequisite(
            "bundle_identifier",
            bundle_ready,
            "The configured bundle identifier resolves to the configured application."
            if bundle_ready
            else "The configured bundle identifier is unavailable or does not match the configured application.",
            "Configure the application's exact CFBundleIdentifier.",
        )
        if bundle_id
        else PlatformPrerequisiteCheck(
            identifier="bundle_identifier",
            status="not_applicable",
            message="No bundle identifier is configured; application path identity is used.",
        ),
    ]
    if application_pending:
        checks = [
            PlatformPrerequisiteCheck(
                identifier=check.identifier,
                status="pending",
                code="environment.application_pending",
                message="Application identity validation awaits identification.",
            )
            if check.identifier in {"application_path", "bundle_identifier"}
            else check
            for check in checks
        ]
    commands: dict[str, tuple[str, ...]] = {}
    if xcode == Path("/Applications/Xcode.app"):
        commands["xcode_developer_directory"] = ("sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer",)
    elif not developer_ready and xcode is not None:
        checks[1] = checks[1].model_copy(update={"action": "Use xcode-select --switch with the Contents/Developer directory of your installed Xcode."})

    final_checks: list[PlatformPrerequisiteCheck] = []
    for check in checks:
        if check.identifier in errors:
            final_checks.append(errors[check.identifier])
            continue
        if check.identifier in commands:
            final_checks.append(check.model_copy(update={"commands": commands[check.identifier] if check.status != "ready" else ()}))
            continue
        final_checks.append(check)
    return tuple(final_checks)


def _macos_application_path_available(path: Path) -> bool:
    return (path.is_dir() and path.suffix.casefold() == ".app") or (path.is_file() and os.access(path, os.X_OK))


def _macos_bundle_matches_target(bundle_id: str, app_path: Path | None) -> bool:
    if app_path is None:
        return _macos_bundle_is_installed(bundle_id)
    if app_path.is_file():
        # Executables inside app bundles use that bundle's identity, never another installed app.
        app_path = next((parent for parent in app_path.parents if parent.suffix.casefold() == ".app"), app_path)
    return _macos_bundle_for_app(app_path) == bundle_id


def _full_xcode_path() -> Path | None:
    candidates = [
        Path("/Applications/Xcode.app"),
        *sorted(Path("/Applications").glob("Xcode*.app")),
        *sorted((Path.home() / "Applications").glob("Xcode*.app")),
    ]
    return next((path for path in candidates if (path / "Contents" / "Developer" / "usr" / "bin" / "xcodebuild").is_file()), None)


def _developer_directory_is_full_xcode(path: Path) -> bool:
    return path.name == "Developer" and path.parent.name == "Contents" and path.parent.parent.suffix.casefold() == ".app" and (path / "usr" / "bin" / "xcodebuild").is_file()


def _active_developer_directory(timeout_seconds: float = 5.0) -> Path | None:
    executable = shutil.which("xcode-select") or "/usr/bin/xcode-select"
    try:
        completed = subprocess.run(  # noqa: S603 - resolved system tool with one fixed read-only argument.
            [executable, "-p"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return Path(value) if completed.returncode == 0 and value else None


def _macos_bundle_for_app(app_path: Path) -> str | None:
    try:
        with (app_path / "Contents" / "Info.plist").open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return None
    value = payload.get("CFBundleIdentifier") if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _macos_bundle_is_installed(bundle_id: str, timeout_seconds: float = 5.0) -> bool:
    metadata_query = shutil.which("mdfind")
    if platform.system() != "Darwin" or metadata_query is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - resolved system metadata tool with a read-only exact bundle-id query.
            [metadata_query, f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and any(Path(line).exists() for line in completed.stdout.splitlines() if line.strip())
