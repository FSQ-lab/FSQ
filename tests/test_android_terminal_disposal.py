# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from asyncio import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fsq_agent.core.interfaces import DriverFactory
from fsq_agent.models import AndroidHarnessSettings, ToolExecutionError

if TYPE_CHECKING:
    from collections.abc import Iterator

if sys.platform != "darwin":
    pytest.skip("Real SDK managed ownership proof requires macOS and its trusted /usr/sbin/lsof.", allow_module_level=True)

from appium_connect_agent import automation as aca_automation

assert callable(aca_automation.Session.dispose), "The mandatory pinned SDK must provide public Session.dispose."

# This is only a local wire peer. Runtime, Session, usage/generation authority,
# account locking, process ownership proof, draining, and cleanup remain real SDK code.
_NODE_PROTOCOL_PEER = r"""import http.server
import json
import os
import signal
import socket
import socketserver
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
        elif self.path.startswith(base + '/session/isolated-session/element/isolated-element/'):
            current = json.loads(entry.read_text())
            suffix = self.path.removeprefix(base + '/session/isolated-session/element/isolated-element/')
            name = suffix.removeprefix('attribute/')
            attributes = current.get('attributes', {})
            if name not in attributes or (current.get('written') and name == 'displayed'):
                self.send_error(500, 'Unexpected attribute read')
            elif attributes[name] == '__stall__':
                while True:
                    time.sleep(1)
            else:
                self.respond(attributes[name])
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
        elif self.path == base + '/session/isolated-session/elements':
            current = json.loads(entry.read_text())
            if payload != current.get('selector'):
                self.send_error(400, 'Unexpected locator strategy or constraint')
            else:
                identifiers = ['isolated-element', 'another-element'][:current.get('match_count', 1)]
                self.respond([{'element-6066-11e4-a52e-4f735466cecf': identifier} for identifier in identifiers])
        elif self.path == base + '/session/isolated-session/element/isolated-element/click':
            self.respond(None)
        elif self.path == base + '/session/isolated-session/execute/sync':
            if payload.get('script') == 'mobile: replaceElementValue':
                current = json.loads(entry.read_text())
                current['attributes']['text'] = payload['args'][0]['text']
                current['attributes']['password'] = current.get('password_after', False)
                current['written'] = True
                entry.write_text(json.dumps(current))
            if payload.get('script') == 'mobile: deviceInfo':
                self.respond({'realDisplaySize': '1080x2400'})
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
# HTTPServer.server_bind performs reverse DNS even for numeric loopback. A new
# peer process must not spend its managed-startup budget in the host resolver.
class LoopbackHTTPServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

LoopbackHTTPServer((host, port), Handler).serve_forever()
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

    def mode(self, value: str, **options: object) -> None:
        _write_json(self.entry, {"mode": value, "journal": str(self.journal), **options})

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def service(self) -> dict[str, Any]:
        return json.loads((self.root / "records" / "service.json").read_text())


@pytest.fixture
def managed_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[_IsolatedManagedDriver]:
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
    settings = AndroidHarnessSettings(**getattr(request, "param", {}))
    settings.app_id = "com.example.isolated"
    settings.serial = "isolated-protocol-device"
    driver = DriverFactory().create_android_driver(settings)
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
    with pytest.raises(ToolExecutionError) as unavailable:
        driver.context()
    assert unavailable.value.context["reason"] == "cleanup_pending"
    assert unavailable.value.context["session_state"] == info.state
    action = driver.launch_app({})
    assert action["status"] == "failed"
    assert action["metadata"]["reason"] == "cleanup_pending"
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
    with pytest.raises(ToolExecutionError) as timed_out:
        driver.screenshot({"timeout_seconds": 0.25})
    assert driver._session is owner.session
    assert timed_out.value.context["capture_diagnostics"] == {
        "backend_code": "operation_timeout",
        "request_state": "response_unknown",
        "session_state": "uncertain",
        "stage": "screenshot",
    }
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
def test_failed_terminal_close_retains_real_cleanup_for_explicit_continuation(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
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


def test_unknown_quit_disposes_locally_and_retains_safe_diagnostic_history(managed_driver: _IsolatedManagedDriver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.android._appium_connect")
    managed_driver.mode("quit-disconnect")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)

    driver.close()

    assert driver._terminal_disposal_diagnostic == {
        "backend_code": "session_quit_unknown",
        "request_state": "response_unknown",
        "remote_quit_confirmed": False,
        "terminal_disposal_completed": True,
    }
    _assert_owner_settled(managed_driver, owner)
    assert owner.session.session_info().appium_session_id == "isolated-session"
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events
    assert driver._terminal_disposal_diagnostic["terminal_disposal_completed"] is True
    records = [record for record in caplog.records if getattr(record, "backend_code", None) == "session_quit_unknown"]
    assert len(records) == 1
    assert records[0].request_state == "response_unknown"
    assert records[0].remote_quit_confirmed is False
    assert records[0].terminal_disposal_completed is True


def test_other_real_resource_state_error_is_not_settled_quit_history(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib3 import PoolManager

    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    original_clear = PoolManager.clear
    failures: list[OSError] = []

    def fail_client_pool_clear(pool: PoolManager) -> None:
        if not failures:
            failure = OSError("controlled isolated client-pool close failure")
            failures.append(failure)
            raise failure
        original_clear(pool)

    with monkeypatch.context() as boundary_patch:
        boundary_patch.setattr(PoolManager, "clear", fail_client_pool_clear)
        with pytest.raises(aca_automation.ResourceStateError) as failed:
            driver.close()

    assert failures
    assert failed.value.code == "cleanup_failed"
    assert failed.value.request_state == "response_received"
    assert failed.value.completed_steps == ("session_deleted",)
    assert failed.value.cleanup_errors == tuple(failures)
    assert driver._terminal_disposal_diagnostic is None
    _assert_owner_settled(managed_driver, owner)
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events


def test_real_managed_scope_failure_retains_owners_after_successful_quit(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    with monkeypatch.context() as boundary_patch:
        failures = _fail_stopped_record_publication(managed_driver, boundary_patch)
        with pytest.raises(OSError, match="controlled isolated service-record publication failure"):
            driver.close()

    assert failures
    assert owner.session.session_info().state == "closed"
    _assert_peer_exited(owner)
    _assert_account_lock(managed_driver, held=True)
    _assert_quarantined(managed_driver, owner)
    assert driver._terminal_disposal_diagnostic is None
    events = managed_driver.events()
    signals = list(managed_driver.signals)

    driver.close()

    _assert_owner_settled(managed_driver, owner, state="closed")
    assert managed_driver.events() == events
    assert managed_driver.signals == signals


@pytest.mark.parametrize("mode", ["serve", "quit-disconnect"])
@pytest.mark.parametrize("shutdown_started", [False, True], ids=["before-shutdown", "after-shutdown"])
def test_executor_shutdown_failure_retains_exact_owner_and_explicit_continuation(
    managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch, mode: str, shutdown_started: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.android._appium_connect")
    managed_driver.mode(mode)
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    executor_type = type(owner.executor)
    original_shutdown = executor_type.shutdown
    shutdown_error = OSError("controlled isolated worker-shutdown failure")

    def fail_shutdown(executor: Any, *, wait: bool = True, cancel_futures: bool = False) -> None:
        assert executor is owner.executor
        if shutdown_started:
            original_shutdown(executor, wait=wait, cancel_futures=cancel_futures)
        raise shutdown_error

    with monkeypatch.context() as boundary_patch:
        boundary_patch.setattr(executor_type, "shutdown", fail_shutdown)
        if mode == "quit-disconnect":
            with pytest.raises(BaseExceptionGroup) as combined:
                driver.close()
            primary, secondary = combined.value.exceptions
            assert isinstance(primary, aca_automation.ResourceStateError)
            assert primary.code == "session_quit_unknown"
            assert secondary is shutdown_error
        else:
            with pytest.raises(OSError, match="controlled isolated worker-shutdown failure") as failed:
                driver.close()
            assert failed.value is shutdown_error

    expected_state = "disposed" if mode == "quit-disconnect" else "closed"
    assert owner.session.session_info().state == expected_state
    assert driver._session is None
    assert driver._runtime is None
    assert driver._connection is None
    assert driver._managed_scope is None
    assert driver._executor is owner.executor
    assert owner.worker.is_alive() is not shutdown_started
    _assert_peer_exited(owner)
    _assert_account_lock(managed_driver, held=False)
    events = managed_driver.events()
    with pytest.raises((ToolExecutionError, RuntimeError)):
        driver.context()
    assert managed_driver.events() == events
    assert driver._executor is owner.executor
    if mode == "quit-disconnect":
        assert driver._terminal_disposal_diagnostic == {
            "backend_code": "session_quit_unknown",
            "request_state": "response_unknown",
            "remote_quit_confirmed": False,
            "terminal_disposal_completed": False,
        }
    else:
        assert driver._terminal_disposal_diagnostic is None
    assert not any(getattr(record, "backend_code", None) == "session_quit_unknown" for record in caplog.records)

    driver.close()

    _assert_owner_settled(managed_driver, owner, state=expected_state)
    assert managed_driver.events() == events
    if mode == "quit-disconnect":
        assert driver._terminal_disposal_diagnostic["terminal_disposal_completed"] is True
        records = [record for record in caplog.records if getattr(record, "backend_code", None) == "session_quit_unknown"]
        assert len(records) == 1
        assert records[0].terminal_disposal_completed is True
    driver.close()
    assert managed_driver.events() == events


def test_real_quit_cancellation_is_not_swallowed_by_later_cleanup(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    from appium_connect_agent.runtime import OperationControl

    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    cancellation = threading.Event()
    cancellation.set()
    control = OperationControl(timeout_seconds=3.0, cancellation=cancellation)
    with monkeypatch.context() as boundary_patch:
        boundary_patch.setattr(driver, "_operation_control", lambda timeout: control)
        with pytest.raises(aca_automation.OperationCancelledError) as cancelled:
            driver.close()

    assert cancelled.value.code == "operation_cancelled"
    assert cancelled.value.request_state == "not_sent"
    assert owner.session.session_info().state == "active"
    assert driver._session is owner.session
    assert driver._runtime is owner.runtime
    assert driver._managed_scope is owner.scope
    assert driver._connection is owner.connection
    assert driver._executor is owner.executor
    assert owner.worker.is_alive()
    assert managed_driver.signals == []
    assert not any(event.get("method") == "DELETE" for event in managed_driver.events())
    _assert_account_lock(managed_driver, held=True)
    assert driver._terminal_disposal_diagnostic is None

    with pytest.raises(aca_automation.OperationCancelledError) as continued:
        driver.close()

    assert continued.value is cancelled.value
    _assert_owner_settled(managed_driver, owner, state="closed")
    assert driver._terminal_disposal_diagnostic is None
    driver.close()


def test_unknown_quit_and_disposal_failure_preserve_order_and_explicit_continuation(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.android._appium_connect")
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
    assert driver._terminal_disposal_diagnostic == {
        "backend_code": "session_quit_unknown",
        "request_state": primary.request_state,
        "remote_quit_confirmed": False,
        "terminal_disposal_completed": False,
    }
    assert not any(getattr(record, "backend_code", None) == "session_quit_unknown" for record in caplog.records)
    signals = list(managed_driver.signals)

    driver.close()

    assert driver._terminal_disposal_diagnostic["terminal_disposal_completed"] is True
    records = [record for record in caplog.records if getattr(record, "backend_code", None) == "session_quit_unknown"]
    assert len(records) == 1
    assert records[0].request_state == primary.request_state
    assert records[0].remote_quit_confirmed is False
    assert records[0].terminal_disposal_completed is True
    _assert_owner_settled(managed_driver, owner)
    assert managed_driver.signals == signals
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    assert len([event for event in managed_driver.events() if "start" in event]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events


@pytest.mark.parametrize(
    "managed_driver",
    [
        {},
        {"uiautomator2_wait_for_idle_timeout_ms": None},
        {"uiautomator2_wait_for_idle_timeout_ms": 0},
        {"uiautomator2_wait_for_idle_timeout_ms": 1000},
        {"uiautomator2_wait_for_idle_timeout_ms": 9_007_199_254_740_991},
        {"new_command_timeout_seconds": None},
        {"new_command_timeout_seconds": 0},
        {"new_command_timeout_seconds": 0.125},
        {"new_command_timeout_seconds": 1800.5, "uiautomator2_wait_for_idle_timeout_ms": 0},
    ],
    indirect=True,
    ids=["omitted", "none", "zero", "explicit", "exact-upper-bound", "command-none", "command-zero", "command-fraction", "both-explicit"],
)
def test_initial_idle_policy_reaches_real_sdk_wire_and_preserves_owned_cleanup(managed_driver: _IsolatedManagedDriver, request: pytest.FixtureRequest) -> None:
    managed_driver.mode("serve")
    assert managed_driver.driver.launch_app({})["status"] == "passed"
    owner = _observe_owner(managed_driver)
    managed_driver.driver.close()
    _assert_owner_settled(managed_driver, owner, state="closed")

    requests = [event for event in managed_driver.events() if "method" in event and not event["path"].endswith("/status")]
    assert [(event["method"], event["path"]) for event in requests] == [
        ("POST", "/session"),
        ("POST", "/session/isolated-session/execute/sync"),
        ("DELETE", "/session/isolated-session"),
    ]
    caps = requests[0]["body"]["capabilities"]["alwaysMatch"]
    assert caps["appium:udid"] == "isolated-protocol-device"
    assert caps["appium:appPackage"] == "com.example.isolated"
    assert caps["appium:noReset"] is True
    assert caps["appium:autoLaunch"] is False
    timeout = request.node.callspec.params["managed_driver"].get("new_command_timeout_seconds", 900.0)
    assert caps["appium:newCommandTimeout"] == (900.0 if timeout is None else timeout)
    expected = request.node.callspec.params["managed_driver"].get("uiautomator2_wait_for_idle_timeout_ms")
    key = "appium:settings[waitForIdleTimeout]"
    if expected is None:
        assert key not in caps
    else:
        assert type(caps[key]) is int
        assert caps[key] == expected


_ASSERT_LOCATOR = {"resourceId": "com.example.isolated:id/fab", "accessibilityId": "Pause"}
_ASSERT_SELECTOR = {"using": "xpath", "value": "//*[@resource-id='com.example.isolated:id/fab' and @content-desc='Pause']"}


@pytest.mark.parametrize(
    ("predicate", "attributes", "expected_output", "reads"),
    [
        ({"element": {**_ASSERT_LOCATOR, "enabled": True}}, {"enabled": True}, {"enabled": True}, ["attribute/enabled"]),
        ({"element": {**_ASSERT_LOCATOR, "enabled": False}}, {"enabled": False}, {"enabled": False}, ["attribute/enabled"]),
        (
            {"element": {**_ASSERT_LOCATOR, "focused": False, "selected": True, "enabled": True}},
            {"enabled": True, "selected": True, "focused": False},
            {"enabled": True, "selected": True, "focused": False},
            ["attribute/enabled", "attribute/selected", "attribute/focused"],
        ),
        ({"element": {**_ASSERT_LOCATOR, "checked": True}}, {"checkable": True, "checked": True}, {"checked": True}, ["attribute/checkable", "attribute/checked"]),
        ({"element": {**_ASSERT_LOCATOR, "enabled": False}, "text": {"contains": "running"}}, {"text": "running clock"}, {"text": "running clock"}, ["text"]),
        ({"element": _ASSERT_LOCATOR, "text": {"equals": "running"}}, {"text": "running"}, {"text": "running"}, ["text"]),
        ({"element": _ASSERT_LOCATOR}, {"enabled": False}, {"exists": True}, ["attribute/enabled"]),
        ({"element": _ASSERT_LOCATOR}, {"enabled": None}, {"exists": True}, ["attribute/enabled"]),
    ],
)
def test_assertion_reads_only_needed_attributes_through_real_client(
    managed_driver: _IsolatedManagedDriver,
    predicate: dict[str, object],
    attributes: dict[str, object],
    expected_output: dict[str, object],
    reads: list[str],
) -> None:
    managed_driver.mode("inspect", selector=_ASSERT_SELECTOR, attributes=attributes)
    result = managed_driver.driver.assert_state(predicate)
    assert result == {"status": "passed", "output": expected_output}
    owner = _observe_owner(managed_driver)
    managed_driver.driver.close()
    _assert_owner_settled(managed_driver, owner, state="closed")
    events = managed_driver.events()
    queries = [event for event in events if event.get("path", "").endswith("/elements")]
    assert len(queries) == 1
    assert queries[0]["body"] == _ASSERT_SELECTOR
    prefix = "/session/isolated-session/element/isolated-element/"
    assert [event["path"].removeprefix(prefix) for event in events if event.get("method") == "GET" and event["path"].startswith(prefix)] == reads


@pytest.mark.parametrize("actual", [False, None])
def test_minimal_state_read_still_fails_mismatched_or_unknown_value(managed_driver: _IsolatedManagedDriver, actual: bool | None) -> None:
    managed_driver.mode("inspect", selector=_ASSERT_SELECTOR, attributes={"enabled": actual})
    result = managed_driver.driver.assert_state({"element": {**_ASSERT_LOCATOR, "enabled": True}})
    assert result["status"] == "failed"
    assert result["failure_category"] == "assertion_error"
    assert result["output"] == {"field": "enabled", "expected": True, "actual": actual}


def test_minimal_inspection_still_rejects_ambiguous_target(managed_driver: _IsolatedManagedDriver) -> None:
    managed_driver.mode("inspect", selector=_ASSERT_SELECTOR, attributes={"enabled": True}, match_count=2)
    result = managed_driver.driver.assert_state({"element": {**_ASSERT_LOCATOR, "enabled": True}})
    assert result["status"] == "failed"
    metadata = result["metadata"]
    assert set(metadata) == {"reason", "backend_code", "request_state", "session_state", "operation_id", "completed_steps"}
    assert metadata["reason"] == "target_ambiguous"
    assert metadata["backend_code"] == "target_ambiguous"
    assert metadata["request_state"] == "response_received"
    assert metadata["session_state"] == "active"
    assert metadata["completed_steps"] == []
    operation_id = metadata["operation_id"]
    assert len(operation_id) == 32
    assert all(character in "0123456789abcdef" for character in operation_id)
    assert not any("/attribute/" in event.get("path", "") for event in managed_driver.events())


def test_minimal_inspection_preserves_real_timeout_quarantine_and_disposal(managed_driver: _IsolatedManagedDriver) -> None:
    managed_driver.mode("inspect", selector=_ASSERT_SELECTOR, attributes={"enabled": "__stall__"})
    result = managed_driver.driver.assert_state({"element": {**_ASSERT_LOCATOR, "enabled": True}})
    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["backend_code"] == "operation_timeout"
    assert result["metadata"]["request_state"] == "response_unknown"
    assert result["metadata"]["session_state"] == "uncertain"
    owner = _observe_owner(managed_driver)
    assert owner.session.session_info().state == "uncertain"
    events = managed_driver.events()
    quarantined = managed_driver.driver.assert_state({"element": {**_ASSERT_LOCATOR, "enabled": True}})
    assert quarantined["status"] == "failed"
    assert managed_driver.events() == events
    managed_driver.driver.close()
    _assert_owner_settled(managed_driver, owner)


def test_minimal_inspection_propagates_real_cancellation_without_request(managed_driver: _IsolatedManagedDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    from appium_connect_agent.runtime import OperationControl

    managed_driver.mode("inspect", selector=_ASSERT_SELECTOR, attributes={"enabled": True})
    assert managed_driver.driver.launch_app({})["status"] == "passed"
    before = managed_driver.events()
    cancellation = threading.Event()
    cancellation.set()
    with monkeypatch.context() as patch:
        patch.setattr(managed_driver.driver, "_operation_control", lambda timeout_seconds: OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation))
        with pytest.raises(CancelledError):
            managed_driver.driver.assert_state({"element": {**_ASSERT_LOCATOR, "enabled": True}})
    assert managed_driver.events() == before


@pytest.mark.parametrize("password_after", [False, True])
def test_input_postread_omits_displayed_without_losing_password_guard(managed_driver: _IsolatedManagedDriver, password_after: bool) -> None:
    locator = {"resourceId": "com.example.isolated:id/input"}
    attributes = {
        "class": "android.widget.EditText",
        "resource-id": locator["resourceId"],
        "package": "com.example.isolated",
        "enabled": True,
        "displayed": True,
        "password": False,
        "text": "before",
        "rect": {"x": 0, "y": 0, "width": 100, "height": 40},
    }
    managed_driver.mode("inspect", selector={"using": "id", "value": locator["resourceId"]}, attributes=attributes, password_after=password_after)
    result = managed_driver.driver.input_text({"locator": locator, "text": "中文 café 👩🏽‍💻"})
    if password_after:
        assert result["status"] == "failed"
        assert result["metadata"]["reason"] == "verification_unavailable"
        assert result["metadata"]["stage"] == "post_verification"
        assert "中文" not in str(result)
    else:
        assert result == {"status": "passed", "output": None}
    events = managed_driver.events()
    replacement = next(index for index, event in enumerate(events) if event.get("body", {}).get("script") == "mobile: replaceElementValue")
    prefix = "/session/isolated-session/element/isolated-element/"
    reads = [event["path"].removeprefix(prefix) for event in events[replacement + 1 :] if event.get("method") == "GET" and event["path"].startswith(prefix)]
    assert reads == ["attribute/class", "attribute/resource-id", "attribute/package", "attribute/enabled", "attribute/password", "text", "rect"]
    binding_queries = [event for event in events[:replacement] if event.get("path", "").endswith("/elements")]
    assert len(binding_queries) >= 2
    assert all(event["body"] == {"using": "id", "value": locator["resourceId"]} for event in binding_queries)
    assert not any("/appium/settings" in event.get("path", "") for event in events)


@pytest.mark.parametrize("password", [False, True, None])
def test_complete_inspection_guards_text_through_real_sdk_and_public_client(managed_driver: _IsolatedManagedDriver, password: bool | None) -> None:
    from fsq_agent.models import AndroidInspectElementParams

    path = "/storage/emulated/0/" + "long-safe-directory/" * 20 + "report.txt"
    locator = {"resourceId": "com.example.isolated:id/path"}
    attributes = {"password": password, "content-desc": path}
    if password is False:
        attributes["text"] = path
    managed_driver.mode("inspect", selector={"using": "id", "value": locator["resourceId"]}, attributes=attributes)
    result = managed_driver.driver.inspect_element(AndroidInspectElementParams(locator=locator))
    assert result["attributes"]["desc"] == {"state": "value", "value": path}
    assert result["attributes"]["text"] == ({"state": "value", "value": path} if password is False else {"state": "unknown", "value": None})
    prefix = "/session/isolated-session/element/isolated-element/"
    reads = [event["path"].removeprefix(prefix) for event in managed_driver.events() if event.get("method") == "GET" and event["path"].startswith(prefix)]
    assert reads == (["attribute/password", "text", "attribute/content-desc"] if password is False else ["attribute/password", "attribute/content-desc"])
