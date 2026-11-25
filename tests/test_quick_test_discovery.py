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


@pytest.fixture
def macos_path_only_fixture(monkeypatch, tmp_path):
    path = tmp_path / "Selected.app"
    (path / "Contents").mkdir(parents=True)
    reference = "/Applications/Selected.app"

    def fixture_path(value):
        if value != reference:
            pytest.fail("Unexpected macOS path lookup.")
        return path

    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications.Path", fixture_path)
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: pytest.fail("Path references must not scan name metadata"))
    return path, reference


@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
def test_macos_path_only_accepts_missing_bundle_identifier(macos_path_only_fixture, provenance):
    from fsq_agent.models import ApplicationLookupHint

    path, value = macos_path_only_fixture
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Selected"}))
    reference = {"explicit_reference": value} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="app_path", value=value)}
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Selected", **reference)
    assert result.status == "complete"
    assert len(result.candidates) == 1
    assert result.candidates[0].app_path == path
    assert result.candidates[0].identity == str(path.resolve())
    assert result.candidates[0].bundle_id is None


@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
@pytest.mark.parametrize("bundle_id", [None, "", False, 7, [], {}, "invalid identifier", "single"])
def test_macos_path_only_rejects_provided_invalid_bundle_identifier(macos_path_only_fixture, provenance, bundle_id):
    from fsq_agent.models import ApplicationLookupHint

    path, value = macos_path_only_fixture
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Selected", "CFBundleIdentifier": bundle_id}, fmt=plistlib.FMT_BINARY))
    reference = {"explicit_reference": value} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="app_path", value=value)}
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Selected", **reference)
    assert result.status == "failed"
    assert result.code == "discovery_failed"
    assert not result.candidates


@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
@pytest.mark.parametrize("contents", [None, b"invalid plist", plistlib.dumps([])])
def test_macos_path_only_rejects_invalid_metadata(macos_path_only_fixture, provenance, contents):
    from fsq_agent.models import ApplicationLookupHint

    path, value = macos_path_only_fixture
    if contents is not None:
        (path / "Contents/Info.plist").write_bytes(contents)
    reference = {"explicit_reference": value} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="app_path", value=value)}
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Selected", **reference)
    assert result.status == "failed"
    assert result.code == "discovery_failed"
    assert not result.candidates


@pytest.mark.parametrize("provenance", ["explicit", "inferred"])
@pytest.mark.parametrize("unrelated_id", [None, "com.vendor.other"])
@pytest.mark.parametrize("matching", [False, True])
def test_macos_path_only_bundle_lookup_requires_exact_identifier(monkeypatch, tmp_path, provenance, unrelated_id, matching):
    from fsq_agent.models import ApplicationLookupHint

    metadata = {"CFBundleName": "Selected"}
    if unrelated_id is not None:
        metadata["CFBundleIdentifier"] = unrelated_id
    other = tmp_path / "Other.app/Contents/Info.plist"
    other.parent.mkdir(parents=True)
    other.write_bytes(plistlib.dumps(metadata))
    selected = tmp_path / "Selected.app"
    if matching:
        info = selected / "Contents/Info.plist"
        info.parent.mkdir(parents=True)
        info.write_bytes(plistlib.dumps({"CFBundleName": "Selected", "CFBundleIdentifier": "com.vendor.selected"}))
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))
    reference = {"explicit_reference": "com.vendor.selected"} if provenance == "explicit" else {"inferred_hint": ApplicationLookupHint(kind="bundle_id", value="com.vendor.selected")}
    result = PlatformRuntimeService().lookup_application(platform="macos", name="Selected", **reference)
    assert result.status == "complete"
    assert len(result.candidates) == int(matching)
    if matching:
        assert result.candidates[0].app_path == selected
        assert result.candidates[0].bundle_id == "com.vendor.selected"


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


def test_android_inventory_prioritizes_third_party_packages_and_caches_by_target_id(monkeypatch):
    from fsq_agent.environments._applications import clear_application_inventory_cache

    clear_application_inventory_cache()
    monkeypatch.setattr(
        "fsq_agent.environments.providers._applications.inventory",
        lambda deadline: [AndroidDevice(serial="dev-1", state="device")],
    )
    calls = []

    def third_party(serial, deadline):
        calls.append(("3p", serial))
        return {"com.tencent.mm", "com.example.shop"}

    def all_packages(serial, deadline):
        calls.append(("all", serial))
        return {"com.tencent.mm", "com.example.shop", "com.android.vending", "com.vendor.hidden.daemon"}

    monkeypatch.setattr("fsq_agent.environments.providers._applications.third_party_package_inventory", third_party)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.package_inventory", all_packages)

    first = PlatformRuntimeService().list_applications(platform="android", target_id="dev-1")
    assert first.status == "complete"
    assert not first.from_cache
    assert first.fingerprint
    app_ids = [item.app_id for item in first.candidates]
    assert app_ids[:2] == ["com.example.shop", "com.tencent.mm"]
    assert "com.android.vending" in app_ids
    assert [item.candidate_id for item in first.candidates] == [f"android:{index + 1}" for index in range(len(first.candidates))]

    second = PlatformRuntimeService().list_applications(platform="android", target_id="dev-1")
    assert second.from_cache
    assert second.fingerprint == first.fingerprint
    assert len(calls) == 2

    refreshed = PlatformRuntimeService().list_applications(platform="android", target_id="dev-1", force_refresh=True)
    assert not refreshed.from_cache
    assert len(calls) == 4
    clear_application_inventory_cache()


def test_macos_inventory_collects_localized_infoplist_strings(monkeypatch, tmp_path):
    from fsq_agent.environments._applications import clear_application_inventory_cache

    clear_application_inventory_cache()
    app = tmp_path / "WeChat.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.tencent.xinWeChat", "CFBundleName": "WeChat", "CFBundleDisplayName": "WeChat"}))
    zh = app / "Contents/Resources/zh-Hans.lproj"
    zh.mkdir(parents=True)
    (zh / "InfoPlist.strings").write_bytes(plistlib.dumps({"CFBundleDisplayName": "微信"}))

    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._macos_roots", lambda: (tmp_path,))

    result = PlatformRuntimeService().list_applications(platform="macos", force_refresh=True)
    assert result.status == "complete"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.candidate_id == "macos:1"
    assert candidate.bundle_id == "com.tencent.xinWeChat"
    assert "微信" in candidate.aliases
    assert candidate.source == "applications_dir"
    clear_application_inventory_cache()


def _make_minimal_lnk(target_path: str) -> bytes:
    # Minimal Shell Link (.lnk) with HasLinkInfo flag (0x02) and ASCII local base path.
    header = bytearray(76)
    struct.pack_into("<I", header, 0, 0x4C)
    header[4:20] = bytes.fromhex("0114020000000000c000000000000046")
    struct.pack_into("<I", header, 20, 0x02)  # HasLinkInfo
    encoded = target_path.encode("ascii") + b"\x00"
    link_info_header_size = 28
    local_base_path_offset = link_info_header_size
    link_info_size = link_info_header_size + len(encoded)
    link_info = bytearray(link_info_size)
    struct.pack_into("<I", link_info, 0, link_info_size)
    struct.pack_into("<I", link_info, 4, link_info_header_size)
    struct.pack_into("<I", link_info, 8, 0x01)  # VolumeIDAndLocalBasePath
    struct.pack_into("<I", link_info, 16, local_base_path_offset)
    link_info[local_base_path_offset:] = encoded
    return bytes(header) + bytes(link_info)


def test_windows_inventory_merges_catalogue_app_paths_and_start_menu_lnk(monkeypatch, tmp_path):
    from fsq_agent.environments._applications import clear_application_inventory_cache
    from fsq_agent.models import WindowsApplicationConfig

    clear_application_inventory_cache()
    edge_exe = tmp_path / "msedge.exe"
    edge_exe.write_bytes(b"fixture")
    reg_exe = tmp_path / "Code.exe"
    reg_exe.write_bytes(b"fixture")
    lnk_exe = tmp_path / "MyTool.exe"
    lnk_exe.write_bytes(b"fixture")
    start_menu = tmp_path / "StartMenu"
    start_menu.mkdir()
    (start_menu / "My Custom Tool.lnk").write_bytes(_make_minimal_lnk(str(lnk_exe)))

    catalogue = (
        WindowsApplicationConfig(
            id="edge",
            name="Microsoft Edge",
            description="Browser",
            path_candidates=[str(edge_exe)],
            window_title_re=".*Microsoft Edge.*",
        ),
    )
    registry = SimpleNamespace(
        HKEY_CURRENT_USER="current",
        HKEY_LOCAL_MACHINE="machine",
        KEY_WOW64_32KEY=1,
        KEY_WOW64_64KEY=2,
        KEY_READ=4,
        REG_SZ=1,
        OpenKey=lambda root, key, *args: nullcontext(key),
        QueryInfoKey=lambda root: (1,),
        EnumKey=lambda root, index: reg_exe.name,
        QueryValueEx=lambda key, name: (str(reg_exe), 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Windows")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._windows_start_menu_roots", lambda: (start_menu,))

    result = PlatformRuntimeService().list_applications(platform="windows", windows_catalogue=catalogue, force_refresh=True)
    assert result.status == "complete"
    by_path = {item.app_path: item for item in result.candidates}
    assert edge_exe.resolve() in by_path
    assert by_path[edge_exe.resolve()].window_title_re == ".*Microsoft Edge.*"
    assert "window_title_re" not in by_path[edge_exe.resolve()].model_summary()
    assert reg_exe.resolve() in by_path
    assert lnk_exe.resolve() in by_path
    assert by_path[lnk_exe.resolve()].label == "My Custom Tool"
    clear_application_inventory_cache()


def test_windows_inventory_cache_separates_distinct_catalogues(monkeypatch, tmp_path):
    from fsq_agent.environments._applications import clear_application_inventory_cache
    from fsq_agent.models import WindowsApplicationConfig

    clear_application_inventory_cache()
    app_a = tmp_path / "AppA.exe"
    app_b = tmp_path / "AppB.exe"
    app_a.write_bytes(b"exe")
    app_b.write_bytes(b"exe")
    monkeypatch.setattr("fsq_agent.environments.providers._applications.platform.system", lambda: "Windows")
    monkeypatch.setattr("fsq_agent.environments.providers._applications._windows_start_menu_roots", lambda: ())
    monkeypatch.setattr("fsq_agent.environments.providers._applications._collect_windows_app_paths", lambda *a, **kw: None)

    cat_a = (WindowsApplicationConfig(id="a", name="App A", description="App A", path_candidates=[str(app_a)], window_title_re=".*A.*"),)
    cat_b = (WindowsApplicationConfig(id="b", name="App B", description="App B", path_candidates=[str(app_b)], window_title_re=".*B.*"),)

    first = PlatformRuntimeService().list_applications(platform="windows", windows_catalogue=cat_a)
    second = PlatformRuntimeService().list_applications(platform="windows", windows_catalogue=cat_b)
    first_cached = PlatformRuntimeService().list_applications(platform="windows", windows_catalogue=cat_a)

    assert first.status == "complete"
    assert not first.from_cache
    assert [c.app_path for c in first.candidates] == [app_a.resolve()]
    assert second.status == "complete"
    assert not second.from_cache
    assert [c.app_path for c in second.candidates] == [app_b.resolve()]
    assert first_cached.status == "complete"
    assert first_cached.from_cache
    assert [c.app_path for c in first_cached.candidates] == [app_a.resolve()]
    clear_application_inventory_cache()


def test_ios_inventory_and_explicit_references(monkeypatch, tmp_path):
    from fsq_agent.environments import _applications as boundary
    from fsq_agent.environments.providers import _ios
    from fsq_agent.models import IOSDevice, IOSDeviceDiscoveryResult

    devices = [IOSDevice(udid=udid, name=udid, kind=kind, state=state) for udid, kind, state in (("sim-a", "simulator", "booted"), ("sim-b", "simulator", "booted"), ("phone", "device", "connected"))]
    monkeypatch.setattr(_ios, "discover_ios_devices", lambda *a: IOSDeviceDiscoveryResult(devices=devices))
    seen = []

    def listapps(udid, deadline):
        seen.append(udid)
        return plistlib.dumps({f"com.example.{udid}": {"CFBundleDisplayName": "Example"}, "com.example.ipa": {"CFBundleDisplayName": "Another App"}})

    monkeypatch.setattr(_ios, "_simulator_listapps_output", listapps)
    service = PlatformRuntimeService()
    boundary.clear_application_inventory_cache()
    try:
        for udid in ("sim-a", "sim-b", "sim-a"):
            result = service.list_applications(platform="ios", target_id=udid)
            assert {item.bundle_id: item.label for item in result.candidates} == {f"com.example.{udid}": "Example", "com.example.ipa": "Another App"}
        assert seen == ["sim-a", "sim-b"]
        devices[0] = devices[0].model_copy(update={"state": "shutdown"})
        assert service.list_applications(platform="ios", target_id="sim-a").status == "failed"
        devices[0] = devices[0].model_copy(update={"state": "booted"})
        assert service.list_applications(platform="ios", target_id="sim-a", force_refresh=True).status == "complete"
        assert seen == ["sim-a", "sim-b", "sim-a"]
        found = service.lookup_application(platform="ios", name="Example", explicit_reference="com.example.sim-a", target_id="sim-a")
        assert [item.bundle_id for item in found.candidates] == ["com.example.sim-a"]
        absent = service.lookup_application(platform="ios", name="Example", explicit_reference="com.example.sim-b", target_id="sim-a")
        assert absent.status == "complete"
        assert not absent.candidates
        for _ in range(2):
            result = service.list_applications(platform="ios", target_id="phone")
            assert (result.status, result.code) == ("unsupported", "unsupported")
        result = service.lookup_application(platform="ios", name="Example", explicit_reference="com.example.app", target_id="phone")
        assert [item.bundle_id for item in result.candidates] == ["com.example.app"]
        assert result.candidates[0].source == "physical_installation_unverified"
        for udid in ("phone", "sim-a"):
            result = service.lookup_application(platform="ios", name="Another App", explicit_reference="com.example.ipa", target_id=udid)
            assert [item.bundle_id for item in result.candidates] == ["com.example.ipa"]
        app = tmp_path / "Example.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.local"}))
        result = service.lookup_application(platform="ios", name="Example", explicit_reference=str(app), target_id="sim-a")
        assert [(item.app_path, item.bundle_id) for item in result.candidates] == [(app.resolve(), "com.example.local")]
        result = service.lookup_application(platform="ios", name="Example", explicit_reference=str(app), target_id="phone")
        assert not result.candidates
        for reference in (str(app.with_suffix(".ipa")), "bad bundle identifier"):
            assert service.lookup_application(platform="ios", name="Example", explicit_reference=reference, target_id="sim-a").status == "failed"
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": " com.example.local "}))
        assert service.lookup_application(platform="ios", name="Example", explicit_reference=str(app), target_id="sim-a").status == "failed"
        assert service.lookup_application(platform="ios", name="Example", explicit_reference="com.example.app", target_id="missing").status == "failed"
    finally:
        boundary.clear_application_inventory_cache()


@pytest.mark.parametrize(
    "output",
    [
        b"not metadata",
        b"[]",
        b'{"com.example.app": {"CFBundleIdentifier": "com.other.app"}}',
        b'{"com.example.app": {}, "com.example.app": {}}',
        b'{"com.example.app": {"unknown": NaN}}',
    ],
)
def test_ios_malformed_inventory_does_not_establish_absence(output):
    from fsq_agent.environments.providers import _ios

    with pytest.raises(ValueError, match="Invalid iOS application"):
        _ios._parse_simulator_listapps(output, float("inf"))


def test_ios_inventory_rejects_exceeded_limits_without_partial_results(monkeypatch):
    from fsq_agent.environments.providers import _ios

    payload = b'{"com.example.app": {}, "com.example.other": {}}'
    monkeypatch.setattr(_ios, "_LISTAPPS_MAX_BYTES", len(payload) - 1)
    with pytest.raises(ValueError, match="size limit"):
        _ios._parse_simulator_listapps(payload, float("inf"))
    monkeypatch.setattr(_ios, "_LISTAPPS_MAX_BYTES", len(payload))
    monkeypatch.setattr(_ios, "_LISTAPPS_MAX_CANDIDATES", 1)
    with pytest.raises(ValueError, match="inventory shape"):
        _ios._parse_simulator_listapps(payload, float("inf"))


@pytest.mark.parametrize(
    ("kind", "team", "identity", "wda", "expected"),
    [
        ("device", "TEAM123456", "Apple Development", None, "ready"),
        ("device", "TEAM123456", None, None, "unavailable"),
        ("device", "private invalid team", "Apple Development", None, "unavailable"),
        ("device", "TEAM123456", "Apple Development", "private invalid bundle", "unavailable"),
        ("simulator", None, None, None, "not_applicable"),
        (None, None, None, None, "not_applicable"),
    ],
)
def test_ios_signing_runs_while_application_is_pending(monkeypatch, kind, team, identity, wda, expected):
    from types import SimpleNamespace

    from fsq_agent.environments.providers import _ios
    from fsq_agent.models import IOSDevice, IOSDeviceDiscoveryResult

    monkeypatch.setattr(_ios.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_ios, "_active_developer_directory", lambda: Path("/Applications/Xcode.app/Contents/Developer"))
    monkeypatch.setattr(_ios, "_developer_directory_is_full_xcode", lambda path: True)
    monkeypatch.setattr(_ios, "_check_ios_appium_connect_runtime", lambda: _ios._fact("appium_connect_runtime", message="ready"))
    devices = [IOSDevice(udid="chosen", name="Chosen", kind=kind, state="connected" if kind == "device" else "booted")] if kind else []
    monkeypatch.setattr(_ios, "discover_ios_devices", lambda: IOSDeviceDiscoveryResult(devices=devices))
    settings = SimpleNamespace(udid="chosen", xcode_org_id=team, xcode_signing_id=identity, updated_wda_bundle_id=wda)
    facts = _ios.ios_prerequisites(settings, application_pending=True, require_explicit_selection=True)
    assert tuple(fact.identifier for fact in facts[4:7]) == ("device_selection", "wda_signing", "application_identifier")
    assert facts[5].status == expected
    if kind is None:
        assert facts[5].code == "ios.dependency_unavailable"
    for secret in filter(None, (team, identity, wda)):
        assert secret not in facts[5].model_dump_json()


def test_ios_simulator_uses_fixed_listapps_and_allowlisted_metadata(monkeypatch):
    from fsq_agent.environments.providers import _ios

    commands = []

    def run(command, deadline):
        commands.append((command, deadline))
        return b'{"com.example.app":{"CFBundleDisplayName":"Example","LocalizedDisplayName":"Localized","Bundle":"/private/path"},"com.apple.system":{}}'

    monkeypatch.setattr(_ios, "_read_bounded_tool", run)
    deadline = 123.0
    apps = _ios._parse_simulator_listapps(_ios._simulator_listapps_output("selected", deadline), deadline)
    assert commands == [(["/usr/bin/xcrun", "simctl", "listapps", "selected"], deadline)]
    assert [(app.bundle_id, app.label, app.aliases, app.app_path) for app in apps] == [
        ("com.apple.system", "com.apple.system", (), None),
        ("com.example.app", "Localized", ("Example",), None),
    ]


def test_ios_openstep_listapps_is_converted_without_using_reported_paths(monkeypatch):
    from fsq_agent.environments.providers import _ios

    source = b'{ "com.example.app" = { Bundle = "/private/app"; }; }'

    def convert(command, deadline, *, data):
        assert command == ["/usr/bin/plutil", "-convert", "xml1", "-o", "-", "--", "-"]
        assert data == source
        return plistlib.dumps({"com.example.app": {"Bundle": "/private/app"}})

    monkeypatch.setattr(_ios, "_read_bounded_tool", convert)
    (candidate,) = _ios._parse_simulator_listapps(source, float("inf"))
    assert candidate.bundle_id == "com.example.app"
    assert candidate.app_path is None


@pytest.mark.parametrize("error", [TimeoutError, OSError])
def test_ios_failed_simulator_query_is_neither_unsupported_nor_empty(monkeypatch, error):
    from fsq_agent.environments.providers import _ios
    from fsq_agent.models import IOSDevice, IOSDeviceDiscoveryResult

    device = IOSDevice(udid="sim-error", name="Selected", kind="simulator", state="booted")
    monkeypatch.setattr(_ios, "discover_ios_devices", lambda *a: IOSDeviceDiscoveryResult(devices=[device]))

    def fail(*args):
        raise error("private tool details")

    monkeypatch.setattr(_ios, "_simulator_listapps_output", fail)
    service = PlatformRuntimeService()
    inventory = service.list_applications(platform="ios", target_id=device.udid, force_refresh=True)
    lookup = service.lookup_application(platform="ios", name="App", explicit_reference="com.example.app", target_id=device.udid)
    for result in (inventory, lookup):
        assert result.status == "failed"
        assert result.code == ("discovery_timeout" if error is TimeoutError else "discovery_failed")
        assert "private tool details" not in result.model_dump_json()
