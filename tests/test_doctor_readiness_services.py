# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.agent import check_dynamic_agent_readiness
from fsq_agent.ai_services import CaseSuggestionAnalyzer, check_case_suggestion_readiness
from fsq_agent.config import Settings
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.providers import check_provider_readiness


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close_sync(self) -> None:
        self.closed = True

    def complete_sync(self, request):
        raise AssertionError("Doctor readiness must not send model inference")


def test_provider_readiness_constructs_and_closes_without_inference(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr("fsq_agent.providers._factory.prepare_model_provider_session", lambda _settings: session)

    readiness = check_provider_readiness(object())

    assert readiness.ready is True
    assert readiness.session_status == "session_available"
    assert session.closed is True


@pytest.mark.parametrize("effort", ["low", "high"])
def test_suggestion_readiness_constructs_analyzer_and_closes_without_inference(monkeypatch, effort) -> None:
    session = _Session()
    settings = Settings()
    settings.agent_runtime.reasoning_effort = effort
    efforts = []

    def create_analyzer(provider_session, *, reasoning_effort="mid"):
        efforts.append(reasoning_effort)
        return CaseSuggestionAnalyzer(provider_session, reasoning_effort=reasoning_effort)

    monkeypatch.setattr("fsq_agent.ai_services._factory.build_model_provider_session", lambda _settings: session)
    monkeypatch.setattr("fsq_agent.ai_services._factory.CaseSuggestionAnalyzer", create_analyzer)

    ready, _, _ = check_case_suggestion_readiness(settings)

    assert ready is True
    assert session.closed is True
    assert efforts == [effort]


def test_web_target_checks_are_static_and_do_not_construct_driver(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("browser", encoding="utf-8")
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="web",
            web=SimpleNamespace(channel="chrome", browser_executable_path=executable),
        )
    )

    def fail_driver_import(*_args, **_kwargs):
        raise AssertionError("Target readiness must not construct a Driver")

    monkeypatch.setattr("fsq_agent.environments.providers._android.discover_android_devices", fail_driver_import)
    service = PlatformRuntimeService()

    assert service.check_target_configuration(settings)[0] is True
    assert service.check_target_availability(settings)[0] is True


@pytest.fixture
def ready_android_runtime(monkeypatch):
    from fsq_agent.models import PlatformPrerequisiteCheck

    monkeypatch.setattr(
        "fsq_agent.environments.providers._android._check_appium_connect_runtime",
        lambda: PlatformPrerequisiteCheck(identifier="appium_connect_runtime", status="ready", message="Isolated host runtime fixture."),
    )


@pytest.mark.usefixtures("ready_android_runtime")
def test_android_target_requires_exact_online_device_and_installed_app(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: "/adb")
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="android",
            android=SimpleNamespace(app_id="com.example.app", serial="device-2"),
        )
    )
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device"), AndroidDevice(serial="device-2", state="device")]),
    )
    calls = []
    monkeypatch.setattr("fsq_agent.environments.providers._android.package_status", lambda serial, app_id: calls.append((serial, app_id)) or "installed")

    ready, _, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is True
    assert calls == [("device-2", "com.example.app")]


@pytest.mark.usefixtures("ready_android_runtime")
def test_android_target_fails_when_application_is_absent(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: "/adb")
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device")]),
    )
    monkeypatch.setattr("fsq_agent.environments.providers._android.package_status", lambda *_args: "absent")

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "not installed" in message


def test_android_target_reports_missing_adb_action(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: None)
    from fsq_agent.models import AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(error_code="adb_missing", error_message="unsafe detail"),
    )

    ready, message, action = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "ADB is unavailable" in message
    assert "Platform-Tools" in action


def _create_ready_macos_managed_root(root: Path, *, with_mac2: bool = True) -> Path:
    import json

    root.mkdir(parents=True)
    root.chmod(0o700)
    marker = root / "managed.json"
    marker.write_text(json.dumps({"owner": "appium-connect-agent", "schema": 1}), encoding="utf-8")
    marker.chmod(0o600)

    records_dir = root / "records"
    records_dir.mkdir()
    records_dir.chmod(0o700)
    install_record = records_dir / "installation.json"
    install_record.write_text(
        json.dumps(
            {
                "schema": 1,
                "state": "complete",
                "operation_id": "op-mac-install-01",
                "combination_id": "macos-android-uiautomator2",
                "changed": True,
            }
        ),
        encoding="utf-8",
    )
    install_record.chmod(0o600)

    appium_dir = root / "appium" / "node_modules" / "appium"
    appium_dir.mkdir(parents=True)
    (root / "appium").chmod(0o700)
    appium_dir.chmod(0o755)
    appium_pkg = appium_dir / "package.json"
    appium_pkg.write_text(json.dumps({"name": "appium", "version": "3.7.0", "bin": {"appium": "index.js"}}), encoding="utf-8")
    appium_pkg.chmod(0o644)
    entry_js = appium_dir / "index.js"
    entry_js.write_text("// appium entry", encoding="utf-8")
    entry_js.chmod(0o644)

    driver_home = root / "driver-home"
    driver_home.mkdir()
    driver_home.chmod(0o700)
    if with_mac2:
        mac2_dir = driver_home / "node_modules" / "appium-mac2-driver"
        mac2_dir.mkdir(parents=True)
        mac2_dir.chmod(0o755)
        mac2_pkg = mac2_dir / "package.json"
        mac2_pkg.write_text(
            json.dumps(
                {
                    "name": "appium-mac2-driver",
                    "version": "4.6.1",
                    "peerDependencies": {"appium": "^3.0.0-rc.2"},
                    "appium": {"driverName": "mac2", "automationName": "Mac2"},
                }
            ),
            encoding="utf-8",
        )
        mac2_pkg.chmod(0o644)
    return root


def test_macos_target_requires_ready_managed_appium_runtime(tmp_path: Path, monkeypatch) -> None:
    from fsq_agent.models import PlatformPrerequisiteCheck

    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(app_path=app, bundle_id=None),
        )
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check_prerequisites",
        lambda *_args: (
            PlatformPrerequisiteCheck(
                identifier="appium_connect_runtime",
                status="unavailable",
                message="The managed Appium runtime root (~/.appium-connect-agent) has not been initialized.",
                action="Initialize the managed Appium runtime.",
            ),
        ),
    )

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "managed Appium runtime" in message


def test_macos_prerequisites_report_each_ready_component_in_stable_order(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "Example.app"
    app.mkdir()
    managed_root = _create_ready_macos_managed_root(tmp_path / "managed-root")
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(app_path=app, bundle_id="com.example.app"),
        )
    )
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: Path("/Applications/Xcode.app"))
    developer = tmp_path / "Xcode.app" / "Contents" / "Developer"
    xcodebuild = developer / "usr" / "bin" / "xcodebuild"
    xcodebuild.parent.mkdir(parents=True)
    xcodebuild.write_bytes(b"")
    node_stub = tmp_path / "node"
    node_stub.write_bytes(b"#!/bin/sh\n")
    node_stub.chmod(0o755)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: developer)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._resolve_first_host_executable", lambda name: node_stub if name == "node" else None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: managed_root)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_for_app", lambda _path: "com.example.app")

    checks = PlatformRuntimeService().check_prerequisites(settings)

    assert [check.identifier for check in checks] == [
        "xcode_installation",
        "xcode_developer_directory",
        "appium_connect_runtime",
        "application_path",
        "bundle_identifier",
    ]
    assert all(check.status == "ready" for check in checks)


def test_macos_prerequisites_distinguish_command_line_tools_and_managed_runtime_states(tmp_path: Path, monkeypatch) -> None:
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(app_path=tmp_path / "Missing.app", bundle_id="com.example.missing"),
        )
    )
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: Path("/Library/Developer/CommandLineTools"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos._resolve_first_host_executable", lambda name: Path("/usr/local/bin/node") if name == "node" else None)
    missing_mac2_root = _create_ready_macos_managed_root(tmp_path / "missing-mac2", with_mac2=False)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: missing_mac2_root)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: False)

    checks = {check.identifier: check for check in PlatformRuntimeService().check_prerequisites(settings)}

    assert checks["xcode_installation"].status == "unavailable"
    assert "Full Xcode" in checks["xcode_installation"].message
    assert checks["xcode_developer_directory"].status == "unavailable"
    assert "Command Line Tools" in checks["xcode_developer_directory"].message
    assert checks["appium_connect_runtime"].status == "unavailable"
    assert checks["appium_connect_runtime"].code == "macos.mac2_driver_missing"
    assert any("ensure_managed_driver('mac2')" in cmd for cmd in checks["appium_connect_runtime"].commands)
    assert checks["application_path"].status == "unavailable"
    assert checks["bundle_identifier"].status == "unavailable"


def test_macos_target_availability_uses_all_prerequisite_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(app_path=app, bundle_id=None),
        )
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check_prerequisites",
        lambda *_args: (
            __import__("fsq_agent.models", fromlist=["PlatformPrerequisiteCheck"]).PlatformPrerequisiteCheck(
                identifier="xcode_developer_directory",
                status="unavailable",
                message="The active developer directory uses Command Line Tools.",
                action="Select full Xcode.",
            ),
        ),
    )

    ready, message, action = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "Command Line Tools" in message
    assert action == "Select full Xcode."


def test_macos_active_developer_directory_accepts_nonstandard_full_xcode(tmp_path: Path) -> None:
    developer = tmp_path / "Xcode-Beta.app" / "Contents" / "Developer"
    xcodebuild = developer / "usr" / "bin" / "xcodebuild"
    xcodebuild.parent.mkdir(parents=True)
    xcodebuild.write_text("", encoding="utf-8")

    from fsq_agent.environments.providers._macos import _developer_directory_is_full_xcode

    assert _developer_directory_is_full_xcode(developer) is True


def test_macos_bundle_mismatch_does_not_fall_back_when_app_plist_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    from fsq_agent.models import PlatformPrerequisiteCheck

    app = tmp_path / "Broken.app"
    app.mkdir()
    managed_root = _create_ready_macos_managed_root(tmp_path / "managed-root-broken-plist")
    settings = SimpleNamespace(harness=SimpleNamespace(platform="macos", macos=SimpleNamespace(app_path=app, bundle_id="com.example.other")))
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: Path("/Applications/Xcode.app"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: Path("/Applications/Xcode.app/Contents/Developer"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos._developer_directory_is_full_xcode", lambda _path: True)
    node_stub = tmp_path / "node-broken"
    node_stub.write_bytes(b"#!/bin/sh\n")
    node_stub.chmod(0o755)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._resolve_first_host_executable", lambda name: node_stub if name == "node" else None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: managed_root)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: True)

    checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(settings)}

    assert isinstance(checks["bundle_identifier"], PlatformPrerequisiteCheck)
    assert checks["bundle_identifier"].status == "unavailable"


@pytest.fixture
def macos_probe_environment(tmp_path: Path, monkeypatch):
    developer = tmp_path / "CustomTools.app" / "Contents" / "Developer"
    builder = developer / "usr" / "bin" / "xcodebuild"
    builder.parent.mkdir(parents=True)
    builder.write_bytes(b"")
    app = tmp_path / "Example.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.app"}))
    managed_root = _create_ready_macos_managed_root(tmp_path / "probe-managed-root")
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: developer)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: None)
    node_stub = tmp_path / "node-probe"
    node_stub.write_bytes(b"#!/bin/sh\n")
    node_stub.chmod(0o755)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._resolve_first_host_executable", lambda name: node_stub if name == "node" else None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: managed_root)
    return SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(
                app_path=app,
                bundle_id="com.example.app",
            ),
        )
    )


def test_custom_active_xcode_satisfies_installation_and_developer_checks(macos_probe_environment) -> None:
    service = PlatformRuntimeService()
    checks = service.check_prerequisites(macos_probe_environment)
    assert len(checks) == 5
    assert all(item.status == "ready" for item in checks)
    assert service.check_target_availability(macos_probe_environment, checks)[0] is True


def test_macos_prerequisites_accept_executable_target(macos_probe_environment, tmp_path: Path) -> None:
    executable = tmp_path / "application"
    executable.write_bytes(b"")
    executable.chmod(0o755)
    macos_probe_environment.harness.macos.app_path = executable
    macos_probe_environment.harness.macos.bundle_id = None
    service = PlatformRuntimeService()
    checks = {item.identifier: item for item in service.check_prerequisites(macos_probe_environment)}
    assert checks["application_path"].status == "ready"
    assert checks["bundle_identifier"].status == "not_applicable"
    assert service.check_target_availability(macos_probe_environment)[0] is True


@pytest.mark.parametrize("plist_content", [plistlib.dumps([]), b"not a plist", plistlib.dumps({"CFBundleIdentifier": 7})])
def test_bad_plist_keeps_other_diagnostics(macos_probe_environment, plist_content: bytes) -> None:
    (macos_probe_environment.harness.macos.app_path / "Contents" / "Info.plist").write_bytes(plist_content)
    checks = PlatformRuntimeService().check_prerequisites(macos_probe_environment)
    assert len(checks) == 5
    assert checks[-1].identifier == "bundle_identifier"
    assert checks[-1].status == "unavailable"
    assert all(item.status == "ready" for item in checks[:-1])


@pytest.mark.parametrize(
    ("helper", "identifier"),
    [
        ("_check_macos_appium_connect_runtime", "appium_connect_runtime"),
        ("_macos_application_path_available", "application_path"),
        ("_macos_bundle_matches_target", "bundle_identifier"),
    ],
)
def test_individual_probe_error_does_not_discard_other_checks(macos_probe_environment, monkeypatch, helper: str, identifier: str) -> None:
    def fail(*_args):
        raise RuntimeError("private probe detail")

    monkeypatch.setattr(f"fsq_agent.environments.providers._macos.{helper}", fail)
    checks = PlatformRuntimeService().check_prerequisites(macos_probe_environment)
    assert len(checks) == 5
    assert next(item for item in checks if item.identifier == identifier).status == "error"
    assert all(item.status == "ready" for item in checks if item.identifier != identifier)
    assert "private probe detail" not in str(checks)


def test_executable_in_bundle_checks_its_own_identity(macos_probe_environment, monkeypatch) -> None:
    bundle = macos_probe_environment.harness.macos.app_path
    executable = bundle / "Contents" / "MacOS" / "Example"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    executable.chmod(0o755)
    macos_probe_environment.harness.macos.app_path = executable
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: True)
    service = PlatformRuntimeService()
    assert service.check_target_availability(macos_probe_environment)[0] is True
    macos_probe_environment.harness.macos.bundle_id = "com.other.installed.app"
    checks = service.check_prerequisites(macos_probe_environment)
    assert checks[-1].status == "unavailable"


def test_macos_runtime_prerequisite_distinguishes_damaged_and_conflict_states(macos_probe_environment, monkeypatch, tmp_path: Path) -> None:
    conflict_root = tmp_path / "conflict-managed-root"
    conflict_root.mkdir()
    conflict_root.chmod(0o700)
    (conflict_root / "foreign.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: conflict_root)
    checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(macos_probe_environment)}
    assert checks["appium_connect_runtime"].status == "error"
    assert checks["appium_connect_runtime"].code == "macos.managed_runtime_conflict"

    damaged_root = _create_ready_macos_managed_root(tmp_path / "damaged-managed-root")
    (damaged_root / "driver-home" / "node_modules" / "appium-mac2-driver" / "package.json").write_text('{"name":"wrong"}', encoding="utf-8")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: damaged_root)
    damaged_checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(macos_probe_environment)}
    assert damaged_checks["appium_connect_runtime"].status == "error"
    assert damaged_checks["appium_connect_runtime"].code == "macos.managed_runtime_damaged"

    incompatible_root = _create_ready_macos_managed_root(tmp_path / "incompatible-managed-root")
    (incompatible_root / "driver-home" / "node_modules" / "appium-mac2-driver" / "package.json").write_text(
        '{"name":"appium-mac2-driver","version":"2.0.0","peerDependencies":{"appium":"^2.0.0"},"appium":{"driverName":"mac2","automationName":"Mac2"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: incompatible_root)
    incompatible_checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(macos_probe_environment)}
    assert incompatible_checks["appium_connect_runtime"].status == "error"
    assert incompatible_checks["appium_connect_runtime"].code == "macos.mac2_driver_incompatible"


def test_managed_runtime_repair_commands_construct_valid_runtime_signatures() -> None:
    from appium_connect_agent.contracts import LocalSettings
    from appium_connect_agent.runtime import InstallRequest, Runtime

    from fsq_agent.environments.providers._android import MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_INSTALL_PYTHON_COMMAND
    from fsq_agent.environments.providers._macos import MANAGED_ENSURE_MAC2_PYTHON_COMMAND

    runtime = Runtime(LocalSettings())
    assert callable(runtime.install)
    assert callable(runtime.cleanup_installation)
    assert callable(runtime.ensure_managed_driver)
    assert isinstance(InstallRequest(), InstallRequest)
    for command in (MANAGED_INSTALL_PYTHON_COMMAND, MANAGED_CLEANUP_PYTHON_COMMAND, MANAGED_ENSURE_MAC2_PYTHON_COMMAND):
        assert "from appium_connect_agent.contracts import LocalSettings;" in command
        assert "Runtime(LocalSettings())" in command
        assert "Runtime()" not in command


def test_macos_appium_connect_runtime_probe_is_zero_subprocess_zero_network_and_preserves_managed_root(macos_probe_environment, monkeypatch, tmp_path: Path) -> None:
    import os
    import socket
    import subprocess

    from fsq_agent.environments.providers._macos import _check_macos_appium_connect_runtime, _managed_root

    managed_root = _managed_root()
    before_snapshot = {path.relative_to(managed_root): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None) for path in sorted(managed_root.rglob("*"))}
    real_open = os.open
    real_mkdir = os.mkdir
    real_chmod = os.chmod
    real_fchmod = os.fchmod
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    fd_paths: dict[int, Path] = {}
    lock_fds: set[int] = set()
    account_home = Path.home()
    allowed_lock_dirs = {
        account_home / "Library",
        account_home / "Library" / "Application Support",
        account_home / "Library" / "Application Support" / "appium-connect-agent",
    }
    allowed_lock_file = account_home / "Library" / "Application Support" / "appium-connect-agent" / "runtime.lock"

    def _resolve_raw(raw_path: object, dir_fd: object = None) -> Path:
        text = os.fsdecode(raw_path) if isinstance(raw_path, (str, bytes, os.PathLike)) else ""
        candidate = Path(text)
        if isinstance(dir_fd, int) and dir_fd in fd_paths and not candidate.is_absolute():
            return fd_paths[dir_fd] / candidate
        return candidate

    def guarded_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        resolved = _resolve_raw(path, kwargs.get("dir_fd"))
        is_lock = resolved == allowed_lock_file
        if (flags & write_flags) and not is_lock:
            pytest.fail(f"Unexpected mutating os.open outside runtime.lock: {resolved}")
        fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        fd_paths[fd] = resolved
        if is_lock or resolved == account_home / "Library" / "Application Support" / "appium-connect-agent":
            lock_fds.add(fd)
        return fd

    def guarded_mkdir(path: object, *args: object, **kwargs: object) -> None:
        resolved = _resolve_raw(path, kwargs.get("dir_fd"))
        if resolved not in allowed_lock_dirs:
            pytest.fail(f"Unexpected os.mkdir outside runtime.lock hierarchy: {resolved}")
        real_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_chmod(path: object, *args: object, **kwargs: object) -> None:
        resolved = _resolve_raw(path, kwargs.get("dir_fd"))
        if resolved != allowed_lock_file and resolved not in allowed_lock_dirs:
            pytest.fail(f"Unexpected os.chmod outside runtime.lock: {resolved}")
        real_chmod(path, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_fchmod(fd: int, mode: int) -> None:
        if fd not in lock_fds:
            pytest.fail(f"Unexpected os.fchmod on non-lock descriptor: {fd} ({fd_paths.get(fd)})")
        real_fchmod(fd, mode)

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Runtime static probe must not spawn subprocesses"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Runtime static probe must not spawn subprocesses"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Runtime static probe must not open sockets"))
    monkeypatch.setattr(os, "open", guarded_open)
    monkeypatch.setattr(os, "mkdir", guarded_mkdir)
    monkeypatch.setattr(os, "chmod", guarded_chmod)
    monkeypatch.setattr(os, "fchmod", guarded_fchmod)
    fact = _check_macos_appium_connect_runtime()
    assert fact.identifier == "appium_connect_runtime"
    assert fact.status == "ready"
    after_snapshot = {path.relative_to(managed_root): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None) for path in sorted(managed_root.rglob("*"))}
    assert after_snapshot == before_snapshot


@pytest.mark.parametrize(
    ("peer_range", "expected_status", "expected_code"),
    [
        ("^3.0.0-rc.2", "ready", None),
        ("3.7.x", "ready", None),
        ("3.0.0 - 3.9.0", "ready", None),
        (">=3.0.0 <4.0.0", "ready", None),
        ("^2.0.0 || ^3.5.0", "ready", None),
        ("^2.0.0", "error", "macos.mac2_driver_incompatible"),
        ("~3.6.0", "error", "macos.mac2_driver_incompatible"),
        ("invalid range ??", "error", "macos.managed_runtime_damaged"),
    ],
)
def test_macos_peer_compatibility_delegates_to_runtime_diagnose_across_npm_range_forms(
    macos_probe_environment,
    monkeypatch,
    tmp_path: Path,
    peer_range: str,
    expected_status: str,
    expected_code: str | None,
) -> None:
    import json

    root = _create_ready_macos_managed_root(tmp_path / f"root-{abs(hash(peer_range))}")
    node_stub = tmp_path / "node"
    node_stub.write_bytes(b"#!/bin/sh\n")
    node_stub.chmod(0o755)
    monkeypatch.setattr(
        "fsq_agent.environments.providers._macos._resolve_first_host_executable",
        lambda name: node_stub if name == "node" else None,
    )
    (root / "driver-home" / "node_modules" / "appium-mac2-driver" / "package.json").write_text(
        json.dumps(
            {
                "name": "appium-mac2-driver",
                "version": "4.6.1",
                "peerDependencies": {"appium": peer_range},
                "appium": {"driverName": "mac2", "automationName": "Mac2"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("fsq_agent.environments.providers._macos._managed_root", lambda: root)
    checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(macos_probe_environment)}
    assert checks["appium_connect_runtime"].status == expected_status
    assert checks["appium_connect_runtime"].code == expected_code


@pytest.mark.parametrize("host", ["Linux", "Windows"])
def test_macos_checks_on_other_hosts_do_not_probe(macos_probe_environment, monkeypatch, host: str) -> None:
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: host)

    def fail():
        raise AssertionError("must not probe macOS on other hosts")

    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", fail)
    service = PlatformRuntimeService()
    assert all(item.status == "not_applicable" for item in service.check_prerequisites(macos_probe_environment))
    assert service.check_target_availability(macos_probe_environment)[0] is False


def test_dynamic_agent_readiness_builds_static_inputs_without_runtime_session(tmp_path: Path, monkeypatch) -> None:
    prompt = SimpleNamespace(agent_template_path=tmp_path / "agent.j2", task_template_path=None, variables={})
    prompt.agent_template_path.write_text("{{ private_knowledge }} {{ skills }}", encoding="utf-8")
    settings = SimpleNamespace(
        harness=SimpleNamespace(platform="web"),
        agent_runtime=SimpleNamespace(prompt=prompt, local_tool_output=None),
        agent_context=SimpleNamespace(knowledge=SimpleNamespace(root_dir=tmp_path, skills=SimpleNamespace(dir=tmp_path), pre_plan=SimpleNamespace(dir=None))),
        cases=SimpleNamespace(dir=tmp_path),
        workspace=SimpleNamespace(root_dir=tmp_path),
        output=SimpleNamespace(root_dir=tmp_path, runs_dir=tmp_path),
        runtime_secrets=None,
        skills=[],
    )
    monkeypatch.setattr("fsq_agent.agent._readiness.validate_runtime_settings", lambda _settings: None)
    monkeypatch.setattr("fsq_agent.agent._readiness.CapabilityDefinitionFactory.platform_definitions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("fsq_agent.agent._readiness.CommonPlatformTools.capability_definitions", list)

    ready, _, _ = check_dynamic_agent_readiness(settings)

    assert ready is True
