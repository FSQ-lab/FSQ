# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ctypes
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.drivers.macos import _appium_connect as appium
from fsq_agent.drivers.macos import _native_fallback as native
from fsq_agent.models import ConfigurationError, MacOSClickOnParams, MacOSLaunchAppParams, MacOSPressKeyParams, MacOSUiSnapshotParams

FINDER = "com.apple.finder"
PAGES = "com.apple.iWork.Pages"
TITLE = "Contains “alpininsts”"
RECT = (844, 373, 244, 22)
CENTER = (966, 384)


class _FinderAX:
    def __init__(self, events):
        self.events = events
        self.attributes = {
            (1, "AXRole"): "AXApplication",
            (1, "AXFocusedUIElement"): 4,
            (1, "AXFocusedWindow"): 2,
            (2, "AXRole"): "AXWindow",
            (2, "AXParent"): 1,
            (3, "AXRole"): "AXToolbar",
            (3, "AXParent"): 2,
            (4, "AXRole"): "AXTextField",
            (4, "AXSubrole"): "AXSearchField",
            (4, "AXParent"): 3,
            (4, "AXChildren"): [5],
            (5, "AXRole"): "SGTMenu",
            (5, "AXParent"): 4,
            (5, "AXChildren"): [6],
            (6, "AXRole"): "AXMenu",
            (6, "AXParent"): 5,
            (6, "AXChildren"): [7, 8],
            (7, "AXRole"): "AXMenuItem",
            (7, "AXTitle"): TITLE,
            (7, "AXPosition"): RECT,
            (7, "AXEnabled"): True,
            (7, "AXParent"): 6,
            (8, "AXRole"): "AXMenuItem",
            (8, "AXTitle"): "Another item",
        }
        self.hits = {1: 7, 99: 7}
        self.recheck_app_hit = None
        self.changed_pids = iter((42, 42))
        self.true = True
        self.array_type = list

    def foreground(self, bundle, **kwargs):
        assert bundle == FINDER
        assert kwargs["deadline"] > native.time.monotonic()
        self.events.append("foreground")
        return next(self.changed_pids)

    def application(self, pid):
        assert pid == 42
        return 1

    def system(self):
        return 99

    def timeout(self, ref, seconds):
        assert ref
        assert 0 < seconds <= 0.25
        return 0

    def attribute(self, ref, key):
        value = self.attributes.get((ref, key))
        return (-25205, None) if value is None else (0, value)

    def hit(self, ref, x, y, pointer):
        assert (x, y) == CENTER
        was_called = "app_hit" in self.events
        self.events.append("system_hit" if ref == 99 else "app_hit")
        result = self.recheck_app_hit if ref == 1 and was_called and self.recheck_app_hit is not None else self.hits[ref]
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = result
        return 0

    def pid(self, ref, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 42
        return 0

    type = staticmethod(type)
    count = staticmethod(len)
    item = staticmethod(lambda values, index: values[index])
    decode_text = staticmethod(lambda value: value if isinstance(value, str) else None)
    decode_bounds = staticmethod(lambda read: read("AXPosition")[1])
    equal = staticmethod(lambda left, right: left == right)
    release = staticmethod(lambda ref: None)


@pytest.fixture
def finder(monkeypatch):
    pytest.importorskip("appium_connect_agent.automation")
    events = []
    ax = _FinderAX(events)
    state = SimpleNamespace(role="XCUIElementTypeMenuItem", width=1533, enabled=True, delivery_error=None)

    def inspect(*, attributes, **kwargs):
        events.append(("inspect", attributes))
        values = {"type": state.role, "title": TITLE, "displayed": True, "enabled": state.enabled, "bounds": SimpleNamespace(**dict(zip(("x", "y", "width", "height"), RECT, strict=True)))}
        return SimpleNamespace(attributes=tuple(SimpleNamespace(name=key, state="value", value=values[key]) for key in attributes))

    def wda_click(**kwargs):
        events.append("wda_click")

    def display_size(**kwargs):
        events.append("display")
        return SimpleNamespace(width=state.width, height=958)

    def click_at(x, y, **kwargs):
        assert (x, y) == CENTER
        assert kwargs["button"] == "left"
        assert kwargs["double"] is False
        assert kwargs["control"].timeout_seconds > 0
        events.append("device_click")
        if state.delivery_error is not None:
            raise state.delivery_error

    element = SimpleNamespace(inspect=inspect, click=wda_click)
    device = SimpleNamespace(click_at=click_at, display_size=display_size)
    session = SimpleNamespace(device=device, element=lambda *args, **kwargs: element)
    driver = appium.AppiumConnectMacOSDriver(bundle_id=FINDER, session=session)
    monkeypatch.setattr(appium.sys, "platform", "darwin")
    monkeypatch.setattr(native, "_AX", lambda: ax)
    monkeypatch.setattr(native, "_macos_ax_process_trusted", lambda: True)
    monkeypatch.setattr(native, "_frontmost_pid", ax.foreground)
    require = driver._require_session

    def require_session():
        events.append("session")
        return require()

    monkeypatch.setattr(driver, "_require_session", require_session)
    return driver, ax, state, events


def _suggestion_params():
    # An explicit coordinate cannot substitute for a failed semantic proof.
    return MacOSClickOnParams(locator={"title": TITLE, "controlType": "XCUIElementTypeMenuItem"}, point={"x": 0, "y": 0})


def test_search_suggestion_final_proof_then_exactly_one_device_click(finder):
    driver, _, _, events = finder
    assert driver.click_on(_suggestion_params())["status"] == "passed"
    assert events[-7:] == ["display", "foreground", "app_hit", "system_hit", "app_hit", "foreground", "device_click"]
    assert events.count("device_click") == 1
    assert "wda_click" not in events


@pytest.mark.parametrize("fault", ["other_hit", "late_hit", "other_pid", "other_title", "other_bounds", "other_field", "other_window", "duplicate", "disabled", "display", "deadline"])
def test_search_suggestion_failed_proof_dispatches_no_mutations(finder, monkeypatch, fault):
    driver, ax, state, events = finder
    if fault == "other_hit":
        ax.hits[99] = 8
    elif fault == "late_hit":
        ax.recheck_app_hit = 8
    elif fault == "other_pid":
        ax.changed_pids = iter((42, 43))
    elif fault == "other_title":
        ax.attributes[7, "AXTitle"] = "Another item"
    elif fault == "other_bounds":
        ax.attributes[7, "AXPosition"] = (845, *RECT[1:])
    elif fault == "other_field":
        ax.attributes[1, "AXFocusedUIElement"] = 3
    elif fault == "other_window":
        ax.attributes[1, "AXFocusedWindow"] = 3
    elif fault == "duplicate":
        ax.attributes[8, "AXTitle"] = TITLE
    elif fault == "disabled":
        ax.attributes[7, "AXEnabled"] = False
    elif fault == "display":
        state.width = 640
    else:
        monkeypatch.setattr(driver, "_finder_click_deadline", lambda params: native.time.monotonic())
    result = driver.click_on(_suggestion_params())
    assert result["failure_category"] == "action_error", result
    assert "device_click" not in events
    assert "wda_click" not in events


def test_search_suggestion_unknown_response_never_replays(finder):
    driver, _, state, events = finder
    state.delivery_error = RuntimeError("untrusted backend details")
    state.delivery_error.code = "execution_unknown"
    state.delivery_error.request_state = "response_unknown"
    result = driver.click_on(_suggestion_params())
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["request_state"] == "response_unknown"
    assert events.count("device_click") == 1
    assert "wda_click" not in events


def test_non_menu_finder_control_keeps_wda_dispatch(finder):
    driver, _, state, events = finder
    state.role = "XCUIElementTypeButton"
    assert driver.click_on(MacOSClickOnParams(target="Ordinary Finder control"))["status"] == "passed"
    assert "display" not in events
    assert "system_hit" not in events
    assert events[-1] == "wda_click"


def test_ordinary_finder_menu_keeps_wda_dispatch(finder):
    driver, ax, _, events = finder
    ax.attributes[6, "AXParent"] = 1
    assert driver.click_on(MacOSClickOnParams(target="Ordinary Finder menu item"))["status"] == "passed"
    assert events[-1] == "wda_click"
    assert "device_click" not in events


@pytest.fixture
def pages(monkeypatch, tmp_path):
    automation = pytest.importorskip("appium_connect_agent.automation")
    runtime = pytest.importorskip("appium_connect_agent.runtime")
    events = []
    sessions = []
    state = SimpleNamespace(failure=None)

    class Runtime:
        managed_cleanup_pending = False

        def __init__(self, settings):
            pass

        def start_managed(self, **kwargs):
            return self

        def __enter__(self):
            events.append("start")
            return object()

        def __exit__(self, *exc):
            events.append("stop")

    def create(connection, *, bundle_id, app_path, arguments, skip_app_kill, **kwargs):
        events.append(("create", bundle_id, app_path, arguments, skip_app_kill))
        if state.failure is not None:
            error, state.failure = state.failure, None
            raise error
        session = SimpleNamespace(session_info=lambda: SimpleNamespace(state="active", id=len(sessions)), idle_failure=False)

        def ensure_alive(**kwargs):
            events.append("ensure")
            if session.idle_failure:
                raise RuntimeError("stale")

        session.ensure_alive = ensure_alive
        session.quit = lambda **kwargs: events.append("quit")
        session.device = SimpleNamespace(send_keys=lambda *args, **kwargs: events.append("keyboard"))
        sessions.append(session)
        return session

    def make_app(folder):
        path = tmp_path / folder / "Pages.app"
        (path / "Contents").mkdir(parents=True)
        with (path / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIdentifier": PAGES}, stream)
        return path.resolve()

    monkeypatch.setattr(appium.sys, "platform", "darwin")
    monkeypatch.setattr(appium, "_macos_ax_process_trusted", lambda: True)
    monkeypatch.setattr(appium, "_resolve_managed_local_settings", lambda settings: None)
    monkeypatch.setattr(appium, "_activate_macos_app_native", lambda bundle, *, app_path, **kwargs: events.append(("activate", bundle, app_path)))
    monkeypatch.setattr(automation, "create_mac2_session", create)
    monkeypatch.setattr(runtime, "Runtime", Runtime)
    return make_app, state, sessions, events


def test_pages_path_launch_reuses_only_same_installation_and_rejects_conflict(pages):
    make_app, _, _, events = pages
    first, other = make_app("first"), make_app("other")
    driver = appium.AppiumConnectMacOSDriver()
    try:
        one = driver.launch_app(MacOSLaunchAppParams(app_path=str(first)))
        assert one["output"]["bundle_id"] == PAGES
        same = driver.launch_app(MacOSLaunchAppParams(app_path=str(first), new_session=False))
        assert one == same
        with pytest.raises(ConfigurationError, match="conflicts"):
            driver.launch_app(MacOSLaunchAppParams(bundle_id=FINDER, app_path=str(other)))
        assert events == ["start", ("create", FINDER, None, None, True), ("activate", PAGES, first), "ensure", ("activate", PAGES, first)]
        assert driver.launch_app(MacOSLaunchAppParams(app_path=str(other), new_session=False))["status"] == "passed"
        assert events[-5:] == ["quit", "stop", "start", ("create", FINDER, None, None, True), ("activate", PAGES, other)]
    finally:
        driver.close()


@pytest.mark.parametrize("scenario", ["same", "permission_modal", "unknown", "different"])
def test_pages_bundle_bound_session_checks_running_installation_before_replacement(pages, monkeypatch, scenario):
    make_app, _, sessions, events = pages
    running = make_app("current")
    requested = make_app("different") if scenario == "different" else running

    def running_path(bundle, *, cancellation):
        assert bundle == PAGES
        if scenario == "unknown":
            raise native._NativeForegroundError("foreground_unobservable", "Installation identity unavailable")
        return running

    def activate(bundle, *, app_path, **kwargs):
        events.append(("activate", bundle, app_path))
        if scenario == "permission_modal":
            raise native._NativeForegroundError("permission_modal", "System permission dialog")

    monkeypatch.setattr(appium, "_running_macos_app_path", running_path)
    driver = appium.AppiumConnectMacOSDriver()
    try:
        assert driver.launch_app(MacOSLaunchAppParams(bundle_id=PAGES, new_session=True))["status"] == "passed"
        session = sessions[0]
        session_id = session.session_info().id
        assert driver._session_app_path is None
        events.clear()
        monkeypatch.setattr(appium, "_activate_macos_app_native", activate)
        result = driver.launch_app(MacOSLaunchAppParams(app_path=str(requested), new_session=False))
        if scenario == "different":
            assert result["status"] == "passed"
            assert events == ["quit", "stop", "start", ("create", FINDER, None, None, True), ("activate", PAGES, requested)]
            assert driver._session is sessions[1]
            return
        if scenario == "same":
            assert result["status"] == "passed"
            assert driver._session_app_path == str(running)
            assert events == ["ensure", ("activate", PAGES, running)]
        else:
            assert result["status"] == "failed"
            assert result["metadata"]["backend_code"] == ("permission_modal" if scenario == "permission_modal" else "foreground_unobservable")
            assert events == (["ensure", ("activate", PAGES, running)] if scenario == "permission_modal" else [])
        assert sessions == [session]
        assert driver._session is session
        assert driver._session.session_info().id == session_id
        assert driver._session.session_info().state == "active"
        assert not driver._cleanup_pending
    finally:
        driver.close()


@pytest.mark.parametrize("skip_app_kill, arguments", [(False, None), (True, ["--fixture"])])
def test_pages_direct_path_honors_skip_app_kill_and_arguments(pages, skip_app_kill, arguments):
    make_app, _, _, events = pages
    installation = make_app("direct")
    driver = appium.AppiumConnectMacOSDriver(skip_app_kill=skip_app_kill)
    try:
        result = driver.launch_app(MacOSLaunchAppParams(app_path=str(installation), arguments=arguments))
        assert result["status"] == "passed", result
        assert events[1] == ("create", None, installation, arguments, skip_app_kill)
        assert not driver._native_ax_mode
    finally:
        driver.close()


def test_pages_retries_clean_replacement_from_saved_exact_path(pages):
    make_app, state, sessions, events = pages
    installation = make_app("recovery")
    driver = appium.AppiumConnectMacOSDriver()
    try:
        assert driver.launch_app(MacOSLaunchAppParams(app_path=str(installation)))["status"] == "passed"
        sessions[0].idle_failure = True
        driver._last_operation_finished_at = native.time.monotonic() - 46
        state.failure = RuntimeError("transient setup error")
        result = driver.press_key(MacOSPressKeyParams(key="a"))
        assert result["failure_category"] == "action_error"
        assert driver._recoverable_session_loss
        assert not driver._cleanup_pending
        assert "keyboard" not in events
        assert driver.launch_app(MacOSLaunchAppParams(new_session=False))["status"] == "passed"
        assert events[-3:] == ["start", ("create", FINDER, None, None, True), ("activate", PAGES, installation)]
    finally:
        driver.close()


def test_pages_native_setup_preserves_activation_and_cleanup_errors(pages, monkeypatch):
    make_app, _, _, _ = pages
    driver = appium.AppiumConnectMacOSDriver()
    activation_error = RuntimeError("activation failed")
    cleanup_error = RuntimeError("cleanup failed")

    def fail_activation(*args, **kwargs):
        raise activation_error

    def fail_cleanup():
        driver._cleanup_pending = True
        raise cleanup_error

    try:
        with monkeypatch.context() as patch:
            patch.setattr(driver, "_activate_bundle_in_session", fail_activation)
            patch.setattr(driver, "_close_owned_resources_for_recreation", fail_cleanup)
            with pytest.raises(BaseExceptionGroup) as observed:
                driver.launch_app(MacOSLaunchAppParams(app_path=str(make_app("dual-failure"))))
        assert observed.value.exceptions == (activation_error, cleanup_error)
        assert driver._cleanup_pending
    finally:
        driver.close()


def test_pages_snapshot_never_substitutes_finder_wda(pages, monkeypatch):
    make_app, _, sessions, events = pages
    installation = make_app("snapshot")
    driver = appium.AppiumConnectMacOSDriver()
    try:
        assert driver.launch_app(MacOSLaunchAppParams(app_path=str(installation)))["status"] == "passed"
        sessions[0].observe_tree = lambda **kwargs: events.append("wda_tree")
        monkeypatch.setattr(appium, "_observe_macos_ax_tree", lambda **kwargs: SimpleNamespace(roots=()))
        result = driver.ui_snapshot(MacOSUiSnapshotParams())
        assert result["failure_category"] == "observation_error"
        assert "wda_tree" not in events
    finally:
        driver.close()


def test_native_pages_activation_uses_exact_path_and_waits_for_stable_pid(monkeypatch):
    asked = Path("/Applications/Desired/Pages.app")
    other = Path("/Applications/Other/Pages.app")
    samples = iter([(PAGES, 51, other), (PAGES, 52, asked), (PAGES, 53, asked), (PAGES, 53, asked), (PAGES, 53, asked)])
    commands = []
    monkeypatch.setattr(native, "_native_command", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(native, "_foreground_identity", lambda **kwargs: next(samples))
    monkeypatch.setattr(native, "_permission_modal_for_target", lambda *args, **kwargs: False)
    native._activate_macos_app_native(PAGES, app_path=asked, timeout_seconds=2, cancellation=None)
    assert commands == [["/usr/bin/open", "-a", str(asked)], ["/usr/bin/open", "-n", "-a", str(asked)]]


def test_native_pages_activation_distinguishes_permission_dialog_without_input(monkeypatch):
    commands = []
    monkeypatch.setattr(native, "_native_command", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(native, "_foreground_identity", lambda **kwargs: ("com.apple.UserNotificationCenter", 53, None))
    monkeypatch.setattr(native, "_permission_modal_for_target", lambda bundle, *args, **kwargs: bundle == PAGES)
    with pytest.raises(native._NativeForegroundError) as outcome:
        native._activate_macos_app_native(PAGES, timeout_seconds=2, cancellation=None)
    assert outcome.value.code == "permission_modal"
    assert commands == [["/usr/bin/open", "-b", PAGES]]
