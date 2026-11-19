# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from fsq_agent.core.interfaces import DriverFactory
from fsq_agent.models import (
    IOSClearTextParams,
    IOSHarnessSettings,
    IOSInputTextParams,
    IOSLaunchAppParams,
    IOSLocator,
    IOSSwipeParams,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
if sys.platform != "darwin":
    pytest.skip("Real SDK managed XCUITest ownership proof requires macOS and /usr/sbin/lsof.", allow_module_level=True)

import fcntl

from appium_connect_agent import automation as aca_automation

assert callable(aca_automation.Session.dispose), "The mandatory pinned SDK must provide public Session.dispose."

_NODE_XCUITEST_PROTOCOL_PEER = r"""import http.server
import json
import os
import signal
import socket
import sys
from pathlib import Path

entry = Path(sys.argv[1])
args = sys.argv[2:]
def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default
host = option('--address')
port = int(option('--port'))
base = option('--base-path', '')
settings = json.loads(entry.read_text())
journal = Path(settings['journal'])
def record(value):
    with journal.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
def terminated(number, frame):
    record({'signal': number})
    raise SystemExit(0)
signal.signal(signal.SIGTERM, terminated)
record({'start': os.getpid()})
os.write(1, b'owned ios stdout\n')
os.write(2, b'owned ios stderr\n')

class Handler(http.server.BaseHTTPRequestHandler):
    def respond(self, value):
        body = json.dumps({'value': value}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def disconnect(self):
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()
    def do_GET(self):
        record({'method': 'GET', 'path': self.path})
        if self.path == base + '/status':
            self.respond({'ready': True, 'build': {'version': '3.7.0'}})
        elif self.path.startswith(base + '/session/isolated-ios-session/element/isolated-ios-el/'):
            current = json.loads(entry.read_text())
            suffix = self.path.removeprefix(base + '/session/isolated-ios-session/element/isolated-ios-el/')
            attrs = current.get('attributes', {})
            if suffix == 'rect':
                self.respond(attrs.get('rect', {'x': 20, 'y': 160, 'width': 340, 'height': 44}))
            elif suffix == 'displayed':
                self.respond(attrs.get('displayed', True))
            elif suffix == 'enabled':
                self.respond(attrs.get('enabled_bool', True))
            elif suffix == 'text':
                self.respond(attrs.get('value', ''))
            elif suffix.startswith('attribute/'):
                attr_name = suffix.removeprefix('attribute/')
                self.respond(attrs.get(attr_name))
            else:
                self.send_error(404)
        else:
            self.send_error(404)
    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(size)) if size else None
        record({'method': 'POST', 'path': self.path, 'body': payload})
        current = json.loads(entry.read_text())
        mode = current['mode']
        if self.path == base + '/session':
            if mode == 'create-disconnect':
                self.disconnect()
            elif mode == 'create-malformed':
                self.respond({'capabilities': {}})
            else:
                self.respond({'sessionId': 'isolated-ios-session', 'capabilities': {'platformName': 'iOS', 'appium:automationName': 'XCUITest'}})
        elif self.path == base + '/session/isolated-ios-session/elements':
            self.respond([{'element-6066-11e4-a52e-4f735466cecf': 'isolated-ios-el'}])
        elif self.path == base + '/session/isolated-ios-session/element/isolated-ios-el/clear':
            ph = current.get('attributes', {}).get('placeholderValue')
            current['attributes']['value'] = ph if ph is not None else ''
            entry.write_text(json.dumps(current))
            self.respond(None)
        elif self.path == base + '/session/isolated-ios-session/element/isolated-ios-el/value':
            if mode == 'send-keys-disconnect':
                self.disconnect()
            else:
                text = payload.get('text', '')
                current['attributes']['value'] = text
                entry.write_text(json.dumps(current))
                self.respond(None)
        elif self.path == base + '/session/isolated-ios-session/actions':
            self.respond(None)
        elif self.path == base + '/session/isolated-ios-session/execute/sync':
            script = payload.get('script') if isinstance(payload, dict) else None
            if script == 'mobile: deviceScreenInfo':
                self.respond({'screenSize': {'width': 402, 'height': 874}})
            elif script == 'mobile: keys':
                args_list = payload.get('args', [{}])
                keys = args_list[0].get('keys', []) if args_list else []
                current['attributes']['value'] = ''.join(str(k) for k in keys)
                entry.write_text(json.dumps(current))
                self.respond(None)
            else:
                self.respond(None)
        else:
            self.send_error(404)
    def do_DELETE(self):
        record({'method': 'DELETE', 'path': self.path})
        if json.loads(entry.read_text())['mode'] == 'quit-disconnect':
            self.disconnect()
        else:
            self.respond(None)
    def log_message(self, format, *args):
        pass

class LoopbackHTTPServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        http.server.ThreadingHTTPServer.server_bind.__wrapped__(self) if hasattr(http.server.ThreadingHTTPServer.server_bind, '__wrapped__') else None
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

LoopbackHTTPServer((host, port), Handler).serve_forever()
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@dataclass
class _IsolatedIOSManagedDriver:
    root: Path
    entry: Path
    journal: Path
    driver: Any
    signals: list[tuple[int, int]]
    initial_threads: frozenset[threading.Thread]

    @property
    def lock_path(self) -> Path:
        return self.root.parent / "Library" / "Application Support" / "appium-connect-agent" / "runtime.lock"

    def mode(self, value: str, **options: object) -> None:
        _write_json(self.entry, {"mode": value, "journal": str(self.journal), **options})

    def events(self) -> list[dict[str, Any]]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def service(self) -> dict[str, Any]:
        return json.loads((self.root / "records" / "service.json").read_text())


@pytest.fixture
def managed_ios_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_IsolatedIOSManagedDriver]:
    from appium_connect_agent import automation as aca_auto
    from appium_connect_agent.runtime import _coordination

    from fsq_agent.drivers.ios import _appium_connect

    home = tmp_path.resolve() / "account"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(_coordination, "_account_home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")

    root = home / ".appium-connect-agent"
    appium_package = root / "appium" / "node_modules" / "appium"
    u2_package = root / "driver-home" / "node_modules" / "appium-uiautomator2-driver"
    xcuitest_package = root / "driver-home" / "node_modules" / "appium-xcuitest-driver"
    for directory in (appium_package, u2_package, xcuitest_package, root / "records", root / "cache", root / "tmp"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    _write_json(root / "managed.json", {"owner": "appium-connect-agent", "schema": 1})
    _write_json(appium_package / "package.json", {"name": "appium", "version": "3.7.0"})
    _write_json(u2_package / "package.json", {"name": "appium-uiautomator2-driver", "version": "8.7.0"})
    _write_json(xcuitest_package / "package.json", {"name": "appium-xcuitest-driver", "version": "10.12.1"})
    _write_json(
        root / "records" / "installation.json",
        {
            "schema": 1,
            "state": "complete",
            "operation_id": "isolated-ios-installation",
            "combination_id": "macos-android-uiautomator2",
            "changed": True,
        },
    )

    node = tmp_path / "node"
    node.write_text(f"#!{sys.executable}\n{_NODE_XCUITEST_PROTOCOL_PEER}")
    node.chmod(0o755)
    monkeypatch.setattr(_appium_connect, "_resolve_managed_local_settings", lambda settings_cls: settings_cls(node_executable=node))
    monkeypatch.setattr(
        aca_auto,
        "list_ios_devices",
        lambda *, control=None: (SimpleNamespace(id="00008110-0011223344556677", state="booted", kind="simulator"),),
    )

    signals: list[tuple[int, int]] = []
    original_killpg = os.killpg

    def observe_killpg(group: int, number: int) -> None:
        if number:
            signals.append((group, number))
        original_killpg(group, number)

    monkeypatch.setattr(os, "killpg", observe_killpg)
    settings = IOSHarnessSettings()
    settings.bundle_id = "com.apple.Preferences"
    settings.udid = "00008110-0011223344556677"
    driver = DriverFactory().create_ios_driver(settings)
    env = _IsolatedIOSManagedDriver(
        root=root,
        entry=appium_package / "index.js",
        journal=tmp_path / "ios_protocol.jsonl",
        driver=driver,
        signals=signals,
        initial_threads=frozenset(threading.enumerate()),
    )
    env.mode("active")
    try:
        yield env
    finally:
        driver.close()


def _assert_account_lock(env: _IsolatedIOSManagedDriver, *, held: bool) -> None:
    with env.lock_path.open("r+b") as stream:
        if held:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _assert_peer_settled(env: _IsolatedIOSManagedDriver, pid: int, *, expected_state: str, session: Any) -> None:
    assert session.session_info().state == expected_state
    assert env.driver._session is None
    assert env.driver._connection is None
    assert env.driver._managed_scope is None
    assert env.driver._runtime is None
    assert env.driver._executor is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert env.service()["state"] == "stopped"
    _assert_account_lock(env, held=False)


@pytest.mark.parametrize("mode", ["create-disconnect", "create-malformed"])
def test_ios_create_unknown_quarantines_session_until_explicit_close_and_frees_xcuitest_reservation(
    managed_ios_driver: _IsolatedIOSManagedDriver,
    mode: str,
) -> None:
    managed_ios_driver.mode(mode)
    res = managed_ios_driver.driver.launch_app(IOSLaunchAppParams())
    assert res["status"] == "failed"
    assert res["metadata"]["backend_code"] == "session_create_unknown"
    assert res["metadata"]["reason"] == "cleanup_pending"
    assert res["metadata"]["session_state"] == "uncertain"

    retained_session = managed_ios_driver.driver._session
    assert retained_session is not None
    assert retained_session.session_info().state == "uncertain"
    starts = [ev["start"] for ev in managed_ios_driver.events() if "start" in ev]
    assert len(starts) == 1
    pid = starts[0]
    _assert_account_lock(managed_ios_driver, held=True)

    # Subsequent calls fail fast with cleanup_pending and NEVER leak/spawn a second peer or recreate the session
    with pytest.raises(ToolExecutionError) as ctx_err:
        managed_ios_driver.driver.context()
    assert ctx_err.value.context["reason"] == "cleanup_pending"
    retry = managed_ios_driver.driver.launch_app(IOSLaunchAppParams())
    assert retry["status"] == "failed"
    assert retry["metadata"]["reason"] == "cleanup_pending"
    assert [ev["start"] for ev in managed_ios_driver.events() if "start" in ev] == [pid]

    # Explicit close() runs public Session.dispose() on the owner thread and releases PGID + account lock + UDID reservation
    managed_ios_driver.driver.close()
    _assert_peer_settled(managed_ios_driver, pid, expected_state="disposed", session=retained_session)
    assert managed_ios_driver.signals == [(pid, signal.SIGTERM)]


def test_ios_quit_unknown_settles_via_real_sdk_dispose_and_logs_safe_diagnostic(
    managed_ios_driver: _IsolatedIOSManagedDriver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    managed_ios_driver.mode("active")
    launch = managed_ios_driver.driver.launch_app(IOSLaunchAppParams())
    assert launch["status"] == "passed"
    session = managed_ios_driver.driver._session
    assert session is not None
    pid = next(ev["start"] for ev in managed_ios_driver.events() if "start" in ev)

    managed_ios_driver.mode("quit-disconnect")
    caplog.set_level("INFO")
    managed_ios_driver.driver.close()

    _assert_peer_settled(managed_ios_driver, pid, expected_state="disposed", session=session)
    records = [rec for rec in caplog.records if "settled an unconfirmed remote quit" in rec.message]
    assert len(records) == 1
    assert getattr(records[0], "backend_code", None) == "session_quit_unknown"
    assert getattr(records[0], "remote_quit_confirmed", None) is False
    assert getattr(records[0], "terminal_disposal_completed", None) is True

    # Closed driver rejects post-close operations cleanly
    with pytest.raises(ToolExecutionError) as post_close:
        managed_ios_driver.driver.context()
    assert post_close.value.context["reason"] == "closed"


def test_ios_real_sdk_wire_swipe_input_clear_and_response_unknown_non_replay(
    managed_ios_driver: _IsolatedIOSManagedDriver,
) -> None:
    base_attrs = {
        "type": "XCUIElementTypeTextField",
        "name": "email_field",
        "label": "Email",
        "value": "old@example.com",
        "placeholderValue": "Enter email",
        "enabled": "true",
        "displayed": True,
        "rect": {"x": 20, "y": 180, "width": 340, "height": 44},
    }
    managed_ios_driver.mode("active", attributes=dict(base_attrs))

    # 1. Full-screen swipe dispatches W3C pointer actions within safe 20%-80% interior coordinates
    swipe_res = managed_ios_driver.driver.swipe(IOSSwipeParams(direction="up"))
    assert swipe_res["status"] == "passed"
    action_posts = [ev for ev in managed_ios_driver.events() if ev.get("path", "").endswith("/actions")]
    assert len(action_posts) == 1
    moves = [item for item in action_posts[0]["body"]["actions"][0]["actions"] if item.get("type") == "pointerMove"]
    assert moves[0]["x"] == 201
    assert moves[0]["y"] == 699
    assert moves[1]["x"] == 201
    assert moves[1]["y"] == 175

    # 2. inputText clears existing text, types new text, and verifies exact post-write value readback over wire
    in_res = managed_ios_driver.driver.input_text(
        IOSInputTextParams(
            locator=IOSLocator(accessibility_id="email_field"),
            text="alice@example.com",
            clear=True,
        )
    )
    assert in_res["status"] == "passed"

    # 3. clearText clears to placeholderValue and passes placeholder-aware empty verification
    clr_res = managed_ios_driver.driver.clear_text(IOSClearTextParams(locator=IOSLocator(accessibility_id="email_field")))
    assert clr_res["status"] == "passed"

    # 4. Mid-POST disconnect during send_keys sets request_state=response_unknown, NEVER sends mobile: keys replay, and quarantines uncertain session
    managed_ios_driver.mode("send-keys-disconnect", attributes=dict(base_attrs))
    unk_res = managed_ios_driver.driver.input_text(
        IOSInputTextParams(
            locator=IOSLocator(accessibility_id="email_field"),
            text="never_duplicate@example.com",
        )
    )
    assert unk_res["status"] == "failed"
    assert unk_res["metadata"]["request_state"] == "response_unknown"
    mobile_keys_posts = [ev for ev in managed_ios_driver.events() if ev.get("path", "").endswith("/execute/sync") and isinstance(ev.get("body"), dict) and ev["body"].get("script") == "mobile: keys"]
    assert mobile_keys_posts == []


@pytest.mark.parametrize("mode", ["active", "quit-disconnect"])
def test_ios_executor_shutdown_failure_retains_executor_and_quit_unknown_for_second_close(
    managed_ios_driver: _IsolatedIOSManagedDriver,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    from appium_connect_agent import automation as aca_automation

    caplog.set_level("INFO", logger="fsq_agent.drivers.ios._appium_connect")
    managed_ios_driver.mode("active")
    assert managed_ios_driver.driver.launch_app(IOSLaunchAppParams())["status"] == "passed"
    session = managed_ios_driver.driver._session
    executor = managed_ios_driver.driver._executor
    assert session is not None
    assert executor is not None
    pid = next(ev["start"] for ev in managed_ios_driver.events() if "start" in ev)
    if mode == "quit-disconnect":
        managed_ios_driver.mode("quit-disconnect")

    executor_type = type(executor)
    shutdown_error = OSError("controlled ios worker-shutdown failure")

    def fail_shutdown(target_executor: Any, *, wait: bool = True, cancel_futures: bool = False) -> None:
        assert target_executor is executor
        _ = (wait, cancel_futures)
        raise shutdown_error

    with monkeypatch.context() as patch:
        patch.setattr(executor_type, "shutdown", fail_shutdown)
        if mode == "quit-disconnect":
            with pytest.raises(BaseExceptionGroup) as combined:
                managed_ios_driver.driver.close()
            primary, secondary = combined.value.exceptions
            assert isinstance(primary, aca_automation.ResourceStateError)
            assert primary.code == "session_quit_unknown"
            assert secondary is shutdown_error
        else:
            with pytest.raises(OSError, match="controlled ios worker-shutdown failure") as failed:
                managed_ios_driver.driver.close()
            assert failed.value is shutdown_error

    expected_state = "disposed" if mode == "quit-disconnect" else "closed"
    assert session.session_info().state == expected_state
    assert managed_ios_driver.driver._executor is executor
    assert managed_ios_driver.driver._cleanup_pending is True
    assert managed_ios_driver.driver._closed is False
    assert not any(getattr(rec, "backend_code", None) == "session_quit_unknown" for rec in caplog.records)

    # Second close() completes executor shutdown and emits deferred diagnostic once
    managed_ios_driver.driver.close()
    _assert_peer_settled(managed_ios_driver, pid, expected_state=expected_state, session=session)
    if mode == "quit-disconnect":
        records = [rec for rec in caplog.records if getattr(rec, "backend_code", None) == "session_quit_unknown"]
        assert len(records) == 1
        assert records[0].terminal_disposal_completed is True


def test_ios_driver_forwards_initial_wda_policies_on_create_and_new_session_replacement(
    managed_ios_driver: _IsolatedIOSManagedDriver,
) -> None:
    from fsq_agent.models import IOSLaunchAppParams

    settings = IOSHarnessSettings(
        xcuitest_wait_for_idle_timeout_seconds=2.0,
        xcuitest_snapshot_max_depth=25,
        xcuitest_accessibility_deadline_seconds=10.0,
    )
    settings.bundle_id = "com.apple.Preferences"
    settings.udid = "00008110-0011223344556677"
    tuned_driver = DriverFactory().create_ios_driver(settings)
    try:
        first = tuned_driver.launch_app(IOSLaunchAppParams())
        assert first["status"] == "passed"
        second = tuned_driver.launch_app(IOSLaunchAppParams(bundle_id="com.apple.MobileSMS", new_session=True))
        assert second["status"] == "passed"
    finally:
        tuned_driver.close()

    create_entries = [entry for entry in managed_ios_driver.events() if entry.get("method") == "POST" and entry.get("path") == "/session"]
    assert len(create_entries) == 2
    for entry in create_entries:
        caps = entry["body"]["capabilities"]["alwaysMatch"]
        assert caps["appium:waitForIdleTimeout"] == 2.0
        assert caps["appium:settings[snapshotMaxDepth]"] == 25
        assert caps["appium:settings[accessibilityDeadline]"] == 10.0
