# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Protocol, runtime_checkable

from fsq_agent.core.interfaces._harness import DriverObservationInterface
from fsq_agent.models import (
    WindowsAssertVisibleParams,
    WindowsAssertWithAIParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsInspectWithAIParams,
    WindowsKillAppParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
    WindowsUiSnapshotParams,
)


@runtime_checkable
class WindowsDriverInterface(DriverObservationInterface, Protocol):
    def context(self) -> dict[str, object]: ...

    def launch_app(self, params: WindowsLaunchAppParams) -> dict[str, object]: ...

    def kill_app(self, params: WindowsKillAppParams) -> dict[str, object]: ...

    def click_on(self, params: WindowsClickOnParams) -> dict[str, object]: ...

    def double_click_on(self, params: WindowsDoubleClickOnParams) -> dict[str, object]: ...

    def right_click_on(self, params: WindowsRightClickOnParams) -> dict[str, object]: ...

    def type_text(self, params: WindowsTypeTextParams) -> dict[str, object]: ...

    def press_key(self, params: WindowsPressKeyParams) -> dict[str, object]: ...

    def hover_on(self, params: WindowsHoverOnParams) -> dict[str, object]: ...

    def scroll_on(self, params: WindowsScrollOnParams) -> dict[str, object]: ...

    def drag_to(self, params: WindowsDragToParams) -> dict[str, object]: ...

    def assert_visible(self, params: WindowsAssertVisibleParams) -> dict[str, object]: ...

    def assert_with_ai(self, params: WindowsAssertWithAIParams) -> dict[str, object]: ...

    def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]: ...

    def inspect_with_ai(self, params: WindowsInspectWithAIParams) -> dict[str, object]: ...

    def screenshot(self, params: object | None = None) -> bytes: ...
