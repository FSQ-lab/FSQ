# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import plistlib
import struct
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.models import AndroidDevice, ApplicationCandidate


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_lookup_preserves_actual_candidate_cardinality(monkeypatch, platform, count):
    candidates = [ApplicationCandidate(identity=f"app-{index}", label="Calculator", app_path=Path(f"/apps/{index}")) for index in range(count)]
    monkeypatch.setattr("fsq_agent.environments._applications.discover_candidates", lambda *args, **kwargs: candidates)
    result = PlatformRuntimeService().lookup_application(platform=platform, name="Calculator", target_id="device")
    assert result.status == "complete"
    assert len(result.candidates) == count


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
@pytest.mark.parametrize("error", [OSError("PRIVATE"), TimeoutError(), ValueError("PRIVATE")])
def test_discovery_failure_never_becomes_empty_match(monkeypatch, platform, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("fsq_agent.environments._applications.discover_candidates", fail)
    result = PlatformRuntimeService().lookup_application(platform=platform, name="Calculator", target_id="device")
    assert result.status == "failed"
    assert not result.candidates
    assert "PRIVATE" not in result.model_dump_json()


def test_lookup_deduplicates_only_same_identity(monkeypatch):
    first = ApplicationCandidate(identity="one", label="Calculator", app_path=Path("/apps/one"))
    second = ApplicationCandidate(identity="two", label="Calculator", app_path=Path("/apps/two"))
    monkeypatch.setattr("fsq_agent.environments._applications.discover_candidates", lambda *args, **kwargs: [first, first, second])
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator")
    assert len(result.candidates) == 2


@pytest.mark.parametrize("name", ["", " ", "a" * 513, "app\ncommand"])
def test_lookup_rejects_unbounded_or_control_input(name):
    result = PlatformRuntimeService().lookup_application(platform="android", name=name, target_id="device")
    assert result.status == "failed"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_android_package_source_counts_all_matching_packages(monkeypatch, count):
    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda serial, deadline: {f"com.vendor{index}.calculator" for index in range(count)})
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: "installed", raising=False)
    result = PlatformRuntimeService().lookup_application(platform="android", name="Calculator", target_id="selected")
    assert result.status == "complete"
    assert len(result.candidates) == count
    if count:
        exact = PlatformRuntimeService().lookup_application(platform="android", name="Calculator", target_id="selected", explicit_reference="com.vendor0.calculator")
        assert [item.app_id for item in exact.candidates] == ["com.vendor0.calculator"]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_macos_bounded_application_metadata_source(monkeypatch, tmp_path, count):
    for index in range(count):
        metadata = tmp_path / f"Calculator{index}.app/Contents/Info.plist"
        metadata.parent.mkdir(parents=True)
        metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": f"com.vendor{index}.calculator", "CFBundleName": "Calculator"}))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator")
    assert result.status == "complete"
    assert len(result.candidates) == count


def test_macos_partial_corrupt_metadata_is_not_unique(monkeypatch, tmp_path):
    for name, content in [("Calculator", plistlib.dumps({"CFBundleIdentifier": "com.calc", "CFBundleName": "Calculator"})), ("Broken", b"not a plist")]:
        path = tmp_path / f"{name}.app/Contents/Info.plist"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator")
    assert result.status == "failed"
    assert not result.candidates


@pytest.mark.parametrize("count", [0, 1, 2])
def test_windows_app_paths_source_deduplicates_registry_views(monkeypatch, tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / f"Calculator{index}.exe"
        path.write_bytes(b"fixture")
        paths.append(path)
    registry = SimpleNamespace(
        HKEY_CURRENT_USER="current",
        HKEY_LOCAL_MACHINE="machine",
        KEY_WOW64_32KEY=1,
        KEY_WOW64_64KEY=2,
        KEY_READ=4,
        REG_SZ=1,
        OpenKey=lambda root, key, *args: nullcontext(key),
        QueryInfoKey=lambda root: (len(paths),),
        EnumKey=lambda root, index: paths[index].name,
        QueryValueEx=lambda key, name: (str(tmp_path / key), 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Windows")
    result = PlatformRuntimeService().lookup_application(platform="windows", name="Calculator")
    assert result.status == "complete"
    assert len(result.candidates) == count


def test_windows_partial_registry_failure_never_returns_unique(monkeypatch):
    def fail(*args):
        raise PermissionError("PRIVATE")

    registry = SimpleNamespace(HKEY_CURRENT_USER="current", HKEY_LOCAL_MACHINE="machine", KEY_WOW64_32KEY=1, KEY_WOW64_64KEY=2, KEY_READ=4, OpenKey=fail)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Windows")
    result = PlatformRuntimeService().lookup_application(platform="windows", name="Calculator")
    assert result.status == "failed"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_web_uses_selected_channel_candidate_paths(monkeypatch, tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / str(index) / "Google Chrome Beta" / "Google Chrome Beta"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
        paths.append(path)
    seen = []
    monkeypatch.setattr("fsq_agent.environments.providers._applications.web_candidate_paths", lambda channel: seen.append(channel) or paths)
    result = PlatformRuntimeService().lookup_application(platform="web", name="Site", target_id="chrome-beta")
    assert result.status == "complete"
    assert len(result.candidates) == count
    assert seen == ["chrome-beta"]


@pytest.mark.parametrize("payload", [b"package:com.calc\n", b"bad output\n"])
def test_android_inventory_wire_uses_fixed_existing_server_query(monkeypatch, payload):
    from fsq_agent.environments.providers import _adb_transport as adb

    buffer = bytearray(b"OKAYOKAY" + struct.pack("<BI", 1, len(payload)) + payload + struct.pack("<BI", 3, 1) + b"\x00")
    sent = []

    def recv(size):
        result = bytes(buffer[:size])
        del buffer[:size]
        return result

    wire = SimpleNamespace(settimeout=lambda value: None, sendall=sent.append, recv=recv)
    monkeypatch.setattr(adb, "connection", lambda deadline: nullcontext(wire))
    monkeypatch.setattr(adb, "remaining", lambda deadline: 1)
    if payload.startswith(b"package:"):
        assert adb.package_inventory("selected", 123) == {"com.calc"}
    else:
        with pytest.raises(ValueError, match="adb_failed"):
            adb.package_inventory("selected", 123)
    assert sent == [b"0017host:transport:selected", b"0026shell,v2,raw:cmd package list packages"]


def test_android_inventory_server_disappearance_never_runs_adb_cli(monkeypatch):
    import socket
    import subprocess

    from fsq_agent.environments.providers import _adb_transport as adb

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Must not start an ADB client"))
    with pytest.raises(ConnectionRefusedError):
        adb.package_inventory("selected", float("inf"))


def test_late_native_metadata_result_cannot_establish_unique_match(monkeypatch):
    from fsq_agent.environments.providers import _applications as provider

    clock = [0.0]

    def late(*args):
        clock[0] = 6.0
        return [ApplicationCandidate(identity="one", label="Calculator", app_path=Path("/Calculator.app"))]

    monkeypatch.setattr(provider.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(provider.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(provider, "_macos", late)
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator")
    assert result.status == "failed"
    assert result.code == "discovery_timeout"


def test_blocked_host_metadata_returns_timeout_without_waiting_for_filesystem(monkeypatch):
    import threading
    import time

    release, finished = threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        try:
            release.wait(2)
            return [ApplicationCandidate(identity="one", label="Calculator")]
        finally:
            finished.set()

    monkeypatch.setattr("fsq_agent.environments._applications.discover_candidates", blocked)
    monkeypatch.setattr("fsq_agent.environments._applications._LOOKUP_TIMEOUT", 0.02)
    started = time.monotonic()
    try:
        result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator")
        assert time.monotonic() - started < 0.5
        assert result.status == "failed"
        assert result.code == "discovery_timeout"
        assert not result.candidates
    finally:
        release.set()
        assert finished.wait(1)


@pytest.mark.parametrize("count", [1, 2])
def test_inferred_package_scopes_validation_instead_of_searching_same_names(monkeypatch, count):
    from fsq_agent.models import ApplicationLookupHint

    packages = {"com.android.calculator2"}
    if count == 2:
        packages.add("com.google.android.calculator")
    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("Concrete references must not enumerate name candidates"))
    queried = []

    def package_status(serial, app_id, **kwargs):
        queried.append((serial, app_id))
        return "installed" if app_id in packages else "absent"

    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", package_status, raising=False)
    result = PlatformRuntimeService().lookup_application(
        platform="android",
        name="Calculator",
        target_id="selected",
        inferred_hint=ApplicationLookupHint(kind="app_id", value="com.android.calculator2"),
    )
    assert result.status == "complete"
    assert [item.app_id for item in result.candidates] == ["com.android.calculator2"]
    assert queried == [("selected", "com.android.calculator2")]


@pytest.mark.parametrize("name", ["微信", "WeChat"])
def test_installed_wechat_hint_succeeds_without_localized_name_metadata(monkeypatch, name):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("Resolved package must not use name discovery"))
    monkeypatch.setattr(
        "fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: "installed" if (serial, app_id) == ("selected", "com.tencent.mm") else "absent", raising=False
    )
    result = PlatformRuntimeService().lookup_application(
        platform="android",
        name=name,
        target_id="selected",
        inferred_hint=ApplicationLookupHint(kind="app_id", value="com.tencent.mm"),
    )
    assert result.status == "complete"
    assert [item.app_id for item in result.candidates] == ["com.tencent.mm"]


def test_inferred_hint_absent_from_actual_installed_packages_is_rejected(monkeypatch):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("Missing inferred package must not fall back to name discovery"))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: "absent", raising=False)
    result = PlatformRuntimeService().lookup_application(
        platform="android",
        name="Calculator",
        target_id="selected",
        inferred_hint=ApplicationLookupHint(kind="app_id", value="com.vendor.wrong"),
    )
    assert result.status == "complete"
    assert not result.candidates


def test_bundle_hint_validates_concrete_identity_not_unrelated_name_match(monkeypatch, tmp_path):
    from fsq_agent.models import ApplicationLookupHint

    for name, bundle in [("Calculator", "com.apple.calculator"), ("Chat", "com.vendor.chat")]:
        metadata = tmp_path / f"{name}.app/Contents/Info.plist"
        metadata.parent.mkdir(parents=True)
        metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleName": name}))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator", inferred_hint=ApplicationLookupHint(kind="bundle_id", value="com.vendor.chat"))
    assert result.status == "complete"
    assert [item.bundle_id for item in result.candidates] == ["com.vendor.chat"]


def test_complete_empty_package_inventory_remains_zero_matches_with_hint(monkeypatch):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda serial, deadline: set())
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: "absent", raising=False)
    result = PlatformRuntimeService().lookup_application(
        platform="android", name="Calculator", target_id="selected", inferred_hint=ApplicationLookupHint(kind="app_id", value="com.android.calculator2")
    )
    assert result.status == "complete"
    assert not result.candidates


@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
def test_scoped_bundle_lookup_keeps_distinct_installations(monkeypatch, tmp_path, provenance):
    from fsq_agent.models import ApplicationLookupHint

    for index in range(2):
        metadata = tmp_path / f"Installation{index}.app/Contents/Info.plist"
        metadata.parent.mkdir(parents=True)
        metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.tencent.xinWeChat", "CFBundleName": f"Localized label {index}"}))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    reference = {"explicit_reference": "com.tencent.xinWeChat"} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="bundle_id", value="com.tencent.xinWeChat")}
    result = PlatformRuntimeService().lookup_application(platform="macos", name="微信", **reference)
    assert result.status == "complete"
    assert len(result.candidates) == 2
    assert len({item.identity for item in result.candidates}) == 2


@pytest.mark.parametrize("platform", ["windows", "macos"])
@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
@pytest.mark.parametrize("exists", [True, False])
def test_native_path_references_never_fall_back_to_name_inventory(monkeypatch, tmp_path, platform, provenance, exists):
    from fsq_agent.models import ApplicationLookupHint

    path = tmp_path / ("Selected.exe" if platform == "windows" else "Selected.app")
    if exists:
        if platform == "windows":
            path.write_bytes(b"fixture")
        else:
            metadata = path / "Contents/Info.plist"
            metadata.parent.mkdir(parents=True)
            metadata.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.vendor.selected", "CFBundleName": "Local metadata name"}))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Windows" if platform == "windows" else "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: pytest.fail("Path references must not scan name metadata"))
    monkeypatch.setitem(sys.modules, "winreg", None)
    reference_value = str(path)
    if platform == "macos":
        # Keep macOS reference syntax independent of the runner's filesystem.
        # Only this exact reference maps to the real, host-native fixture.
        reference_value = "/Applications/Selected.app"

        def fixture_path(value):
            if value != reference_value:
                pytest.fail("Unexpected macOS path lookup.")
            return path

        monkeypatch.setattr("fsq_agent.environments.providers._applications.Path", fixture_path)
    reference = {"explicit_reference": reference_value} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="app_path", value=reference_value)}
    result = PlatformRuntimeService().lookup_application(platform=platform, name="WeChat", **reference)
    assert result.status == "complete"
    assert len(result.candidates) == int(exists)
    if exists:
        assert result.candidates[0].app_path == path


@pytest.mark.parametrize(("status", "code"), [("query_failed", "discovery_failed"), ("timeout", "discovery_timeout"), ("unexpected", "discovery_failed")])
def test_scoped_package_query_errors_are_not_missing_packages(monkeypatch, status, code):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.inventory", lambda deadline: [AndroidDevice(serial="selected", state="device")])
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_status", lambda serial, app_id, **kwargs: status, raising=False)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", lambda *args: pytest.fail("Scoped queries must not retry a name lookup"))
    result = PlatformRuntimeService().lookup_application(platform="android", name="微信", target_id="selected", inferred_hint=ApplicationLookupHint(kind="app_id", value="com.tencent.mm"))
    assert result.status == "failed"
    assert result.code == code


@pytest.mark.parametrize(("kind", "value"), [("bundle_id", "/Applications/Selected.app"), ("app_path", "com.vendor.selected")])
def test_typed_macos_identity_is_not_reinterpreted_as_a_different_kind(monkeypatch, tmp_path, kind, value):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Selected", inferred_hint=ApplicationLookupHint(kind=kind, value=value))
    assert result.status == "failed"
    assert result.code == "discovery_failed"


def test_complete_empty_bundle_scope_remains_zero_matches_with_hint(monkeypatch, tmp_path):
    from fsq_agent.models import ApplicationLookupHint

    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Calculator", inferred_hint=ApplicationLookupHint(kind="bundle_id", value="com.apple.calculator"))
    assert result.status == "complete"
    assert not result.candidates
