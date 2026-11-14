# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fsq_agent.core.interfaces import DriverFactory
from fsq_agent.models import AndroidHarnessSettings, ToolExecutionError

if TYPE_CHECKING:
    from collections.abc import Iterator

aca_automation = pytest.importorskip("appium_connect_agent.automation", reason="Android lifecycle verification requires appium-connect-agent.")
pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="Real SDK managed ownership proof requires macOS and its trusted /usr/sbin/lsof."),
    pytest.mark.skipif(
        not callable(getattr(aca_automation.Session, "dispose", None)),
        reason="Pinned SDK predates public Session.dispose; use the audited terminal-disposal SDK source on temporary PYTHONPATH without installing or upgrading it.",
    ),
]

# This is only a local wire peer. Runtime, Session, usage/generation authority,
# account locking, process ownership proof, draining, and cleanup remain real SDK code.
_NODE_PROTOCOL_PEER = r"""import http.server
import json
import os
import signal
import socket
import sys
import time
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
os.write(1, b'owned stdout\n')
os.write(2, b'owned stderr\n')
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
        elif self.path == base + '/session/isolated-session/screenshot':
            # Keep the request unanswered until the real client deadline expires.
            # The entire peer, including this daemon thread, belongs to the SDK PGID.
            while True:
                time.sleep(1)
        else:
            self.send_error(404)
    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(size)) if size else None
        record({'method': 'POST', 'path': self.path, 'body': payload})
        mode = json.loads(entry.read_text())['mode']
        if self.path == base + '/session':
            if mode == 'create-disconnect':
                self.disconnect()
            elif mode == 'create-malformed':
                self.respond({'capabilities': {}})
            else:
                self.respond({'sessionId': 'isolated-session', 'capabilities': {'platformName': 'Android', 'appium:automationName': 'UiAutomator2'}})
        elif self.path == base + '/session/isolated-session/execute/sync':
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
http.server.ThreadingHTTPServer((host, port), Handler).serve_forever()
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@dataclass
class _IsolatedManagedDriver:
    root: Path
    entry: Path
    journal: Path
    driver: Any
    signals: list[tuple[int, int]]
    initial_threads: frozenset[threading.Thread]

    @property
    def lock_path(self) -> Path:
        return self.root.parent / "Library" / "Application Support" / "appium-connect-agent" / "runtime.lock"

    def mode(self, value: str) -> None:
        _write_json(self.entry, {"mode": value, "journal": str(self.journal)})

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def service(self) -> dict[str, Any]:
        return json.loads((self.root / "records" / "service.json").read_text())


@pytest.fixture
def managed_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_IsolatedManagedDriver]:
    from appium_connect_agent.runtime import _coordination

    from fsq_agent.drivers.android import _appium_connect

    # SDK account_home uses pwd, not HOME: isolate that exact filesystem boundary.
    # Keep the real process-wide mutex and flock implementation intact.
    home = tmp_path.resolve() / "account"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(_coordination, "_account_home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME"):
        monkeypatch.delenv(key, raising=False)
    root = home / ".appium-connect-agent"
    appium_package = root / "appium" / "node_modules" / "appium"
    driver_package = root / "driver-home" / "node_modules" / "appium-uiautomator2-driver"
    for directory in (appium_package, driver_package, root / "records", root / "cache", root / "tmp"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    _write_json(root / "managed.json", {"owner": "appium-connect-agent", "schema": 1})
    _write_json(appium_package / "package.json", {"name": "appium", "version": "3.7.0"})
    _write_json(driver_package / "package.json", {"name": "appium-uiautomator2-driver", "version": "8.7.0"})
    _write_json(
        root / "records" / "installation.json",
        {"schema": 1, "state": "complete", "operation_id": "isolated-test-installation", "combination_id": "macos-android-uiautomator2", "changed": True},
    )
    node = tmp_path / "node"
    node.write_text(f"#!{sys.executable}\n{_NODE_PROTOCOL_PEER}")
    node.chmod(0o755)
    # Bypass host Node/JDK/Android discovery only; managed-root validation and the
    # real trusted lsof listener/PGID ownership proof are deliberately untouched.
    monkeypatch.setattr(_appium_connect, "_resolve_managed_local_settings", lambda settings_cls: settings_cls(node_executable=node))
    monkeypatch.setattr(_appium_connect, "DEFAULT_OPERATION_TIMEOUT_SECONDS", 0.25)
    signals: list[tuple[int, int]] = []
    original_killpg = os.killpg

    def observe_killpg(group: int, number: int) -> None:
        if number:
            signals.append((group, number))
        original_killpg(group, number)

    monkeypatch.setattr(os, "killpg", observe_killpg)
    driver = DriverFactory().create_android_driver(AndroidHarnessSettings(), app_id="com.example.isolated", serial="isolated-protocol-device")
    environment = _IsolatedManagedDriver(root, appium_package / "index.js", tmp_path / "protocol.jsonl", driver, signals, frozenset(threading.enumerate()))
    environment.mode("create-disconnect")
    try:
        yield environment
    finally:
        driver.close()


@dataclass(frozen=True)
class _ObservedOwner:
    session: Any
    runtime: Any
    scope: Any
    connection: Any
    executor: Any
    pid: int
    group_id: int
    drain: threading.Thread
    worker: threading.Thread


def _observe_owner(environment: _IsolatedManagedDriver) -> _ObservedOwner:
    driver = environment.driver
    assert driver._session is not None
    assert driver._runtime is not None
    assert driver._managed_scope is not None
    assert driver._connection is not None
    assert driver._executor is not None
    starts = [event["start"] for event in environment.events() if "start" in event]
    assert len(starts) == 1
    pid = starts[0]
    group_id = os.getpgid(pid)
    assert group_id == pid
    new_threads = set(threading.enumerate()) - environment.initial_threads
    drains = [thread for thread in new_threads if thread.name == "appium-connect-agent-managed-drain"]
    workers = [thread for thread in new_threads if thread.name.startswith("fsq-android-driver")]
    assert len(drains) == 1
    assert len(workers) == 1
    assert drains[0].is_alive()
    assert workers[0].is_alive()
    return _ObservedOwner(driver._session, driver._runtime, driver._managed_scope, driver._connection, driver._executor, pid, group_id, drains[0], workers[0])


def _assert_account_lock(environment: _IsolatedManagedDriver, *, held: bool) -> None:
    import fcntl

    # Probe the exact isolated lock file with a separate real OS open description;
    # never call SDK lock/lease helpers or replace its ownership authority.
    with environment.lock_path.open("r+b") as stream:
        if held:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _create_unknown(environment: _IsolatedManagedDriver) -> _ObservedOwner:
    with pytest.raises(aca_automation.ResourceStateError) as failed:
        environment.driver.context()
    assert failed.value.code == "session_create_unknown"
    owner = _observe_owner(environment)
    assert failed.value.session is owner.session
    assert owner.session.session_info().state == "uncertain"
    assert environment.service()["state"] == "active"
    assert environment.signals == []
    _assert_account_lock(environment, held=True)
    return owner


def _assert_quarantined(environment: _IsolatedManagedDriver, owner: _ObservedOwner) -> None:
    driver = environment.driver
    events = environment.events()
    record = environment.service()
    info = owner.session.session_info()
    signals = list(environment.signals)
    with pytest.raises(ToolExecutionError):
        driver.context()
    assert driver.launch_app({})["status"] != "passed"
    assert driver._session is owner.session
    assert driver._runtime is owner.runtime
    assert driver._managed_scope is owner.scope
    assert driver._connection is owner.connection
    assert driver._executor is owner.executor
    assert owner.worker.is_alive()
    assert owner.session.session_info() == info
    assert environment.service() == record
    assert environment.events() == events
    assert environment.signals == signals


def _assert_peer_exited(owner: _ObservedOwner) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(owner.pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(owner.group_id, 0)
    # The SDK, not this assertion, must already have reaped its exact owned child.
    with pytest.raises(ChildProcessError):
        os.waitpid(owner.pid, os.WNOHANG)
    assert not owner.drain.is_alive()
    assert owner.drain not in threading.enumerate()


def _assert_owner_settled(environment: _IsolatedManagedDriver, owner: _ObservedOwner, *, state: str = "disposed") -> None:
    from appium_connect_agent.runtime import InvalidStateError

    # Public disposed proves local client/stream/usage/managed settlement, not
    # remote Session deletion. Independently observe process, thread, record,
    # account flock, and stale public connection facts without SDK private reads.
    assert owner.session.session_info().state == state
    assert environment.driver._session is None
    assert environment.driver._connection is None
    assert environment.driver._managed_scope is None
    assert environment.driver._runtime is None
    assert environment.driver._executor is None
    assert not owner.worker.is_alive()
    _assert_peer_exited(owner)
    assert environment.service()["state"] == "stopped"
    _assert_account_lock(environment, held=False)
    with pytest.raises(InvalidStateError) as invalidated:
        owner.connection.acquire_usage()
    assert invalidated.value.code == "connection_closed"


def _fail_stopped_record_publication(environment: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    failures: list[bool] = []
    original_replace = os.replace

    def fail_publish(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        if not failures and Path(destination) == environment.root / "records" / "service.json" and json.loads(Path(source).read_text())["state"] == "stopped":
            failures.append(True)
            raise OSError("controlled isolated service-record publication failure")
        original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_publish)
    return failures


@pytest.mark.parametrize("mode", ["create-disconnect", "create-malformed"])
def test_create_unknown_retains_real_owner_until_explicit_terminal_close(managed_driver: _IsolatedManagedDriver, mode: str) -> None:
    managed_driver.mode(mode)
    owner = _create_unknown(managed_driver)
    _assert_quarantined(managed_driver, owner)

    managed_driver.driver.close()

    _assert_owner_settled(managed_driver, owner)
    assert managed_driver.signals == [(owner.group_id, signal.SIGTERM)]
    assert not any(event.get("method") == "DELETE" for event in managed_driver.events())
    assert len([event for event in managed_driver.events() if "start" in event]) == 1
    events = managed_driver.events()
    managed_driver.driver.close()
    owner.session.dispose()
    assert managed_driver.events() == events


def test_real_action_timeout_quarantines_without_reconnect_or_cleanup(managed_driver: _IsolatedManagedDriver) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    with pytest.raises(aca_automation.OperationTimeoutError) as timed_out:
        driver.screenshot()
    assert driver._session is owner.session
    assert timed_out.value.request_state == "response_unknown"
    assert owner.session.session_info().state == "uncertain"
    assert os.getpgid(owner.pid) == owner.group_id
    assert owner.drain.is_alive()
    assert managed_driver.signals == []
    _assert_account_lock(managed_driver, held=True)
    _assert_quarantined(managed_driver, owner)

    driver.close()

    _assert_owner_settled(managed_driver, owner)
    assert owner.session.session_info().appium_session_id == "isolated-session"
    assert not any(event.get("method") == "DELETE" for event in managed_driver.events())
    assert len([event for event in managed_driver.events() if "start" in event]) == 1


@pytest.mark.parametrize("preflight", ["cancelled", "expired"])
def test_public_disposal_preflight_failure_keeps_uncertain_owner(managed_driver: _IsolatedManagedDriver, preflight: str) -> None:
    from appium_connect_agent.runtime import OperationControl

    owner = _create_unknown(managed_driver)
    cancellation = threading.Event()
    if preflight == "cancelled":
        cancellation.set()
    control = OperationControl(timeout_seconds=3.0 if preflight == "cancelled" else 1e-12, cancellation=cancellation)
    error_type = aca_automation.OperationCancelledError if preflight == "cancelled" else aca_automation.OperationTimeoutError
    with pytest.raises(error_type) as pending:
        managed_driver.driver._run_sync(lambda: owner.session.dispose(control=control))
    assert pending.value.session is owner.session
    assert owner.session.session_info().state == "uncertain"
    assert os.getpgid(owner.pid) == owner.group_id
    assert managed_driver.signals == []
    _assert_quarantined(managed_driver, owner)

    managed_driver.driver.close()

    _assert_owner_settled(managed_driver, owner)


@pytest.mark.parametrize("boundary", ["service-record", "account-lock"])
def test_failed_terminal_close_retains_real_cleanup_for_explicit_continuation(
    managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    owner = _create_unknown(managed_driver)
    failures: list[bool] = []
    original_close = os.close
    lock_info = managed_driver.lock_path.stat()

    def close_lock_then_fail(descriptor: int) -> None:
        info = os.fstat(descriptor)
        is_lock = (info.st_dev, info.st_ino) == (lock_info.st_dev, lock_info.st_ino)
        original_close(descriptor)
        if not failures and is_lock:
            failures.append(True)
            raise OSError("controlled isolated account-lock close failure")

    with monkeypatch.context() as boundary_patch:
        if boundary == "service-record":
            failures = _fail_stopped_record_publication(managed_driver, boundary_patch)
        else:
            boundary_patch.setattr(os, "close", close_lock_then_fail)
        with pytest.raises(aca_automation.ResourceStateError) as pending:
            managed_driver.driver.close()

    assert failures
    assert pending.value.code == "terminal_disposal_pending"
    assert pending.value.session is owner.session
    assert pending.value.cleanup_errors
    assert owner.session.session_info().state == "disposing"
    _assert_peer_exited(owner)
    _assert_account_lock(managed_driver, held=boundary == "service-record")
    _assert_quarantined(managed_driver, owner)
    signals = list(managed_driver.signals)

    managed_driver.driver.close()

    _assert_owner_settled(managed_driver, owner)
    assert managed_driver.signals == signals
    assert not any(event.get("method") == "DELETE" for event in managed_driver.events())
    assert len([event for event in managed_driver.events() if "start" in event]) == 1
    managed_driver.driver.close()


def test_active_close_uses_real_quit_then_managed_scope_exit(managed_driver: _IsolatedManagedDriver) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    assert owner.session.session_info().state == "active"
    assert managed_driver.signals == []

    driver.close()

    _assert_owner_settled(managed_driver, owner, state="closed")
    events = managed_driver.events()
    deletion = next(index for index, event in enumerate(events) if event.get("method") == "DELETE")
    termination = next(index for index, event in enumerate(events) if "signal" in event)
    assert deletion < termination
    assert len([event for event in events if event.get("method") == "DELETE"]) == 1
    assert managed_driver.signals == [(owner.group_id, signal.SIGTERM)]
    driver.close()
    assert managed_driver.events() == events


def test_unknown_quit_disposes_locally_and_preserves_original_failure(managed_driver: _IsolatedManagedDriver) -> None:
    managed_driver.mode("quit-disconnect")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)

    with pytest.raises(aca_automation.ResourceStateError) as failed:
        driver.close()

    assert failed.value.code == "session_quit_unknown"
    _assert_owner_settled(managed_driver, owner)
    assert owner.session.session_info().appium_session_id == "isolated-session"
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events


def test_unknown_quit_and_disposal_failure_preserve_order_and_explicit_continuation(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    managed_driver.mode("quit-disconnect")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)

    with monkeypatch.context() as boundary_patch:
        failures = _fail_stopped_record_publication(managed_driver, boundary_patch)
        with pytest.raises(BaseExceptionGroup) as combined:
            driver.close()

    assert failures
    assert len(combined.value.exceptions) == 2
    primary, secondary = combined.value.exceptions
    assert isinstance(primary, aca_automation.ResourceStateError)
    assert isinstance(secondary, aca_automation.ResourceStateError)
    assert primary.code == "session_quit_unknown"
    assert secondary.code == "terminal_disposal_pending"
    assert secondary.session is owner.session
    assert secondary.cleanup_errors
    assert owner.session.session_info().state == "disposing"
    _assert_peer_exited(owner)
    _assert_account_lock(managed_driver, held=True)
    _assert_quarantined(managed_driver, owner)
    signals = list(managed_driver.signals)

    with pytest.raises(aca_automation.ResourceStateError) as continued:
        driver.close()

    assert continued.value is primary
    _assert_owner_settled(managed_driver, owner)
    assert managed_driver.signals == signals
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    assert len([event for event in managed_driver.events() if "start" in event]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events
