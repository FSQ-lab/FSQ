# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Protocol, runtime_checkable

from fsq_agent.core.interfaces._harness import DriverObservationInterface
from fsq_agent.models import (
    MacOSAssertElementsOrderParams,
    MacOSAssertNotVisibleParams,
    MacOSAssertStateParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSInspectElementParams,
    MacOSInspectWithAIParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSScrollOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
)


@runtime_checkable
class MacOSDriverInterface(DriverObservationInterface, Protocol):
    def context(self) -> dict[str, object]: ...

    def launch_app(self, params: MacOSLaunchAppParams) -> dict[str, object]: ...

    def kill_app(self, params: MacOSKillAppParams) -> dict[str, object]: ...

    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]: ...

    def double_click_on(self, params: MacOSDoubleClickOnParams) -> dict[str, object]: ...

    def right_click_on(self, params: MacOSRightClickOnParams) -> dict[str, object]: ...

    def type_text(self, params: MacOSTypeTextParams) -> dict[str, object]: ...

    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]: ...

    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]: ...

    def scroll_on(self, params: MacOSScrollOnParams) -> dict[str, object]: ...

    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]: ...

    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes | dict[str, object]: ...

    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]: ...

    def inspect_element(self, params: MacOSInspectElementParams) -> dict[str, object]: ...

    def inspect_with_ai(self, params: MacOSInspectWithAIParams) -> dict[str, object]: ...

    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]: ...

    def assert_not_visible(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]: ...

    def assert_state(self, params: MacOSAssertStateParams) -> dict[str, object]: ...

    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]: ...

    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]: ...
    def screenshot(self, params: object | None = None) -> bytes: ...
