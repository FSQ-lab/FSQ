# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import ntpath
import os
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from math import ceil, hypot
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _windows_driver_tool
from fsq_agent.models import (
    ConfigurationError,
    WindowsAssertVisibleParams,
    WindowsAssertWithAIParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsKillAppParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
    WindowsUiSnapshotParams,
)

DEFAULT_WINDOWS_WAIT_TIMEOUT_SECONDS = 10.0
WINDOW_READY_TIMEOUT_SECONDS = 30.0
WINDOW_LAUNCH_WAIT_FOR = "exists visible enabled"
UI_SNAPSHOT_MAX_DEPTH = 20
UI_SNAPSHOT_MAX_NODES = 1200
UI_SNAPSHOT_MAX_CHILDREN = 60
UI_SNAPSHOT_MAX_BYTES = 800000
MOUSE_DRAG_STEP_PIXELS = 15
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7
GW_HWNDPREV = 3
DWMWA_CLOAKED = 14
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0
_T = TypeVar("_T")
_HOST_FRAME_EXECUTABLE = "applicationframehost.exe"


def _normalize_windows_executable_path(raw_path: str) -> str:
    expanded = ntpath.expandvars(raw_path.strip().strip('"'))
    if os.name == "nt":
        with suppress(OSError):
            expanded = os.path.realpath(expanded)
    return ntpath.normpath(expanded.replace("/", "\\")).casefold()


class PywinautoWindowsDriver(AIAssertionBackendToolMixin):
    backend = "pywinauto"

    def __init__(
        self,
        *,
        app_path: str | Path | None = None,
        backend_kind: str = "uia",
        window_title_re: str | None = None,
        launch_args: list[str] | None = None,
    ) -> None:
        raw_app_path = str(app_path).strip() if app_path is not None else ""
        self.app_path = raw_app_path or None
        self.backend_kind = backend_kind
        self.window_title_re = window_title_re.strip() if isinstance(window_title_re, str) and window_title_re.strip() else None
        self.launch_args = list(launch_args) if launch_args else []
        self._app: object | None = None
        self._launched: bool = False
        self._launched_pid: int | None = None
        self._bound_handle: int | None = None
        self._pre_existing_handles: set[int] = set()
        self._pre_existing_pids: set[int] = set()
        self._owns_process: bool = False
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-pywinauto")

    def context(self) -> dict[str, object]:
        return self._run_sync(self._context_payload)

    def _context_payload(self) -> dict[str, object]:
        return {
            "session_id": f"pywinauto:{self.backend_kind}",
            "current_url": None,
            "screen_size": self._window_size(),
            "metadata": {
                "backend": self.backend,
                "backend_kind": self.backend_kind,
                "app_path_configured": self.app_path is not None,
                "window_title_re_configured": self.window_title_re is not None,
                "app_launched": self._launched or (self.app_path is None and self.window_title_re is not None),
            },
        }

    @_windows_driver_tool(
        "launchApp",
        description="Launch the configured Windows desktop application.",
    )
    def launch_app(self, params: WindowsLaunchAppParams) -> dict[str, object]:
        return self._run_sync(lambda: self._launch_app(params))

    def _launch_app(self, params: WindowsLaunchAppParams) -> dict[str, object]:
        app_path = self.app_path
        if not app_path:
            return self._failed("configuration_error", "Windows app path is not configured.")
        if self._launched:
            self._cleanup_owned_target()
        application_cls = self._application_cls()
        target_norm = _normalize_windows_executable_path(app_path)
        pre_all_handles, pre_matching_pids = self._snapshot_pre_launch_state(target_norm)
        launch_args = [*self.launch_args, *(params.extra_args or [])]
        cmd = subprocess.list2cmdline([app_path, *launch_args])
        started = application_cls(backend=self.backend_kind).start(cmd)
        started_pid = getattr(started, "process", None)
        self._app = started
        self._launched = True
        self._launched_pid = started_pid if isinstance(started_pid, int) else None
        self._bound_handle = None
        self._pre_existing_handles = pre_all_handles
        self._pre_existing_pids = pre_matching_pids
        self._owns_process = len(pre_matching_pids) == 0
        try:
            window = self._resolve_main_window(wait=True, wait_for=WINDOW_LAUNCH_WAIT_FOR)
            self._ensure_window_foreground(window)
        except BaseException:
            with suppress(Exception):
                self._cleanup_owned_target()
            raise
        return self._passed({"app_path": app_path, "launch_args": launch_args, "window_title_re": self.window_title_re})

    def _resolve_main_window(self, *, wait: bool = False, wait_for: str = "exists visible") -> object:
        application_cls = self._application_cls()
        deadline = time.monotonic() + WINDOW_READY_TIMEOUT_SECONDS if wait else None
        while True:
            try:
                connected, window, handle, owns_process = self._select_target_window(application_cls, wait_for=wait_for, wait=wait)
            except Exception as exc:
                details = f"app_path={self.app_path!r}, title_re={self.window_title_re!r}, wait_for={wait_for!r}, backend={self.backend_kind!r}; last error: {exc}"
                if deadline is None:
                    raise RuntimeError(f"Failed to resolve Windows main window ({details})") from exc
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out after {WINDOW_READY_TIMEOUT_SECONDS:.1f} seconds resolving Windows main window ({details})") from exc
                time.sleep(1.0)
            else:
                self._app = connected
                if handle is not None:
                    self._bound_handle = handle
                if owns_process is False:
                    self._owns_process = False
                return window

    def _select_target_window(
        self,
        application_cls: Any,
        *,
        wait_for: str,
        wait: bool,
    ) -> tuple[object, object, int | None, bool | None]:
        timeout = 2 if wait else 0
        if self.app_path:
            if not self._launched:
                raise LookupError("Configured Windows application has not been launched.")
            if self._bound_handle is not None:
                rebound = self._try_connect_window_handle(application_cls, self._bound_handle, wait_for=wait_for, timeout=timeout)
                if rebound is not None:
                    return rebound[0], rebound[1], self._bound_handle, None
                self._bound_handle = None
            target_norm = _normalize_windows_executable_path(self.app_path)
            direct_candidates: list[tuple[int, int, bool]] = []
            host_frame_candidates: list[int] = []
            top_query: dict[str, Any] = {"title_re": self.window_title_re, "visible_only": self.backend_kind != "uia"}
            if self.backend_kind == "uia":
                top_query["control_type"] = "Window"
            for elem in self._list_top_windows(**top_query):
                hwnd = getattr(elem, "handle", None)
                pid = getattr(elem, "process_id", None)
                if not isinstance(hwnd, int) or not isinstance(pid, int):
                    continue
                if self.backend_kind == "uia" and not self._is_candidate_top_window_visible_or_minimized(elem, hwnd):
                    continue
                is_started_pid = self._launched_pid is not None and pid == self._launched_pid
                exe_norm = self._safe_normalized_process_path(pid)
                if is_started_pid or exe_norm == target_norm:
                    direct_candidates.append((hwnd, pid, hwnd not in self._pre_existing_handles))
                elif self.window_title_re and exe_norm is not None and ntpath.basename(exe_norm) == _HOST_FRAME_EXECUTABLE and hwnd not in self._pre_existing_handles:
                    host_frame_candidates.append(hwnd)

            ordered_direct = [(hwnd, pid) for hwnd, pid, is_new in direct_candidates if is_new] + [(hwnd, pid) for hwnd, pid, is_new in direct_candidates if not is_new]
            for hwnd, pid in ordered_direct:
                resolved = self._try_connect_window_handle(application_cls, hwnd, wait_for=wait_for, timeout=timeout)
                if resolved is not None:
                    borrowed = hwnd in self._pre_existing_handles or pid in self._pre_existing_pids
                    return resolved[0], resolved[1], hwnd, (False if borrowed else None)
            for hwnd in host_frame_candidates:
                resolved = self._try_connect_window_handle(application_cls, hwnd, wait_for=wait_for, timeout=timeout)
                if resolved is not None:
                    return resolved[0], resolved[1], hwnd, False

            raise LookupError("No visible top-level window matched the configured Windows executable.")

        if self.window_title_re:
            connected, window = self._connect_window_by_title_re(application_cls, wait_for=wait_for, timeout=timeout)
            resolved_handle = self._extract_window_handle(window)
            return connected, window, resolved_handle, False
        raise LookupError("Neither Windows app_path nor window_title_re is configured.")

    def _connect_window_by_title_re(self, application_cls: Any, *, wait_for: str, timeout: int) -> tuple[object, object]:
        window_kwargs: dict[str, Any] = {"title_re": self.window_title_re}
        if self.backend_kind == "uia":
            window_kwargs["control_type"] = "Window"
        try:
            connected = application_cls(backend=self.backend_kind).connect(title_re=self.window_title_re)
            window = connected.window(**window_kwargs)
            window.wait(wait_for, timeout=timeout)
        except Exception:
            if self.backend_kind != "uia":
                raise
            iconic_candidates = [getattr(elem, "handle", None) for elem in self._list_top_windows(title_re=self.window_title_re, control_type="Window", visible_only=False)]
            for hwnd in iconic_candidates:
                if isinstance(hwnd, int) and self._is_win32_window(hwnd) and self._is_window_iconic(hwnd) and self._is_win32_window_visible(hwnd):
                    resolved = self._try_connect_window_handle(application_cls, hwnd, wait_for=wait_for, timeout=timeout)
                    if resolved is not None:
                        return resolved
            raise
        return connected, window

    def _is_candidate_top_window_visible_or_minimized(self, elem: Any, hwnd: int) -> bool:
        elem_visible = getattr(elem, "visible", None)
        if callable(elem_visible):
            elem_visible = self._safe_call(elem_visible)
        if elem_visible is True:
            return True
        if self._is_win32_window(hwnd):
            return self._is_window_iconic(hwnd) or self._is_win32_window_visible(hwnd)
        return elem_visible is not False

    def _effective_wait_for(self, wait_for: str, hwnd: int) -> str:
        if self._is_win32_window(hwnd) and self._is_window_iconic(hwnd):
            parts = [state_name for state_name in wait_for.split() if state_name != "visible"]
            return " ".join(parts) or "exists"
        return wait_for

    def _try_connect_window_handle(self, application_cls: Any, handle: int, *, wait_for: str, timeout: int) -> tuple[object, object] | None:
        try:
            connected = application_cls(backend=self.backend_kind).connect(handle=handle)
            window = connected.window(handle=handle)
            effective_wait = self._effective_wait_for(wait_for, handle)
            window.wait(effective_wait, timeout=timeout)
        except Exception:  # noqa: BLE001 - candidate handle may have closed or become hidden mid-probe.
            return None
        return connected, window

    def _snapshot_pre_launch_state(self, target_norm: str) -> tuple[set[int], set[int]]:
        all_handles: set[int] = set()
        matching_pids: set[int] = set()
        snapshot_query: dict[str, Any] = {"visible_only": False}
        if self.backend_kind == "uia":
            snapshot_query["control_type"] = "Window"
        for elem in self._list_top_windows(**snapshot_query):
            hwnd = getattr(elem, "handle", None)
            pid = getattr(elem, "process_id", None)
            if isinstance(hwnd, int):
                all_handles.add(hwnd)
            if isinstance(pid, int) and self._safe_normalized_process_path(pid) == target_norm:
                matching_pids.add(pid)
        for pid in self._list_process_ids():
            if pid not in matching_pids and self._safe_normalized_process_path(pid) == target_norm:
                matching_pids.add(pid)
        return all_handles, matching_pids

    def _safe_normalized_process_path(self, pid: int) -> str | None:
        try:
            raw = self._process_executable_path(pid)
        except Exception:  # noqa: BLE001 - ignore elevated/exited unrelated desktop processes.
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        return _normalize_windows_executable_path(raw)

    @_windows_driver_tool("killApp", description="Stop the launched Windows desktop application.")
    def kill_app(self, params: WindowsKillAppParams) -> dict[str, object]:
        return self._run_sync(self._kill_app)

    def _kill_app(self) -> dict[str, object]:
        self._cleanup_owned_target()
        return self._passed()

    def _cleanup_owned_target(self) -> None:
        if not self._launched:
            return
        if self._owns_process and self._app is not None:
            kill = getattr(self._app, "kill", None)
            if callable(kill):
                kill()
        elif self._bound_handle is not None and self._bound_handle not in self._pre_existing_handles and self._window_handle_exists(self._bound_handle):
            application_cls = self._application_cls()
            connected = application_cls(backend=self.backend_kind).connect(handle=self._bound_handle)
            window = connected.window(handle=self._bound_handle)
            close_fn = getattr(window, "close", None)
            if callable(close_fn):
                close_fn()
        self._reset_launch_state()

    def _reset_launch_state(self) -> None:
        self._app = None
        self._launched = False
        self._launched_pid = None
        self._bound_handle = None
        self._pre_existing_handles = set()
        self._pre_existing_pids = set()
        self._owns_process = False

    @_windows_driver_tool("clickOn", description="Click a Windows control resolved from the UI snapshot.")
    def click_on(self, params: WindowsClickOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._click_on(params))

    def _click_on(self, params: WindowsClickOnParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        control = self._control(params)
        button = params.button or "left"
        if params.double:
            control.double_click_input(button=button)
        else:
            control.click_input(button=button)
        return self._passed()

    @_windows_driver_tool("doubleClickOn", description="Double-click a Windows control.")
    def double_click_on(self, params: WindowsDoubleClickOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._double_click_on(params))

    def _double_click_on(self, params: WindowsDoubleClickOnParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        control = self._control(params)
        control.double_click_input(button=params.button or "left")
        return self._passed()

    @_windows_driver_tool("rightClickOn", description="Right-click a Windows control.")
    def right_click_on(self, params: WindowsRightClickOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._right_click_on(params))

    def _right_click_on(self, params: WindowsRightClickOnParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        control = self._control(params)
        control.click_input(button="right")
        return self._passed()

    @_windows_driver_tool("typeText", description="Type text into a Windows control.")
    def type_text(self, params: WindowsTypeTextParams) -> dict[str, object]:
        return self._run_sync(lambda: self._type_text(params))

    def _type_text(self, params: WindowsTypeTextParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        control = self._control(params)
        control.click_input()
        if params.clear:
            control.type_keys("^a{BACKSPACE}", with_spaces=True)
        control.type_keys(params.text, with_spaces=True)
        return self._passed()

    @_windows_driver_tool("pressKey", description="Send a keyboard key sequence to the active Windows window.")
    def press_key(self, params: WindowsPressKeyParams) -> dict[str, object]:
        return self._run_sync(lambda: self._press_key(params))

    def _press_key(self, params: WindowsPressKeyParams) -> dict[str, object]:
        window = self._ensure_window_foreground()
        window.type_keys(params.key, with_spaces=True, set_foreground=True)
        return self._passed({"key": params.key})

    @_windows_driver_tool("hoverOn", description="Move the mouse over a Windows control.")
    def hover_on(self, params: WindowsHoverOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._hover_on(params))

    def _hover_on(self, params: WindowsHoverOnParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        point = self._control_center(self._control(params))
        self._mouse_module().move(coords=point)
        return self._passed()

    @_windows_driver_tool("scrollOn", description="Scroll over a Windows control.")
    def scroll_on(self, params: WindowsScrollOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._scroll_on(params))

    def _scroll_on(self, params: WindowsScrollOnParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        point = self._control_center(self._control(params))
        self._mouse_module().scroll(coords=point, wheel_dist=params.wheel_dist)
        return self._passed()

    @_windows_driver_tool("dragTo", description="Drag between Windows controls or points.")
    def drag_to(self, params: WindowsDragToParams) -> dict[str, object]:
        return self._run_sync(lambda: self._drag_to(params))

    def _drag_to(self, params: WindowsDragToParams) -> dict[str, object]:
        self._ensure_window_foreground_if_configured()
        start = self._mouse_source_point(params)
        end = self._mouse_destination_point(params, start)
        mouse = self._mouse_module()
        pressed = False
        try:
            mouse.press(coords=start, button=params.mouse_button)
            pressed = True
            for point in self._drag_path(start, end):
                mouse.move(coords=point)
            mouse.release(coords=end, button=params.mouse_button)
            pressed = False
        finally:
            if pressed:
                # Best-effort mouse cleanup crosses pywinauto and injected fake backends with no shared error type.
                with suppress(Exception):
                    mouse.release(coords=end, button=params.mouse_button)
        return self._passed()

    @_windows_driver_tool("assertVisible", description="Assert that a Windows control is visible.")
    def assert_visible(self, params: WindowsAssertVisibleParams) -> dict[str, object]:
        return self._run_sync(lambda: self._assert_visible(params))

    def _assert_visible(self, params: WindowsAssertVisibleParams) -> dict[str, object]:
        control = self._control(params)
        if control.is_visible():
            return self._passed()
        return self._target_missing(params)

    @_windows_driver_tool("assertWithAI", description="Evaluate an explicit Windows visual assertion with AI.")
    def assert_with_ai(self, params: WindowsAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    @_windows_driver_tool("uiSnapshot", description="Return the current Windows window control tree snapshot.")
    def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
        return self._run_sync(self._ui_snapshot)

    def _ui_snapshot(self) -> dict[str, object]:
        window = self._require_window()
        state = {"count": 0, "bytes": 0, "truncated": False}
        root = self._extract_element(window, depth=0, seen=set(), state=state)
        return {
            "snapshot_type": "control_tree",
            "node_count": state["count"],
            "byte_size": state["bytes"],
            "truncated": state["truncated"],
            "root": root,
        }

    def _extract_element(self, element: Any, *, depth: int, seen: set, state: dict[str, Any]) -> dict[str, Any] | None:
        if state["count"] >= UI_SNAPSHOT_MAX_NODES or state["bytes"] >= UI_SNAPSHOT_MAX_BYTES:
            state["truncated"] = True
            return None
        runtime_id = self._element_runtime_id(element)
        if runtime_id is not None and runtime_id in seen:
            return None
        control_type = self._element_attr(element, "control_type")
        info: dict[str, Any] = {
            "title": self._safe_call(getattr(element, "window_text", None)),
            "control_type": control_type,
            "automation_id": self._element_attr(element, "automation_id"),
            "class_name": self._element_attr(element, "class_name"),
            "rectangle": self._element_rectangle(element),
        }
        value = self._safe_call(getattr(element, "get_value", None))
        if isinstance(value, str) and value:
            info["value"] = value
        if control_type == "CheckBox":
            toggle = self._safe_call(getattr(element, "get_toggle_state", None))
            if isinstance(toggle, int):
                info["is_checked"] = toggle == 1
        node_bytes = len(json.dumps(info, ensure_ascii=False, default=str))
        if state["bytes"] + node_bytes > UI_SNAPSHOT_MAX_BYTES:
            state["truncated"] = True
            return None
        if runtime_id is not None:
            seen.add(runtime_id)
        state["count"] += 1
        state["bytes"] += node_bytes
        children: list[dict[str, Any]] = []
        if depth < UI_SNAPSHOT_MAX_DEPTH:
            child_elements = self._safe_call(getattr(element, "children", None)) or []
            for child in list(child_elements)[:UI_SNAPSHOT_MAX_CHILDREN]:
                if state["count"] >= UI_SNAPSHOT_MAX_NODES or state["bytes"] >= UI_SNAPSHOT_MAX_BYTES:
                    state["truncated"] = True
                    break
                child_info = self._extract_element(child, depth=depth + 1, seen=seen, state=state)
                if child_info is not None:
                    children.append(child_info)
        info["children"] = children
        return info

    def _safe_call(self, func: Any) -> Any:
        if not callable(func):
            return None
        try:
            return func()
        # Snapshot probes call heterogeneous pywinauto wrappers whose exception hierarchy is not stable.
        except Exception:  # noqa: BLE001
            return None

    def _element_attr(self, element: Any, name: str) -> str | None:
        info = getattr(element, "element_info", None)
        value = getattr(info, name, None)
        return value if isinstance(value, str) and value else None

    def _element_rectangle(self, element: Any) -> dict[str, int] | None:
        rect = self._safe_call(getattr(element, "rectangle", None))
        if rect is None:
            return None
        try:
            return {"left": int(rect.left), "top": int(rect.top), "right": int(rect.right), "bottom": int(rect.bottom)}
        except (AttributeError, TypeError, ValueError):
            return None

    def _element_runtime_id(self, element: Any) -> tuple | None:
        info = getattr(element, "element_info", None)
        runtime_id = getattr(info, "runtime_id", None)
        if isinstance(runtime_id, (list, tuple)) and runtime_id:
            return tuple(runtime_id)
        return None

    def screenshot(self, params: object | None = None) -> bytes:
        return self._run_sync(self._screenshot)

    def _screenshot(self) -> bytes:
        from io import BytesIO

        window = self._require_window()
        hwnd = self._extract_window_handle(window)
        if hwnd is not None and self._is_win32_window(hwnd) and not self._is_unoccluded_foreground_target(hwnd, window):
            was_iconic = self._is_window_iconic(hwnd)
            if was_iconic:
                self._restore_window(hwnd, activate=False)
            try:
                image = self._capture_window_via_print_window(hwnd)
            finally:
                if was_iconic:
                    with suppress(Exception):
                        self._minimize_window_no_activate(hwnd)
        else:
            image = window.capture_as_image()
            if image is None:
                raise ConfigurationError(
                    "Windows screenshot capture returned no image. Pillow is required for pywinauto screenshots.",
                    context={"action": "Reinstall or repair fsq-agent."},
                )
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def close(self) -> None:
        try:
            self._run_sync(self._kill_app)
        finally:
            self._shutdown_executor()

    def _run_sync(self, func: Callable[[], _T]) -> _T:
        if self._executor is None:
            return func()
        return self._executor.submit(func).result()

    def _shutdown_executor(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def _application_cls(self) -> Any:
        try:
            from pywinauto import Application
        except ImportError as exc:
            raise ConfigurationError(
                "pywinauto is required for PywinautoWindowsDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        return Application

    def _list_top_windows(self, **kwargs: Any) -> list[Any]:
        try:
            from pywinauto import findwindows
        except ImportError as exc:
            raise ConfigurationError(
                "pywinauto is required for PywinautoWindowsDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        query = {"top_level_only": True, "backend": self.backend_kind, **kwargs}
        try:
            return list(findwindows.find_elements(**query))
        except Exception:  # noqa: BLE001 - empty or transient desktop enumeration returns no matches.
            return []

    def _window_handle_exists(self, handle: int) -> bool:
        return len(self._list_top_windows(handle=handle, visible_only=False)) > 0

    def _list_process_ids(self) -> list[int]:
        try:
            import win32process

            return [pid for pid in win32process.EnumProcesses() if isinstance(pid, int) and pid > 0]
        except Exception:  # noqa: BLE001 - non-Windows test environments or unavailable process enumeration fall back to window PIDs.
            return []

    def _process_executable_path(self, pid: int) -> str | None:
        import win32api
        import win32con
        import win32process

        query_flag = getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION", 0x1000)
        handle = win32api.OpenProcess(query_flag, 0, pid)
        if not handle:
            return None
        try:
            return str(win32process.GetModuleFileNameEx(handle, 0))
        finally:
            win32api.CloseHandle(handle)

    def _mouse_module(self) -> Any:
        try:
            from pywinauto import mouse
        except ImportError as exc:
            raise ConfigurationError(
                "pywinauto is required for Windows mouse actions.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        return mouse

    def _control_center(self, control: Any) -> tuple[int, int]:
        midpoint = control.rectangle().mid_point()
        if hasattr(midpoint, "x") and hasattr(midpoint, "y"):
            return int(midpoint.x), int(midpoint.y)
        x, y = midpoint
        return int(x), int(y)

    def _mouse_source_point(self, params: WindowsDragToParams) -> tuple[int, int]:
        if params.source.locator is not None:
            locator = params.source.locator.model_dump(mode="python", exclude_none=True)
            return self._control_center(self._control_from_kwargs(locator))
        point = params.source.point
        if point is None:
            raise ValueError("Windows drag source is missing.")
        return point.x, point.y

    def _mouse_destination_point(self, params: WindowsDragToParams, start: tuple[int, int]) -> tuple[int, int]:
        destination = params.destination
        if destination.locator is not None:
            locator = destination.locator.model_dump(mode="python", exclude_none=True)
            return self._control_center(self._control_from_kwargs(locator))
        if destination.point is not None:
            return destination.point.x, destination.point.y
        offset = destination.offset
        if offset is None:
            raise ValueError("Windows drag destination is missing.")
        return start[0] + offset.x, start[1] + offset.y

    def _drag_path(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
        distance = hypot(end[0] - start[0], end[1] - start[1])
        steps = max(1, ceil(distance / MOUSE_DRAG_STEP_PIXELS))
        return [
            (
                round(start[0] + (end[0] - start[0]) * index / steps),
                round(start[1] + (end[1] - start[1]) * index / steps),
            )
            for index in range(1, steps + 1)
        ]

    def _extract_window_handle(self, window: Any = None) -> int | None:
        if window is not None:
            direct = getattr(window, "handle", None)
            if isinstance(direct, int) and direct > 0:
                return direct
            info = getattr(window, "element_info", None)
            info_handle = getattr(info, "handle", None)
            if isinstance(info_handle, int) and info_handle > 0:
                return info_handle
            wrapper_fn = getattr(window, "wrapper_object", None)
            if callable(wrapper_fn):
                wrapper = self._safe_call(wrapper_fn)
                if wrapper is not None and wrapper is not window:
                    wrapped_handle = getattr(wrapper, "handle", None)
                    if isinstance(wrapped_handle, int) and wrapped_handle > 0:
                        return wrapped_handle
                    wrapped_info = getattr(getattr(wrapper, "element_info", None), "handle", None)
                    if isinstance(wrapped_info, int) and wrapped_info > 0:
                        return wrapped_info
        if isinstance(self._bound_handle, int) and self._bound_handle > 0:
            return self._bound_handle
        return None

    def _has_window_target(self) -> bool:
        if self.app_path is not None or self.window_title_re is not None or self._bound_handle is not None:
            return True
        return "_resolve_main_window" in self.__dict__ or "_require_window" in self.__dict__

    def _ensure_window_foreground_if_configured(self) -> Any:
        if not self._has_window_target():
            return None
        return self._ensure_window_foreground()

    def _ensure_window_foreground(self, window: Any = None) -> Any:
        if window is None:
            window = self._require_window()
        hwnd = self._extract_window_handle(window)
        if hwnd is not None and self._is_win32_window(hwnd):
            if self._is_window_iconic(hwnd):
                self._restore_window(hwnd)
        else:
            is_minimized = getattr(window, "is_minimized", None)
            restore = getattr(window, "restore", None)
            minimized = bool(is_minimized()) if callable(is_minimized) else bool(is_minimized)
            if minimized and callable(restore):
                restore()
        set_focus = getattr(window, "set_focus", None)
        if callable(set_focus):
            with suppress(Exception):
                set_focus()
        return window

    def _is_win32_window(self, hwnd: int) -> bool:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            is_window = user32.IsWindow
            is_window.argtypes = [wintypes.HWND]
            is_window.restype = wintypes.BOOL
            return bool(is_window(hwnd))
        except Exception:  # noqa: BLE001
            return False

    def _is_win32_window_visible(self, hwnd: int) -> bool:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        is_visible = user32.IsWindowVisible
        is_visible.argtypes = [wintypes.HWND]
        is_visible.restype = wintypes.BOOL
        return bool(is_visible(hwnd))

    def _is_window_iconic(self, hwnd: int) -> bool:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        is_iconic = user32.IsIconic
        is_iconic.argtypes = [wintypes.HWND]
        is_iconic.restype = wintypes.BOOL
        return bool(is_iconic(hwnd))

    def _restore_window(self, hwnd: int, *, activate: bool = True) -> None:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        show_window = user32.ShowWindow
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        show_window(hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE)

    def _minimize_window_no_activate(self, hwnd: int) -> None:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        show_window = user32.ShowWindow
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        show_window(hwnd, SW_SHOWMINNOACTIVE)

    def _foreground_window_handle(self) -> int:
        if os.name != "nt":
            return 0
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get_fg = user32.GetForegroundWindow
        get_fg.argtypes = []
        get_fg.restype = wintypes.HWND
        fg = get_fg()
        return int(fg) if fg else 0

    def _window_process_id(self, hwnd: int) -> int | None:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return None
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get_pid = user32.GetWindowThreadProcessId
        get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_pid.restype = wintypes.DWORD
        pid = wintypes.DWORD(0)
        thread_id = get_pid(hwnd, ctypes.byref(pid))
        if not thread_id or pid.value <= 0:
            return None
        return int(pid.value)

    def _win32_window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return None
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get_rect = user32.GetWindowRect
        get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        get_rect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not get_rect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _is_window_cloaked(self, hwnd: int) -> bool:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            dwmapi = ctypes.windll.dwmapi
            get_attr = dwmapi.DwmGetWindowAttribute
            get_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            get_attr.restype = ctypes.c_long
            cloaked = wintypes.DWORD(0)
            hr = get_attr(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        except Exception:  # noqa: BLE001
            return False
        return hr == 0 and cloaked.value != 0

    def _iter_visible_windows_above(self, hwnd: int) -> list[tuple[int, tuple[int, int, int, int]]]:
        if os.name != "nt" or not isinstance(hwnd, int) or hwnd <= 0:
            return []
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get_window = user32.GetWindow
        get_window.argtypes = [wintypes.HWND, wintypes.UINT]
        get_window.restype = wintypes.HWND
        is_visible = user32.IsWindowVisible
        is_visible.argtypes = [wintypes.HWND]
        is_visible.restype = wintypes.BOOL

        above: list[tuple[int, tuple[int, int, int, int]]] = []
        seen: set[int] = {hwnd}
        curr = get_window(hwnd, GW_HWNDPREV)
        while curr:
            above_hwnd = int(curr)
            if above_hwnd in seen or len(seen) >= 2048:
                break
            seen.add(above_hwnd)
            if is_visible(above_hwnd) and not self._is_window_iconic(above_hwnd) and not self._is_window_cloaked(above_hwnd):
                rect = self._win32_window_rect(above_hwnd)
                if rect is not None and rect[2] > rect[0] and rect[3] > rect[1]:
                    above.append((above_hwnd, rect))
            curr = get_window(above_hwnd, GW_HWNDPREV)
        return above

    def _is_unoccluded_foreground_target(self, hwnd: int, window: Any = None) -> bool:
        if not isinstance(hwnd, int) or hwnd <= 0:
            return True
        if self._is_window_iconic(hwnd):
            return False
        fg_hwnd = self._foreground_window_handle()
        if fg_hwnd <= 0:
            return False
        target_pid = self._window_process_id(hwnd)
        if target_pid is None:
            window_pid = getattr(window, "process_id", None) if window is not None else None
            target_pid = window_pid if isinstance(window_pid, int) and window_pid > 0 else self._launched_pid
        fg_pid = self._window_process_id(fg_hwnd)
        if fg_hwnd != hwnd and (target_pid is None or fg_pid != target_pid):
            return False
        target_rect = self._win32_window_rect(hwnd)
        if target_rect is None:
            return False
        left, top, right, bottom = target_rect
        if right <= left or bottom <= top:
            return False
        for above_hwnd, (l2, t2, r2, b2) in self._iter_visible_windows_above(hwnd):
            if left < r2 and right > l2 and top < b2 and bottom > t2:
                above_pid = self._window_process_id(above_hwnd)
                if target_pid is None or above_pid != target_pid:
                    return False
        return True

    def _capture_window_via_print_window(self, hwnd: int) -> Any:
        if os.name != "nt":
            raise RuntimeError(f"Win32 PrintWindow capture is only supported on Windows (hwnd={hwnd}).")
        import ctypes
        from ctypes import wintypes

        try:
            from PIL import Image
        except ImportError as exc:
            raise ConfigurationError(
                "Pillow is required for Windows PrintWindow screenshots.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc

        class _BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        rect = self._win32_window_rect(hwnd)
        if rect is None:
            raise RuntimeError(f"Failed to read window bounds for Win32 PrintWindow capture (hwnd={hwnd}).")
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Window has non-positive dimensions ({width}x{height}) for Win32 PrintWindow capture (hwnd={hwnd}).")

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        get_window_dc = user32.GetWindowDC
        get_window_dc.argtypes = [wintypes.HWND]
        get_window_dc.restype = wintypes.HDC

        release_dc = user32.ReleaseDC
        release_dc.argtypes = [wintypes.HWND, wintypes.HDC]
        release_dc.restype = ctypes.c_int

        print_window = user32.PrintWindow
        print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        print_window.restype = wintypes.BOOL

        create_compatible_dc = gdi32.CreateCompatibleDC
        create_compatible_dc.argtypes = [wintypes.HDC]
        create_compatible_dc.restype = wintypes.HDC

        delete_dc = gdi32.DeleteDC
        delete_dc.argtypes = [wintypes.HDC]
        delete_dc.restype = wintypes.BOOL

        create_compatible_bitmap = gdi32.CreateCompatibleBitmap
        create_compatible_bitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        create_compatible_bitmap.restype = wintypes.HBITMAP

        select_object = gdi32.SelectObject
        select_object.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        select_object.restype = wintypes.HGDIOBJ

        delete_object = gdi32.DeleteObject
        delete_object.argtypes = [wintypes.HGDIOBJ]
        delete_object.restype = wintypes.BOOL

        get_di_bits = gdi32.GetDIBits
        get_di_bits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        get_di_bits.restype = ctypes.c_int

        wnd_dc = None
        mem_dc = None
        bitmap = None
        prev_obj = None
        try:
            wnd_dc = get_window_dc(hwnd)
            if not wnd_dc:
                raise RuntimeError(f"GetWindowDC failed for Win32 PrintWindow capture (hwnd={hwnd}).")
            mem_dc = create_compatible_dc(wnd_dc)
            if not mem_dc:
                raise RuntimeError(f"CreateCompatibleDC failed for Win32 PrintWindow capture (hwnd={hwnd}).")
            bitmap = create_compatible_bitmap(wnd_dc, width, height)
            if not bitmap:
                raise RuntimeError(f"CreateCompatibleBitmap failed for Win32 PrintWindow capture (hwnd={hwnd}).")
            prev_obj = select_object(mem_dc, bitmap)
            if not prev_obj:
                raise RuntimeError(f"SelectObject failed for Win32 PrintWindow capture (hwnd={hwnd}).")
            if not print_window(hwnd, mem_dc, PW_RENDERFULLCONTENT):
                raise RuntimeError(f"Win32 PrintWindow(PW_RENDERFULLCONTENT) failed for occluded/background window (hwnd={hwnd}).")

            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = BI_RGB
            buf_size = width * height * 4
            raw_buf = ctypes.create_string_buffer(buf_size)
            scanlines = get_di_bits(mem_dc, bitmap, 0, height, raw_buf, ctypes.byref(bmi), DIB_RGB_COLORS)
            if scanlines != height:
                raise RuntimeError(f"GetDIBits returned {scanlines}/{height} scanlines for Win32 PrintWindow capture (hwnd={hwnd}).")
            raw_bytes = raw_buf.raw
        finally:
            if mem_dc and prev_obj:
                with suppress(Exception):
                    select_object(mem_dc, prev_obj)
            if bitmap:
                with suppress(Exception):
                    delete_object(bitmap)
            if mem_dc:
                with suppress(Exception):
                    delete_dc(mem_dc)
            if wnd_dc:
                with suppress(Exception):
                    release_dc(hwnd, wnd_dc)

        return Image.frombuffer("RGBA", (width, height), raw_bytes, "raw", "BGRA", 0, 1).convert("RGB")

    def _require_window(self) -> Any:
        return self._resolve_main_window()

    def _control(self, params: BaseModel) -> Any:
        data = params.model_dump(mode="python", exclude_none=True)
        return self._control_from_kwargs(data["locator"])

    def _control_from_kwargs(self, locator: dict[str, Any]) -> Any:
        kwargs: dict[str, Any] = {}
        title = locator.get("title")
        if isinstance(title, str):
            kwargs["title"] = title
        if isinstance(locator.get("control_type"), str):
            kwargs["control_type"] = locator["control_type"]
        if isinstance(locator.get("automation_id"), str):
            kwargs["auto_id"] = locator["automation_id"]
        if isinstance(locator.get("class_name"), str):
            kwargs["class_name"] = locator["class_name"]
        index = locator.get("index")
        kwargs["found_index"] = (index - 1) if isinstance(index, int) and index >= 1 else 0
        if not kwargs:
            raise LookupError("Windows control query is empty. query_dict={}")
        window = self._require_window()
        control = self._child_window(window=window, **kwargs)
        if control is not None and control.exists():
            return control.wrapper_object()
        if not isinstance(title, str):
            raise self._control_not_found(kwargs)
        regex_kwargs = {key: value for key, value in kwargs.items() if key != "title"}
        regex_kwargs["title_re"] = title
        control = self._child_window(window=window, **regex_kwargs)
        if control is not None and control.exists():
            return control.wrapper_object()
        raise self._control_not_found(regex_kwargs)

    def _control_not_found(self, query: dict[str, Any]) -> LookupError:
        query_dict = json.dumps(query, ensure_ascii=False, sort_keys=True)
        return LookupError(f"Windows control was not found. query_dict={query_dict}")

    def _child_window(self, *, window: Any = None, **kwargs: Any) -> Any:
        if window is None:
            window = self._require_window()
        return window.child_window(**kwargs)

    def _window_size(self) -> tuple[int, int] | None:
        try:
            window = self._require_window()
            rectangle = getattr(window, "rectangle", None)
            if not callable(rectangle):
                return None
            rect = rectangle()
            width = getattr(rect, "width", None)
            height = getattr(rect, "height", None)
            if callable(width) and callable(height):
                return int(width()), int(height())
        except Exception:  # noqa: BLE001 - context must tolerate a missing or closed window.
            return None
        return None

    def _target_missing(self, params: BaseModel) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target was not found.",
            metadata={"params": params.model_dump(mode="json", exclude_none=True)},
        )

    def _passed(self, output: object | None = None) -> dict[str, object]:
        return {"status": "passed", "output": output}

    def _failed(
        self,
        failure_category: str,
        error_message: str,
        *,
        output: object | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return {
            "status": "failed",
            "failure_category": failure_category,
            "error_message": error_message,
            "output": output,
            "metadata": metadata or {},
        }
