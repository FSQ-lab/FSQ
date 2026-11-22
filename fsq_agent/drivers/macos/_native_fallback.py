# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ctypes
import json
import re
import subprocess
import sys
import time
from asyncio import CancelledError
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4
from xml.etree import ElementTree


def _macos_ax_process_trusted() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        framework = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        probe = framework.AXIsProcessTrusted
    except (OSError, AttributeError):
        return False
    probe.argtypes = []
    probe.restype = ctypes.c_bool
    return bool(probe())


# Only controls with known non-secret value semantics may trigger AXValue IPC.
_READABLE_VALUE_TYPES = frozenset({("AXTextField", ""), ("AXTextArea", ""), ("AXStaticText", ""), ("AXTextField", "AXSearchField")})
_READABLE_VALUE_ROLES = frozenset(role for role, _ in _READABLE_VALUE_TYPES)
_OPTIONAL_METADATA = frozenset({"AXSubrole", "AXTitle", "AXDescription", "AXIdentifier", "AXPlaceholderValue", "AXEnabled", "AXFocused", "AXSelected", "AXPosition", "AXSize"})


def _read_ax_identity_and_value(read: Callable[[str], str | None]) -> tuple[str | None, str | None, str | None]:
    role = read("AXRole")
    if role not in _READABLE_VALUE_ROLES:
        return role, None, None
    subrole = read("AXSubrole")
    if (role, subrole) not in _READABLE_VALUE_TYPES:
        return role, subrole, None
    return role, subrole, read("AXValue")


_FINDER_BUNDLE = "com.apple.finder"
_PERMISSION_UI_BUNDLES = ("com.apple.UserNotificationCenter", "com.apple.SecurityAgent")
_AX_MISSING = {-25205, -25212}


class _NativeForegroundError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _remaining(deadline: float, cancellation: Event | None) -> float:
    if cancellation is not None and cancellation.is_set():
        raise CancelledError
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("macOS native operation timed out.")
    return seconds


def _native_command(command: list[str], *, deadline: float, cancellation: Event | None) -> str:
    return subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        timeout=min(1.0, _remaining(deadline, cancellation)),
        check=True,
    ).stdout.strip()


def _foreground_identity(*, deadline: float, cancellation: Event | None, include_path: bool = False) -> tuple[str, int, Path | None]:
    def read(*args: str) -> str:
        return _native_command(["/usr/bin/lsappinfo", *args], deadline=deadline, cancellation=cancellation)

    try:
        front = read("front")
        if front.startswith("ASN:"):
            keys = ("-only", "bundlepath") if include_path else ()
            info = read("info", "-only", "bundleid", "-only", "pid", *keys, front)
            pid = re.search(r'^"pid"\s*=\s*([1-9]\d*)\s*$', info, re.MULTILINE)
            bundle = re.search(r'^"CFBundleIdentifier"\s*=\s*"([^"\n]+)"\s*$', info, re.MULTILINE)
            if pid is not None and bundle is not None and read("front") == front:
                path = re.search(r'^"LSBundlePath"\s*=\s*"([^"\n]+)"\s*$', info, re.MULTILINE)
                return bundle.group(1), int(pid.group(1)), Path(path.group(1)).resolve() if path else None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise _NativeForegroundError("foreground_unobservable", "The macOS foreground application could not be identified.") from exc
    raise _NativeForegroundError("foreground_unobservable", "The macOS foreground application could not be identified.")


def _running_macos_app_path(bundle_id: str, *, cancellation: Event | None) -> Path:
    deadline = time.monotonic() + 2.5

    def read(*args: str) -> str:
        return _native_command(["/usr/bin/lsappinfo", *args], deadline=deadline, cancellation=cancellation)

    try:
        asn = read("find", f"bundleid={bundle_id}")
        if asn.startswith("ASN:") and "\n" not in asn:
            info = read("info", "-only", "bundleid", "-only", "pid", "-only", "bundlepath", asn)
            bundle = re.search(r'^"CFBundleIdentifier"\s*=\s*"([^"\n]+)"\s*$', info, re.MULTILINE)
            pid = re.search(r'^"pid"\s*=\s*[1-9]\d*\s*$', info, re.MULTILINE)
            path = re.search(r'^"LSBundlePath"\s*=\s*"(/[^"\n]+)"\s*$', info, re.MULTILINE)
            if bundle is not None and bundle.group(1) == bundle_id and pid is not None and path is not None and read("find", f"bundleid={bundle_id}") == asn:
                return Path(path.group(1)).resolve(strict=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise _NativeForegroundError("foreground_unobservable", "The running macOS application installation could not be verified.") from exc
    raise _NativeForegroundError("foreground_unobservable", "The running macOS application installation could not be verified.")


def _activate_macos_app_native(
    bundle_id: str,
    *,
    app_path: Path | None = None,
    timeout_seconds: float,
    cancellation: Event | None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    command = ["/usr/bin/open", "-a", str(app_path)] if app_path is not None else ["/usr/bin/open", "-b", bundle_id]
    _native_command(command, deadline=deadline, cancellation=cancellation)
    previous_pid = None
    retried_wrong_installation = False
    failure = _NativeForegroundError("foreground_unobservable", "The macOS foreground application could not be identified.")
    while time.monotonic() < deadline:
        try:
            identity, pid, actual_path = _foreground_identity(deadline=deadline, cancellation=cancellation, include_path=app_path is not None)
        except _NativeForegroundError as exc:
            previous_pid = None
            failure = exc
        else:
            if (identity in _PERMISSION_UI_BUNDLES or previous_pid == pid) and _permission_modal_for_target(
                bundle_id,
                app_path,
                deadline=deadline,
                cancellation=cancellation,
                foreground=(identity, pid),
            ):
                raise _NativeForegroundError("permission_modal", "A macOS system permission dialog for the target requires user attention.")
            if identity == bundle_id and (app_path is None or actual_path == app_path):
                if previous_pid == pid:
                    current = _foreground_identity(deadline=deadline, cancellation=cancellation, include_path=app_path is not None)
                    if current == (identity, pid, actual_path):
                        return
                    previous_pid = None
                    continue
                previous_pid = pid
                failure = _NativeForegroundError("foreground_unobservable", "The macOS foreground application did not stabilize.")
            else:
                previous_pid = None
                if identity == bundle_id and app_path is not None and actual_path is None:
                    failure = _NativeForegroundError("foreground_unobservable", "The macOS foreground application installation could not be identified.")
                else:
                    failure = _NativeForegroundError("target_not_foreground", "The requested macOS application installation is not foreground.")
                    if identity == bundle_id and app_path is not None and not retried_wrong_installation:
                        # Launch Services can otherwise reuse a different running copy with the same identifier.
                        _native_command(["/usr/bin/open", "-n", "-a", str(app_path)], deadline=deadline, cancellation=cancellation)
                        retried_wrong_installation = True
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    raise failure


class _AXPair(ctypes.Structure):
    _fields_ = [("first", ctypes.c_double), ("second", ctypes.c_double)]


class _AXReadError(Exception):
    def __init__(self, status: int | None = None):
        super().__init__(status)
        self.status = status


class _AX:
    def __init__(self) -> None:
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr = ctypes.c_void_p
        integer = ctypes.c_int
        self.application = self._bind(ax, "AXUIElementCreateApplication", ptr, integer)
        self.system = self._bind(ax, "AXUIElementCreateSystemWide", ptr)
        self.timeout = self._bind(ax, "AXUIElementSetMessagingTimeout", integer, ptr, ctypes.c_float)
        self.copy = self._bind(ax, "AXUIElementCopyAttributeValue", integer, ptr, ptr, ctypes.POINTER(ptr))
        self.hit = self._bind(ax, "AXUIElementCopyElementAtPosition", integer, ptr, ctypes.c_float, ctypes.c_float, ctypes.POINTER(ptr))
        self.pid = self._bind(ax, "AXUIElementGetPid", integer, ptr, ctypes.POINTER(integer))
        self.pair = self._bind(ax, "AXValueGetValue", ctypes.c_bool, ptr, integer, ctypes.POINTER(_AXPair))
        self.string = self._bind(cf, "CFStringCreateWithCString", ptr, ptr, ctypes.c_char_p, ctypes.c_uint32)
        self.string_type = self._bind(cf, "CFStringGetTypeID", ctypes.c_ulong)()
        self.array_type = self._bind(cf, "CFArrayGetTypeID", ctypes.c_ulong)()
        self.type = self._bind(cf, "CFGetTypeID", ctypes.c_ulong, ptr)
        self.decode = self._bind(cf, "CFStringGetCString", ctypes.c_bool, ptr, ptr, ctypes.c_long, ctypes.c_uint32)
        self.count = self._bind(cf, "CFArrayGetCount", ctypes.c_long, ptr)
        self.item = self._bind(cf, "CFArrayGetValueAtIndex", ptr, ptr, ctypes.c_long)
        self.release = self._bind(cf, "CFRelease", None, ptr)
        self.retain = self._bind(cf, "CFRetain", ptr, ptr)
        self.equal = self._bind(cf, "CFEqual", ctypes.c_bool, ptr, ptr)
        self.true = ptr.in_dll(cf, "kCFBooleanTrue")

    @staticmethod
    def _bind(library, name, result, *args):
        function = getattr(library, name)
        function.restype = result
        function.argtypes = args
        return function

    def attribute(self, element: int, name: str) -> tuple[int, int | None]:
        key = self.string(None, name.encode(), 0x08000100)
        if not key:
            raise MemoryError
        try:
            value = ctypes.c_void_p()
            status = self.copy(element, key, ctypes.byref(value))
            return status, value.value
        finally:
            self.release(key)

    def decode_text(self, value: int) -> str | None:
        if self.type(value) != self.string_type:
            return None
        data = ctypes.create_string_buffer(65_536)
        return data.value.decode("utf-8") if self.decode(value, data, len(data), 0x08000100) else None

    def decode_bounds(self, read: Callable[[str], tuple[int, int | None]]) -> tuple[int, int, int, int] | None:
        geometry = []
        for attribute, kind in (("AXPosition", 1), ("AXSize", 2)):
            _, value = read(attribute)
            if not value:
                return None
            try:
                pair = _AXPair()
                if not self.pair(value, kind, ctypes.byref(pair)):
                    return None
                geometry.extend((round(pair.first), round(pair.second)))
            finally:
                self.release(value)
        return tuple(geometry)


class _AXHandle:
    def __init__(self, backend: _AX, element: int, pid: int) -> None:
        self.backend = backend
        self.element = backend.retain(element)
        self.pid = pid

    def __del__(self) -> None:
        self.backend.release(self.element)


def _frontmost_pid(bundle_id: str, *, app_path: Path | None = None, deadline: float | None = None, cancellation: Event | None = None) -> int:
    deadline = deadline if deadline is not None else time.monotonic() + 2.5
    observed_bundle, pid, observed_path = _foreground_identity(deadline=deadline, cancellation=cancellation, include_path=app_path is not None)
    if observed_bundle == bundle_id and app_path is not None and observed_path is None:
        raise _NativeForegroundError("foreground_unobservable", "The macOS foreground application installation could not be identified.")
    if observed_bundle != bundle_id or (app_path is not None and observed_path != app_path):
        raise _NativeForegroundError("target_not_foreground", "The requested macOS application installation is not foreground.")
    return pid


class _ScopedAXRead:
    """Own copied references and cap every AX request by one deadline."""

    def __init__(self, ax: _AX, stack: ExitStack, deadline: float, cancellation: Event | None) -> None:
        self.ax = ax
        self.stack = stack
        self.deadline = deadline
        self.cancellation = cancellation

    def keep(self, ref: int | None) -> int | None:
        if ref:
            self.stack.callback(self.ax.release, ref)
        return ref

    def prepare(self, ref: int) -> None:
        status = self.ax.timeout(ref, min(0.25, _remaining(self.deadline, self.cancellation)))
        if status:
            raise _AXReadError(status)
        _remaining(self.deadline, self.cancellation)

    def reference(self, ref: int, name: str) -> int | None:
        self.prepare(ref)
        status, result = self.ax.attribute(ref, name)
        self.keep(result)
        if status and status not in _AX_MISSING:
            raise _AXReadError(status)
        return result if not status else None

    def text(self, ref: int, name: str) -> str | None:
        value = self.reference(ref, name)
        return self.ax.decode_text(value) if value else None

    def children(self, ref: int, name: str = "AXChildren") -> tuple[int, ...]:
        value = self.reference(ref, name)
        if value is None:
            return ()
        if self.ax.type(value) != self.ax.array_type or not 0 <= (size := self.ax.count(value)) <= 64:
            raise _AXReadError
        result = tuple(self.ax.item(value, index) for index in range(size))
        if not all(result):
            raise _AXReadError
        return result

    def bounds(self, ref: int) -> tuple[int, int, int, int] | None:
        def copy(name: str) -> tuple[int, int | None]:
            # decode_bounds owns each copied value itself.
            self.prepare(ref)
            status, value = self.ax.attribute(ref, name)
            if status:
                self.keep(value)
                return status, None
            return status, value

        return self.ax.decode_bounds(copy)

    def hit(self, ref: int, x: int, y: int) -> int:
        self.prepare(ref)
        result = ctypes.c_void_p()
        status = self.ax.hit(ref, x, y, ctypes.byref(result))
        hit = self.keep(result.value)
        if not hit or status:
            raise _AXReadError(status)
        return hit

    def pid(self, ref: int) -> int:
        result = ctypes.c_int()
        if self.ax.pid(ref, ctypes.byref(result)) or result.value <= 0:
            raise _AXReadError
        return result.value


def _permission_ui_pid(bundle_id: str, *, deadline: float, cancellation: Event | None) -> int | None:
    try:
        asn = _native_command(["/usr/bin/lsappinfo", "find", f"bundleid={bundle_id}"], deadline=deadline, cancellation=cancellation)
        if not asn.startswith("ASN:"):
            return None
        info = _native_command(["/usr/bin/lsappinfo", "info", "-only", "pid", asn], deadline=deadline, cancellation=cancellation)
    except (OSError, subprocess.SubprocessError):
        return None
    pid = re.fullmatch(r'"pid"\s*=\s*([1-9]\d*)', info)
    return int(pid.group(1)) if pid else None


def _permission_modal_for_target(
    bundle_id: str,
    app_path: Path | None,
    *,
    deadline: float,
    cancellation: Event | None,
    foreground: tuple[str, int],
) -> bool:
    name = (app_path.stem if app_path is not None else bundle_id.rsplit(".", 1)[-1]).casefold()
    with ExitStack() as stack:
        ax = _AX()
        read = _ScopedAXRead(ax, stack, deadline, cancellation)
        for owner in _PERMISSION_UI_BUNDLES:
            try:
                pid = foreground[1] if owner == foreground[0] else _permission_ui_pid(owner, deadline=deadline, cancellation=cancellation)
                if pid is None or not (app := read.keep(ax.application(pid))):
                    continue
                for window in read.children(app, "AXWindows"):
                    if read.pid(window) != pid:
                        continue
                    pending = [window]
                    target_text = False
                    permission_buttons: set[str] = set()
                    for _ in range(32):
                        if not pending:
                            break
                        node = pending.pop()

                        def read_identity(name: str, ref: int = node) -> str | None:
                            return read.text(ref, name) or ("" if name == "AXSubrole" else None)

                        role, _, safe_value = _read_ax_identity_and_value(read_identity)
                        if role == "AXButton":
                            permission_buttons.add((read.text(node, "AXTitle") or "").casefold())
                        elif role in {"AXStaticText", "AXWindow", "AXSheet"}:
                            texts = (read.text(node, "AXTitle"), read.text(node, "AXDescription"), safe_value)
                            for text in texts:
                                normalized = (text or "").casefold()
                                target_text |= any(f"{left}{name}{right}" in normalized for left, right in (('"', '"'), ("“", "”"), ("«", "»")))
                        if target_text and permission_buttons & {"allow", "ok"} and permission_buttons & {"don\u2019t allow", "don't allow"}:
                            return True
                        pending.extend(reversed(read.children(node)))
            except _AXReadError:
                continue
    return False


def _verify_finder_search_click(
    title: str,
    bounds: tuple[int, int, int, int],
    x: int,
    y: int,
    *,
    deadline: float,
    cancellation: Event | None,
) -> bool:
    """Return True for a proved suggestion and False for a proved ordinary Finder menu item."""
    if not _macos_ax_process_trusted():
        raise PermissionError("macOS Accessibility trust is required for Finder menu proof.")
    pid = _frontmost_pid(_FINDER_BUNDLE, deadline=deadline, cancellation=cancellation)
    ax = _AX()
    with ExitStack() as stack:
        read = _ScopedAXRead(ax, stack, deadline, cancellation)
        app, system = read.keep(ax.application(pid)), read.keep(ax.system())
        if app is None or system is None:
            raise _AXReadError

        def validate_hit(hit: int) -> None:
            enabled = read.reference(hit, "AXEnabled")
            if (
                read.pid(hit) != pid
                or read.text(hit, "AXRole") != "AXMenuItem"
                or read.text(hit, "AXTitle") != title
                or read.bounds(hit) != bounds
                or enabled is None
                or not ax.equal(enabled, ax.true)
            ):
                raise _AXReadError

        app_hit = read.hit(app, x, y)
        if read.pid(app_hit) != pid:
            raise _AXReadError
        menu = read.reference(app_hit, "AXParent")
        if menu is None or read.pid(menu) != pid or read.text(menu, "AXRole") != "AXMenu":
            raise _AXReadError
        search = window = None
        search_popup = False
        current = menu
        for _ in range(24):
            if ax.equal(current, app):
                break
            if read.pid(current) != pid:
                raise _AXReadError
            role = read.text(current, "AXRole")
            if role is None:
                raise _AXReadError
            if role == "SGTMenu":
                search_popup = True
            elif role == "AXTextField" and read.text(current, "AXSubrole") == "AXSearchField":
                search = current
            elif role == "AXWindow":
                window = current
            parent = read.reference(current, "AXParent")
            if parent is None:
                raise _AXReadError
            current = parent
        else:
            raise _AXReadError
        is_search = search is not None or search_popup
        if is_search:
            if search is None or window is None:
                raise _AXReadError
            matches: list[int] = []
            seen_menus: list[int] = []
            pending = list(read.children(search))
            for _ in range(64):
                if not pending:
                    break
                node = pending.pop()
                role = read.text(node, "AXRole")
                if role is None or read.pid(node) != pid:
                    raise _AXReadError
                if role == "AXMenu":
                    if any(ax.equal(node, seen) for seen in seen_menus):
                        continue
                    seen_menus.append(node)
                    for child in read.children(node):
                        child_role = read.text(child, "AXRole")
                        if child_role is None:
                            raise _AXReadError
                        if child_role == "AXMenuItem" and read.text(child, "AXTitle") == title and not any(ax.equal(child, seen) for seen in matches):
                            matches.append(child)
                elif role != "AXMenuItem":
                    pending.extend(read.children(node))
            if pending or len(matches) != 1 or not any(ax.equal(menu, seen) for seen in seen_menus) or not ax.equal(matches[0], app_hit):
                raise _AXReadError
            focused_field = read.reference(app, "AXFocusedUIElement")
            focused_window = read.reference(app, "AXFocusedWindow")
            if focused_field is None or focused_window is None or not ax.equal(search, focused_field) or not ax.equal(window, focused_window):
                raise _AXReadError
        for hit in (read.hit(system, x, y), read.hit(app, x, y)):
            if not ax.equal(hit, app_hit):
                raise _AXReadError
            validate_hit(hit)
        if _frontmost_pid(_FINDER_BUNDLE, deadline=deadline, cancellation=cancellation) != pid:
            raise _AXReadError
        _remaining(deadline, cancellation)
        return is_search


def _verify_ax_focus(handle: _AXHandle | None) -> bool:
    if handle is None:
        return False
    ax = handle.backend
    try:
        with ExitStack() as stack:
            read = _ScopedAXRead(ax, stack, time.monotonic() + 2.5, None)
            system = read.keep(ax.system())
            focused = read.reference(system, "AXFocusedUIElement") if system is not None else None
            return focused is not None and read.pid(focused) == handle.pid and ax.equal(focused, handle.element)
    except (_AXReadError, TimeoutError):
        return False


def _verify_ax_point_hit(handle: _AXHandle | None, x: int, y: int) -> bool:
    if handle is None:
        return False
    ax = handle.backend
    try:
        with ExitStack() as stack:
            read = _ScopedAXRead(ax, stack, time.monotonic() + 2.5, None)
            system = read.keep(ax.system())
            if system is None:
                return False
            current: int | None = read.hit(system, x, y)
            for _ in range(32):
                if current is None or read.pid(current) != handle.pid:
                    return False
                if ax.equal(current, handle.element):
                    return True
                current = read.reference(current, "AXParent")
    except (_AXReadError, TimeoutError):
        return False
    return False


_AX_TO_XCUI = {
    "AXApplication": "Application",
    "AXWindow": "Window",
    "AXSheet": "Sheet",
    "AXButton": "Button",
    "AXTextField": "TextField",
    "AXTextArea": "TextArea",
    "AXStaticText": "StaticText",
    "AXGroup": "Group",
    "AXScrollArea": "ScrollView",
    "AXMenuBar": "MenuBar",
    "AXMenuBarItem": "MenuBarItem",
    "AXMenu": "Menu",
    "AXMenuItem": "MenuItem",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXPopUpButton": "PopUpButton",
    "AXTabGroup": "TabGroup",
    "AXTable": "Table",
    "AXRow": "Row",
    "AXCell": "Cell",
    "AXOutline": "Outline",
    "AXToolbar": "Toolbar",
    "AXWebArea": "WebArea",
}


def _observe_macos_ax_tree(*, bundle_id: str, max_nodes: int, max_depth: int, control=None, app_path: Path | None = None):
    from appium_connect_agent.automation import Bounds, ObservationScope, ObservedValue, SourceMetadata, TreeNode, TreeObservation, Viewport

    @dataclass(frozen=True, slots=True)
    class NativeNode(TreeNode):
        extra_attributes: dict[str, str] | None = None
        handle: _AXHandle | None = None
        unknown_metadata: frozenset[str] = frozenset()

    @dataclass(frozen=True, slots=True)
    class NativeObservation(TreeObservation):
        raw_xml: bytes = b""
        unknown_metadata: frozenset[str] = frozenset()

    unknown = ObservedValue("unknown")
    na = ObservedValue("not_applicable")

    def project(node: NativeNode):
        xml = ElementTree.Element(node.type.value or "XCUIElementTypeAny", node.extra_attributes or {})
        payload = {}
        for field in fields(TreeNode):
            name = field.name
            data = getattr(node, name)
            if name == "children":
                payload[name] = []
                for child in data:
                    child_data, child_xml = project(child)
                    payload[name].append(child_data)
                    xml.append(child_xml)
            elif isinstance(data, ObservedValue):
                payload[name] = asdict(data)
                if data.state == "value" and name != "type":
                    if isinstance(data.value, Bounds):
                        xml.attrib.update({key: str(value) for key, value in asdict(data.value).items()})
                    elif name == "text":
                        xml.text = str(data.value)
                    else:
                        xml.set({"id": "identifier", "placeholder_value": "placeholderValue"}.get(name, name), str(data.value))
            else:
                payload[name] = data
        return payload, xml

    if not _macos_ax_process_trusted():
        raise PermissionError("macOS Accessibility trust is required.")
    deadline = time.monotonic() + (control.timeout_seconds if control is not None else 10)
    cancellation = control.cancellation if control is not None else None
    pid = _frontmost_pid(bundle_id, app_path=app_path, deadline=deadline, cancellation=cancellation)
    ax = _AX()
    app = ax.application(pid)
    if not app:
        raise RuntimeError("Cannot bind foreground accessibility application.")
    count = 0
    unknown_metadata: set[str] = set()
    complete = True
    finder = bundle_id == _FINDER_BUNDLE
    projected_suggestion_menus: list[_AXHandle] = []

    def observed(value):
        return ObservedValue("missing") if value is None else ObservedValue("value", value)

    def visit(element: int, depth: int, ancestors: tuple[int, ...], *, parent_role: str | None = None, search_scope=False, menu_roots_only=False):
        nonlocal count, complete
        if control is not None and control.cancellation is not None and control.cancellation.is_set():
            raise CancelledError
        if depth > max_depth or count >= max_nodes or time.monotonic() >= deadline:
            complete = False
            return None
        if any(ax.equal(element, parent) for parent in ancestors):
            complete = False
            return None
        count += 1
        messaging_timeout = None
        failed_metadata: set[str] = set()

        def read(name):
            nonlocal messaging_timeout, complete
            if control is not None and control.cancellation is not None and control.cancellation.is_set():
                raise CancelledError
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _AXReadError
            seconds = min(0.25, remaining)
            if seconds != messaging_timeout:
                status = ax.timeout(element, seconds)
                if status != 0:
                    raise _AXReadError(status)
                messaging_timeout = seconds
                if time.monotonic() >= deadline:
                    raise _AXReadError
            status, value = ax.attribute(element, name)
            if status == 0:
                return status, value
            if value is not None:
                ax.release(value)
            if status in {-25204, -25202, -25211}:
                raise _AXReadError(status)
            if status == -25200 and name in _OPTIONAL_METADATA:
                unknown_metadata.add(name)
                failed_metadata.add(name)
                return status, None
            if status not in {-25205, -25212}:
                complete = False
            return status, None

        def text(name):
            status, value = read(name)
            if value is None:
                return "" if name == "AXSubrole" and status in {-25205, -25212} else None
            try:
                return ax.decode_text(value)
            finally:
                ax.release(value)

        def boolean(name):
            _, value = read(name)
            if value is None:
                return None
            try:
                return bool(ax.equal(value, ax.true))
            finally:
                ax.release(value)

        children = []

        def collect(name, *, exclude=None, menu_roots_only=False):
            nonlocal complete
            status, collection = read(name)
            if collection is None:
                return status not in {0, -25205, -25212}
            try:
                if ax.type(collection) != ax.array_type:
                    complete = False
                    return True
                size = ax.count(collection)
                for index in range(size):
                    if depth >= max_depth or count >= max_nodes or time.monotonic() >= deadline:
                        complete = False
                        break
                    child = ax.item(collection, index)
                    if not child:
                        complete = False
                    elif exclude is None or not ax.equal(child, exclude):
                        result = visit(child, depth + 1, (*ancestors, element), parent_role=role, search_scope=in_search_scope, menu_roots_only=menu_roots_only)
                        if result is not None:
                            children.append(result)
                return size != 0
            finally:
                ax.release(collection)

        try:
            role = text("AXRole")
            if role is None or (depth == 1 and role != "AXApplication"):
                complete = False
                return None
            if menu_roots_only and role not in {"AXMenu", "SGTMenu"}:
                return None
            _, subrole, value = _read_ax_identity_and_value(lambda name: role if name == "AXRole" else text(name))
            in_search_scope = finder and (search_scope or subrole == "AXSearchField" or role == "SGTMenu")
            suggestion_menu = finder and role == "AXMenu" and (in_search_scope or depth == 2)
            if suggestion_menu and any(ax.equal(element, handle.element) for handle in projected_suggestion_menus):
                return None
            if (parent_role == "AXMenuBar" and role != "AXMenuBarItem") or (parent_role == "AXMenuBarItem" and role != "AXMenu"):
                # A sibling that is not a menu is outside the menu-bar projection.
                return None
            kind = "SecureTextField" if subrole == "AXSecureTextField" else _AX_TO_XCUI.get(role, "Any")
            identity = text("AXIdentifier")
            label = text("AXDescription")
            title = text("AXTitle")
            geometry = ax.decode_bounds(read)
            if suggestion_menu and (geometry is None or geometry[2] <= 0 or geometry[3] <= 0):
                if {"AXPosition", "AXSize"} & failed_metadata:
                    complete = False
                return None
            enabled = boolean("AXEnabled")
            placeholder = text("AXPlaceholderValue")
            selected = boolean("AXSelected") if role == "AXMenuBarItem" else None
            expanded = role == "AXMenuBarItem" and (selected or boolean("AXExpanded"))
            if role == "AXMenuBarItem" and not expanded and "AXSelected" in failed_metadata:
                complete = False
        except _AXReadError:
            complete = False
            return None

        try:
            if depth == 1:
                use_child_fallback = not collect("AXWindows")
                _, menu = read("AXMenuBar")
                try:
                    if use_child_fallback:
                        collect("AXChildren", exclude=menu)
                    elif finder and complete:
                        collect("AXChildren", exclude=menu, menu_roots_only=True)
                    if menu is not None:
                        result = visit(menu, depth + 1, (element,), parent_role="AXApplication")
                        if result is not None:
                            children.append(result)
                finally:
                    if menu is not None:
                        ax.release(menu)
            elif parent_role != "AXMenu" and (role not in {"AXMenu", "AXMenuItem", "AXMenuBarItem"} or expanded or suggestion_menu or (role == "AXMenu" and parent_role == "AXMenuBarItem")):
                collect("AXChildren")
        except _AXReadError as exc:
            complete = False
            if exc.status is not None and depth != 1:
                return None
        visible = geometry is not None and geometry[2] > 0 and geometry[3] > 0
        handle = _AXHandle(ax, element, pid)
        if suggestion_menu:
            projected_suggestion_menus.append(handle)
        return NativeNode(
            type=observed("XCUIElementType" + kind) if kind != "Any" and (role not in _READABLE_VALUE_ROLES or subrole is not None) else unknown,
            id=unknown if "AXIdentifier" in failed_metadata else observed(identity),
            label=unknown if "AXDescription" in failed_metadata else observed(label),
            title=unknown if "AXTitle" in failed_metadata else observed(title),
            placeholder_value=unknown if "AXPlaceholderValue" in failed_metadata else observed(placeholder),
            value=unknown if "AXSubrole" in failed_metadata else observed(value),
            text=unknown if "AXSubrole" in failed_metadata else observed(value),
            enabled=unknown if "AXEnabled" in failed_metadata else observed(enabled),
            bounds=unknown if failed_metadata.intersection(("AXPosition", "AXSize")) else observed(Bounds(*geometry) if geometry is not None else None),
            visibility="visible" if visible else "unknown",
            children=tuple(children),
            desc=na,
            package=na,
            focused=unknown,
            checked=na,
            selected=unknown if selected is None else observed(selected),
            viewport_relation="unknown",
            window_id=na,
            extra_attributes={"role": role} if role else {},
            handle=handle,
            unknown_metadata=frozenset(failed_metadata),
        )

    try:
        root = visit(app, 1, ())
        if _frontmost_pid(bundle_id, app_path=app_path, deadline=deadline, cancellation=cancellation) != pid:
            raise _NativeForegroundError("target_not_foreground", "The macOS foreground application changed during AX capture.")
    finally:
        ax.release(app)
    observed_at = datetime.now(UTC)
    operation_id = uuid4().hex
    scope = ObservationScope("root", None, None)
    source = SourceMetadata(False, True, max_depth)
    viewport = Viewport(unknown, unknown, unknown)
    coverage = "requested_scope" if complete else "visible_with_ancestors"
    payload, xml = project(root) if root is not None else (None, None)
    document = {
        "schema": 1,
        "kind": "tree",
        "operation_id": operation_id,
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "scope": asdict(scope),
        "source": asdict(source),
        "coverage": coverage,
        "complete_for_requested_scope": complete,
        "upstream_completeness": "unknown",
        "payload": {"roots": [payload] if root is not None else [], "viewport": asdict(viewport)},
    }
    return NativeObservation(
        operation_id,
        observed_at,
        scope,
        coverage,
        complete,
        "unknown",
        source,
        viewport,
        (root,) if root is not None else (),
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(),
        ElementTree.tostring(xml, encoding="utf-8") if xml is not None else b"",
        frozenset(unknown_metadata),
    )
