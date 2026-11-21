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
from fsq_agent.models import MacOSHarnessSettings, MacOSLaunchAppParams, ToolExecutionError

if TYPE_CHECKING:
    from collections.abc import Iterator

if sys.platform != "darwin":
    pytest.skip("Real SDK managed ownership proof requires macOS and its trusted /usr/sbin/lsof.", allow_module_level=True)

from appium_connect_agent import automation as aca_automation

assert callable(aca_automation.Session.dispose), "The mandatory pinned SDK must provide public Session.dispose."

# Local wire peer only. Runtime, Mac2 Session, Mac2 reservation/systemPort,
# usage/generation authority, account locking, process ownership proof, draining,
# and cleanup remain real SDK code.
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
        elif self.path == base + '/session/isolated-mac2-session/screenshot':
            while True:
                time.sleep(1)
        elif self.path == base + '/session/isolated-mac2-session/window/rect':
            self.respond({'x': 0, 'y': 0, 'width': 1440, 'height': 900})
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
                self.respond({'sessionId': 'isolated-mac2-session', 'capabilities': {'platformName': 'Mac', 'appium:automationName': 'Mac2'}})
        elif self.path == base + '/session/isolated-mac2-session/execute/sync':
            script = payload.get('script') if isinstance(payload, dict) else None
            if script == 'macos: listDisplays':
                if mode == 'probe-dead':
                    err = json.dumps({'value': {'error': 'unknown error', 'message': 'Mac2 helper unresponsive', 'stacktrace': ''}}).encode()
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                else:
                    self.respond([{'id': 1, 'frame': {'x': 0, 'y': 0, 'width': 1440, 'height': 900}}])
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
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

LoopbackHTTPServer((host, port), Handler).serve_forever()
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@dataclass
class _IsolatedManagedMacDriver:
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
def managed_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[_IsolatedManagedMacDriver]:
    from appium_connect_agent.runtime import _coordination

    from fsq_agent.drivers.macos import _appium_connect

    monkeypatch.setattr(_appium_connect, "_macos_ax_process_trusted", lambda: False)

    home = tmp_path.resolve() / "account"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(_coordination, "_account_home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    root = home / ".appium-connect-agent"
    appium_package = root / "appium" / "node_modules" / "appium"
    uia2_package = root / "driver-home" / "node_modules" / "appium-uiautomator2-driver"
    mac2_package = root / "driver-home" / "node_modules" / "appium-mac2-driver"
    for directory in (appium_package, uia2_package, mac2_package, root / "records", root / "cache", root / "tmp"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    _write_json(root / "managed.json", {"owner": "appium-connect-agent", "schema": 1})
    _write_json(appium_package / "package.json", {"name": "appium", "version": "3.7.0"})
    _write_json(uia2_package / "package.json", {"name": "appium-uiautomator2-driver", "version": "8.7.0"})
    _write_json(mac2_package / "package.json", {"name": "appium-mac2-driver", "version": "2.1.2"})
    _write_json(
        root / "records" / "installation.json",
        {"schema": 1, "state": "complete", "operation_id": "isolated-test-installation", "combination_id": "macos-android-uiautomator2", "changed": True},
    )
    node = tmp_path / "node"
    node.write_text(f"#!{sys.executable}\n{_NODE_PROTOCOL_PEER}")
    node.chmod(0o755)
    monkeypatch.setattr(_appium_connect, "_resolve_managed_local_settings", lambda settings_cls: settings_cls(node_executable=node))
    monkeypatch.setattr(_appium_connect, "DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS", 0.5)
    signals: list[tuple[int, int]] = []
    original_killpg = os.killpg

    def observe_killpg(group: int, number: int) -> None:
        if number:
            signals.append((group, number))
        original_killpg(group, number)

    monkeypatch.setattr(os, "killpg", observe_killpg)
    settings = MacOSHarnessSettings(**getattr(request, "param", {}))
    settings.bundle_id = "com.example.isolated.macos"
    driver = DriverFactory().create_macos_driver(settings)
    environment = _IsolatedManagedMacDriver(
        root,
        appium_package / "index.js",
        tmp_path / "protocol.jsonl",
        driver,
        signals,
        frozenset(threading.enumerate()),
    )
    environment.mode("create-disconnect")
    try:
        yield environment
    finally:
        driver.close()


@dataclass(frozen=True)
class _ObservedMacOwner:
    session: Any
    runtime: Any
    scope: Any
    connection: Any
    executor: Any
    pid: int
    group_id: int
    drain: threading.Thread
    worker: threading.Thread


def _observe_owner(environment: _IsolatedManagedMacDriver) -> _ObservedMacOwner:
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
    workers = [thread for thread in new_threads if thread.name.startswith("fsq-macos-driver")]
    assert len(drains) == 1
    assert len(workers) == 1
    assert drains[0].is_alive()
    assert workers[0].is_alive()
    return _ObservedMacOwner(
        driver._session,
        driver._runtime,
        driver._managed_scope,
        driver._connection,
        driver._executor,
        pid,
        group_id,
        drains[0],
        workers[0],
    )


def _assert_account_lock(environment: _IsolatedManagedMacDriver, *, held: bool) -> None:
    import fcntl

    with environment.lock_path.open("r+b") as stream:
        if held:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _create_unknown(environment: _IsolatedManagedMacDriver) -> _ObservedMacOwner:
    res = environment.driver.launch_app(MacOSLaunchAppParams(new_session=True))
    assert res["status"] == "failed"
    assert res["metadata"]["backend_code"] == "session_create_unknown"
    owner = _observe_owner(environment)
    assert owner.session.session_info().state == "uncertain"
    assert environment.service()["state"] == "active"
    assert environment.signals == []
    _assert_account_lock(environment, held=True)
    return owner


def _assert_quarantined(environment: _IsolatedManagedMacDriver, owner: _ObservedMacOwner) -> None:
    driver = environment.driver
    events = environment.events()
    record = environment.service()
    info = owner.session.session_info()
    signals = list(environment.signals)
    ctx = driver.context()
    assert ctx["session_id"] is None
    action = driver.launch_app(MacOSLaunchAppParams(new_session=False))
    assert action["status"] == "failed"
    assert action["metadata"]["reason"] in {"session_unavailable", "cleanup_pending"}
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


def _assert_peer_exited(owner: _ObservedMacOwner) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(owner.pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(owner.group_id, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(owner.pid, os.WNOHANG)
    assert not owner.drain.is_alive()
    assert owner.drain not in threading.enumerate()


def _assert_owner_settled(environment: _IsolatedManagedMacDriver, owner: _ObservedMacOwner, *, state: str = "disposed") -> None:
    from appium_connect_agent.runtime import InvalidStateError

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


def _fail_stopped_record_publication(environment: _IsolatedManagedMacDriver, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
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
def test_macos_create_unknown_retains_real_owner_until_explicit_terminal_close(managed_driver: _IsolatedManagedMacDriver, mode: str) -> None:
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


@pytest.mark.parametrize("managed_driver", [{"skip_app_kill": False}], indirect=True)
def test_macos_real_action_timeout_quarantines_when_skip_app_kill_false_and_probe_dead(managed_driver: _IsolatedManagedMacDriver) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.skip_app_kill is False
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
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
    _assert_account_lock(managed_driver, held=True)

    # Remote session is gone (`probe-dead`) and skip_app_kill=False: first reuse probes ensure_alive (`listDisplays`), fails, and sets `_cleanup_pending=True`; thereafter the session is strictly quarantined with zero further HTTP traffic.
    managed_driver.mode("probe-dead")
    first_reuse = driver.launch_app(MacOSLaunchAppParams(new_session=False))
    assert first_reuse["status"] == "failed"
    assert driver._cleanup_pending is True
    _assert_quarantined(managed_driver, owner)

    # Explicit launch_app(new_session=True) runs on the owner worker thread, disposes the uncertain session/runtime, and opens a fresh session.
    managed_driver.mode("serve")
    recreated = driver.launch_app(MacOSLaunchAppParams(new_session=True))
    assert recreated["status"] == "passed"
    assert owner.session.session_info().state == "disposed"
    _assert_peer_exited(owner)
    assert driver._session is not None
    assert driver._session is not owner.session
    assert driver._session.session_info().state == "active"

    driver.close()
    assert driver._session is None
    assert driver._executor is None


def test_macos_real_action_timeout_replaces_uncertain_session_losslessly(managed_driver: _IsolatedManagedMacDriver) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.skip_app_kill is True
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
    owner = _observe_owner(managed_driver)
    with pytest.raises(ToolExecutionError):
        driver.screenshot({"timeout_seconds": 0.25})
    assert owner.session.session_info().state == "uncertain"

    # The pinned SDK rejects ensure_alive on uncertain sessions before probing the remote peer.
    replaced = driver.launch_app(MacOSLaunchAppParams(new_session=False))
    assert replaced["status"] == "passed"
    assert replaced["output"]["bundle_id"] == "com.example.isolated.macos"
    assert owner.session.session_info().state == "disposed"
    _assert_peer_exited(owner)
    assert driver._session is not None
    assert driver._session is not owner.session
    assert driver._session.session_info().state == "active"
    assert driver._executor is owner.executor

    driver.close()
    assert driver._session is None
    assert driver._executor is None


def test_macos_active_close_uses_real_quit_then_managed_scope_exit(managed_driver: _IsolatedManagedMacDriver) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
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


def test_macos_unknown_quit_disposes_locally_and_retains_safe_diagnostic_history(managed_driver: _IsolatedManagedMacDriver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.macos._appium_connect")
    managed_driver.mode("quit-disconnect")
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
    owner = _observe_owner(managed_driver)

    driver.close()

    assert driver._terminal_disposal_diagnostic == {
        "backend_code": "session_quit_unknown",
        "request_state": "response_unknown",
        "remote_quit_confirmed": False,
        "terminal_disposal_completed": True,
    }
    _assert_owner_settled(managed_driver, owner)
    assert owner.session.session_info().appium_session_id == "isolated-mac2-session"
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


def test_macos_other_real_resource_state_error_is_not_settled_quit_history(managed_driver: _IsolatedManagedMacDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib3 import PoolManager

    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
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
    assert len(failed.value.cleanup_errors) == len(failures)
    assert all(isinstance(err, RuntimeError) and err.args == ("transport_cleanup_failed",) for err in failed.value.cleanup_errors)
    assert driver._terminal_disposal_diagnostic is None
    _assert_owner_settled(managed_driver, owner)
    assert len([event for event in managed_driver.events() if event.get("method") == "DELETE"]) == 1
    events = managed_driver.events()
    driver.close()
    assert managed_driver.events() == events


def test_macos_real_managed_scope_failure_retains_owners_after_successful_quit(managed_driver: _IsolatedManagedMacDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    managed_driver.mode("serve")
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
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
def test_macos_executor_shutdown_failure_retains_exact_owner_and_explicit_continuation(
    managed_driver: _IsolatedManagedMacDriver,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    shutdown_started: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.macos._appium_connect")
    managed_driver.mode(mode)
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
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
    assert driver.context()["session_id"] is None
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


def test_macos_unknown_quit_and_disposal_failure_preserve_order_and_explicit_continuation(
    managed_driver: _IsolatedManagedMacDriver, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="fsq_agent.drivers.macos._appium_connect")
    managed_driver.mode("quit-disconnect")
    driver = managed_driver.driver
    assert driver.launch_app(MacOSLaunchAppParams(new_session=True))["status"] == "passed"
    owner = _observe_owner(managed_driver)

    with monkeypatch.context() as boundary_patch:
        failures = _fail_stopped_record_publication(managed_driver, boundary_patch)
        with pytest.raises(BaseExceptionGroup) as combined:
            driver.close()

    assert failures
    assert len(combined.value.exceptions) == 2
    primary, secondary = combined.value.exceptions
    assert isinstance(primary, aca_automation.ResourceStateError)
    assert primary.code == "session_quit_unknown"
    assert isinstance(secondary, aca_automation.ResourceStateError)
    assert secondary.code == "terminal_disposal_pending"
    assert driver._terminal_disposal_diagnostic == {
        "backend_code": "session_quit_unknown",
        "request_state": "response_unknown",
        "remote_quit_confirmed": False,
        "terminal_disposal_completed": False,
    }
    assert not any(getattr(record, "backend_code", None) == "session_quit_unknown" for record in caplog.records)
    _assert_peer_exited(owner)
    _assert_account_lock(managed_driver, held=True)

    driver.close()

    _assert_owner_settled(managed_driver, owner)
    assert driver._terminal_disposal_diagnostic["terminal_disposal_completed"] is True
    records = [record for record in caplog.records if getattr(record, "backend_code", None) == "session_quit_unknown"]
    assert len(records) == 1
    assert records[0].terminal_disposal_completed is True
