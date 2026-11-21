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
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
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


def _activate_macos_app_native(bundle_id: str, *, timeout_seconds: float, cancellation: Event | None) -> None:
    deadline = time.monotonic() + timeout_seconds

    def run(command: list[str]) -> str:
        if cancellation is not None and cancellation.is_set():
            raise CancelledError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("macOS foreground activation timed out.")
        return subprocess.run(command, capture_output=True, text=True, timeout=remaining, check=True).stdout.strip()  # noqa: S603

    run(["/usr/bin/open", "-b", bundle_id])
    while True:
        front = run(["/usr/bin/lsappinfo", "front"])
        if front.startswith("ASN:"):
            identity = run(["/usr/bin/lsappinfo", "info", "-only", "bundleid", front])
            if identity == f'"CFBundleIdentifier"="{bundle_id}"':
                return
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


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
    def __init__(self, backend: _AX, element: int) -> None:
        self.backend = backend
        self.element = backend.retain(element)

    def __del__(self) -> None:
        self.backend.release(self.element)


def _frontmost_pid(bundle_id: str) -> int:
    front = subprocess.run(["/usr/bin/lsappinfo", "front"], capture_output=True, text=True, timeout=1, check=True).stdout.strip()
    info = subprocess.run(  # noqa: S603
        ["/usr/bin/lsappinfo", "info", "-only", "bundleid", "-only", "pid", front], capture_output=True, text=True, timeout=1, check=True
    ).stdout
    pid = re.search(r'"pid"\s*=\s*(\d+)', info)
    identity = re.search(r'"CFBundleIdentifier"\s*=\s*"([^"]+)"', info)
    if pid is None or identity is None or identity.group(1) != bundle_id or int(pid.group(1)) <= 0:
        raise RuntimeError("The requested AX application is not foreground.")
    return int(pid.group(1))


def _verify_ax_focus(handle: _AXHandle | None) -> bool:
    if handle is None:
        return False
    ax = handle.backend
    system = ax.system()
    if not system:
        return False
    try:
        ax.timeout(system, 0.25)
        _, focused = ax.attribute(system, "AXFocusedUIElement")
        if not focused:
            return False
        try:
            return bool(ax.equal(focused, handle.element))
        finally:
            ax.release(focused)
    finally:
        ax.release(system)


def _verify_ax_point_hit(handle: _AXHandle | None, x: int, y: int) -> bool:
    if handle is None:
        return False
    ax = handle.backend
    system = ax.system()
    if not system:
        return False
    hit = ctypes.c_void_p()
    try:
        ax.timeout(system, 0.25)
        if ax.hit(system, x, y, ctypes.byref(hit)) != 0:
            return False
    finally:
        ax.release(system)
    current = hit.value
    try:
        for _ in range(32):
            if not current:
                break
            if ax.equal(current, handle.element):
                return True
            _, parent = ax.attribute(current, "AXParent")
            ax.release(current)
            current = parent
        return False
    finally:
        if current:
            ax.release(current)


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


def _observe_macos_ax_tree(*, bundle_id: str, max_nodes: int, max_depth: int, control=None):
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
    pid = _frontmost_pid(bundle_id)
    ax = _AX()
    app = ax.application(pid)
    if not app:
        raise RuntimeError("Cannot bind foreground accessibility application.")
    count = 0
    unknown_metadata: set[str] = set()
    complete = True

    def observed(value):
        return ObservedValue("missing") if value is None else ObservedValue("value", value)

    def visit(element: int, depth: int, ancestors: tuple[int, ...], *, parent_role: str | None = None):
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

        def collect(name, *, exclude=None):
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
                        result = visit(child, depth + 1, (*ancestors, element), parent_role=role)
                        if result is not None:
                            children.append(result)
                return size != 0
            finally:
                ax.release(collection)

        try:
            role, subrole, value = _read_ax_identity_and_value(text)
            if (parent_role == "AXMenuBar" and role != "AXMenuBarItem") or (parent_role == "AXMenuBarItem" and role != "AXMenu"):
                if role is None:
                    complete = False
                return None
            kind = "SecureTextField" if subrole == "AXSecureTextField" else _AX_TO_XCUI.get(role, "Any")
            identity = text("AXIdentifier")
            label = text("AXDescription")
            title = text("AXTitle")
            geometry = ax.decode_bounds(read)
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
                    if menu is not None:
                        result = visit(menu, depth + 1, (element,), parent_role="AXApplication")
                        if result is not None:
                            children.append(result)
                finally:
                    if menu is not None:
                        ax.release(menu)
            elif parent_role != "AXMenu" and (role not in {"AXMenu", "AXMenuItem", "AXMenuBarItem"} or expanded or (role == "AXMenu" and parent_role == "AXMenuBarItem")):
                collect("AXChildren")
        except _AXReadError as exc:
            complete = False
            if exc.status is not None and depth != 1:
                return None
        visible = geometry is not None and geometry[2] > 0 and geometry[3] > 0
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
            handle=_AXHandle(ax, element),
            unknown_metadata=frozenset(failed_metadata),
        )

    try:
        root = visit(app, 1, ())
        if _frontmost_pid(bundle_id) != pid:
            raise RuntimeError("macOS foreground application changed during AX capture.")
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
