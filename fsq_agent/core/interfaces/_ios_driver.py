# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Protocol, runtime_checkable

from fsq_agent.core.interfaces._harness import DriverObservationInterface
from fsq_agent.models import (
    IOSAssertNotVisibleParams,
    IOSAssertStateParams,
    IOSAssertVisibleParams,
    IOSAssertWithAIParams,
    IOSClearTextParams,
    IOSDoubleTapOnParams,
    IOSHandleAlertParams,
    IOSHideKeyboardParams,
    IOSInputTextParams,
    IOSInspectElementParams,
    IOSInspectWithAIParams,
    IOSLaunchAppParams,
    IOSLongPressOnParams,
    IOSPressKeyParams,
    IOSScrollToParams,
    IOSStopAppParams,
    IOSSwipeParams,
    IOSTakeScreenshotParams,
    IOSTapAtParams,
    IOSTapOnParams,
    IOSUiSnapshotParams,
)


@runtime_checkable
class IOSDriverInterface(DriverObservationInterface, Protocol):
    def context(self) -> dict[str, object]: ...

    def launch_app(self, params: IOSLaunchAppParams) -> dict[str, object]: ...

    def stop_app(self, params: IOSStopAppParams) -> dict[str, object]: ...

    def tap_on(self, params: IOSTapOnParams) -> dict[str, object]: ...

    def double_tap_on(self, params: IOSDoubleTapOnParams) -> dict[str, object]: ...

    def long_press_on(self, params: IOSLongPressOnParams) -> dict[str, object]: ...

    def tap_at(self, params: IOSTapAtParams) -> dict[str, object]: ...

    def input_text(self, params: IOSInputTextParams) -> dict[str, object]: ...

    def clear_text(self, params: IOSClearTextParams) -> dict[str, object]: ...

    def hide_keyboard(self, params: IOSHideKeyboardParams) -> dict[str, object]: ...

    def press_key(self, params: IOSPressKeyParams) -> dict[str, object]: ...

    def swipe(self, params: IOSSwipeParams) -> dict[str, object]: ...

    def scroll_to(self, params: IOSScrollToParams) -> dict[str, object]: ...

    def handle_alert(self, params: IOSHandleAlertParams) -> dict[str, object]: ...

    def take_screenshot(self, params: IOSTakeScreenshotParams) -> bytes: ...

    def assert_visible(self, params: IOSAssertVisibleParams) -> dict[str, object]: ...

    def assert_not_visible(self, params: IOSAssertNotVisibleParams) -> dict[str, object]: ...

    def assert_state(self, params: IOSAssertStateParams) -> dict[str, object]: ...

    def assert_with_ai(self, params: IOSAssertWithAIParams) -> dict[str, object]: ...

    def screenshot(self, params: object | None = None) -> bytes: ...

    def ui_snapshot(self, params: IOSUiSnapshotParams) -> dict[str, object]: ...

    def inspect_element(self, params: IOSInspectElementParams) -> dict[str, object]: ...

    def inspect_with_ai(self, params: IOSInspectWithAIParams) -> dict[str, object]: ...
