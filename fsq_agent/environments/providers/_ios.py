# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import selectors
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from xml.parsers.expat import ExpatError

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
from fsq_agent.environments.providers._macos import (
    _active_developer_directory,
    _developer_directory_is_full_xcode,
    _full_xcode_path,
)
from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import ApplicationCandidate, IOSDevice, IOSDeviceDiscoveryResult, PlatformPrerequisiteCheck

if TYPE_CHECKING:
    from collections.abc import Callable

_ProbeValue = TypeVar("_ProbeValue")
IOS_RUNTIME_PROVIDER = RuntimeProvider(platform="ios", module="appium_connect_agent", required_host="Darwin")
MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND = (
    "python -c \"from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import Runtime; Runtime(LocalSettings()).ensure_managed_driver('xcuitest')\""
)
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_UDID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLIST_MAX_BYTES = 262_144
_SIMCTL_MAX_OUTPUT_BYTES = 65_536
_LISTAPPS_MAX_BYTES = 2 * 1024 * 1024
_LISTAPPS_MAX_CANDIDATES = 512
_APPLE_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*")
_OPENSTEP_DICT = re.compile(rb'\{\s*(?:"(?:[^"\\]|\\.)*"|[A-Za-z0-9._-]+)\s*=')
_SIGNING_TEAM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _fact(
    identifier: str,
    status: str = "ready",
    message: str = "Requirement is ready.",
    code: str | None = None,
    action: str | None = None,
    commands: tuple[str, ...] = (),
    target_id: str | None = None,
) -> PlatformPrerequisiteCheck:
    return PlatformPrerequisiteCheck(
        identifier=identifier,
        status=status,  # type: ignore[arg-type]
        message=message,
        code=code,
        action=action,
        commands=commands,
        target_id=target_id,
    )


def _blocked(identifier: str, dependency: str) -> PlatformPrerequisiteCheck:
    return _fact(
        identifier,
        "not_applicable",
        f"Check blocked by {dependency}.",
        "ios.dependency_unavailable",
        "Resolve the preceding requirement and recheck.",
    )


def discover_ios_devices(timeout_seconds: float = 5.0) -> IOSDeviceDiscoveryResult:
    if platform.system() != "Darwin":
        return IOSDeviceDiscoveryResult(
            error_code="unsupported_host",
            error_message="iOS Simulator and physical device discovery requires a macOS host.",
        )
    runtime = IOS_RUNTIME_PROVIDER.check()
    if not runtime.ready:
        return IOSDeviceDiscoveryResult(
            error_code="runtime_missing",
            error_message="The appium-connect-agent runtime dependency is missing from FSQ.",
        )
    try:
        from appium_connect_agent.automation import list_ios_devices
        from appium_connect_agent.runtime import OperationControl

        safe_timeout = max(0.5, min(float(timeout_seconds), 30.0))
        items = list_ios_devices(control=OperationControl(timeout_seconds=safe_timeout))
    except Exception as exc:  # noqa: BLE001 - normalize SDK discovery failure without leaking host paths
        code = getattr(exc, "code", None)
        if code == "operation_timeout" or isinstance(exc, TimeoutError):
            return IOSDeviceDiscoveryResult(
                error_code="discovery_timeout",
                error_message="iOS device discovery timed out before Xcode tools responded.",
            )
        if code == "xcode_unavailable":
            return IOSDeviceDiscoveryResult(
                error_code="discovery_failed",
                error_message="Active full Xcode developer tools (simctl/xcdevice) are unavailable.",
            )
        return IOSDeviceDiscoveryResult(
            error_code="discovery_failed",
            error_message="iOS device discovery could not be completed safely.",
        )

    devices: list[IOSDevice] = []
    for item in items:
        udid = str(getattr(item, "id", "") or "").strip()
        if not _UDID_PATTERN.fullmatch(udid):
            continue
        name = str(getattr(item, "name", None) or udid).strip()[:256] or udid
        kind = getattr(item, "kind", "simulator")
        if kind not in {"simulator", "device"}:
            continue
        state = getattr(item, "state", "unknown")
        if state not in {"booted", "shutdown", "connected", "unavailable", "unknown"}:
            state = "unknown"
        raw_version = getattr(item, "platform_version", None)
        platform_version = str(raw_version).strip()[:64] if isinstance(raw_version, str) and raw_version.strip() else None
        raw_interface = getattr(item, "interface", None)
        interface = str(raw_interface).strip()[:64] if isinstance(raw_interface, str) and raw_interface.strip() else None
        devices.append(
            IOSDevice(
                udid=udid,
                name=name,
                kind=kind,
                platform_version=platform_version,
                state=state,
                interface=interface,
            )
        )
    return IOSDeviceDiscoveryResult(devices=devices)


def _validate_xcuitest_peer_compatibility_via_runtime(
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
    ).diagnose(target_driver="xcuitest")
    checks = {check.check_id: check for check in report.checks}
    appium_check = checks.get("appium_installation")
    if appium_check is None or appium_check.status != "pass":
        raise ValueError("appium_component_mismatch")
    driver_check = checks.get("driver_installation")
    if driver_check is None or driver_check.status != "pass":
        raise ValueError("xcuitest_driver_damaged")
    compat_check = checks.get("declared_compatibility")
    if compat_check is None:
        raise ValueError("xcuitest_driver_damaged")
    if any(item.startswith("driver.appium=unsupported_constraint;") for item in compat_check.evidence):
        raise ValueError("xcuitest_driver_damaged")
    if compat_check.status == "fail":
        raise ValueError("xcuitest_driver_incompatible")


def _validate_managed_xcuitest_packages(root: Path, *, node_executable: Path | None = None) -> None:
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

    driver_pkg_dir = root / "driver-home" / "node_modules" / "appium-xcuitest-driver"
    if not driver_pkg_dir.exists() and not driver_pkg_dir.is_symlink():
        raise FileNotFoundError("xcuitest_driver_missing")
    _check_safe_owned_dir(driver_pkg_dir)
    driver_pkg = _read_owned_json(driver_pkg_dir / "package.json", max_bytes=MAX_MANAGED_METADATA_BYTES)
    appium_meta = driver_pkg.get("appium") if isinstance(driver_pkg, dict) else None
    version = driver_pkg.get("version") if isinstance(driver_pkg, dict) else None
    peers = driver_pkg.get("peerDependencies") if isinstance(driver_pkg, dict) else None
    peer_appium = peers.get("appium") if isinstance(peers, dict) else None
    if (
        not isinstance(driver_pkg, dict)
        or driver_pkg.get("name") != "appium-xcuitest-driver"
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(appium_meta, dict)
        or appium_meta.get("driverName") != "xcuitest"
        or appium_meta.get("automationName") != "XCUITest"
        or not isinstance(peer_appium, str)
        or not peer_appium.strip()
    ):
        raise ValueError("xcuitest_driver_damaged")

    _validate_xcuitest_peer_compatibility_via_runtime(
        appium_entry_js=entry_js,
        driver_home=root / "driver-home",
        node_executable=node_executable,
    )


def _inspect_ios_managed_root(root: Path, *, node_executable: Path | None = None) -> PlatformPrerequisiteCheck:
    if not root.exists() and not root.is_symlink():
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "ios.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 runtime and official XCUITest driver using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND),
        )
    try:
        _check_safe_owned_dir(root, exact_700=True)
        entries = {entry.name for entry in root.iterdir()}
    except (OSError, ValueError):
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root (~/.appium-connect-agent) has unsafe permissions or ownership.",
            "ios.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent permissions/ownership manually before running managed installation.",
        )
    if entries - ALLOWED_MANAGED_ROOT_ENTRIES:
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root contains unrecognized entries.",
            "ios.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually and keep only appium-connect-agent owned artifacts.",
        )
    marker_path = root / "managed.json"
    if not marker_path.exists() and not marker_path.is_symlink():
        if entries:
            return _fact(
                "appium_connect_runtime",
                "error",
                "~/.appium-connect-agent is non-empty without a valid managed ownership marker.",
                "ios.managed_runtime_conflict",
                "Inspect ~/.appium-connect-agent manually before running managed installation.",
            )
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "ios.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 runtime and official XCUITest driver using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND),
        )
    try:
        _validate_managed_marker_and_subdirs(root, marker_path, entries)
    except (OSError, ValueError, UnicodeDecodeError):
        return _fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime ownership marker or directory structure is invalid.",
            "ios.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually or repair managed installation.",
        )

    records_dir = root / "records"
    if not records_dir.exists():
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed Appium installation records are incomplete and require recovery.",
            "ios.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND),
        )
    try:
        _validate_managed_records(records_dir)
    except (OSError, ValueError, UnicodeDecodeError):
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime requires cleanup or installation completion before starting a session.",
            "ios.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND),
        )

    try:
        _validate_managed_xcuitest_packages(root, node_executable=node_executable)
    except FileNotFoundError:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The official Appium XCUITest driver is not installed in ~/.appium-connect-agent.",
            "ios.xcuitest_driver_missing",
            "Install the managed XCUITest driver using the Python API, then recheck.",
            commands=(MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND,),
        )
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        if str(exc) == "appium_component_mismatch":
            return _fact(
                "appium_connect_runtime",
                "error",
                "Managed Appium 3.7.0 package under ~/.appium-connect-agent is missing or damaged.",
                "ios.managed_runtime_damaged",
                "Repair the managed Appium runtime via the Python API, then recheck.",
                commands=(MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND),
            )
        if str(exc) == "xcuitest_driver_incompatible":
            return _fact(
                "appium_connect_runtime",
                "error",
                "Installed Appium XCUITest driver under ~/.appium-connect-agent is incompatible with managed Appium 3.7.0.",
                "ios.xcuitest_driver_incompatible",
                "Reinstall a compatible managed XCUITest driver via the Python API, then recheck.",
                commands=(MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND,),
            )
        return _fact(
            "appium_connect_runtime",
            "error",
            "Managed XCUITest driver metadata under ~/.appium-connect-agent is missing or damaged.",
            "ios.managed_runtime_damaged",
            "Repair the managed XCUITest driver via the Python API, then recheck.",
            commands=(MANAGED_ENSURE_XCUITEST_PYTHON_COMMAND,),
        )

    return _fact(
        "appium_connect_runtime",
        message="Managed Appium 3.7.0 + XCUITest runtime and Node.js host prerequisites are ready.",
    )


def _check_ios_appium_connect_runtime() -> PlatformPrerequisiteCheck:
    runtime = IOS_RUNTIME_PROVIDER.check()
    if not runtime.ready:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "The appium-connect-agent dependency is missing from FSQ.",
            "ios.runtime_missing",
            "Reinstall or repair FSQ using the same Python environment.",
        )
    if platform.system() != "Darwin":
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed appium-connect-agent iOS runtime requires a macOS host.",
            "ios.unsupported_host",
            "Run iOS automation on a macOS host.",
        )
    node_executable = _resolve_first_host_executable("node")
    if node_executable is None:
        return _fact(
            "appium_connect_runtime",
            "unavailable",
            "Node.js executable was not found on PATH for managed Appium startup.",
            "ios.node_missing",
            "Install Node.js (^20.19.0 || ^22.12.0 || >=24.0.0) and make node available on PATH.",
        )
    return _inspect_ios_managed_root(_managed_root(), node_executable=node_executable)


def _device_is_usable(device: IOSDevice) -> bool:
    return (device.kind == "simulator" and device.state == "booted") or (device.kind == "device" and device.state == "connected")


def _select_ios_device(
    devices: list[IOSDevice],
    configured_udid: str,
    *,
    require_explicit_selection: bool = False,
) -> tuple[IOSDevice | None, PlatformPrerequisiteCheck, PlatformPrerequisiteCheck]:
    usable = [item for item in devices if _device_is_usable(item)]
    if usable:
        connection = _fact(
            "device_connection",
            message=f"{len(usable)} usable iOS target(s) discovered.",
        )
    elif devices:
        states = sorted({f"{item.kind}:{item.state}" for item in devices})
        connection = _fact(
            "device_connection",
            "unavailable",
            f"iOS targets were discovered ({', '.join(states)}), but none is booted or connected.",
            "ios.no_usable_device",
            "Boot an iOS Simulator in Simulator.app (`xcrun simctl boot <UDID>`) or connect and trust an iOS device.",
        )
    else:
        connection = _fact(
            "device_connection",
            "unavailable",
            "No iOS Simulators or connected physical devices were discovered.",
            "ios.no_devices",
            "Create and boot an iOS Simulator in Xcode or connect a physical iOS device.",
        )

    if configured_udid:
        matched = [item for item in devices if item.udid == configured_udid]
        if not matched:
            return (
                None,
                connection,
                _fact(
                    "device_selection",
                    "unavailable",
                    "The configured iOS target UDID is not present in current Simulator or physical device inventory.",
                    "ios.selected_device_missing",
                    "Boot or connect the configured iOS target, or select an available UDID.",
                ),
            )
        usable_match = next((item for item in matched if _device_is_usable(item)), None)
        if usable_match is not None:
            return (
                usable_match,
                connection,
                _fact(
                    "device_selection",
                    message=f"Selected iOS {usable_match.kind} target is ready.",
                    target_id=usable_match.udid,
                ),
            )
        found = matched[0]
        if found.kind == "simulator" and found.state == "shutdown":
            return (
                None,
                connection,
                _fact(
                    "device_selection",
                    "unavailable",
                    "The configured iOS Simulator is currently shut down.",
                    "ios.simulator_shutdown",
                    "Boot the configured iOS Simulator before running checks or automation.",
                ),
            )
        return (
            None,
            connection,
            _fact(
                "device_selection",
                "unavailable",
                f"The configured iOS {found.kind} is currently {found.state}.",
                "ios.selected_device_unavailable",
                "Unlock, trust, or boot the configured iOS target and recheck.",
            ),
        )

    if not usable:
        return None, connection, _blocked("device_selection", "device_connection")
    if len(usable) > 1 or require_explicit_selection:
        return (
            None,
            connection,
            _fact(
                "device_selection",
                "unavailable",
                "Multiple usable iOS targets are active; explicit UDID selection is required." if len(usable) > 1 else "Explicit iOS target UDID selection is required.",
                "ios.multiple_devices" if len(usable) > 1 else "ios.selection_required",
                "Configure `harness.ios.udid` or pass `--udid` to select one target.",
            ),
        )
    sole = usable[0]
    return (
        sole,
        connection,
        _fact(
            "device_selection",
            message=f"Auto-selected sole usable iOS {sole.kind} target.",
            target_id=sole.udid,
        ),
    )


def _read_ios_app_info(app_path: Path) -> dict[str, object] | None:
    plist_path = app_path / "Info.plist"
    if not plist_path.is_file() or plist_path.is_symlink():
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(plist_path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(_PLIST_MAX_BYTES + 1)
        if len(raw) > _PLIST_MAX_BYTES:
            return None
        payload = plistlib.loads(raw, dict_type=_UniquePlistDictionary)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException, ExpatError):
        return None
    return payload if isinstance(payload, dict) else None


def _ios_bundle_id_from_app_dir(app_path: Path) -> str | None:
    metadata = _read_ios_app_info(app_path)
    bundle_id = metadata.get("CFBundleIdentifier") if metadata is not None else None
    return bundle_id.strip() if isinstance(bundle_id, str) and bundle_id.strip() else None


def _ios_bundle_id_from_ipa(ipa_path: Path) -> str | None:
    if not ipa_path.is_file() or ipa_path.is_symlink():
        return None
    try:
        with zipfile.ZipFile(ipa_path, "r") as archive:
            for member in archive.infolist():
                parts = Path(member.filename).parts
                if len(parts) == 3 and parts[0] == "Payload" and parts[1].lower().endswith(".app") and parts[2] == "Info.plist":
                    if member.file_size > _PLIST_MAX_BYTES:
                        return None
                    raw = archive.read(member)
                    payload = plistlib.loads(raw)
                    bundle_id = payload.get("CFBundleIdentifier") if isinstance(payload, dict) else None
                    return bundle_id.strip() if isinstance(bundle_id, str) and bundle_id.strip() else None
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError, plistlib.InvalidFileException):
        return None
    return None


def _inspect_ios_app_path(
    app_path: Path,
    selected_device: IOSDevice | None,
) -> tuple[bool, str | None, str, str, str | None]:
    resolved = app_path.expanduser()
    suffix = resolved.suffix.casefold()
    if suffix == ".app":
        if not resolved.is_dir():
            return (
                False,
                None,
                "The configured iOS .app bundle directory does not exist.",
                "ios.app_path_missing",
                "Configure an existing Simulator .app bundle directory.",
            )
        if selected_device is not None and selected_device.kind == "device":
            return (
                False,
                None,
                "A Simulator .app directory cannot be installed onto a physical iOS device.",
                "ios.app_path_kind_mismatch",
                "Provide a signed .ipa package or installed bundle_id for physical iOS devices.",
            )
        bundle_id = _ios_bundle_id_from_app_dir(resolved)
        if not bundle_id or not _BUNDLE_ID_PATTERN.fullmatch(bundle_id):
            return (
                False,
                None,
                "The configured iOS .app bundle is missing a valid Info.plist CFBundleIdentifier.",
                "ios.app_path_invalid_plist",
                "Rebuild or point to a valid iOS Simulator .app bundle containing Info.plist.",
            )
        return True, bundle_id, "Configured iOS .app bundle is available.", "ios.app_path_ready", None

    if suffix == ".ipa":
        if not resolved.is_file():
            return (
                False,
                None,
                "The configured iOS .ipa archive file does not exist.",
                "ios.app_path_missing",
                "Configure an existing signed .ipa file.",
            )
        if selected_device is not None and selected_device.kind == "simulator":
            return (
                False,
                None,
                "A physical-device .ipa archive cannot be installed onto an iOS Simulator.",
                "ios.app_path_kind_mismatch",
                "Provide a Simulator-built .app bundle directory or installed bundle_id for iOS Simulators.",
            )
        bundle_id = _ios_bundle_id_from_ipa(resolved)
        if not bundle_id or not _BUNDLE_ID_PATTERN.fullmatch(bundle_id):
            return (
                False,
                None,
                "The configured iOS .ipa archive does not contain a valid Payload/*.app/Info.plist CFBundleIdentifier.",
                "ios.app_path_invalid_ipa",
                "Provide a valid iOS .ipa archive containing Payload/<App>.app/Info.plist.",
            )
        return True, bundle_id, "Configured iOS .ipa archive is available.", "ios.app_path_ready", None

    return (
        False,
        None,
        "Configured iOS application path must be a .app bundle directory or .ipa archive.",
        "ios.app_path_invalid_extension",
        "Configure an existing .app directory (Simulator) or .ipa file (physical device).",
    )


def _valid_apple_bundle_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 255 and _APPLE_BUNDLE_ID.fullmatch(value) is not None


def _inventory_device(target_id: str | None, deadline: float) -> IOSDevice:
    if target_id is None or not _UDID_PATTERN.fullmatch(target_id):
        raise ValueError("Invalid iOS device selection.")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    result = discover_ios_devices(remaining)
    if result.error_code == "discovery_timeout":
        raise TimeoutError
    matches = [device for device in result.devices if device.udid == target_id]
    if result.error_code or len(matches) != 1 or not _device_is_usable(matches[0]):
        raise ValueError("Selected iOS device is unavailable.")
    return matches[0]


def _read_bounded_tool(command: list[str], deadline: float, *, data: bytes | None = None) -> bytes:
    output = bytearray()
    with (
        subprocess.Popen(  # noqa: S603 - fixed read-only host tools and validated arguments
            command,
            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process,
        selectors.DefaultSelector() as selector,
    ):
        try:
            if process.stdout is None:
                raise ValueError("Read-only iOS host tool has no output pipe.")
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = memoryview(data or b"")
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                for key, event in selector.select(remaining):
                    if event == selectors.EVENT_WRITE:
                        try:
                            pending = pending[os.write(key.fd, pending) :]
                        except BlockingIOError:
                            continue
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, min(65_536, _LISTAPPS_MAX_BYTES + 1 - len(output)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output) > _LISTAPPS_MAX_BYTES:
                            raise ValueError("iOS application metadata exceeds size limit.")
            if process.wait(timeout=max(0.0, deadline - time.monotonic())) != 0:
                raise ValueError("Read-only iOS host tool failed.")
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError from exc
        finally:
            if process.poll() is None:
                process.kill()
    return bytes(output)


def _simulator_listapps_output(udid: str, deadline: float) -> bytes:
    return _read_bounded_tool(["/usr/bin/xcrun", "simctl", "listapps", udid], deadline)


def _unique_metadata(pairs: list[tuple[str, object]]) -> dict[str, object]:
    metadata = dict(pairs)
    if len(metadata) != len(pairs):
        raise ValueError("Duplicate iOS application metadata key.")
    return metadata


def _reject_json_constant(_value: str) -> object:
    raise ValueError("Invalid iOS application JSON constant.")


class _UniquePlistDictionary(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError("Duplicate iOS application metadata key.")
        super().__setitem__(key, value)


def _parse_simulator_listapps(raw: bytes, deadline: float) -> list[ApplicationCandidate]:
    if len(raw) > _LISTAPPS_MAX_BYTES:
        raise ValueError("iOS application metadata exceeds size limit.")
    try:
        start = raw.lstrip()
        if raw.startswith(b"bplist00") or start.startswith((b"<", b"\xef\xbb\xbf<")):
            payload = plistlib.loads(raw, dict_type=_UniquePlistDictionary)
        elif _OPENSTEP_DICT.match(start):
            # simctl also writes OpenStep property lists; convert in memory
            # with the system plist parser, not by inspecting output paths.
            xml = _read_bounded_tool(["/usr/bin/plutil", "-convert", "xml1", "-o", "-", "--", "-"], deadline, data=raw)
            payload = plistlib.loads(xml, dict_type=_UniquePlistDictionary)
        else:
            payload = json.loads(raw, object_pairs_hook=_unique_metadata, parse_constant=_reject_json_constant)
    except (ValueError, plistlib.InvalidFileException, TypeError, ExpatError) as exc:
        raise ValueError("Invalid iOS application inventory metadata.") from exc
    if not isinstance(payload, dict) or len(payload) > _LISTAPPS_MAX_CANDIDATES:
        raise ValueError("Invalid iOS application inventory shape.")
    candidates = []
    for bundle_id, metadata in payload.items():
        if not _valid_apple_bundle_id(bundle_id) or not isinstance(metadata, dict) or metadata.get("CFBundleIdentifier", bundle_id) != bundle_id:
            raise ValueError("Invalid iOS application inventory entry.")
        names = _application_names(metadata)
        candidates.append(ApplicationCandidate(identity=bundle_id, bundle_id=bundle_id, label=names[0] if names else bundle_id, aliases=names[1:], source="simulator"))
    return sorted(candidates, key=lambda item: (item.label.casefold(), item.identity))


def _application_names(metadata: dict[str, object]) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for key in ("LocalizedDisplayName", "CFBundleDisplayName", "LocalizedName", "CFBundleName"):
        value = metadata.get(key)
        if isinstance(value, str):
            value = value.strip()
            if 0 < len(value) <= 512 and value.isprintable():
                names[value] = None
    return tuple(names)


def _ios_application_inventory(target_id: str | None, deadline: float) -> list[ApplicationCandidate] | None:
    device = _inventory_device(target_id, deadline)
    if device.kind == "device":
        return None  # The current SDK has no read-only physical-device app enumeration.
    return _parse_simulator_listapps(_simulator_listapps_output(device.udid, deadline), deadline)


def _lookup_ios_application(target_id: str | None, reference: str | None, deadline: float) -> list[ApplicationCandidate]:
    if reference is None:
        raise ValueError("A concrete iOS application reference is required.")
    device = _inventory_device(target_id, deadline)
    path = Path(reference)
    if path.is_absolute():
        if device.kind != "simulator" or path.suffix.casefold() != ".app":
            raise ValueError("Only local Simulator application bundles are supported.")
        if not path.is_dir():
            return []
        path = path.resolve(strict=True)
        if path.suffix.casefold() != ".app":
            raise ValueError("An application bundle directory is required.")
        metadata = _read_ios_app_info(path)
        bundle_id = metadata.get("CFBundleIdentifier") if metadata is not None else None
        if metadata is None or not isinstance(bundle_id, str) or not _valid_apple_bundle_id(bundle_id):
            raise ValueError("Invalid iOS application bundle metadata.")
        names = _application_names(metadata)
        return [ApplicationCandidate(identity=str(path), app_path=path, bundle_id=bundle_id, label=names[0] if names else bundle_id, aliases=names[1:], source="simulator_app_bundle")]
    if not _valid_apple_bundle_id(reference):
        raise ValueError("Invalid iOS bundle reference.")
    if device.kind == "device":
        return [ApplicationCandidate(identity=reference, bundle_id=reference, label=reference, source="physical_installation_unverified")]
    installed = _parse_simulator_listapps(_simulator_listapps_output(device.udid, deadline), deadline)
    return [candidate for candidate in installed if candidate.bundle_id == reference]


def _simulator_app_installation_status(
    developer_dir: Path | None,
    udid: str,
    bundle_id: str,
    *,
    timeout_seconds: float = 5.0,
) -> tuple[str, str, str | None, str | None]:
    simctl_candidates: list[str] = []
    if developer_dir is not None:
        dev_simctl = developer_dir / "usr" / "bin" / "simctl"
        if dev_simctl.is_file() and os.access(dev_simctl, os.X_OK):
            simctl_candidates.append(str(dev_simctl))
    xcrun_bin = shutil.which("xcrun") or "/usr/bin/xcrun"
    cmd = [simctl_candidates[0], "appinfo", udid, bundle_id] if simctl_candidates else [xcrun_bin, "simctl", "appinfo", udid, bundle_id]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed read-only simctl appinfo query with validated UDID and bundle_id
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            "error",
            "Read-only Simulator application lookup timed out.",
            "ios.app_lookup_timeout",
            "Verify that CoreSimulator is responsive and recheck.",
        )
    except OSError:
        return (
            "error",
            "Read-only Simulator application lookup could not be executed.",
            "ios.app_lookup_failed",
            "Verify Xcode Simulator command-line tools (`simctl`) and recheck.",
        )

    stdout = (completed.stdout or "")[:_SIMCTL_MAX_OUTPUT_BYTES]
    stderr = (completed.stderr or "")[:_SIMCTL_MAX_OUTPUT_BYTES]
    combined = f"{stdout}\n{stderr}".lower()
    if completed.returncode == 0 and ("cfbundleidentifier" in combined or "bundle" in combined):
        return (
            "ready",
            "The configured bundle identifier is installed on the selected booted iOS Simulator.",
            None,
            None,
        )
    if "no application record found" in combined or "application not found" in combined or "code=3" in combined:
        return (
            "unavailable",
            "The configured bundle identifier is not installed on the selected booted iOS Simulator.",
            "ios.app_not_installed",
            "Install the app onto the Simulator (`xcrun simctl install <UDID> <PathToApp>`) or configure `harness.ios.app_path`.",
        )
    return (
        "error",
        "Simulator application installation state could not be verified safely.",
        "ios.app_lookup_failed",
        "Verify that the selected iOS Simulator is booted and responsive, then recheck.",
    )


def _wda_signing_prerequisite(settings: Any, device: IOSDevice | None) -> PlatformPrerequisiteCheck:
    if device is None:
        return _blocked("wda_signing", "device_selection")
    if device.kind == "simulator":
        return _fact("wda_signing", "not_applicable", "A Simulator does not require physical-device WDA signing configuration.")
    team = getattr(settings, "xcode_org_id", None)
    identity = getattr(settings, "xcode_signing_id", None)
    wda_bundle = getattr(settings, "updated_wda_bundle_id", None)
    if not (
        isinstance(team, str)
        and _SIGNING_TEAM_ID.fullmatch(team)
        and isinstance(identity, str)
        and 0 < len(identity) <= 128
        and identity.strip()
        and identity.isascii()
        and identity.isprintable()
        and (wda_bundle is None or _valid_apple_bundle_id(wda_bundle))
    ):
        return _fact(
            "wda_signing",
            "unavailable",
            "Physical-device WDA signing configuration is missing or invalid.",
            "ios.wda_signing_invalid",
            "Configure a valid Xcode team ID and signing identity in the local iOS Workspace. If supplied, use a valid WDA bundle identifier.",
        )
    return _fact("wda_signing", message="Physical-device WDA signing is configured. Certificates, provisioning, and WDA startup are not verified here.")


def ios_prerequisites(
    settings: Any,
    *,
    application_pending: bool = False,
    require_explicit_selection: bool = False,
) -> tuple[PlatformPrerequisiteCheck, ...]:
    ordered_ids = (
        "xcode_installation",
        "xcode_developer_directory",
        "appium_connect_runtime",
        "device_connection",
        "device_selection",
        "wda_signing",
        "application_identifier",
        "application_path",
        "application_installation",
    )
    if platform.system() != "Darwin":
        return tuple(
            _fact(
                identifier,
                "not_applicable",
                "iOS prerequisite checks require a macOS host.",
                "ios.unsupported_host",
                "Run iOS platform tests on a macOS host.",
            )
            for identifier in ordered_ids
        )

    errors: dict[str, PlatformPrerequisiteCheck] = {}

    def probe(identifier: str, operation: Callable[[], _ProbeValue]) -> _ProbeValue | None:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - isolate each host probe safely
            errors[identifier] = _fact(
                identifier,
                "error",
                f"The {identifier.replace('_', ' ')} check could not be completed safely.",
                "ios.probe_error",
                "Inspect this prerequisite using the iOS platform preparation guidance.",
            )
            return None

    developer = probe("xcode_developer_directory", _active_developer_directory)
    developer_ready = bool(developer and probe("xcode_developer_directory", lambda: _developer_directory_is_full_xcode(developer)))
    xcode = developer.parent.parent if developer is not None and developer_ready else probe("xcode_installation", _full_xcode_path)
    runtime_check = probe("appium_connect_runtime", _check_ios_appium_connect_runtime)

    xcode_check = errors.get("xcode_installation") or _fact(
        "xcode_installation",
        "ready" if xcode is not None else "unavailable",
        "Full Xcode is installed." if xcode is not None else "Full Xcode is unavailable; Command Line Tools alone are insufficient for iOS XCUITest.",
        None if xcode is not None else "ios.xcode_missing",
        None if xcode is not None else "Install full Xcode from the Mac App Store.",
    )

    dev_commands: tuple[str, ...] = ()
    dev_action: str | None = None
    if not developer_ready:
        if xcode == Path("/Applications/Xcode.app"):
            dev_commands = ("sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer",)
            dev_action = "Run 'sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer' after installing Xcode."
        elif xcode is not None:
            dev_action = "Use xcode-select --switch with the Contents/Developer directory of your installed Xcode."
        else:
            dev_action = "Install full Xcode and switch xcode-select to its Contents/Developer directory."
    developer_check = errors.get("xcode_developer_directory") or _fact(
        "xcode_developer_directory",
        "ready" if developer_ready else "unavailable",
        "The active developer directory uses full Xcode." if developer_ready else "The active developer directory uses Command Line Tools or is unavailable.",
        None if developer_ready else "ios.developer_dir_invalid",
        dev_action,
        commands=dev_commands,
    )

    resolved_runtime_check = errors.get("appium_connect_runtime") or (
        runtime_check
        if runtime_check is not None
        else _fact(
            "appium_connect_runtime",
            "error",
            "The appium connect runtime check could not be completed safely.",
            "ios.probe_error",
            "Inspect ~/.appium-connect-agent and Python environment readiness.",
        )
    )

    configured_udid = (getattr(settings, "udid", None) or "").strip()
    selected_device: IOSDevice | None = None
    if configured_udid and not _UDID_PATTERN.fullmatch(configured_udid):
        connection_check = _fact(
            "device_connection",
            "not_applicable",
            "Check blocked by invalid configured UDID.",
            "ios.dependency_unavailable",
            "Repair `harness.ios.udid` and recheck.",
        )
        selection_check = _fact(
            "device_selection",
            "unavailable",
            "Configured iOS UDID format is invalid.",
            "ios.invalid_udid",
            "Configure a valid iOS Simulator or physical device UDID without whitespace.",
        )
    elif not developer_ready:
        connection_check = _blocked("device_connection", "xcode_developer_directory")
        selection_check = _blocked("device_selection", "xcode_developer_directory")
    else:
        discovery = probe("device_connection", discover_ios_devices)
        if discovery is None:
            connection_check = errors["device_connection"]
            selection_check = _blocked("device_selection", "device_connection")
        elif discovery.error_code is not None:
            status = "unavailable" if discovery.error_code == "runtime_missing" else "error"
            connection_check = _fact(
                "device_connection",
                status,
                discovery.error_message or "iOS device discovery failed.",
                f"ios.{discovery.error_code}",
                "Verify full Xcode tools (`simctl` and `xcdevice`) and appium-connect-agent installation.",
            )
            selection_check = _blocked("device_selection", "device_connection")
        else:
            selected_device, connection_check, selection_check = _select_ios_device(
                discovery.devices,
                configured_udid,
                require_explicit_selection=require_explicit_selection,
            )

    signing_check = _wda_signing_prerequisite(settings, selected_device)

    if application_pending:
        pending_fact = lambda ident: _fact(  # noqa: E731
            ident,
            "pending",
            "Application identity validation awaits identification.",
            "environment.application_pending",
        )
        return (
            xcode_check,
            developer_check,
            resolved_runtime_check,
            connection_check,
            selection_check,
            signing_check,
            pending_fact("application_identifier"),
            pending_fact("application_path"),
            pending_fact("application_installation"),
        )

    bundle_id = (getattr(settings, "bundle_id", None) or "").strip()
    raw_app_path = getattr(settings, "app_path", None)
    app_path = Path(raw_app_path) if raw_app_path is not None else None

    path_bundle_id: str | None = None
    if app_path is not None:
        inspected = probe("application_path", lambda: _inspect_ios_app_path(app_path, selected_device))
        if inspected is None:
            app_path_check = errors["application_path"]
        else:
            ok, path_bundle_id, msg, code, action = inspected
            app_path_check = _fact(
                "application_path",
                "ready" if ok else "unavailable",
                msg,
                None if ok else code,
                action,
            )
    else:
        app_path_check = _fact(
            "application_path",
            "not_applicable",
            "No iOS application bundle path is configured; bundle identifier target is used.",
        )

    if bundle_id:
        if not _BUNDLE_ID_PATTERN.fullmatch(bundle_id):
            app_id_check = _fact(
                "application_identifier",
                "unavailable",
                "Configured iOS bundle identifier is invalid.",
                "ios.invalid_bundle_id",
                "Configure a valid reverse-DNS CFBundleIdentifier such as com.example.app.",
            )
        elif path_bundle_id is not None and path_bundle_id != bundle_id:
            app_id_check = _fact(
                "application_identifier",
                "unavailable",
                "Configured bundle_id does not match CFBundleIdentifier inside the configured application bundle.",
                "ios.bundle_id_mismatch",
                "Align `harness.ios.bundle_id` with the bundle's Info.plist CFBundleIdentifier or clear one of them.",
            )
        else:
            app_id_check = _fact(
                "application_identifier",
                message="Configured iOS bundle identifier is valid.",
            )
    elif app_path is not None:
        if app_path_check.status == "ready" and path_bundle_id:
            app_id_check = _fact(
                "application_identifier",
                "not_applicable",
                "No explicit bundle_id is configured; CFBundleIdentifier from application_path is used.",
            )
        else:
            app_id_check = _blocked("application_identifier", "application_path")
    else:
        app_id_check = _fact(
            "application_identifier",
            "unavailable",
            "Neither `bundle_id` nor `app_path` is configured for the iOS target.",
            "ios.application_unconfigured",
            "Configure `harness.ios.bundle_id` or `harness.ios.app_path`.",
        )

    if app_path is not None:
        if app_path_check.status != "ready":
            installation_check = _blocked("application_installation", "application_path")
        else:
            installation_check = _fact(
                "application_installation",
                "not_applicable",
                "Explicit application bundle path is configured; installation or launch occurs at XCUITest session execution.",
            )
    elif app_id_check.status != "ready":
        installation_check = _blocked("application_installation", "application_identifier")
    elif selection_check.status != "ready" or selected_device is None:
        installation_check = _blocked("application_installation", "device_selection")
    elif selected_device.kind == "simulator":
        sim_install = probe(
            "application_installation",
            lambda: _simulator_app_installation_status(developer, selected_device.udid, bundle_id),
        )
        if sim_install is None:
            installation_check = errors["application_installation"]
        else:
            inst_status, inst_msg, inst_code, inst_action = sim_install
            installation_check = _fact(
                "application_installation",
                inst_status,
                inst_msg,
                inst_code,
                inst_action,
            )
    else:
        installation_check = _fact(
            "application_installation",
            "not_applicable",
            "Physical iOS device app installation is unverified until XCUITest session startup.",
        )

    return (
        xcode_check,
        developer_check,
        resolved_runtime_check,
        connection_check,
        selection_check,
        signing_check,
        app_id_check,
        app_path_check,
        installation_check,
    )
