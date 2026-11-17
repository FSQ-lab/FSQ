# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Protocol, runtime_checkable

from fsq_agent.core.interfaces._harness import DriverObservationInterface
from fsq_agent.models import (
    AndroidAssertNotVisibleParams,
    AndroidAssertStateParams,
    AndroidAssertVisibleParams,
    AndroidAssertWithAIParams,
    AndroidInputTextParams,
    AndroidInspectElementParams,
    AndroidInspectWithAIParams,
    AndroidKillAppParams,
    AndroidLaunchAppParams,
    AndroidLongPressOnParams,
    AndroidPerformActionsParams,
    AndroidPressKeyParams,
    AndroidSwipeParams,
    AndroidTapAtParams,
    AndroidTapOnParams,
    AndroidUiTreeParams,
)


@runtime_checkable
class AndroidDriverInterface(DriverObservationInterface, Protocol):
    def context(self) -> dict[str, object]: ...

    def launch_app(self, params: AndroidLaunchAppParams) -> dict[str, object]: ...

    def kill_app(self, params: AndroidKillAppParams) -> dict[str, object]: ...

    def tap_on(self, params: AndroidTapOnParams) -> dict[str, object]: ...

    def tap_at(self, params: AndroidTapAtParams) -> dict[str, object]: ...

    def long_press_on(self, params: AndroidLongPressOnParams) -> dict[str, object]: ...

    def input_text(self, params: AndroidInputTextParams) -> dict[str, object]: ...

    def press_key(self, params: AndroidPressKeyParams) -> dict[str, object]: ...

    def swipe(self, params: AndroidSwipeParams) -> dict[str, object]: ...

    def perform_actions(self, params: AndroidPerformActionsParams) -> dict[str, object]: ...

    def assert_visible(self, params: AndroidAssertVisibleParams) -> dict[str, object]: ...

    def assert_not_visible(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]: ...

    def assert_state(self, params: AndroidAssertStateParams) -> dict[str, object]: ...

    def assert_with_ai(self, params: AndroidAssertWithAIParams) -> dict[str, object]: ...

    def screenshot(self, params: object | None = None) -> bytes: ...

    def ui_snapshot(self, params: AndroidUiTreeParams) -> dict[str, object]: ...

    def inspect_element(self, params: AndroidInspectElementParams) -> dict[str, object]: ...

    def inspect_with_ai(self, params: AndroidInspectWithAIParams) -> dict[str, object]: ...
