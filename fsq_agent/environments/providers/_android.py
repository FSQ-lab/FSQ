# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from fsq_agent.environments.providers._adb_transport import endpoint, inventory, package_status, valid_package
from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import AndroidDeviceDiscoveryResult, PlatformPrerequisiteCheck

ANDROID_RUNTIME_PROVIDER = RuntimeProvider(platform="android", module="appium_connect_agent")
MANAGED_COMBINATION_ID = "macos-android-uiautomator2"
MANAGED_APPIUM_VERSION = "3.7.0"
MANAGED_DRIVER_VERSION = "8.7.0"
MAX_MANAGED_RECORD_BYTES = 64 * 1024
MAX_MANAGED_METADATA_BYTES = 1024 * 1024
ALLOWED_MANAGED_ROOT_ENTRIES = frozenset({"managed.json", "appium", "driver-home", "records", "cache", "tmp"})
SAFE_BASE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
MANAGED_INSTALL_PYTHON_COMMAND = 'python -c "from appium_connect_agent.runtime import InstallRequest, Runtime; Runtime().install(InstallRequest())"'
MANAGED_CLEANUP_PYTHON_COMMAND = 'python -c "from appium_connect_agent.runtime import Runtime; Runtime().cleanup_installation()"'
ERRORS = {
    "adb_missing": ("ADB is unavailable.", "Install Android SDK Platform-Tools and make adb available on PATH."),
    "adb_server_unavailable": ("The existing ADB server is unavailable.", "Start ADB manually at the configured local endpoint, then recheck."),
    "adb_timeout": ("ADB diagnosis timed out.", "Check the local ADB server and device connection, then recheck."),
    "adb_endpoint_invalid": (
        "The ADB endpoint configuration is unsupported or invalid.",
        "Use loopback ANDROID_ADB_SERVER_HOST and valid ANDROID_ADB_SERVER_PORT; remove conflicting ADB_SERVER_SOCKET or ADB_SERVER_PORT overrides.",
    ),
    "adb_failed": ("ADB returned an invalid or failed diagnostic response.", "Inspect the existing ADB server and device manually, then recheck."),
}


def discover_android_devices(timeout_seconds: float = 5.0) -> AndroidDeviceDiscoveryResult:
    if shutil.which("adb") is None:
        code = "adb_missing"
    else:
        try:
            return AndroidDeviceDiscoveryResult(devices=inventory(time.monotonic() + min(max(timeout_seconds, 0.01), 10.0)))
        except TimeoutError:
            code = "adb_timeout"
        except OSError:
            code = "adb_server_unavailable"
        except (ValueError, UnicodeError) as exc:
            code = "adb_endpoint_invalid" if str(exc) == "adb_endpoint_invalid" else "adb_failed"
    return AndroidDeviceDiscoveryResult(error_code=code, error_message=ERRORS[code][0])


def fact(identifier, status="ready", message="Requirement is ready.", code=None, action=None, commands=(), target_id=None):
    return PlatformPrerequisiteCheck(identifier=identifier, status=status, message=message, code=code, action=action, commands=commands, target_id=target_id)


def blocked(identifier, dependency):
    return fact(identifier, "not_applicable", f"Check blocked by {dependency}.", "android.dependency_unavailable", "Resolve the preceding requirement and recheck.")


def _managed_root() -> Path:
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".appium-connect-agent"
    except (ImportError, KeyError, AttributeError):
        return Path.home() / ".appium-connect-agent"


def _resolve_first_host_executable(name: str) -> Path | None:
    raw_path = os.environ.get("PATH", "")
    for entry in raw_path.split(os.pathsep)[:64]:
        if not entry:
            continue
        candidate_dir = Path(entry)
        if not candidate_dir.is_absolute():
            continue
        candidate = candidate_dir / name
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        except OSError:
            continue
    return None


def _resolve_android_sdk_root() -> Path | None:
    for env_key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            candidate = Path(raw)
            if candidate.is_absolute() and (candidate / "platform-tools" / "adb").is_file() and (candidate / "platform-tools" / "source.properties").is_file():
                return candidate
    adb_executable = _resolve_first_host_executable("adb")
    if adb_executable is not None and adb_executable.parent.name == "platform-tools":
        sdk_candidate = adb_executable.parent.parent
        if sdk_candidate.is_absolute() and (sdk_candidate / "platform-tools" / "source.properties").is_file():
            return sdk_candidate
    default_mac_sdk = Path.home() / "Library" / "Android" / "sdk"
    if default_mac_sdk.is_absolute() and (default_mac_sdk / "platform-tools" / "adb").is_file() and (default_mac_sdk / "platform-tools" / "source.properties").is_file():
        return default_mac_sdk
    return None


def _resolve_java_home() -> Path | None:
    raw = os.environ.get("JAVA_HOME", "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file() and (candidate / "release").is_file():
            return candidate
    jvm_dir = Path("/Library/Java/JavaVirtualMachines")
    try:
        entries = sorted(jvm_dir.iterdir(), reverse=True) if jvm_dir.is_dir() else []
    except OSError:
        entries = []
    for entry in entries:
        candidate = entry / "Contents" / "Home"
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file() and (candidate / "release").is_file():
            return candidate
    for candidate in (
        Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
        Path("/usr/local/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
    ):
        if candidate.is_absolute() and (candidate / "bin" / "java").is_file() and (candidate / "release").is_file():
            return candidate
    return None


def _read_owned_json(path: Path, *, max_bytes: int, mode_mask: int = 0o022) -> Any:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
        raise ValueError("untrusted_file")
    if hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & mode_mask):
        raise ValueError("untrusted_file")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("untrusted_file")
    return json.loads(raw.decode("utf-8"))


def _check_safe_owned_dir(path: Path, *, exact_700: bool = False) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("untrusted_dir")
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("untrusted_dir")
        if exact_700 and stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("untrusted_dir")


def _validate_managed_marker_and_subdirs(root: Path, marker_path: Path, entries: set[str]) -> None:
    marker = _read_owned_json(marker_path, max_bytes=MAX_MANAGED_RECORD_BYTES)
    if marker != {"owner": "appium-connect-agent", "schema": 1}:
        raise ValueError("marker_mismatch")
    for dirname in entries - {"managed.json"}:
        _check_safe_owned_dir(root / dirname)


def _is_valid_operation_id(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(32 < ord(ch) < 127 for ch in value)


def _is_valid_normalized_base_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value:
        return True
    if any(ord(ch) <= 32 or ord(ch) >= 127 for ch in value) or any(ch in value for ch in "\\%?#") or "//" in value or not value.startswith("/") or value.endswith("/"):
        return False
    return all(segment not in {".", ".."} and SAFE_BASE_PATH_SEGMENT.fullmatch(segment) is not None for segment in value[1:].split("/"))


def _validate_managed_records(records_dir: Path) -> None:
    record_names = {entry.name for entry in records_dir.iterdir()}
    if "installation.json" not in record_names or record_names - {"installation.json", "service.json", "maintenance.json"}:
        raise ValueError("records_invalid")

    install_record = _read_owned_json(records_dir / "installation.json", max_bytes=MAX_MANAGED_RECORD_BYTES, mode_mask=0o022)
    if (
        not isinstance(install_record, dict)
        or set(install_record) != {"schema", "state", "operation_id", "combination_id", "changed"}
        or type(install_record["schema"]) is not int
        or install_record["schema"] != 1
        or install_record["state"] != "complete"
        or install_record["combination_id"] != MANAGED_COMBINATION_ID
        or not _is_valid_operation_id(install_record["operation_id"])
        or type(install_record["changed"]) is not bool
    ):
        raise ValueError("install_incomplete")

    if "service.json" in record_names:
        service_record = _read_owned_json(records_dir / "service.json", max_bytes=MAX_MANAGED_RECORD_BYTES, mode_mask=0o077)
        if (
            not isinstance(service_record, dict)
            or set(service_record) != {"schema", "state", "operation_id", "host", "port", "base_path"}
            or type(service_record["schema"]) is not int
            or service_record["schema"] != 1
            or service_record["state"] != "stopped"
            or not _is_valid_operation_id(service_record["operation_id"])
            or not isinstance(service_record["host"], str)
            or service_record["host"] not in {"127.0.0.1", "::1"}
            or type(service_record["port"]) is not int
            or not (1 <= service_record["port"] <= 65535)
            or not _is_valid_normalized_base_path(service_record["base_path"])
            or len(f"http://{'[::1]' if service_record['host'] == '::1' else service_record['host']}:{service_record['port']}{service_record['base_path']}") > 2048
        ):
            raise ValueError("service_not_stopped")

    if "maintenance.json" in record_names:
        maint_record = _read_owned_json(records_dir / "maintenance.json", max_bytes=MAX_MANAGED_RECORD_BYTES, mode_mask=0o077)
        if (
            not isinstance(maint_record, dict)
            or set(maint_record) != {"schema", "state", "operation_id", "operation", "driver", "may_have_changed"}
            or type(maint_record["schema"]) is not int
            or maint_record["schema"] != 1
            or maint_record["state"] not in {"complete", "failed"}
            or not _is_valid_operation_id(maint_record["operation_id"])
            or maint_record["operation"] not in {"driver_install", "driver_doctor"}
            or maint_record["driver"] not in {"uiautomator2", "xcuitest", "mac2"}
            or type(maint_record["may_have_changed"]) is not bool
        ):
            raise ValueError("maintenance_not_settled")


def _validate_managed_packages(root: Path) -> None:
    appium_pkg_dir = root / "appium" / "node_modules" / "appium"
    driver_pkg_dir = root / "driver-home" / "node_modules" / "appium-uiautomator2-driver"
    _check_safe_owned_dir(appium_pkg_dir)
    _check_safe_owned_dir(driver_pkg_dir)
    appium_pkg = _read_owned_json(appium_pkg_dir / "package.json", max_bytes=MAX_MANAGED_METADATA_BYTES)
    driver_pkg = _read_owned_json(driver_pkg_dir / "package.json", max_bytes=MAX_MANAGED_METADATA_BYTES)
    entry_js = appium_pkg_dir / "index.js"
    entry_info = entry_js.lstat()
    if (
        not stat.S_ISREG(entry_info.st_mode)
        or stat.S_ISLNK(entry_info.st_mode)
        or not isinstance(appium_pkg, dict)
        or appium_pkg.get("name") != "appium"
        or appium_pkg.get("version") != MANAGED_APPIUM_VERSION
        or not isinstance(driver_pkg, dict)
        or driver_pkg.get("name") != "appium-uiautomator2-driver"
        or driver_pkg.get("version") != MANAGED_DRIVER_VERSION
    ):
        raise ValueError("component_mismatch")


def _inspect_managed_root(root: Path) -> PlatformPrerequisiteCheck:
    if not root.exists() and not root.is_symlink():
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "android.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 + UiAutomator2 8.7.0 runtime using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND,),
        )
    try:
        _check_safe_owned_dir(root, exact_700=True)
        entries = {entry.name for entry in root.iterdir()}
    except (OSError, ValueError):
        return fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root (~/.appium-connect-agent) has unsafe permissions or ownership.",
            "android.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent permissions/ownership manually before running managed installation.",
        )
    if entries - ALLOWED_MANAGED_ROOT_ENTRIES:
        return fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime root contains unrecognized entries.",
            "android.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually and keep only appium-connect-agent owned artifacts.",
        )
    marker_path = root / "managed.json"
    if not marker_path.exists() and not marker_path.is_symlink():
        if entries:
            return fact(
                "appium_connect_runtime",
                "error",
                "~/.appium-connect-agent is non-empty without a valid managed ownership marker.",
                "android.managed_runtime_conflict",
                "Inspect ~/.appium-connect-agent manually before running managed installation.",
            )
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
            "android.managed_runtime_uninitialized",
            "Initialize the managed Appium 3.7.0 + UiAutomator2 8.7.0 runtime using the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND,),
        )
    try:
        _validate_managed_marker_and_subdirs(root, marker_path, entries)
    except (OSError, ValueError, UnicodeDecodeError):
        return fact(
            "appium_connect_runtime",
            "error",
            "The managed Appium runtime ownership marker or directory structure is invalid.",
            "android.managed_runtime_conflict",
            "Inspect ~/.appium-connect-agent manually or repair managed installation.",
        )

    records_dir = root / "records"
    if not records_dir.exists():
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed Appium installation records are incomplete and require recovery.",
            "android.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND),
        )
    try:
        _validate_managed_records(records_dir)
    except (OSError, ValueError, UnicodeDecodeError):
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "The managed Appium runtime requires cleanup or installation completion before starting a session.",
            "android.managed_runtime_recovery_required",
            "Run managed cleanup and installation via the Python API, then recheck.",
            commands=(MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND),
        )

    try:
        _validate_managed_packages(root)
    except (OSError, ValueError, UnicodeDecodeError):
        return fact(
            "appium_connect_runtime",
            "error",
            "Managed Appium 3.7.0 or UiAutomator2 8.7.0 packages under ~/.appium-connect-agent are missing or damaged.",
            "android.managed_runtime_damaged",
            "Repair the managed Appium runtime via the Python API, then recheck.",
            commands=(MANAGED_INSTALL_PYTHON_COMMAND,),
        )

    return fact(
        "appium_connect_runtime",
        message="Managed Appium 3.7.0 + UiAutomator2 8.7.0 runtime and host prerequisites are ready.",
    )


def _check_appium_connect_runtime() -> PlatformPrerequisiteCheck:
    runtime = ANDROID_RUNTIME_PROVIDER.check()
    if not runtime.ready:
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "The appium-connect-agent dependency is missing from FSQ.",
            "android.runtime_missing",
            "Reinstall or repair FSQ using the same Python environment.",
        )
    if platform.system() != "Darwin":
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "Managed appium-connect-agent runtime currently requires a macOS host.",
            "android.unsupported_host",
            "Run Android automation on a supported macOS host.",
        )

    node_executable = _resolve_first_host_executable("node")
    if node_executable is None:
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "Node.js executable was not found on PATH for managed Appium startup.",
            "android.node_missing",
            "Install Node.js (^20.19.0 || ^22.12.0 || >=24.0.0) and make node available on PATH.",
        )

    java_home = _resolve_java_home()
    if java_home is None:
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "Java JDK layout (JAVA_HOME with bin/java and release) is unavailable for UiAutomator2.",
            "android.java_missing",
            "Install a JDK and set JAVA_HOME to its root directory.",
        )

    android_sdk_root = _resolve_android_sdk_root()
    if android_sdk_root is None:
        return fact(
            "appium_connect_runtime",
            "unavailable",
            "Android SDK root layout (platform-tools/adb and source.properties) is unavailable.",
            "android.sdk_missing",
            "Set ANDROID_HOME or ANDROID_SDK_ROOT to a valid Android SDK installation.",
        )

    return _inspect_managed_root(_managed_root())


def _selection(devices, serial, *, require_explicit_selection=False):
    online = [d for d in devices if d.state == "device"]
    selected = next((d for d in devices if d.serial == serial), None) if serial else online[0] if len(online) == 1 and not require_explicit_selection else None
    connection = fact("device_connection", message="An online authorized device is connected.")
    selection = fact("device_selection", message="The exact device selection is available.", target_id=selected.serial if selected else serial or None)
    if serial and selected is None:
        connection = fact(
            "device_connection", "unavailable", "The selected Android device is no longer connected.", "android.selected_device_missing", "Reconnect that device or explicitly select another device."
        )
    elif selected and selected.state != "device":
        state = selected.state if selected.state in {"unauthorized", "offline"} else "unavailable"
        action = "Unlock the device and manually accept USB debugging authorization." if state == "unauthorized" else "Check the USB/network connection and device state manually."
        connection = fact("device_connection", "unavailable", f"The selected device is {state}.", f"android.device_{state}", action)
    elif not online:
        state = devices[0].state if len(devices) == 1 else "unavailable"
        state = state if state in {"unauthorized", "offline"} else "unavailable"
        code = f"android.device_{state}" if devices else "android.no_device"
        connection = fact(
            "device_connection", "unavailable", f"No authorized online Android device is available ({state}).", code, "Connect a device, enable USB debugging and manually authorize this computer."
        )
    if connection.status != "ready":
        selection = blocked("device_selection", "device connection/authorization").model_copy(update={"target_id": serial or None})
    elif selected is None:
        selection = fact(
            "device_selection",
            "unavailable",
            "Select an online Android device explicitly." if require_explicit_selection else "Multiple online Android devices require explicit selection.",
            "android.selection_required",
            "Select a device in Control Plane, or make the connection unambiguous before running CLI Doctor.",
        )
    return selected, connection, selection


def _installation(serial, app_id):
    status = package_status(serial, app_id)
    if status == "installed":
        return fact("application_installation", message="The configured application is installed on the selected device.")
    if status == "absent":
        return fact(
            "application_installation",
            "unavailable",
            "The application is not installed on the selected device.",
            "android.application_missing",
            "Install the intended application manually or repair its configured package ID.",
        )
    return fact(
        "application_installation",
        "error",
        "The application query timed out." if status == "timeout" else "Application installation could not be checked.",
        "android.application_timeout" if status == "timeout" else "android.application_query_failed",
        "Inspect the selected device and package manager, then recheck; this does not prove the app is missing.",
    )


def android_prerequisites(settings, *, application_pending=False, require_explicit_selection=False):
    adb = fact("adb_cli", message="ADB is available on PATH.") if shutil.which("adb") else fact("adb_cli", "unavailable", ERRORS["adb_missing"][0], "android.adb_missing", ERRORS["adb_missing"][1])
    try:
        dependency = _check_appium_connect_runtime()
    except Exception:  # noqa: BLE001 -- isolate independent dependency inspection.
        dependency = fact("appium_connect_runtime", "error", "The managed Android runtime check failed.", "android.runtime_query_failed", "Inspect the FSQ Python and managed Appium environment.")
    app_id = settings.app_id or ""
    app = (
        fact("application_identifier")
        if valid_package(app_id)
        else fact(
            "application_identifier", "error", "The Android application ID is invalid.", "android.application_identifier_invalid", "Edit the Android target configuration with a valid package ID."
        )
    )
    if application_pending:
        app = fact("application_identifier", "pending", "Application identification is pending.", "environment.application_pending")
    discovery = discover_android_devices()
    if discovery.error_code:
        code = discovery.error_code
        message, action = ERRORS.get(code, ERRORS["adb_failed"])
        commands = ("adb start-server",) if code == "adb_server_unavailable" and endpoint() == ("127.0.0.1", 5037) else ()
        server = (
            blocked("adb_server", "ADB CLI")
            if code == "adb_missing"
            else fact("adb_server", "error" if code in {"adb_failed", "adb_timeout", "adb_endpoint_invalid"} else "unavailable", message, f"android.{code}", action, commands)
        )
        return (adb, dependency, server, blocked("device_connection", "ADB server"), blocked("device_selection", "device discovery"), app, blocked("application_installation", "device selection"))
    server = fact("adb_server", message="Existing ADB server answered without auto-start.")
    selected, connection, selection = _selection(discovery.devices, settings.serial or "", require_explicit_selection=require_explicit_selection)
    installation = _installation(selected.serial, app_id) if selection.status == "ready" and app.status == "ready" else blocked("application_installation", "device selection or application ID")
    if application_pending and selection.status == "ready":
        installation = fact("application_installation", "pending", "Application installation validation awaits identification.", "environment.application_pending")
    return (adb, dependency, server, connection, selection, app, installation)
