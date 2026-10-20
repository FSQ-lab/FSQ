# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import builtins
from asyncio import CancelledError
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from fsq_agent.core import AndroidDriverInterface, CapabilityRegistry, StepRunner
from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.harness._android import AndroidHarness
from fsq_agent.core.harness._driver_tools import _discover_driver_capability_definitions
from fsq_agent.core.harness._uiautomator2_driver import UiAutomator2AndroidDriver
from fsq_agent.models import AndroidAssertWithAIParams, ConfigurationError, ExecutableStep, HarnessContext, ReplayPolicy


class FakeSelector:
    def __init__(self, device: "FakeDevice", query: dict[str, object], exists: bool | None = None) -> None:
        self.device = device
        self.query = query
        self.exists = device.exists if exists is None else exists

    @property
    def count(self) -> object:
        self.device.calls.append(("count", self.query))
        if any(call[0] == "set_text" for call in self.device.calls):
            return self.device.post_write_match_count
        return self.device.match_count

    def wait(self, **kwargs: object) -> bool:
        self.device.calls.append(("wait", self.query, kwargs))
        return self.device.wait_result

    def wait_gone(self, **kwargs: object) -> bool:
        self.device.calls.append(("wait_gone", self.query, kwargs))
        return self.device.wait_gone_result

    def click(self) -> None:
        self.device.calls.append(("click", self.query))

    def long_click(self) -> None:
        self.device.calls.append(("long_click", self.query))

    def set_text(self, text: str) -> bool | None:
        self.device.calls.append(("set_text", self.query, text))
        if self.device.write_mode == "reject":
            return False
        if self.device.write_mode == "error":
            raise RuntimeError("private backend payload")
        if self.device.write_mode == "accept":
            self.device.text = text
        return None

    def clear_text(self) -> None:
        self.device.calls.append(("clear_text", self.query))
        self.device.text = ""

    def get_text(self) -> str:
        self.device.calls.append(("get_text", self.query))
        return self.device.text

    def info(self) -> dict[str, object]:
        self.device.calls.append(("info", self.query))
        info = {"text": self.device.text, **self.device.selector_info}
        if any(call[0] == "set_text" for call in self.device.calls):
            info.update(self.device.post_write_info)
        return info


class FakeXPathSelector(FakeSelector):
    def all(self) -> list[FakeSelector]:
        self.device.calls.append(("all", self.query))
        count = self.device.post_write_match_count if any(call[0] == "set_text" for call in self.device.calls) else self.device.match_count
        if type(count) is not int:
            raise RuntimeError("private backend payload")
        return [self] * count

    def wait(self, **kwargs: object) -> bool:
        self.device.calls.append(("xpath_wait", self.query, kwargs))
        if "exists" in kwargs:
            raise TypeError("xpath wait does not accept exists")
        return self.device.wait_result


class FakeDevice:
    def __init__(
        self,
        *,
        exists: bool = True,
        text: str = "Loaded",
        wait_result: bool = True,
        wait_gone_result: bool = True,
        hierarchy: str = "<hierarchy />",
    ) -> None:
        self.exists = exists
        self.text = text
        self.wait_result = wait_result
        self.wait_gone_result = wait_gone_result
        self.hierarchy = hierarchy
        self.write_mode = "accept"
        self.match_count: object = 1
        self.post_write_match_count: object = 1
        self.post_write_info: dict[str, object] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.selector_info: dict[str, object] = {
            "className": "android.widget.EditText",
            "password": False,
            "resourceName": "com.example:id/input",
            "packageName": "com.example",
            "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 50},
            "enabled": True,
            "checked": False,
            "selected": False,
            "clickable": True,
            "focused": False,
        }
        self.info = {
            "displayWidth": 1080,
            "displayHeight": 2400,
            "currentPackageName": "com.example.app",
        }

    def __call__(self, **query: object) -> FakeSelector:
        self.calls.append(("select", query))
        return FakeSelector(self, query)

    def xpath(self, value: str) -> FakeSelector:
        self.calls.append(("xpath", value))
        return FakeXPathSelector(self, {"xpath": value})

    def app_start(self, app_id: str, **options: object) -> None:
        self.calls.append(("app_start", app_id, options))

    def app_stop(self, app_id: str) -> None:
        self.calls.append(("app_stop", app_id))

    def app_current(self) -> dict[str, str]:
        self.calls.append(("app_current",))
        raise AssertionError("context must not issue a separate foreground-activity probe")

    def press(self, key: str) -> None:
        self.calls.append(("press", key))

    def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float) -> None:
        self.calls.append(("swipe", sx, sy, ex, ey, duration))

    # Match uiautomator2's public keyword exactly so the fake verifies call compatibility.
    def screenshot(self, format: str = "raw") -> bytes:  # noqa: A002
        self.calls.append(("screenshot", format))
        return FakeImage()

    def dump_hierarchy(self) -> str:
        self.calls.append(("dump_hierarchy",))
        return self.hierarchy


class FakeCompressedDevice(FakeDevice):
    def __init__(self, *, compressed_hierarchy: str, hierarchy: str = "<hierarchy />") -> None:
        super().__init__(hierarchy=hierarchy)
        self.compressed_hierarchy = compressed_hierarchy

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.calls.append(("dump_hierarchy", compressed))
        if compressed:
            return self.compressed_hierarchy
        return self.hierarchy


class FakeImage:
    # Match Pillow's public keyword exactly so the fake verifies call compatibility.
    def save(self, output: BytesIO, format: str) -> None:  # noqa: A002
        output.write(f"fake-{format.lower()}".encode())


def test_uiautomator2_driver_context_launch_kill_and_artifacts() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    context = driver.context()

    assert isinstance(driver, AndroidDriverInterface)
    assert context["session_id"] == "uiautomator2:fake-device"
    assert context["screen_size"] == (1080, 2400)
    assert context["metadata"] == {
        "backend": "uiautomator2",
        "current_package": "com.example.app",
    }
    assert "current_activity" not in context
    assert AndroidHarness(driver=driver).get_context() == HarnessContext(
        platform="android",
        session_id="uiautomator2:fake-device",
        current_activity=None,
        screen_size=(1080, 2400),
        metadata={
            "backend": "uiautomator2",
            "current_package": "com.example.app",
        },
    )
    assert driver.launch_app({})["status"] == "passed"
    assert driver.kill_app({})["status"] == "passed"
    assert driver.screenshot() == b"fake-png"
    assert driver.ui_snapshot({})["xml"] == "<hierarchy />"
    assert device.calls == [
        ("app_start", "com.example.app", {}),
        ("app_stop", "com.example.app"),
        ("screenshot", "pillow"),
        ("dump_hierarchy",),
    ]


def _shared_memory_rpc_error() -> Exception:
    errors = pytest.importorskip("uiautomator2.exceptions")
    return errors.RPCUnknownError(
        "Unknown RPC error: -32001 java.lang.IllegalStateException",
        {},
        "java.lang.IllegalStateException: ApplicationSharedMemory not initialized\n"
        "\tat com.android.internal.os.ApplicationSharedMemory.getInstance(ApplicationSharedMemory.java:85)\n"
        "\tat com.wetest.uia2.stub.AutomatorServiceImpl.deviceInfo(AutomatorServiceImpl.java:158)\n",
    )


class ContextReadDevice(FakeDevice):
    def __init__(self, outcomes: list[object], *, launch_error: Exception | None = None) -> None:
        super().__init__()
        self.outcomes = iter(outcomes)
        self.info_reads = 0
        self.launch_error = launch_error

    @property
    def info(self) -> dict[str, object]:
        self.info_reads += 1
        outcome = next(self.outcomes, self.normal_info)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @info.setter
    def info(self, value: dict[str, object]) -> None:
        self.normal_info = value

    def app_start(self, app_id: str, **options: object) -> None:
        super().app_start(app_id, **options)
        if self.launch_error is not None:
            raise self.launch_error


def test_context_recovers_known_initialization_fault_and_returns_fresh_info() -> None:
    fresh = {"displayWidth": 720, "displayHeight": 1600, "currentPackageName": "fresh.package"}
    device = ContextReadDevice([_shared_memory_rpc_error(), fresh])
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    context = driver.context()

    assert context["screen_size"] == (720, 1600)
    assert context["metadata"] == {"backend": "uiautomator2", "current_package": "fresh.package"}
    assert "current_activity" not in context
    assert device.info_reads == 2
    assert device.calls == []
    assert driver.context()["screen_size"] == (1080, 2400)
    assert device.info_reads == 3


@pytest.mark.parametrize("second_fault", ["same", "disconnect", "cancel"])
def test_context_recovery_is_bounded_and_preserves_second_fault(second_fault: str) -> None:
    error = {"same": _shared_memory_rpc_error(), "disconnect": ConnectionError("offline"), "cancel": CancelledError()}[second_fault]
    device = ContextReadDevice([_shared_memory_rpc_error(), error])
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    with pytest.raises(type(error)) as caught:
        driver.context()

    assert caught.value is error
    assert device.info_reads == 2
    assert device.calls == []


@pytest.mark.parametrize(
    "fault", ["timeout", "disconnect", "ordinary-lookalike", "rpc-params", "rpc-wrong-code", "rpc-wrong-java", "rpc-missing-frame", "rpc-malformed", "cancel", "interrupt", "exit"]
)
def test_context_does_not_retry_other_faults_or_lookalikes(fault: str) -> None:
    known = _shared_memory_rpc_error()
    errors = pytest.importorskip("uiautomator2.exceptions")
    summary, _, stack = known.args
    error = {
        "timeout": TimeoutError("offline"),
        "disconnect": errors.UiAutomationNotConnectedError("offline"),
        "ordinary-lookalike": RuntimeError(*known.args),
        "rpc-params": errors.RPCUnknownError(summary, {"locator": stack}, "java.lang.IllegalStateException: other failure"),
        "rpc-wrong-code": errors.RPCUnknownError("Unknown RPC error: -32002 java.lang.IllegalStateException", {}, stack),
        "rpc-wrong-java": errors.RPCUnknownError(summary, {}, stack.replace("java.lang.IllegalStateException:", "java.lang.SecurityException:", 1)),
        "rpc-missing-frame": errors.RPCUnknownError(summary, {}, "java.lang.IllegalStateException: ApplicationSharedMemory not initialized"),
        "rpc-malformed": errors.RPCUnknownError(summary, {}, {"stack": stack}),
        "cancel": CancelledError(),
        "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(),
    }[fault]
    device = ContextReadDevice([error])
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    with pytest.raises(type(error)) as caught:
        driver.context()

    assert caught.value is error
    assert device.info_reads == 1
    assert device.calls == []


def test_context_recovery_uses_actual_sdk_info_and_rpc_error_offline(monkeypatch) -> None:
    sdk = pytest.importorskip("uiautomator2")
    sdk_core = pytest.importorskip("uiautomator2.core")
    summary, _, stack = _shared_memory_rpc_error().args
    response = SimpleNamespace(text="initialization fault", json=lambda: {"error": {"code": -32001, "message": summary.removeprefix("Unknown RPC error: -32001 "), "data": stack}})
    responses = iter([response, SimpleNamespace(json=lambda: {"result": {"displayWidth": 600, "displayHeight": 900, "currentPackageName": "sdk.package"}})])
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    class Rpc:
        def deviceInfo(self, *, http_timeout):
            return sdk_core._jsonrpc_call(None, 9008, "deviceInfo", {}, http_timeout, False)

    class Device:
        info = sdk.Device.info
        jsonrpc = Rpc()

    monkeypatch.setattr(sdk_core, "_http_request", request)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=Device())

    assert driver.context()["screen_size"] == (600, 900)
    assert len(calls) == 2
    assert all(call[0][4] == {"jsonrpc": "2.0", "id": 1, "method": "deviceInfo", "params": {}} for call in calls)


@pytest.mark.parametrize("mode", ["transient", "persistent", "launch-failure", "launch-and-evidence-failure"])
def test_context_recovery_through_runner_never_retries_launch(mode: str, tmp_path) -> None:
    outcomes = [_shared_memory_rpc_error()]
    if mode == "persistent":
        outcomes.append(_shared_memory_rpc_error())
    device = ContextReadDevice(outcomes, launch_error=RuntimeError("app start rejected") if mode.startswith("launch") else None)
    if mode == "launch-and-evidence-failure":

        def failed_capture(*args, **kwargs):
            raise RuntimeError("capture unavailable")

        device.screenshot = failed_capture
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)
    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    registry = CapabilityRegistry()
    for definition in _discover_driver_capability_definitions(driver, platform="android"):
        registry.register(definition)
    runner = StepRunner(harness, capability_registry=registry)
    step = ExecutableStep(step_id="launch", action_name="launch_app", params={}, kind="setup")

    result = runner.run_step("context-recovery", step)

    assert len([call for call in device.calls if call[0] == "app_start"]) == (0 if mode == "persistent" else 1)
    if mode == "persistent":
        assert device.info_reads == 2
        assert result.status == "failed"
        assert [(phase.phase, phase.status) for phase in result.phase_reports] == [("prepare", "failed")]
        assert "RPCUnknownError" in result.error_message
    else:
        assert result.phase_reports[0].status == "passed"
        invoke = next(phase for phase in result.phase_reports if phase.phase == "invoke")
        assert invoke.status == ("failed" if mode.startswith("launch") else "passed")
        assert result.status == ("failed" if mode.startswith("launch") else "passed")
        if mode.startswith("launch"):
            assert "app start rejected" in result.error_message
            assert result.failure_category == "harness_error"
        if mode == "launch-and-evidence-failure":
            assert result.evidence_errors


def test_uiautomator2_driver_actions_use_locator_selectors() -> None:
    device = FakeDevice(text="bing.com")
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    assert driver.tap_on({"locator": {"resourceId": "login"}})["status"] == "passed"
    assert driver.tap_at({"point": {"x": 100, "y": 200}})["status"] == "passed"
    assert driver.long_press_on({"locator": {"accessibilityId": "Menu"}})["status"] == "passed"
    assert driver.input_text({"text": "hello", "locator": {"resourceId": "search"}})["status"] == "passed"
    assert driver.press_key({"key": "Back"})["status"] == "passed"
    assert driver.swipe({"direction": "up", "duration": 100})["status"] == "passed"
    assert driver.assert_state({"element": {"resourceId": "url"}, "text": {"contains": "hello"}})["status"] == "passed"

    assert device.calls[:16] == [
        ("select", {"resourceId": "login"}),
        ("wait", {"resourceId": "login"}, {"exists": True, "timeout": 10.0}),
        ("click", {"resourceId": "login"}),
        ("click", 100, 200),
        ("select", {"description": "Menu"}),
        ("wait", {"description": "Menu"}, {"exists": True, "timeout": 10.0}),
        ("long_click", {"description": "Menu"}),
        ("select", {"resourceId": "search"}),
        ("wait", {"resourceId": "search"}, {"exists": True, "timeout": 10.0}),
        ("count", {"resourceId": "search"}),
        ("info", {"resourceId": "search"}),
        ("click", {"resourceId": "search"}),
        ("set_text", {"resourceId": "search"}, "hello"),
        ("count", {"resourceId": "search"}),
        ("info", {"resourceId": "search"}),
        ("press", "back"),
    ]


def test_uiautomator2_driver_supports_point_based_swipe() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.swipe(
        {
            "start": {"x": 800, "y": 1900},
            "end": {"x": 200, "y": 1900},
            "duration": 1000,
        }
    )

    assert result == {
        "status": "passed",
        "output": {"start": {"x": 800, "y": 1900}, "end": {"x": 200, "y": 1900}},
    }
    assert device.calls == [("swipe", 800, 1900, 200, 1900, 1.0)]


def test_uiautomator2_driver_supports_point_based_tap() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.tap_at({"point": {"x": 320, "y": 640}})

    assert result == {"status": "passed", "output": {"point": {"x": 320, "y": 640}}}
    assert device.calls == [("click", 320, 640)]


def test_uiautomator2_driver_scales_point_based_tap_from_reference_screen_size() -> None:
    device = FakeDevice()
    device.info["displayWidth"] = 540
    device.info["displayHeight"] = 1200
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.tap_at(
        {
            "point": {"x": 100, "y": 200},
            "reference_screen_size": {"width": 1080, "height": 2400},
        }
    )

    assert result == {"status": "passed", "output": {"point": {"x": 50, "y": 100}}}
    assert device.calls == [("click", 50, 100)]


def test_uiautomator2_driver_scales_point_based_swipe_from_reference_screen_size() -> None:
    device = FakeDevice()
    device.info["displayWidth"] = 540
    device.info["displayHeight"] = 1200
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.swipe(
        {
            "start": {"x": 800, "y": 1900},
            "end": {"x": 200, "y": 1900},
            "reference_screen_size": {"width": 1080, "height": 2400},
            "duration": 1000,
        }
    )

    assert result == {
        "status": "passed",
        "output": {"start": {"x": 400, "y": 950}, "end": {"x": 100, "y": 950}},
    }
    assert device.calls == [("swipe", 400, 950, 100, 950, 1.0)]


def test_uiautomator2_driver_exposes_ui_snapshot_output_with_ui_tree_alias() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == "<hierarchy />"
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_uses_compressed_hierarchy_when_supported() -> None:
    device = FakeCompressedDevice(
        hierarchy='<hierarchy><node text="Raw" /></hierarchy>',
        compressed_hierarchy='<hierarchy><node text="Compressed" /></hierarchy>',
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == '<hierarchy><node text="Compressed" /></hierarchy>'
    assert device.calls == [("dump_hierarchy", True)]


def test_uiautomator2_driver_compacts_android_ui_snapshot_xml() -> None:
    long_text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    device = FakeDevice(
        hierarchy=f'''
<hierarchy rotation="0">
    <node
        index="0"
        package="com.example"
        class="android.widget.FrameLayout"
        bounds="[0,0][1080,2400]"
        enabled="true"
        displayed="true"
        clickable="false">
        <node
            index="0"
            package="com.example"
            class="android.widget.LinearLayout"
            bounds="[0,0][1080,2400]"
            enabled="true"
            displayed="true"
            focusable="false">
            <node
                index="1"
                text="{long_text}"
                resource-id="com.example:id/title"
                class="android.widget.TextView"
                package="com.example"
                checkable="false"
                checked="false"
                clickable="false"
                enabled="true"
                focusable="false"
                focused="false"
                long-clickable="false"
                scrollable="false"
                password="false"
                selected="false"
                bounds="[0,0][100,50]"
                displayed="true" />
            <node
                index="2"
                text=""
                class="android.view.View"
                package="com.example"
                bounds="[0,50][100,100]"
                enabled="true"
                displayed="true" />
            <node
                index="3"
                text=""
                content-desc="Open menu"
                class="android.widget.ImageButton"
                package="com.example"
                clickable="true"
                enabled="true"
                bounds="[100,0][160,60]"
                displayed="true" />
            <node
                index="4"
                text=""
                resource-id="com.example:id/disabled"
                class="android.widget.Button"
                package="com.example"
                enabled="false"
                displayed="false"
                bounds="[0,100][100,160]" />
        </node>
    </node>
</hierarchy>
'''
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    # The XML comes from the in-process fake device and is trusted test data.
    root = ElementTree.fromstring(str(result["xml"]))  # noqa: S314
    nodes = list(root)
    assert [node.attrib for node in nodes] == [
        {
            "text": long_text[:50],
            "resource-id": "com.example:id/title",
            "class": "android.widget.TextView",
            "bounds": "[0,0][100,50]",
        },
        {
            "content-desc": "Open menu",
            "class": "android.widget.ImageButton",
            "clickable": "true",
            "bounds": "[100,0][160,60]",
        },
        {
            "resource-id": "com.example:id/disabled",
            "class": "android.widget.Button",
            "bounds": "[0,100][100,160]",
            "displayed": "false",
            "enabled": "false",
        },
    ]
    assert "package" not in result["xml"]
    assert "index" not in result["xml"]
    assert "FrameLayout" not in result["xml"]
    assert "LinearLayout" not in result["xml"]
    assert "android.view.View" not in result["xml"]
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_returns_raw_snapshot_when_xml_is_malformed() -> None:
    device = FakeDevice(hierarchy="<hierarchy><node")
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == "<hierarchy><node"
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_falls_back_to_raw_hierarchy_when_compaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeCompressedDevice(
        hierarchy='<hierarchy><node text="Raw" /></hierarchy>',
        compressed_hierarchy='<hierarchy><node text="Compressed" /></hierarchy>',
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    def fail_compaction(source_xml: str) -> str:
        raise RuntimeError(f"cannot compact {source_xml}")

    monkeypatch.setattr(driver, "_compact_ui_snapshot_xml", fail_compaction)

    result = driver.ui_snapshot({})

    assert result["xml"] == '<hierarchy><node text="Raw" /></hierarchy>'
    assert device.calls == [("dump_hierarchy", True), ("dump_hierarchy", False)]


def test_uiautomator2_driver_rejects_malformed_point_based_swipe() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.swipe({"start": {"x": 800}, "end": {"x": 200, "y": 1900}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "start.x, start.y, end.x, and end.y" in str(result["error_message"])


def test_uiautomator2_driver_rejects_malformed_point_based_tap() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.tap_at({"point": {"x": 320}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "point.x and point.y" in str(result["error_message"])


def test_uiautomator2_driver_waits_for_targets_before_actions() -> None:
    device = FakeDevice(wait_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    assert driver.tap_on({"locator": {"resourceId": "login"}})["status"] == "passed"
    assert driver.long_press_on({"locator": {"accessibilityId": "Menu"}})["status"] == "passed"
    assert driver.input_text({"text": "hello", "locator": {"resourceId": "search"}})["status"] == "passed"

    assert device.calls[:6] == [
        ("select", {"resourceId": "login"}),
        ("wait", {"resourceId": "login"}, {"exists": True, "timeout": 10.0}),
        ("click", {"resourceId": "login"}),
        ("select", {"description": "Menu"}),
        ("wait", {"description": "Menu"}, {"exists": True, "timeout": 10.0}),
        ("long_click", {"description": "Menu"}),
    ]
    assert ("wait", {"resourceId": "search"}, {"exists": True, "timeout": 10.0}) in device.calls


def test_uiautomator2_driver_input_text_focuses_replaces_and_verifies_text() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.input_text({"text": "machine learning tutorials", "locator": {"resourceId": "url_bar"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("select", {"resourceId": "url_bar"}),
        ("wait", {"resourceId": "url_bar"}, {"exists": True, "timeout": 10.0}),
        ("count", {"resourceId": "url_bar"}),
        ("info", {"resourceId": "url_bar"}),
        ("click", {"resourceId": "url_bar"}),
        ("set_text", {"resourceId": "url_bar"}, "machine learning tutorials"),
        ("count", {"resourceId": "url_bar"}),
        ("info", {"resourceId": "url_bar"}),
    ]


@pytest.mark.parametrize("backend", ["selector", "xpath"])
@pytest.mark.parametrize(
    "before_bounds",
    [
        {"left": 58, "top": 1209, "right": 1038, "bottom": 1346},
        {"left": 436, "top": 2056, "right": 728, "bottom": 2193},
    ],
)
def test_input_text_accepts_unique_target_layout_change_on_first_write(backend: str, before_bounds: dict[str, int]) -> None:
    device = FakeDevice()
    device.selector_info.update({"resourceName": "com.microsoft.emmx:id/url_bar", "packageName": "com.microsoft.emmx", "bounds": before_bounds})
    device.post_write_info = {"bounds": {"left": 186, "top": 1209, "right": 943, "bottom": 1346}}
    locator = {"resourceId": "com.microsoft.emmx:id/url_bar"} if backend == "selector" else {"xpath": "//android.widget.EditText[@resource-id='com.microsoft.emmx:id/url_bar']"}

    result = UiAutomator2AndroidDriver(app_id="com.microsoft.emmx", device=device).input_text({"text": "FSQ FSQ-lab", "locator": locator})

    assert result == {"status": "passed", "output": None}
    assert sum(call[0] == "set_text" for call in device.calls) == 1
    assert not any(call[0] == "clear_text" for call in device.calls)


@pytest.mark.parametrize("backend", ["selector", "xpath"])
@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("count", [0, 2, None, "1", True])
def test_input_text_requires_unique_target_before_and_after_entry(backend: str, after_write: bool, count: object) -> None:
    device = FakeDevice()
    if after_write:
        device.post_write_match_count = count
    else:
        device.match_count = count
    locator = {"resourceId": "input"} if backend == "selector" else {"xpath": "//android.widget.EditText[1]"}

    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": locator})

    assert result["status"] == "failed"
    assert sum(call[0] == "set_text" for call in device.calls) == int(after_write)
    if not after_write:
        assert not any(call[0] in {"click", "clear_text"} for call in device.calls)
    assert all(value not in str(result) for value in ["submitted-private", "private backend payload", "params"])


@pytest.mark.parametrize("backend", ["selector", "xpath"])
@pytest.mark.parametrize("after_write", [False, True])
def test_input_text_match_inspection_failure_is_safe(backend: str, after_write: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    original_count = FakeSelector.count.fget
    original_all = FakeXPathSelector.all

    def inspect_count(selector: FakeSelector) -> object:
        if after_write == any(call[0] == "set_text" for call in device.calls):
            raise RuntimeError("submitted-private private backend payload")
        return original_count(selector)

    def inspect_all(selector: FakeXPathSelector) -> list[FakeSelector]:
        inspect_count(selector)
        return original_all(selector)

    monkeypatch.setattr(FakeSelector, "count", property(inspect_count))
    monkeypatch.setattr(FakeXPathSelector, "all", inspect_all)
    locator = {"resourceId": "input"} if backend == "selector" else {"xpath": "//android.widget.EditText[1]"}

    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": locator})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable" if after_write else "entry_backend_error"}
    assert sum(call[0] == "set_text" for call in device.calls) == int(after_write)
    assert all(value not in str(result) for value in ["submitted-private", "private backend payload", "params"])


@pytest.mark.parametrize("resource_name", [None, ""])
@pytest.mark.parametrize("layout_changed", [False, True])
def test_input_text_structural_xpath_without_resource_id_requires_stable_bounds(resource_name: str | None, layout_changed: bool) -> None:
    device = FakeDevice()
    device.selector_info["resourceName"] = resource_name
    if layout_changed:
        device.post_write_info = {"bounds": {"left": 186, "top": 1209, "right": 943, "bottom": 1346}}

    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "Unicode 中文", "locator": {"xpath": "//android.widget.EditText[1]"}})

    if layout_changed:
        assert result["status"] == "failed"
        assert result["metadata"] == {"reason": "verification_unavailable"}
    else:
        assert result == {"status": "passed", "output": None}
    assert sum(call[0] == "set_text" for call in device.calls) == 1


@pytest.mark.parametrize("resource_name", ["", "shared"])
def test_input_text_real_xpath_positional_retarget_cannot_verify_noop_write(resource_name: str) -> None:
    xpath = pytest.importorskip("uiautomator2.xpath")

    class Device(FakeDevice):
        wait_timeout = 0.01

        def xpath(self, value: str) -> object:
            return xpath.XPathEntry(self)(value)

        def dump_hierarchy(self) -> str:
            nodes = [
                {"content-desc": "original", "text": "old-private", "bounds": "[0,0][100,50]"},
                {"content-desc": "other", "text": "replacement-private", "bounds": "[0,100][100,150]"},
            ]
            if any(call[0] == "set_text" for call in self.calls):
                nodes.reverse()
            root = ElementTree.Element("hierarchy")
            for node in nodes:
                ElementTree.SubElement(root, "node", {"class": "android.widget.EditText", "package": "app", "resource-id": resource_name, "enabled": "true", "password": "false", **node})
            return ElementTree.tostring(root, encoding="unicode")

        def clear_text(self) -> None:
            self.calls.append(("clear_text",))

        def send_keys(self, value: str) -> None:
            self.calls.append(("set_text", value))

    device = Device()
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "replacement-private", "locator": {"xpath": "//android.widget.EditText[1]"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert sum(call[0] == "set_text" for call in device.calls) == 1
    assert all(value not in str(result) for value in ["old-private", "replacement-private", "params"])
    # This XML is constructed entirely from the static offline fixture above.
    nodes = ElementTree.fromstring(device.dump_hierarchy()).findall("node")  # noqa: S314
    assert next(node for node in nodes if node.get("content-desc") == "original").get("text") == "old-private"


@pytest.mark.parametrize("package_name", [None, "", " "])
def test_input_text_missing_package_identity_is_not_verifiable(package_name: str | None) -> None:
    device = FakeDevice()
    device.selector_info["packageName"] = package_name

    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"className": "android.widget.FrameLayout", "clickable": True, "focused": True},
        {"className": "android.inputmethodservice.KeyboardView", "focusable": True},
        {"className": "custom.ChatInput", "editable": True},
        {"className": 123},
        {"className": "android.widget.EditText", "enabled": False, "password": False},
    ],
)
def test_input_text_rejects_unproven_editability_without_side_effects(info: dict[str, object]) -> None:
    device = FakeDevice()
    device.selector_info = info
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"] == {"reason": "invalid_input_target"}
    assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)
    assert "submitted-private" not in str(result)


@pytest.mark.parametrize("text", ["", "hello", "中文 café 👩🏽‍💻\n第二行", " existing whitespace "])
@pytest.mark.parametrize("class_name", ["android.widget.EditText", "android.widget.AutoCompleteTextView", "android.widget.MultiAutoCompleteTextView"])
def test_input_text_requires_exact_replacement_readback(text: str, class_name: str) -> None:
    device = FakeDevice(text="previous text")
    device.selector_info["className"] = class_name
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": text, "locator": {"resourceId": "input"}})

    assert result == {"status": "passed", "output": None}
    assert device.text == text
    assert sum(call[0] == "set_text" for call in device.calls) == 1
    assert device.calls[-1] == ("info", {"resourceId": "input"})


@pytest.mark.parametrize(
    ("mode", "post_info", "reason"),
    [
        ("reject", {}, "entry_rejected"),
        ("noop", {}, "entry_mismatch"),
        ("accept", {"text": "observed-private"}, "entry_mismatch"),
        ("accept", {"text": None}, "verification_unavailable"),
        ("accept", {"text": 123}, "verification_unavailable"),
        ("accept", {"resourceName": "different"}, "verification_unavailable"),
        ("accept", {"resourceName": None}, "verification_unavailable"),
        ("accept", {"packageName": "different"}, "verification_unavailable"),
        ("accept", {"packageName": None}, "verification_unavailable"),
        ("accept", {"enabled": False}, "verification_unavailable"),
        ("accept", {"className": "android.widget.FrameLayout"}, "verification_unavailable"),
        ("accept", {"className": "android.widget.AutoCompleteTextView"}, "verification_unavailable"),
        ("accept", {"password": True}, "verification_unavailable"),
        ("accept", {"text": "••••"}, "verification_unavailable"),
    ],
)
def test_input_text_backend_outcomes_are_safe_and_never_retried(mode: str, post_info: dict[str, object], reason: str) -> None:
    device = FakeDevice()
    device.write_mode = mode
    device.post_write_info = post_info
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert result["metadata"] == {"reason": reason}
    assert "submitted-private" not in str(result)
    assert "observed-private" not in str(result)
    assert "params" not in str(result)
    assert sum(call[0] == "set_text" for call in device.calls) == 1
    assert sum(call[0] == "select" for call in device.calls) == 1


@pytest.mark.parametrize("password", [True, None, "false", 0])
def test_input_text_requires_explicit_unmasked_metadata(password: object) -> None:
    device = FakeDevice()
    device.selector_info["password"] = password
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "••••", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)


@pytest.mark.parametrize("mask", ["••••", "****", "●●●●"])
def test_input_text_cannot_pass_by_matching_mask_characters(mask: str) -> None:
    device = FakeDevice()
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": mask, "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert mask not in str(result)


def test_input_text_missing_target_does_not_include_text_or_params() -> None:
    device = FakeDevice(exists=False, wait_result=False)
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "target": "target-private"})

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"] == {"reason": "target_missing"}
    assert "submitted-private" not in str(result)
    assert "target-private" not in str(result)
    assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)


@pytest.mark.parametrize(
    "locator_params",
    [
        {"target": "original"},
        {"locator": {"text": "original"}},
        {"locator": {"text": "original", "resourceId": "input"}},
        {"locator": {"xpath": "//android.widget.EditText[@text='original']"}},
        {"locator": {"xpath": "//*[@*[.='original']]"}},
    ],
)
def test_input_text_does_not_reresolve_mutated_text_locators(locator_params: dict[str, object]) -> None:
    device = FakeDevice(text="original")
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "replacement", **locator_params})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert device.text == "replacement"
    assert device.calls[-1][0] == "set_text"
    assert not any(call[0] == "clear_text" for call in device.calls)
    assert sum(call[0] == "info" for call in device.calls) == 1


@pytest.mark.parametrize("stage", ["select", "wait", "info_before", "click", "set_text", "info_after"])
def test_input_text_contains_backend_exceptions_without_leaking(stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    original_info = FakeSelector.info

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("submitted-private observed-private private backend payload")

    def info(selector: FakeSelector) -> dict[str, object]:
        after = any(call[0] == "set_text" for call in device.calls)
        if (stage == "info_after") == after:
            fail()
        return original_info(selector)

    if stage == "select":
        monkeypatch.setattr(FakeDevice, "__call__", fail)
    elif stage.startswith("info_"):
        monkeypatch.setattr(FakeSelector, "info", info)
    else:
        monkeypatch.setattr(FakeSelector, stage, fail)
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"]["reason"] == ("verification_unavailable" if stage == "info_after" else "entry_backend_error")
    assert all(value not in str(result) for value in ["submitted-private", "observed-private", "private backend payload", "params"])


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, CancelledError])
def test_input_text_preserves_process_interrupts(error: type[BaseException], monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_selector: FakeSelector, _text: str) -> None:
        raise error

    monkeypatch.setattr(FakeSelector, "set_text", interrupt)
    with pytest.raises(error):
        UiAutomator2AndroidDriver(app_id="app", device=FakeDevice()).input_text({"text": "private", "locator": {"resourceId": "input"}})


def test_input_text_supports_info_property(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeSelector, "info", property(FakeSelector.info))
    device = FakeDevice()
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "replacement", "locator": {"resourceId": "input"}})

    assert result["status"] == "passed"
    assert device.calls[-1] == ("info", {"resourceId": "input"})


@pytest.mark.parametrize("backend", ["selector", "xpath"])
@pytest.mark.parametrize("text", ["", "Unicode 中文 👋"])
@pytest.mark.parametrize(("before_count", "after_count"), [(1, 1), (2, 1), (1, 2)])
@pytest.mark.parametrize(
    "before_bounds",
    [
        {"left": 58, "top": 1209, "right": 1038, "bottom": 1346},
        {"left": 436, "top": 2056, "right": 728, "bottom": 2193},
    ],
)
def test_input_text_real_uiautomator2_apis_offline(backend: str, text: str, before_count: int, after_count: int, before_bounds: dict[str, int]) -> None:
    selectors = pytest.importorskip("uiautomator2._selector")
    xpath = pytest.importorskip("uiautomator2.xpath")

    class Rpc:
        def __init__(self, device: FakeDevice) -> None:
            self.device = device

        def waitForExists(self, *_args: object, **_kwargs: object) -> bool:
            return True

        def count(self, _selector: object) -> int:
            return after_count if any(call[0] in {"set_text", "clear_text"} for call in self.device.calls) else before_count

        def objInfo(self, _selector: object) -> dict[str, object]:
            # The shipped native ObjInfo DTO does not expose password state.
            info = {"text": self.device.text, **{key: value for key, value in self.device.selector_info.items() if key != "password"}}
            if any(call[0] in {"set_text", "clear_text"} for call in self.device.calls):
                info.update(self.device.post_write_info)
            return info

        def setText(self, _selector: object, value: str) -> bool:
            self.device.calls.append(("set_text", value))
            self.device.text = value
            return True

        def clearTextField(self, _selector: object) -> bool:
            self.device.calls.append(("clear_text",))
            self.device.text = ""
            return True

    class Device(FakeDevice):
        wait_timeout = 0.01

        def __init__(self) -> None:
            super().__init__()
            self.selector_info["resourceName"] = "input"
            self.selector_info["bounds"] = before_bounds
            self.post_write_info = {"bounds": {"left": 186, "top": 1209, "right": 943, "bottom": 1346}}
            self.jsonrpc = Rpc(self)

        def __call__(self, **query: object) -> object:
            return selectors.UiObject(self, selectors.Selector(**query))

        def xpath(self, value: str) -> object:
            return xpath.XPathEntry(self)(value)

        def dump_hierarchy(self) -> str:
            info = self.jsonrpc.objInfo(None)
            bounds = info["bounds"]
            root = ElementTree.Element("hierarchy")
            for _ in range(self.jsonrpc.count(None)):
                ElementTree.SubElement(
                    root,
                    "node",
                    {
                        "class": "android.widget.EditText",
                        "text": self.text,
                        "password": "false",
                        "resource-id": "input",
                        "package": "com.example",
                        "bounds": f"[{bounds['left']},{bounds['top']}][{bounds['right']},{bounds['bottom']}]",
                    },
                )
            return ElementTree.tostring(root, encoding="unicode")

        def clear_text(self) -> None:
            self.calls.append(("clear_text",))
            self.text = ""

        def send_keys(self, value: str) -> None:
            self.calls.append(("set_text", value))
            self.text = value

    device = Device()
    locator = {"resourceId": "input"} if backend == "selector" else {"xpath": "//android.widget.EditText[@resource-id='input']"}
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": text, "locator": locator})

    if before_count == after_count == 1:
        assert result == {"status": "passed", "output": None}
        assert device.text == text
    else:
        assert result["status"] == "failed"
        if text:
            assert text not in str(result)
    assert sum(call[0] == "set_text" or (call[0] == "clear_text" and backend == "selector" and not text) for call in device.calls) == int(before_count == 1)


@pytest.mark.parametrize(
    ("nodes", "passes"),
    [
        ('<node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="same" password="false" />', True),
        ('<node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="same" password="true" />', False),
        ('<node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="same" />', False),
        ('<node class="android.widget.EditText" resource-id="other" package="com.example" bounds="[0,0][100,50]" text="same" password="false" />', False),
        ('<node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="different" password="false" />', False),
        ("<node", False),
        ("", False),
    ],
)
def test_input_text_native_password_state_requires_matching_hierarchy(nodes: str, passes: bool) -> None:
    device = FakeDevice(text="same", hierarchy=f"<hierarchy>{nodes}</hierarchy>")
    del device.selector_info["password"]
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "same", "locator": {"resourceId": "input"}})

    assert result["status"] == ("passed" if passes else "failed")
    if not passes:
        assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)
    assert "same" not in str(result)


def test_input_text_ambiguous_hierarchy_is_not_proof_of_unmasked_target() -> None:
    node = '<node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="same" password="false" />'
    device = FakeDevice(text="same", hierarchy=f"<hierarchy>{node}{node}</hierarchy>")
    del device.selector_info["password"]
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "same", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable"}
    assert not any(call[0] in {"click", "clear_text", "set_text"} for call in device.calls)


@pytest.mark.parametrize("after_write", [False, True])
def test_input_text_native_hierarchy_read_failure_is_safe(after_write: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    del device.selector_info["password"]

    def hierarchy() -> str:
        after = any(call[0] == "set_text" for call in device.calls)
        if not after_write or after:
            raise RuntimeError("submitted-private observed-private raw hierarchy")
        return f'<hierarchy><node class="android.widget.EditText" resource-id="com.example:id/input" package="com.example" bounds="[0,0][100,50]" text="{device.text}" password="false" /></hierarchy>'

    monkeypatch.setattr(device, "dump_hierarchy", hierarchy)
    result = UiAutomator2AndroidDriver(app_id="app", device=device).input_text({"text": "submitted-private", "locator": {"resourceId": "input"}})

    assert result["status"] == "failed"
    assert result["metadata"] == {"reason": "verification_unavailable" if after_write else "entry_backend_error"}
    assert all(value not in str(result) for value in ["submitted-private", "observed-private", "raw hierarchy"])
    assert sum(call[0] == "set_text" for call in device.calls) == int(after_write)


@pytest.mark.parametrize(
    ("mode", "post_info", "reason"),
    [
        ("reject", {}, "entry_rejected"),
        ("noop", {}, "entry_mismatch"),
        ("accept", {"text": None}, "verification_unavailable"),
        ("error", {}, "entry_backend_error"),
    ],
)
def test_input_text_failure_propagates_through_android_harness(mode: str, post_info: dict[str, object], reason: str) -> None:
    device = FakeDevice()
    device.write_mode = mode
    device.post_write_info = post_info
    harness = AndroidHarness(driver=UiAutomator2AndroidDriver(app_id="app", device=device))
    step = ExecutableStep(step_id="input", kind="action", action_name="inputText", params={"text": "submitted-private", "locator": {"resourceId": "input"}})

    result = harness.invoke_action(step, harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "action_error"
    assert result.metadata == {"reason": reason}
    assert result.output is None
    assert all(value not in result.model_dump_json() for value in ["submitted-private", "private backend payload", "params"])


def test_uiautomator2_driver_assert_visible_waits_before_missing_target() -> None:
    device = FakeDevice(exists=False, wait_result=False)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_visible({"locator": {"text": "Missing"}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert device.calls == [
        ("select", {"text": "Missing"}),
        ("wait", {"text": "Missing"}, {"exists": True, "timeout": 10.0}),
    ]


def test_uiautomator2_driver_assert_not_visible_waits_for_visible_target_to_disappear() -> None:
    device = FakeDevice(exists=True, wait_gone_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_not_visible({"locator": {"text": "Dialog"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("select", {"text": "Dialog"}),
        ("wait_gone", {"text": "Dialog"}, {"timeout": 10.0}),
    ]


def test_uiautomator2_driver_xpath_wait_retries_without_exists_argument() -> None:
    device = FakeDevice(wait_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_visible({"locator": {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("xpath", "//android.widget.TextView[@text='Browse InPrivate']"),
        (
            "xpath_wait",
            {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"},
            {"exists": True, "timeout": 10.0},
        ),
        (
            "xpath_wait",
            {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"},
            {"timeout": 10.0},
        ),
    ]


def test_uiautomator2_driver_assertion_and_missing_target_results() -> None:
    device = FakeDevice(exists=False, wait_result=False)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    missing = driver.assert_visible({"locator": {"text": "Missing"}})
    absent = driver.assert_not_visible({"locator": {"text": "Missing"}})

    assert missing["status"] == "failed"
    assert missing["failure_category"] == "target_resolution_error"
    assert absent["status"] == "passed"


def test_uiautomator2_driver_asserts_android_element_state_fields() -> None:
    device = FakeDevice()
    device.selector_info = {
        "enabled": False,
        "checked": True,
        "selected": True,
        "clickable": False,
        "focused": True,
    }
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state(
        {
            "element": {
                "xpath": "//android.widget.FrameLayout[1]",
                "enabled": False,
                "checked": True,
                "selected": True,
                "clickable": False,
                "focused": True,
            }
        }
    )

    assert result["status"] == "passed"
    assert result["output"] == {
        "enabled": False,
        "checked": True,
        "selected": True,
        "clickable": False,
        "focused": True,
    }
    assert device.calls == [
        ("xpath", "//android.widget.FrameLayout[1]"),
        ("xpath_wait", {"xpath": "//android.widget.FrameLayout[1]"}, {"exists": True, "timeout": 10.0}),
        ("xpath_wait", {"xpath": "//android.widget.FrameLayout[1]"}, {"timeout": 10.0}),
        ("info", {"xpath": "//android.widget.FrameLayout[1]"}),
    ]


def test_uiautomator2_driver_reports_android_element_state_mismatch() -> None:
    device = FakeDevice()
    device.selector_info = {"enabled": True}
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state({"element": {"resourceId": "item", "enabled": False}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "assertion_error"
    assert result["error_message"] == "Element state assertion failed."
    assert result["output"] == {"field": "enabled", "expected": False, "actual": True}


def test_uiautomator2_driver_reports_unsupported_assertion_shape() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.assert_state({"element": {}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "text or supported element state assertion" in str(result["error_message"])


def test_uiautomator2_driver_asserts_element_existence_with_locator_only() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state({"element": {"xpath": "//android.widget.EditText[@text='chinatravel.com']"}})

    assert result == {"status": "passed", "output": {"exists": True}}
    assert device.calls == [
        ("xpath", "//android.widget.EditText[@text='chinatravel.com']"),
        (
            "xpath_wait",
            {"xpath": "//android.widget.EditText[@text='chinatravel.com']"},
            {"exists": True, "timeout": 10.0},
        ),
        (
            "xpath_wait",
            {"xpath": "//android.widget.EditText[@text='chinatravel.com']"},
            {"timeout": 10.0},
        ),
    ]


def test_uiautomator2_driver_reports_unimplemented_backend_operations() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    perform_result = driver.perform_actions({"actions": []})

    assert perform_result["status"] == "failed"
    assert perform_result["failure_category"] == "configuration_error"


def test_uiautomator2_driver_owns_ai_assertion_backend_tool() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    definitions = {
        definition.name: definition
        for definition in _discover_driver_capability_definitions(
            UiAutomator2AndroidDriver,
            platform="android",
            metadata={"driver_class": "UiAutomator2AndroidDriver", "backend": "uiautomator2"},
        )
    }
    result = driver.assert_with_ai(AndroidAssertWithAIParams(prompt="Verify the screen."))

    assert definitions["assert_with_ai"].replay == ReplayPolicy(kind="fsq_command", alias="assertWithAI")
    assert definitions["assert_with_ai"].executor_kind == "driver"
    assert definitions["assert_with_ai"].owner == "driver"
    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"


def test_uiautomator2_driver_raises_configuration_error_when_dependency_missing(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "uiautomator2":
            raise ImportError("missing uiautomator2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ConfigurationError) as exc_info:
        UiAutomator2AndroidDriver(app_id="com.example.app")

    assert exc_info.value.context == {"action": "Reinstall or repair fsq-agent."}
