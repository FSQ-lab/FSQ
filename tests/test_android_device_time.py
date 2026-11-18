# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_appium_connect_android_driver import FakeSession, _tree_observation
from test_coding_agent_runtime import _EmptyToolFactory, _test_request

from fsq_agent.adapters.coding_agent import DefaultCodingAgentRuntime
from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent_engine import ToolCall, ToolOutputEntry
from fsq_agent.config import Settings
from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.models import ToolExecutionError


def _clock(iso_local, device_time_type):
    parsed = datetime.fromisoformat(iso_local)
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    return device_time_type(iso_local=iso_local, date=parsed.date().isoformat(), time=parsed.strftime("%H:%M:%S"), weekday=weekdays[parsed.weekday()], utc_offset=iso_local[-6:])


def test_snapshots_read_fresh_device_date_across_midnight_without_context_clock_reads(monkeypatch, android_sdk):
    session = FakeSession()
    observed = iter([_clock("2023-10-15T23:59:59+05:45", android_sdk.automation.DeviceTime), _clock("2023-10-16T00:00:01+05:45", android_sdk.automation.DeviceTime)])
    calls = []

    def read_time(*, control):
        calls.append(control)
        return next(observed)

    monkeypatch.setattr(session.device, "device_time", read_time, raising=False)
    driver = AppiumConnectAndroidDriver(app_id="fixture.app", session=session)
    try:
        driver.context()
        assert calls == []
        first = driver.ui_snapshot({})
        second = driver.ui_snapshot({})
    finally:
        driver.close()
    assert first["device_local_time"] == {"status": "available", "local_datetime": "2023-10-15T23:59:59+05:45", "iso_weekday": 7, "source": "android_device"}
    assert second["device_local_time"] == {"status": "available", "local_datetime": "2023-10-16T00:00:01+05:45", "iso_weekday": 1, "source": "android_device"}
    assert len(calls) == 2
    assert all(control.timeout_seconds > 0 for control in calls)


@pytest.mark.parametrize("iso_local", ["2023-10-15T15:35:12+00:00", "2023-10-15T15:35:12-03:30", "2023-10-15T15:35:12+05:45"])
def test_clock_metadata_survives_unfolded_xml_fallback(monkeypatch, iso_local, android_sdk):
    session = FakeSession()
    calls = []

    def read_time(**kwargs):
        calls.append(kwargs)
        return _clock(iso_local, android_sdk.automation.DeviceTime)

    monkeypatch.setattr(session.device, "device_time", read_time, raising=False)
    driver = AppiumConnectAndroidDriver(app_id="fixture.app", session=session)
    build = driver._build_hierarchy_xml

    def fail_compaction(observation, *, compact):
        if compact:
            raise ValueError("private-clock-canary")
        return build(observation, compact=compact)

    monkeypatch.setattr(driver, "_build_hierarchy_xml", fail_compaction)
    try:
        snapshot = driver.ui_snapshot({})
    finally:
        driver.close()
    assert snapshot["coverage"]["reason"] == "uncompacted_tree_fallback"
    assert snapshot["device_local_time"]["local_datetime"] == iso_local
    assert len(calls) == 1
    assert "private-clock-canary" not in str(snapshot)


@pytest.mark.parametrize("failure", ["absent", "malformed", "unknown"])
def test_clock_unavailability_retains_tree_without_fabrication_or_retry(monkeypatch, failure, request):
    session = FakeSession()
    state = ["active"]
    calls = []
    monkeypatch.setattr(session, "session_info", lambda: SimpleNamespace(id="local-clock", state=state[0]))
    if failure == "unknown":
        command_error = request.getfixturevalue("android_sdk").automation.CommandError

    def read_time(**kwargs):
        calls.append(kwargs)
        if failure == "unknown":
            state[0] = "uncertain"
            raise command_error("operation_timeout", "a" * 32, "response_unknown")
        return SimpleNamespace(iso_local="private-clock-canary")

    if failure != "absent":
        monkeypatch.setattr(session.device, "device_time", read_time, raising=False)
    driver = AppiumConnectAndroidDriver(app_id="fixture.app", session=session)
    try:
        snapshot = driver.ui_snapshot({})
        unavailable = snapshot["device_local_time"]
        assert snapshot["xml"].startswith("<hierarchy")
        assert unavailable["status"] == "unavailable"
        assert "local_datetime" not in unavailable
        assert "iso_weekday" not in unavailable
        assert "private-clock-canary" not in str(snapshot)
        if failure == "unknown":
            assert unavailable["diagnostics"]["request_state"] == "response_unknown"
            assert unavailable["diagnostics"]["session_state"] == "uncertain"
            before = list(session.calls)
            with pytest.raises(ToolExecutionError):
                driver.ui_snapshot({})
            assert session.calls == before
        assert len(calls) == int(failure != "absent")
    finally:
        driver.close()


def test_clock_cancellation_is_not_converted_to_ordinary_unavailability(monkeypatch, android_sdk):
    session = FakeSession()
    calls = []

    def cancel(**kwargs):
        calls.append(kwargs)
        raise android_sdk.automation.OperationCancelledError("operation_cancelled", "a" * 32, "not_sent")

    monkeypatch.setattr(session.device, "device_time", cancel, raising=False)
    driver = AppiumConnectAndroidDriver(app_id="fixture.app", session=session)
    try:
        with pytest.raises(asyncio.CancelledError):
            driver.ui_snapshot({})
    finally:
        driver.close()
    assert len(calls) == 1


@pytest.mark.usefixtures("macos_android_sdk")
async def test_real_sdk_clock_reaches_model_snapshot_output_without_ui_actions(tmp_path):
    from appium_connect_agent.automation import create_session
    from appium_connect_agent.runtime import LocalSettings, Runtime

    events = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self, value):
            body = json.dumps({"value": value}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            events.append(("GET", self.path))
            if self.path == "/status":
                self.respond({"ready": True, "build": {"version": "3.7.0"}})
            else:
                self.send_error(404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            events.append(("POST", self.path, payload))
            if self.path == "/session":
                self.respond({"sessionId": "clock-session", "capabilities": {"platformName": "Android", "appium:automationName": "UiAutomator2"}})
            elif self.path.endswith("/execute/sync") and payload["script"] == "mobile: getDeviceTime":
                self.respond("2023-10-15T15:35:12Z")
            elif self.path.endswith("/execute/sync") and payload["script"] == "mobile: deviceInfo":
                self.respond({"realDisplaySize": "1080x2400"})
            else:
                self.send_error(404)

        def do_DELETE(self):
            self.respond(None)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with Runtime(LocalSettings(server_url=f"http://127.0.0.1:{server.server_port}")).connect() as connection:
            session = create_session(connection, "clock-consumer-device")

            class SessionView:
                device = session.device

                def session_info(self):
                    return session.session_info()

                def observe_tree(self, **kwargs):
                    return _tree_observation()

            harness = AndroidHarness(driver=AppiumConnectAndroidDriver(app_id="fixture.app", session=SessionView()))
            try:
                adapter = HarnessToolAdapter(harness, run_id="clock-consumer")
                tool = next(binding for binding in adapter.build_tools() if binding.name == "ui_snapshot")
                raw = await tool.invoke(ToolCall(name=tool.name, arguments={}, call_id="clock"))
                settings = Settings()
                settings.output.runs_dir = tmp_path
                output_filter = _test_request(DefaultCodingAgentRuntime(settings, _EmptyToolFactory()), run_id="clock-consumer").tool_output_filter
                projected = json.loads(output_filter((ToolOutputEntry(1, "clock", tool.name, raw),)).get(1, raw))
                assert projected["status"] == "passed", projected
                assert projected["output"]["device_local_time"] == {"status": "available", "local_datetime": "2023-10-15T15:35:12+00:00", "iso_weekday": 7, "source": "android_device"}
                commands = [event[2]["script"] for event in events if event[0] == "POST" and event[1].endswith("/execute/sync")]
                assert commands.count("mobile: getDeviceTime") == 1
                assert set(commands) <= {"mobile: deviceInfo", "mobile: getDeviceTime"}
            finally:
                harness.close()
                session.quit()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
