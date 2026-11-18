# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import threading
from types import SimpleNamespace

import pytest

from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver


@pytest.mark.usefixtures("android_sdk")
async def test_capture_checks_asyncio_cancellation_on_the_calling_task(monkeypatch):
    owner_task = asyncio.current_task()
    owner_thread = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    settled = threading.Event()
    observed = []
    pixels = b"isolated-screenshot-bytes"

    def take_screenshot(*, control):
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Calling context did not release its capture.")
            return pixels
        finally:
            settled.set()

    def check():
        observed.append((threading.get_ident(), asyncio.current_task()))
        if started.is_set():
            release.set()

    driver = AppiumConnectAndroidDriver(app_id="com.example.offline")
    monkeypatch.setattr(driver, "_ensure_session", lambda: SimpleNamespace(device=SimpleNamespace(take_screenshot=take_screenshot)))
    try:
        result = driver.screenshot({"cancellation_check": check})
        assert result == pixels
        assert settled.is_set()
        assert observed
        assert all(thread == owner_thread and task is owner_task for thread, task in observed)
    finally:
        release.set()
        driver.close()


@pytest.mark.usefixtures("android_sdk")
def test_capture_cancellation_settles_the_same_sdk_operation_and_preserves_identity(monkeypatch):
    started = threading.Event()
    settled = threading.Event()
    interrupted = asyncio.CancelledError()
    controls = []
    calls = []

    def take_screenshot(*, control):
        calls.append("capture")
        controls.append(control)
        started.set()
        try:
            if not control.cancellation.wait(5):
                raise TimeoutError("SDK cancellation signal was not delivered.")
            return b"late-value-must-not-be-accepted"
        finally:
            settled.set()

    def check():
        if started.is_set():
            raise interrupted

    driver = AppiumConnectAndroidDriver(app_id="com.example.offline")
    monkeypatch.setattr(driver, "_ensure_session", lambda: SimpleNamespace(device=SimpleNamespace(take_screenshot=take_screenshot)))
    try:
        with pytest.raises(asyncio.CancelledError) as failure:
            driver.screenshot({"cancellation_check": check})
        assert failure.value is interrupted
        assert settled.is_set()
        assert calls == ["capture"]
        assert controls[0].cancellation.is_set()
        assert interrupted.fsq_capture_diagnostics["stage"] == "screenshot"
    finally:
        driver.close()


def _borrowed_capture_session(take_screenshot):
    cleanup_calls = []
    session = SimpleNamespace(
        device=SimpleNamespace(take_screenshot=take_screenshot),
        state="active",
        cleanup_calls=cleanup_calls,
        quit=lambda **_: cleanup_calls.append("quit"),
        dispose=lambda **_: cleanup_calls.append("dispose"),
    )
    session.session_info = lambda: SimpleNamespace(state=session.state)
    return session


@pytest.mark.usefixtures("android_sdk")
async def test_borrowed_capture_checks_on_caller_and_preserves_inline_defaults():
    owner_task = asyncio.current_task()
    owner_thread = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    settled = threading.Event()
    observed = []
    default_threads = []
    capture_workers = []
    pixels = b"isolated-borrowed-screenshot-bytes"

    def take_screenshot(*, control):
        if control.cancellation is None:
            default_threads.append(threading.get_ident())
            return pixels
        capture_workers.append(threading.current_thread())
        assert control.timeout_seconds == 3.0
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Calling context did not release its borrowed capture.")
            assert not control.cancellation.is_set()
            return pixels
        finally:
            settled.set()

    def check():
        observed.append((threading.get_ident(), asyncio.current_task()))
        if started.is_set():
            release.set()

    session = _borrowed_capture_session(take_screenshot)
    driver = AppiumConnectAndroidDriver(app_id="com.example.offline", session=session)
    try:
        assert driver.screenshot() == pixels
        assert driver.screenshot({"cancellation_check": check, "timeout_seconds": 3.0}) == pixels
        assert settled.is_set()
        assert observed
        assert all(thread == owner_thread and task is owner_task for thread, task in observed)
        assert len(capture_workers) == 1
        assert capture_workers[0].ident != owner_thread
        assert driver.screenshot() == pixels
    finally:
        release.set()
        driver.close()
    assert all(not worker.is_alive() for worker in capture_workers)
    assert driver.screenshot() == pixels
    assert default_threads == [owner_thread, owner_thread, owner_thread]
    assert session.cleanup_calls == []


@pytest.mark.usefixtures("android_sdk")
@pytest.mark.parametrize("completion", ["late_bytes", "sdk_cancelled", "sdk_timeout", "sdk_error"])
@pytest.mark.parametrize("cancellation_type", [asyncio.CancelledError, InterruptedError])
def test_borrowed_capture_cancellation_settles_without_accepting_late_pixels(completion, cancellation_type):
    from appium_connect_agent.automation import CommandError, OperationCancelledError, OperationTimeoutError

    started = threading.Event()
    signalled = threading.Event()
    settled = threading.Event()
    interrupted = cancellation_type("private runtime cancellation")
    controls = []
    errors = {
        "sdk_cancelled": OperationCancelledError("operation_cancelled", "isolated-capture", "response_unknown"),
        "sdk_timeout": OperationTimeoutError("operation_timeout", "isolated-capture", "response_unknown"),
        "sdk_error": CommandError("command_failed", "isolated-capture", "response_received"),
    }
    sdk_error = errors.get(completion)

    def take_screenshot(*, control):
        controls.append(control)
        started.set()
        try:
            if not control.cancellation.wait(5):
                raise TimeoutError("Borrowed SDK cancellation signal was not delivered.")
            signalled.set()
            if sdk_error is not None:
                if sdk_error.request_state == "response_unknown":
                    session.state = "uncertain"
                raise sdk_error
            return b"late-borrowed-pixels-must-not-be-accepted"
        finally:
            settled.set()

    def check():
        if started.is_set():
            raise interrupted

    session = _borrowed_capture_session(take_screenshot)
    driver = AppiumConnectAndroidDriver(app_id="com.example.offline", session=session)
    try:
        with pytest.raises(cancellation_type) as failure:
            driver.screenshot({"cancellation_check": check})
        assert failure.value is interrupted
        assert signalled.is_set()
        assert settled.is_set()
        assert len(controls) == 1
        assert controls[0].cancellation.is_set()
        diagnostics = interrupted.fsq_capture_diagnostics
        expected = {"stage": "screenshot", "session_state": session.state}
        if sdk_error is not None:
            expected.update(backend_code=sdk_error.code, request_state=sdk_error.request_state)
        assert diagnostics == expected
        assert "private" not in repr(diagnostics)
        assert "late-borrowed-pixels" not in repr(diagnostics)
    finally:
        driver.close()
    assert session.cleanup_calls == []


@pytest.mark.usefixtures("android_sdk")
@pytest.mark.parametrize("sdk_failure", ["timeout", "command_error"])
@pytest.mark.parametrize("cancellation_type", [asyncio.CancelledError, InterruptedError])
def test_borrowed_capture_sdk_failure_retains_current_cancellation(sdk_failure, cancellation_type):
    from appium_connect_agent.automation import CommandError, OperationTimeoutError

    cancellation_requested = threading.Event()
    settled = threading.Event()
    interrupted = cancellation_type("private current cancellation")
    controls = []
    sdk_error = (
        OperationTimeoutError("operation_timeout", "isolated-capture", "response_unknown") if sdk_failure == "timeout" else CommandError("command_failed", "isolated-capture", "response_received")
    )

    def take_screenshot(*, control):
        controls.append(control)
        try:
            cancellation_requested.set()
            if sdk_failure == "timeout":
                session.state = "uncertain"
            raise sdk_error
        finally:
            settled.set()

    def check():
        if cancellation_requested.is_set():
            raise interrupted

    session = _borrowed_capture_session(take_screenshot)
    driver = AppiumConnectAndroidDriver(app_id="com.example.offline", session=session)
    try:
        with pytest.raises(cancellation_type) as failure:
            driver.screenshot({"cancellation_check": check})
        assert failure.value is interrupted
        assert settled.is_set()
        assert len(controls) == 1
        assert interrupted.fsq_capture_diagnostics == {
            "backend_code": sdk_error.code,
            "request_state": sdk_error.request_state,
            "session_state": session.state,
            "stage": "screenshot",
        }
    finally:
        driver.close()
    assert session.cleanup_calls == []


@pytest.mark.usefixtures("android_sdk")
def test_idle_liveness_probe_shares_capture_cancellation_and_never_takes_late_pixels(monkeypatch):
    from appium_connect_agent.automation import OperationCancelledError

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr("fsq_agent.drivers.android._appium_connect.time", SimpleNamespace(monotonic=lambda: clock.now))
    started = threading.Event()
    settled = threading.Event()
    interrupted = asyncio.CancelledError()
    calls = []
    controls = []

    def take_screenshot(*, control):
        calls.append("capture")
        return b"initial-pixels"

    def ensure_alive(*, control):
        calls.append("ensure_alive")
        controls.append(control)
        started.set()
        try:
            assert control.timeout_seconds == 25.0
            assert control.cancellation.wait(5), "Idle probe did not receive capture cancellation."
            raise OperationCancelledError("operation_cancelled", "a" * 32, "not_sent")
        finally:
            settled.set()

    def check():
        if started.is_set():
            raise interrupted

    session = _borrowed_capture_session(take_screenshot)
    session.ensure_alive = ensure_alive
    driver = AppiumConnectAndroidDriver(app_id="com.example.offline", session=session)
    try:
        assert driver.screenshot() == b"initial-pixels"
        clock.now = 45.0
        with pytest.raises(asyncio.CancelledError) as caught:
            driver.screenshot({"cancellation_check": check})
        assert caught.value is interrupted
        assert settled.is_set()
        assert controls[0].cancellation.is_set()
        assert calls == ["capture", "ensure_alive"]
        assert interrupted.fsq_capture_diagnostics["backend_code"] == "operation_cancelled"
    finally:
        driver.close()
    assert session.cleanup_calls == []
