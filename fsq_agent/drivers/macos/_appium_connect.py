# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import inspect
import json
import logging
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from itertools import pairwise
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar
from xml.etree import ElementTree

from lxml import etree
from pydantic import ValidationError

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _macos_driver_tool
from fsq_agent.drivers._color_sample import _sample_color
from fsq_agent.drivers._visual_inspection import _run_visual_inspection
from fsq_agent.drivers.macos._elements import candidate
from fsq_agent.drivers.macos._native_fallback import (
    _activate_macos_app_native,
    _foreground_identity,
    _frontmost_pid,
    _macos_ax_process_trusted,
    _NativeForegroundError,
    _observe_macos_ax_tree,
    _permission_modal_for_target,
    _running_macos_app_path,
    _verify_ax_focus,
    _verify_ax_point_hit,
    _verify_finder_search_click,
)
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertElementsOrderParams,
    MacOSAssertNotVisibleParams,
    MacOSAssertStateParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragEndpoint,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSInspectElementParams,
    MacOSInspectWithAIParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSLocator,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSScrollOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
    SampleColorParams,
    ToolExecutionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from pydantic import BaseModel

_T = TypeVar("_T")

DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH = 12
DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS = 10
DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS = 900.0
DEFAULT_MACOS_SKIP_APP_KILL = True
DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS = 50
DEFAULT_MACOS_SNAPSHOT_MAX_JSON_CHARS = 28_000
DEFAULT_MACOS_ACTIVATE_TIMEOUT_SECONDS = 30.0
DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS = 15.0
DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS = 10.0
DEFAULT_IDLE_ENSURE_ALIVE_SECONDS = 45.0
DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS = 25.0
MAX_PORT_BIND_ATTEMPTS = 3
MACOS_COMPACT_SIBLING_STEPDOWNS = (None, 80, 40, 20, 12)
MACOS_EXCLUDED_FRONTMOST_BUNDLES = frozenset({"com.apple.loginwindow", "com.apple.SecurityAgent"})
MACOS_FALLBACK_FRONTMOST_BUNDLE = "com.apple.finder"
MACOS_EDITABLE_DISABLED_TOLERANT_TYPES = frozenset({"TextArea", "TextView", "WebArea"})
MACOS_STATE_ASSERTION_FIELDS = ("enabled", "focused", "selected", "hittable", "displayed")
MACOS_MASK_GLYPHS = frozenset({"*", "•", "●"})
MACOS_MUTABLE_TEXT_SELECTOR_TOKEN = re.compile(r"\b(?:value|label|title|name|text)\b")
MACOS_SESSION_STATES = frozenset({"creating", "active", "uncertain", "disposing", "disposed", "closing", "closed"})
MACOS_SAFE_BACKEND_CODES = frozenset(
    {
        "invalid_argument",
        "invalid_selector",
        "command_failed",
        "operation_timeout",
        "condition_timeout",
        "operation_cancelled",
        "target_not_found",
        "target_ambiguous",
        "target_not_displayed",
        "target_not_enabled",
        "target_stale",
        "session_not_active",
        "session_invalid",
        "session_lost",
        "session_closed",
        "session_uncertain",
        "session_disposed",
        "session_busy",
        "session_create_unknown",
        "session_quit_unknown",
        "execution_unknown",
        "response_unknown",
        "connection_unavailable",
        "connection_closed",
        "connection_closing",
        "cleanup_failed",
        "source_settings_invalid",
        "source_invalid",
        "unsupported_operation",
        "unsupported_platform",
        "invalid_response",
        "pointer_mapping_unavailable",
        "invalid_visual_response",
        "frame_not_single",
        "observation_too_large",
        "ax_not_trusted",
        "foreground_unobservable",
        "target_not_foreground",
        "permission_modal",
        "app_not_running",
        "app_activation_timeout",
        "unsupported_host",
    }
)
MACOS_SAFE_COMPLETED_STEPS = frozenset(
    {
        "target_resolved",
        "target_displayed",
        "target_enabled",
        "action_completed",
        "settings_changed",
        "settings_restored",
        "attributes_read",
        "display_size_read",
        "session_deleted",
        "observation_built",
        "source_parsed",
    }
)

MACOS_PREDICATE_CLAUSE_RE = re.compile(
    r"^(?P<field>identifier|name|label|value|title|placeholderValue|amType|type|role|enabled|visible|displayed|selected|focused|hittable|amRect\.x|amRect\.y|amRect\.width|amRect\.height)\s*"
    r"(?P<op>==|!=|CONTAINS(?:\[cd\])?|BEGINSWITH(?:\[cd\])?|ENDSWITH(?:\[cd\])?)\s*"
    r'(?P<rhs>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|true|false|TRUE|FALSE|-?\d+)$',
    re.IGNORECASE,
)


XCTEST_QUIESCENCE_BLACKLIST_BUNDLES = frozenset({"com.apple.iWork.Keynote", "com.apple.iWork.Pages", "com.apple.iWork.Numbers", "org.libreoffice.script"})


def _unquote_predicate_rhs(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return raw


def _split_top_level_predicate_boolean(expr: str, keyword: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_quote: str | None = None
    escape = False
    token_len = len(keyword)
    start = 0
    idx = 0
    while idx < len(expr):
        ch = expr[idx]
        if escape:
            escape = False
            idx += 1
            continue
        if ch == "\\" and in_quote is not None:
            escape = True
            idx += 1
            continue
        if in_quote is not None:
            if ch == in_quote:
                in_quote = None
            idx += 1
            continue
        if ch in {'"', "'"}:
            in_quote = ch
            idx += 1
            continue
        if ch == "(":
            depth += 1
            idx += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            idx += 1
            continue
        if depth == 0 and expr[idx : idx + token_len].upper() == keyword:
            before_ok = idx == 0 or expr[idx - 1].isspace() or expr[idx - 1] == ")"
            after_idx = idx + token_len
            after_ok = after_idx == len(expr) or expr[after_idx].isspace() or expr[after_idx] == "("
            if before_ok and after_ok:
                parts.append(expr[start:idx].strip())
                idx = after_idx
                start = idx
                continue
        idx += 1
    parts.append(expr[start:].strip())
    return parts


def _strip_outer_parens(expr: str) -> str:
    cleaned = expr.strip()
    while len(cleaned) >= 2 and cleaned[0] == "(" and cleaned[-1] == ")":
        depth = 0
        in_quote: str | None = None
        escape = False
        wraps_all = True
        for idx, ch in enumerate(cleaned):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_quote is not None:
                escape = True
                continue
            if in_quote is not None:
                if ch == in_quote:
                    in_quote = None
                continue
            if ch in {'"', "'"}:
                in_quote = ch
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and idx < len(cleaned) - 1:
                    wraps_all = False
                    break
        if wraps_all and depth == 0:
            cleaned = cleaned[1:-1].strip()
        else:
            break
    return cleaned


def _eval_macos_predicate_on_xml_node(expr: str, xml_node: ElementTree.Element, *, failed: frozenset[str] | None = None) -> bool | str | None:
    cleaned = _strip_outer_parens(expr)
    if not cleaned:
        return None
    or_parts = _split_top_level_predicate_boolean(cleaned, "OR")
    if len(or_parts) > 1:
        outcomes = [_eval_macos_predicate_on_xml_node(part, xml_node, failed=failed) for part in or_parts]
        if any(item is None for item in outcomes):
            return None
        return True if True in outcomes else ("unknown" if "unknown" in outcomes else False)
    and_parts = _split_top_level_predicate_boolean(cleaned, "AND")
    if len(and_parts) > 1:
        outcomes = [_eval_macos_predicate_on_xml_node(part, xml_node, failed=failed) for part in and_parts]
        if any(item is None for item in outcomes):
            return None
        return False if False in outcomes else ("unknown" if "unknown" in outcomes else True)
    if cleaned.upper().startswith("NOT ") or cleaned.upper().startswith("NOT("):
        inner = cleaned[3:].strip()
        sub = _eval_macos_predicate_on_xml_node(inner, xml_node, failed=failed)
        return not sub if isinstance(sub, bool) else sub
    match = MACOS_PREDICATE_CLAUSE_RE.match(cleaned)
    if match is None:
        return None
    field = match.group("field")
    op = match.group("op").upper()
    rhs_raw = match.group("rhs")
    attrs = xml_node.attrib
    field_lower = field.lower()
    if failed:
        dependencies = {
            "role": (),
            "label": ("AXDescription",),
            "title": ("AXTitle",),
            "identifier": ("AXIdentifier",),
            "enabled": ("AXEnabled",),
            "selected": ("AXSelected",),
            "focused": ("AXFocused",),
            "visible": ("AXPosition", "AXSize"),
            "displayed": ("AXPosition", "AXSize"),
            "placeholdervalue": ("AXPlaceholderValue",),
            "x": ("AXPosition", "AXSize"),
            "y": ("AXPosition", "AXSize"),
            "width": ("AXPosition", "AXSize"),
            "height": ("AXPosition", "AXSize"),
            "value": ("AXSubrole",),
            "type": ("AXSubrole",),
            "amtype": ("AXSubrole",),
            "amrect.x": ("AXPosition", "AXSize"),
            "amrect.y": ("AXPosition", "AXSize"),
            "amrect.width": ("AXPosition", "AXSize"),
            "amrect.height": ("AXPosition", "AXSize"),
        }
        relevant = dependencies.get(field_lower, failed)
        if field_lower == "name":
            relevant = ()
            if not attrs.get("name"):
                for key, ax_name in (("identifier", "AXIdentifier"), ("label", "AXDescription"), ("title", "AXTitle"), ("value", "AXSubrole")):
                    if ax_name in failed:
                        relevant = (ax_name,)
                        break
                    if attrs.get(key):
                        break
        if failed.intersection(relevant):
            return None if rhs_raw.lower() in {"true", "false"} and op not in {"==", "!="} else "unknown"
    actual: object
    if field_lower in {"amtype", "type"}:
        actual = xml_node.tag
    elif field_lower == "name":
        actual = attrs.get("name") or attrs.get("identifier") or attrs.get("label") or attrs.get("title") or attrs.get("value") or ""
    elif field_lower in {"visible", "displayed", "enabled", "selected", "focused", "hittable"}:
        raw_state = attrs.get("visible" if field_lower == "displayed" else field_lower)
        if raw_state not in {"true", "false"}:
            return None
        actual = raw_state == "true"
    elif field_lower.startswith("amrect."):
        coord_key = field_lower.split(".", 1)[1]
        raw_coord = attrs.get(coord_key)
        if raw_coord is None:
            return False
        try:
            actual = int(raw_coord)
        except ValueError:
            return False
    else:
        actual_key = "placeholderValue" if field_lower == "placeholdervalue" else field
        actual = attrs.get(actual_key, "")

    if rhs_raw.lower() in {"true", "false"}:
        expected_bool = rhs_raw.lower() == "true"
        actual_bool = actual if isinstance(actual, bool) else str(actual).lower() == "true"
        return (actual_bool == expected_bool) if op == "==" else (actual_bool != expected_bool if op == "!=" else None)
    if isinstance(actual, int) and re.fullmatch(r"-?\d+", rhs_raw):
        expected_int = int(rhs_raw)
        return (actual == expected_int) if op == "==" else (actual != expected_int if op == "!=" else None)

    rhs_str = _unquote_predicate_rhs(rhs_raw)
    actual_str = str(actual)
    case_insensitive = "[CD]" in op or "[C]" in op
    lhs_cmp = actual_str.casefold() if case_insensitive else actual_str
    rhs_cmp = rhs_str.casefold() if case_insensitive else rhs_str
    if "[CD]" in op:
        lhs_cmp, rhs_cmp = ("".join(char for char in unicodedata.normalize("NFD", text) if not unicodedata.combining(char)) for text in (lhs_cmp, rhs_cmp))
    base_op = op.split("[", 1)[0]
    if base_op == "==":
        if field_lower in {"amtype", "type"}:
            prefixed = rhs_str if rhs_str.startswith("XCUIElementType") else f"XCUIElementType{rhs_str}"
            return actual_str in {rhs_str, prefixed}
        return lhs_cmp == rhs_cmp
    if base_op == "!=":
        if field_lower in {"amtype", "type"}:
            prefixed = rhs_str if rhs_str.startswith("XCUIElementType") else f"XCUIElementType{rhs_str}"
            return actual_str not in {rhs_str, prefixed}
        return lhs_cmp != rhs_cmp
    if base_op == "CONTAINS":
        return rhs_cmp in lhs_cmp
    if base_op == "BEGINSWITH":
        return lhs_cmp.startswith(rhs_cmp)
    if base_op == "ENDSWITH":
        return lhs_cmp.endswith(rhs_cmp)
    return None


class _NativeAXElement:
    """Element adapter backed by live macOS Accessibility ``TreeNode`` inspection and Finder-anchored ``Device`` synthesis."""

    def __init__(
        self,
        driver: AppiumConnectMacOSDriver,
        node: Any,
        *,
        bundle_id: str | None,
        reanchor_locator: MacOSLocator | None = None,
    ) -> None:
        self._driver = driver
        self._node = node
        self._bundle_id = bundle_id
        self._reanchor_locator = reanchor_locator
        self._mutated = False

    def _refresh_node(self) -> Any:
        if self._reanchor_locator is None:
            return self._node
        refreshed = self._driver._resolve_native_ax_node_once(self._reanchor_locator, preferred_bundle_id=self._bundle_id)
        if refreshed is None:
            raise ToolExecutionError("Native AX element identity could not be re-established.")
        original_handle = getattr(self._node, "handle", None)
        refreshed_handle = getattr(refreshed, "handle", None)
        if original_handle is not None and (refreshed_handle is None or not original_handle.backend.equal(original_handle.element, refreshed_handle.element)):
            raise ToolExecutionError("Native AX element identity changed before dispatch.")
        return refreshed

    def _current_bounds_center(self) -> tuple[int, int]:
        node = self._refresh_node()
        if getattr(node, "visibility", None) not in {"visible", "partially_visible"}:
            raise ToolExecutionError("Native AX pointer target is not visibly actionable.")
        bounds_obs = getattr(node, "bounds", None)
        bounds = getattr(bounds_obs, "value", None) if getattr(bounds_obs, "state", None) == "value" else None
        if bounds is None or not all(hasattr(bounds, k) for k in ("x", "y", "width", "height")):
            raise ToolExecutionError("Native AX element has no actionable screen bounds.", context={"stage": "native_ax_element"})
        bw, bh = int(bounds.width), int(bounds.height)
        if bw <= 0 or bh <= 0:
            raise ToolExecutionError("Native AX element has zero-area bounds.", context={"stage": "native_ax_element"})
        x, y = int(bounds.x + bw / 2.0), int(bounds.y + bh / 2.0)
        handle = getattr(node, "handle", None)
        if not _verify_ax_point_hit(handle, x, y):
            raise ToolExecutionError("Native AX pointer target did not pass hit testing.")
        if handle is not None and handle.pid != self._driver._verify_native_foreground():
            raise ToolExecutionError("Native AX pointer target foreground changed before dispatch.")
        return x, y

    def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
        del control
        node = self._refresh_node() if self._mutated else self._node
        results: list[Any] = []
        visibility = getattr(node, "visibility", "unknown")
        is_displayed = visibility in {"visible", "partially_visible"}

        for name in attributes:
            if name == "displayed":
                results.append(SimpleNamespace(name="displayed", state="unknown" if visibility == "unknown" else "value", value=None if visibility == "unknown" else is_displayed))
            elif name == "hittable":
                results.append(SimpleNamespace(name="hittable", state="unknown", value=None))
            elif name == "focused":
                f_obs = getattr(node, "focused", None)
                if getattr(f_obs, "state", None) == "value" and type(f_obs.value) is bool:
                    results.append(SimpleNamespace(name="focused", state="value", value=f_obs.value))
                else:
                    results.append(SimpleNamespace(name="focused", state="unknown", value=None))
            elif name == "text":
                t_obs = getattr(node, "text", None)
                if getattr(t_obs, "state", None) == "value" and isinstance(t_obs.value, str):
                    results.append(SimpleNamespace(name="text", state="value", value=t_obs.value))
                else:
                    raw_text = getattr(node, "raw_text", None)
                    if isinstance(raw_text, str) and raw_text:
                        results.append(SimpleNamespace(name="text", state="value", value=raw_text))
                    else:
                        for fallback_attr in ("value", "label", "title"):
                            fb = getattr(node, fallback_attr, None)
                            if getattr(fb, "state", None) == "value" and isinstance(fb.value, str):
                                results.append(SimpleNamespace(name="text", state="value", value=fb.value))
                                break
                        else:
                            results.append(SimpleNamespace(name="text", state="missing", value=None))
            else:
                obs = getattr(node, name, None)
                state = getattr(obs, "state", "missing") if obs is not None else "missing"
                val = getattr(obs, "value", None) if state == "value" else None
                results.append(SimpleNamespace(name=name, state=state, value=val))
        return SimpleNamespace(attributes=tuple(results))

    def click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds
        session = self._driver._require_session()
        cx, cy = self._current_bounds_center()
        session.device.click_at(cx, cy, button="left", double=False, key_modifier_flags=key_modifier_flags, control=control)

    def double_click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds
        session = self._driver._require_session()
        cx, cy = self._current_bounds_center()
        session.device.click_at(cx, cy, button="left", double=True, key_modifier_flags=key_modifier_flags, control=control)

    def right_click(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds
        session = self._driver._require_session()
        cx, cy = self._current_bounds_center()
        session.device.click_at(cx, cy, button="right", double=False, key_modifier_flags=key_modifier_flags, control=control)

    def hover(self, *, key_modifier_flags: int | None = None, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del key_modifier_flags, wait_timeout_seconds
        session = self._driver._require_session()
        cx, cy = self._current_bounds_center()
        session.device.hover_at(cx, cy, control=control)

    def _require_focus(self) -> None:
        handle = getattr(self._refresh_node(), "handle", None)
        if not _verify_ax_focus(handle):
            raise ToolExecutionError("Native AX keyboard target does not have focus.")
        pid = self._driver._verify_native_foreground()
        if handle is not None and handle.pid != pid:
            raise ToolExecutionError("Native AX keyboard target foreground changed before dispatch.")

    def clear(self, *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds
        session = self._driver._require_session()
        select_all = self._driver._build_key_item("a", MACOS_KEY_MODIFIER_FLAGS["COMMAND"])
        self._require_focus()
        session.device.send_keys([select_all], control=control)
        self._require_focus()
        session.device.send_keys([MACOS_KEY_NAMES["DELETE"]], control=control)
        self._mutated = True

    def send_keys(self, text: str, *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds, control
        self._driver._send_macos_text(text, element=self)
        self._mutated = True

    def send_macos_keys(self, keys: list[Any], *, wait_timeout_seconds: float = 10.0, control: Any = None) -> None:
        del wait_timeout_seconds
        session = self._driver._require_session()
        self._require_focus()
        session.device.send_keys(keys, control=control)
        self._mutated = True


class _AmbiguousResolutionError(Exception):
    def __init__(self, candidates: list[dict[str, object]], *, cause: Exception | None = None) -> None:
        super().__init__("macOS locator matches multiple elements; refine the locator.")
        self.candidates = candidates
        self.cause = cause


MACOS_KEY_MODIFIER_FLAGS = {
    "CAPS_LOCK": 1 << 0,
    "SHIFT": 1 << 1,
    "CONTROL": 1 << 2,
    "OPTION": 1 << 3,
    "ALT": 1 << 3,
    "COMMAND": 1 << 4,
    "FUNCTION": 1 << 5,
}
MACOS_KEY_NAMES = {
    "BACKSPACE": "XCUIKeyboardKeyDelete",
    "BKSP": "XCUIKeyboardKeyDelete",
    "DELETE": "XCUIKeyboardKeyDelete",
    "FORWARD_DELETE": "XCUIKeyboardKeyForwardDelete",
    "FORWARDDELETE": "XCUIKeyboardKeyForwardDelete",
    "DEL": "XCUIKeyboardKeyForwardDelete",
    "DOWN": "XCUIKeyboardKeyDownArrow",
    "ARROWDOWN": "XCUIKeyboardKeyDownArrow",
    "ARROW_DOWN": "XCUIKeyboardKeyDownArrow",
    "DOWNARROW": "XCUIKeyboardKeyDownArrow",
    "DOWN_ARROW": "XCUIKeyboardKeyDownArrow",
    "END": "XCUIKeyboardKeyEnd",
    "ENTER": "XCUIKeyboardKeyReturn",
    "RETURN": "XCUIKeyboardKeyReturn",
    "NUMPADENTER": "XCUIKeyboardKeyEnter",
    "NUMPAD_ENTER": "XCUIKeyboardKeyEnter",
    "KPENTER": "XCUIKeyboardKeyEnter",
    "KP_ENTER": "XCUIKeyboardKeyEnter",
    "ESC": "XCUIKeyboardKeyEscape",
    "ESCAPE": "XCUIKeyboardKeyEscape",
    "HOME": "XCUIKeyboardKeyHome",
    "LEFT": "XCUIKeyboardKeyLeftArrow",
    "ARROWLEFT": "XCUIKeyboardKeyLeftArrow",
    "ARROW_LEFT": "XCUIKeyboardKeyLeftArrow",
    "LEFTARROW": "XCUIKeyboardKeyLeftArrow",
    "LEFT_ARROW": "XCUIKeyboardKeyLeftArrow",
    "PAGEDOWN": "XCUIKeyboardKeyPageDown",
    "PAGE_DOWN": "XCUIKeyboardKeyPageDown",
    "PGDN": "XCUIKeyboardKeyPageDown",
    "PG_DN": "XCUIKeyboardKeyPageDown",
    "PAGEUP": "XCUIKeyboardKeyPageUp",
    "PAGE_UP": "XCUIKeyboardKeyPageUp",
    "PGUP": "XCUIKeyboardKeyPageUp",
    "PG_UP": "XCUIKeyboardKeyPageUp",
    "RIGHT": "XCUIKeyboardKeyRightArrow",
    "ARROWRIGHT": "XCUIKeyboardKeyRightArrow",
    "ARROW_RIGHT": "XCUIKeyboardKeyRightArrow",
    "RIGHTARROW": "XCUIKeyboardKeyRightArrow",
    "RIGHT_ARROW": "XCUIKeyboardKeyRightArrow",
    "SPACE": " ",
    "SPACEBAR": " ",
    "SPACE_BAR": " ",
    "TAB": "XCUIKeyboardKeyTab",
    "UP": "XCUIKeyboardKeyUpArrow",
    "ARROWUP": "XCUIKeyboardKeyUpArrow",
    "ARROW_UP": "XCUIKeyboardKeyUpArrow",
    "UPARROW": "XCUIKeyboardKeyUpArrow",
    "UP_ARROW": "XCUIKeyboardKeyUpArrow",
    "CLEAR": "XCUIKeyboardKeyClear",
    "HELP": "XCUIKeyboardKeyHelp",
    "INS": "XCUIKeyboardKeyHelp",
    "INSERT": "XCUIKeyboardKeyHelp",
    "CAPS_LOCK": "XCUIKeyboardKeyCapsLock",
    "CAPSLOCK": "XCUIKeyboardKeyCapsLock",
    **{f"F{index}": f"XCUIKeyboardKeyF{index}" for index in range(1, 20)},
}
MACOS_SDK_NAMED_KEY_LITERALS = frozenset(
    {
        "XCUIKeyboardKeyDelete",
        "XCUIKeyboardKeyReturn",
        "XCUIKeyboardKeyEnter",
        "XCUIKeyboardKeyTab",
        "XCUIKeyboardKeySpace",
        "XCUIKeyboardKeyEscape",
        "XCUIKeyboardKeyUpArrow",
        "XCUIKeyboardKeyDownArrow",
        "XCUIKeyboardKeyLeftArrow",
        "XCUIKeyboardKeyRightArrow",
        *(f"XCUIKeyboardKeyF{index}" for index in range(1, 20)),
        "XCUIKeyboardKeyForwardDelete",
        "XCUIKeyboardKeyHome",
        "XCUIKeyboardKeyEnd",
        "XCUIKeyboardKeyPageUp",
        "XCUIKeyboardKeyPageDown",
        "XCUIKeyboardKeyClear",
        "XCUIKeyboardKeyHelp",
        "XCUIKeyboardKeyCapsLock",
        "XCUIKeyboardKeyShift",
        "XCUIKeyboardKeyControl",
        "XCUIKeyboardKeyOption",
        "XCUIKeyboardKeyCommand",
        "XCUIKeyboardKeyRightShift",
        "XCUIKeyboardKeyRightControl",
        "XCUIKeyboardKeyRightOption",
        "XCUIKeyboardKeyRightCommand",
        "XCUIKeyboardKeySecondaryFn",
    }
)
MACOS_SDK_NAMED_KEYS_BY_UPPER = {name.upper(): name for name in MACOS_SDK_NAMED_KEY_LITERALS}
MACOS_BUNDLE_ID_SYNTAX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,254}\Z")
DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS = frozenset(
    {
        "identifier",
        "name",
        "label",
        "value",
        "title",
        "type",
        "role",
        "enabled",
        "visible",
        "selected",
        "x",
        "y",
        "width",
        "height",
    }
)
MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS = frozenset({"name", "label", "value", "title", "placeholderValue"})
MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS = frozenset({"identifier", "name", "label", "value", "title"})
MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS = frozenset({*MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS, "type", "role"})
MACOS_SNAPSHOT_STATE_DEFAULTS = {"enabled": "true", "visible": "true", "selected": "false"}
MACOS_INTERACTIVE_SHORT_TYPES = frozenset(
    {
        "Button",
        "CheckBox",
        "RadioButton",
        "PopUpButton",
        "ComboBox",
        "TextField",
        "SecureTextField",
        "TextArea",
        "Slider",
        "Switch",
        "Link",
    }
)


def _is_strict_int(value: object) -> bool:
    return type(value) is int


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def _predicate_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_host_executable(name: str) -> Path | None:
    raw_path = os.environ.get("PATH", "")
    for entry in raw_path.split(os.pathsep)[:64]:
        if not entry:
            continue
        candidate_dir = Path(entry)
        if not candidate_dir.is_absolute():
            continue
        candidate_path = candidate_dir / name
        try:
            resolved = candidate_path.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        except OSError:
            continue
    return None


def _resolve_managed_local_settings(local_settings_cls: type[Any]) -> Any:
    return local_settings_cls(
        node_executable=_resolve_host_executable("node"),
        npm_executable=_resolve_host_executable("npm"),
    )


def _resolve_app_bundle_path(app_path: str) -> Path:
    path = Path(app_path).expanduser()
    if path.suffix.casefold() == ".app":
        return path
    for parent in path.parents:
        if parent.suffix.casefold() == ".app":
            return parent
    return path


def _read_app_bundle_plist(bundle_dir: Path) -> dict[str, Any] | None:
    plist_path = bundle_dir / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return None
    return payload if isinstance(payload, dict) else None


def _bundle_id_from_app_path(app_path: str | Path) -> str | None:
    bundle_dir = _resolve_app_bundle_path(str(app_path))
    if bundle_dir.suffix.casefold() != ".app" or not bundle_dir.is_dir():
        return None
    payload = _read_app_bundle_plist(bundle_dir)
    if payload is None:
        return None
    raw_id = payload.get("CFBundleIdentifier")
    return raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None


def _iter_local_application_bundles() -> list[Path]:
    roots = (
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path("/System/Library/CoreServices"),
        Path.home() / "Applications",
    )
    bundles: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries[:512]:
            try:
                if entry.suffix.casefold() == ".app" and entry.is_dir():
                    if entry not in seen:
                        seen.add(entry)
                        bundles.append(entry)
                elif entry.is_dir() and not entry.is_symlink() and root in {Path("/Applications"), Path.home() / "Applications"}:
                    for child in sorted(entry.iterdir())[:64]:
                        if child.suffix.casefold() == ".app" and child.is_dir() and child not in seen:
                            seen.add(child)
                            bundles.append(child)
            except OSError:
                continue
    return bundles


def _resolve_installed_bundle_identifier(candidate_text: str) -> str | None:
    cleaned = candidate_text.strip()
    if not cleaned:
        return None
    query_cf = cleaned.casefold()
    stem_cf = cleaned[:-4].strip().casefold() if query_cf.endswith(".app") else query_cf
    name_match: str | None = None
    for bundle_dir in _iter_local_application_bundles():
        payload = _read_app_bundle_plist(bundle_dir)
        if payload is None:
            continue
        raw_id = payload.get("CFBundleIdentifier")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        bundle_id = raw_id.strip()
        if bundle_id == cleaned:
            return bundle_id
        if bundle_id.casefold() == query_cf:
            return bundle_id
        if name_match is None:
            candidates = [bundle_dir.stem]
            for key in ("CFBundleDisplayName", "CFBundleName"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    candidates.append(val.strip())
            if any(item.casefold() in {query_cf, stem_cf} for item in candidates):
                name_match = bundle_id
    return name_match


def _detect_frontmost_macos_bundle_id(*, timeout_seconds: float = 5.0) -> str:
    lsappinfo = "/usr/bin/lsappinfo"
    try:
        front_proc = subprocess.run(  # noqa: S603 - fixed read-only macOS LaunchServices query.
            [lsappinfo, "front"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        asn = front_proc.stdout.strip()
        if front_proc.returncode == 0 and asn and asn != "ASN:0x0-0x0:":
            info_proc = subprocess.run(  # noqa: S603 - fixed read-only macOS LaunchServices bundle-id query.
                [lsappinfo, "info", "-only", "bundleid", asn],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            if info_proc.returncode == 0:
                match = re.search(r'"CFBundleIdentifier"\s*=\s*"([^"]+)"', info_proc.stdout)
                if match:
                    bundle_id = match.group(1).strip()
                    if bundle_id and bundle_id not in MACOS_EXCLUDED_FRONTMOST_BUNDLES:
                        return bundle_id
    except (OSError, subprocess.TimeoutExpired):
        pass
    return MACOS_FALLBACK_FRONTMOST_BUNDLE


def _is_macos_bundle_running(bundle_id: str, *, timeout_seconds: float = 5.0) -> bool:
    if sys.platform != "darwin" or not bundle_id or MACOS_BUNDLE_ID_SYNTAX.fullmatch(bundle_id) is None:
        return False
    lsappinfo = shutil.which("lsappinfo") or "/usr/bin/lsappinfo"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed read-only LaunchServices PID lookup for validated bundle_id.
            [lsappinfo, "info", "-only", "pid", bundle_id],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    match = re.search(r'"pid"\s*=\s*(\d+)', proc.stdout)
    return bool(match and int(match.group(1)) > 0)


def _preactivate_running_macos_bundle(bundle_id: str, *, timeout_seconds: float = 10.0) -> None:
    if sys.platform != "darwin" or not bundle_id or MACOS_BUNDLE_ID_SYNTAX.fullmatch(bundle_id) is None:
        return
    open_cmd = shutil.which("open") or "/usr/bin/open"
    with suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(  # noqa: S603 - explicit validated bundle_id foregrounded via open -b without shell.
            [open_cmd, "-b", bundle_id],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )


def _normalize_macos_key_token(raw_key: str) -> str:
    stripped = raw_key.strip()
    if stripped in MACOS_SDK_NAMED_KEY_LITERALS:
        return stripped
    sdk_ci = MACOS_SDK_NAMED_KEYS_BY_UPPER.get(stripped.upper())
    if sdk_ci is not None:
        return sdk_ci
    normalized_upper = re.sub(r"[\s\-]+", "_", stripped).upper()
    compact_upper = normalized_upper.replace("_", "")
    if normalized_upper in MACOS_KEY_NAMES:
        return MACOS_KEY_NAMES[normalized_upper]
    if compact_upper in MACOS_KEY_NAMES:
        return MACOS_KEY_NAMES[compact_upper]
    if len(stripped) == 1:
        return stripped
    if len(raw_key) == 1 and raw_key == " ":
        return " "
    raise ConfigurationError(f"Unsupported macOS key: {raw_key}.")


class AppiumConnectMacOSDriver(AIAssertionBackendToolMixin):
    backend = "appium_connect_agent"

    def __init__(
        self,
        *,
        bundle_id: str | None = None,
        app_path: str | Path | None = None,
        page_source_max_depth: int = DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH,
        action_timeout_seconds: int = DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS,
        new_command_timeout_seconds: float | None = DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS,
        skip_app_kill: bool = DEFAULT_MACOS_SKIP_APP_KILL,
        session: object | None = None,
        native_ax_fallback_bundles: Sequence[str] | None = None,
    ) -> None:
        if type(skip_app_kill) is not bool:
            raise ConfigurationError("macOS skip_app_kill must be a boolean.")
        if isinstance(bundle_id, str) and bundle_id.strip():
            stripped_bundle = bundle_id.strip()
            self.bundle_id: str | None = "auto" if stripped_bundle.casefold() == "auto" else stripped_bundle
        else:
            self.bundle_id = None
        self.app_path = str(Path(app_path)) if app_path else None
        self._native_ax_fallback_bundles = XCTEST_QUIESCENCE_BLACKLIST_BUNDLES if native_ax_fallback_bundles is None else frozenset(native_ax_fallback_bundles)
        self.page_source_max_depth = page_source_max_depth
        self.action_timeout_seconds = action_timeout_seconds
        self.new_command_timeout_seconds = new_command_timeout_seconds
        self.skip_app_kill = skip_app_kill
        self._borrowed_session = session is not None
        self._session: Any | None = session
        self._active_bundle_id: str | None = None if self.bundle_id in {None, "auto"} else self.bundle_id
        self._wda_host_bundle_id: str | None = None
        self._native_ax_mode = False
        self._session_app_path: str | None = self.app_path
        self._session_had_startup_arguments = False
        self._uncertain_from_timeout = False
        self._recoverable_session_loss = False
        self._runtime: Any | None = None
        self._managed_scope: AbstractContextManager[Any] | None = None
        self._connection: Any | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary: BaseException | None = None
        self._closed = False
        self._cleanup_pending = False
        self._operation_cancellation: Event | None = None
        self._last_operation_finished_at: float | None = None
        self._terminal_disposal_diagnostic: dict[str, object] | None = None

    def close(self) -> None:
        if self._borrowed_session:
            self._shutdown_executor()
            self._closed = True
            return
        if self._session is not None or self._managed_scope is not None or self._runtime is not None or self._connection is not None:
            self._run_sync(self._close_owned_resources)
        try:
            self._shutdown_executor()
        except BaseException as shutdown_error:
            self._cleanup_pending = True
            primary_error = self._cleanup_primary
            if primary_error is not None:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from shutdown_error
                raise BaseExceptionGroup("macOS session and owner-thread shutdown failed.", [primary_error, shutdown_error]) from None
            raise
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        self._cleanup_pending = False
        self._closed = True
        if self._terminal_disposal_diagnostic is not None:
            if not self._terminal_disposal_diagnostic["terminal_disposal_completed"]:
                self._terminal_disposal_diagnostic["terminal_disposal_completed"] = True
                logging.getLogger(__name__).info("macOS terminal disposal settled an unconfirmed remote quit.", extra=self._terminal_disposal_diagnostic)
        elif primary_error is not None:
            raise primary_error

    def _close_owned_resources(self) -> None:
        self._cleanup_pending = True
        primary_error = self._cleanup_primary
        managed_settled = False
        try:
            session = self._session
            if session is not None and not self._session_settled:
                info_fn = getattr(session, "session_info", None)
                state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                if state == "active":
                    try:
                        session.quit(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as exc:  # Retain quit failure while explicitly disposing uncertain resources.
                        if getattr(exc, "code", None) in {"session_closed", "session_disposed"}:
                            self._session_settled = True
                        else:
                            primary_error = self._cleanup_primary = exc
                            state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                            if state in {"uncertain", "disposing"} and getattr(exc, "code", None) == "session_quit_unknown" and type(exc).__name__ == "ResourceStateError":
                                req_state = getattr(exc, "request_state", None)
                                self._terminal_disposal_diagnostic = {
                                    "backend_code": "session_quit_unknown",
                                    "request_state": req_state if req_state in {"not_sent", "response_unknown", "response_received"} else "unknown",
                                    "remote_quit_confirmed": False,
                                    "terminal_disposal_completed": False,
                                }
                            if state not in {"uncertain", "disposing", "disposed"}:
                                raise
                    else:
                        self._session_settled = True
                if not self._session_settled:
                    dispose_fn = getattr(session, "dispose", None)
                    if state in {"uncertain", "disposing"} and callable(dispose_fn):
                        dispose_fn(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                        state = getattr(info_fn(), "state", "disposed") if callable(info_fn) else "disposed"
                        if state != "disposed":
                            raise ToolExecutionError("Owned Mac2 terminal disposal did not complete.", context={"state": state})  # noqa: TRY301
                        managed_settled = True
                    elif state == "disposed":
                        managed_settled = True
                    elif state != "closed":
                        raise ToolExecutionError("Owned Mac2 session is not available for terminal cleanup.", context={"state": state})  # noqa: TRY301
                    self._session_settled = True

            if self._scope_exit_attempted and self._runtime is not None and not managed_settled:
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
            elif self._managed_scope is not None and not self._scope_exit_attempted:
                self._scope_exit_attempted = True
                self._managed_scope.__exit__(None, None, None)
                if self._runtime is not None and bool(getattr(self._runtime, "managed_cleanup_pending", False)):
                    self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
            elif self._runtime is not None and not managed_settled and bool(getattr(self._runtime, "managed_cleanup_pending", False)):
                self._runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                if isinstance(primary_error, (CancelledError, KeyboardInterrupt, SystemExit)):
                    raise primary_error from cleanup_error
                raise BaseExceptionGroup("macOS session and owned-resource cleanup failed.", [primary_error, cleanup_error]) from None
            raise

        self._session = None
        self._wda_host_bundle_id = None
        self._native_ax_mode = False
        self._managed_scope = None
        self._connection = None
        self._runtime = None
        self._session_settled = False
        self._scope_exit_attempted = False
        self._uncertain_from_timeout = False

    def _close_owned_resources_for_recreation(self) -> None:
        self._close_owned_resources()
        primary_error = self._cleanup_primary
        self._cleanup_primary = None
        self._cleanup_pending = False
        if self._terminal_disposal_diagnostic is not None:
            if not self._terminal_disposal_diagnostic["terminal_disposal_completed"]:
                self._terminal_disposal_diagnostic["terminal_disposal_completed"] = True
                logging.getLogger(__name__).info(
                    "macOS terminal disposal settled an unconfirmed remote quit before session transition.",
                    extra=self._terminal_disposal_diagnostic,
                )
            self._terminal_disposal_diagnostic = None
        elif primary_error is not None:
            raise primary_error

    def context(self) -> dict[str, object]:
        if self._session is None:
            return {
                "session_id": None,
                "current_url": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        return self._run_on_worker(self._context_impl)

    def _context_impl(self) -> dict[str, object]:
        session = self._session
        if session is None or self._closed:
            return {
                "session_id": None,
                "current_url": None,
                "screen_size": None,
                "capabilities": {},
                "metadata": {
                    "backend": self.backend,
                    "bundle_id_configured": self.bundle_id is not None,
                    "app_path_configured": self.app_path is not None,
                },
            }
        info_fn = getattr(session, "session_info", None)
        session_id: str | None = None
        if callable(info_fn):
            info = info_fn()
            state = getattr(info, "state", "active")
            if state != "active":
                session_id = None
            else:
                raw_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None)
                session_id = str(raw_id) if raw_id else None
        else:
            raw_id = getattr(session, "session_id", None)
            session_id = raw_id if isinstance(raw_id, str) else None

        screen_size = self._viewport_size(session) if session_id is not None else None
        return {
            "session_id": session_id,
            "current_url": None,
            "screen_size": screen_size,
            "capabilities": {"platformName": "Mac", "automationName": "Mac2"} if session_id is not None else {},
            "metadata": {
                "backend": self.backend,
                "bundle_id_configured": self.bundle_id is not None,
                "app_path_configured": self.app_path is not None,
            },
        }

    @_macos_driver_tool(
        "launchApp",
        description=(
            "Create or rebind a managed Appium Mac2 session (new_session=true; idempotently reuses an already-bound healthy session on the same bundle) or activate a macOS bundle_id or .app app_path in the active session (new_session=false)."
        ),
    )
    def launch_app(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._launch_app_impl(params), allow_cleanup_pending=bool(params.new_session))

    def _launch_app_impl(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        session = self._ensure_session(params)
        info_fn = getattr(session, "session_info", None)
        if callable(info_fn):
            info = info_fn()
            session_id = getattr(info, "appium_session_id", None) or getattr(info, "id", None)
        else:
            session_id = getattr(session, "session_id", None)
        return self._passed(
            {
                "session_id": str(session_id) if session_id is not None else None,
                "bundle_id": self._effective_bundle_id(params.bundle_id),
                "app_path": self._session_app_path,
            }
        )

    @_macos_driver_tool("killApp", description="Terminate the active macOS application or close the Mac2 session.")
    def kill_app(self, params: MacOSKillAppParams) -> dict[str, object]:
        return self._run_tool(lambda: self._kill_app_impl(params))

    def _kill_app_impl(self, params: MacOSKillAppParams) -> dict[str, object]:
        if params.close_session:
            self._recoverable_session_loss = False
        session = self._session
        if session is None:
            if self._recoverable_session_loss:
                return self._failed("action_error", "Cannot terminate the macOS application without a live session.", metadata={"reason": "session_unavailable"})
            return self._passed()
        bundle_id = self._concrete_kill_bundle_id(params.bundle_id)
        if params.close_session:
            if self._borrowed_session:
                quit_fn = getattr(session, "quit", None)
                if callable(quit_fn):
                    quit_fn()
                self._session = None
            else:
                self._close_owned_resources_for_recreation()
            return self._passed({"bundle_id": bundle_id, "close_session": True})
        if not bundle_id:
            raise ConfigurationError("macOS application termination requires bundle_id or a configured bundle id.")
        session = self._require_session()
        try:
            session.device.terminate_app(bundle_id, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="kill_app")
        return self._passed({"bundle_id": bundle_id, "close_session": False})

    @_macos_driver_tool("clickOn", description="Click a macOS element or point resolved through Appium Mac2.")
    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._click_on_impl(params))

    def _click_on_impl(self, params: MacOSClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        finder_deadline = self._finder_click_deadline(params)
        element = self._resolve_element_or_none(params)
        if element is not None:
            if finder_deadline is not None:
                session, finder_point = self._finder_menu_click_point(element, deadline=finder_deadline)
                if finder_point is not None:
                    session.device.click_at(
                        *finder_point,
                        button="left",
                        double=False,
                        key_modifier_flags=modifier_flags,
                        control=self._remaining_finder_click_control(finder_deadline),
                    )
                    return self._passed()
            try:
                element.click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="click_on")
            return self._passed()
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="left", double=False, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json")})

    def _finder_click_deadline(self, params: BaseModel) -> float | None:
        if sys.platform == "darwin" and self._active_bundle_id == MACOS_FALLBACK_FRONTMOST_BUNDLE and not self._native_ax_mode and self._has_semantic_target(params):
            return time.monotonic() + max(5.0, float(self.action_timeout_seconds) + 5.0)
        return None

    def _remaining_finder_click_control(self, deadline: float) -> Any:
        if self._operation_cancellation is not None and self._operation_cancellation.is_set():
            raise CancelledError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolExecutionError("Finder menu validation exceeded its operation deadline.", context={"stage": "finder_menu_proof"})
        return self._operation_control(remaining, cancellation=self._operation_cancellation)

    def _finder_menu_click_point(self, element: Any, *, deadline: float) -> tuple[Any, tuple[int, int] | None]:
        # No session or WDA reads are permitted after final foreground and AX hit validation.
        session = self._require_session()
        if self._active_bundle_id != MACOS_FALLBACK_FRONTMOST_BUNDLE or self._native_ax_mode:
            raise ToolExecutionError("The logical Finder target changed before dispatch.", context={"stage": "finder_menu_proof"})
        inspected = element.inspect(attributes=("type",), control=self._remaining_finder_click_control(deadline))
        target_type = next((item.value for item in inspected.attributes if item.name == "type" and item.state == "value"), None)
        if not isinstance(target_type, str) or target_type in {"Any", "XCUIElementTypeAny"}:
            raise ToolExecutionError("The Finder target role could not be proved.", context={"stage": "finder_menu_proof"})
        if target_type not in {"MenuItem", "XCUIElementTypeMenuItem"}:
            return session, None
        inspected = element.inspect(attributes=("title", "bounds", "displayed", "enabled"), control=self._remaining_finder_click_control(deadline))
        evidence = {item.name: item.value for item in inspected.attributes if item.state == "value"}
        title, rectangle = evidence.get("title"), evidence.get("bounds")
        if not isinstance(title, str) or not title or evidence.get("displayed") is not True or evidence.get("enabled") is not True:
            raise ToolExecutionError("The Finder menu target is not uniquely actionable.", context={"stage": "finder_menu_proof"})
        left, top, width, height = (getattr(rectangle, key, None) for key in ("x", "y", "width", "height"))
        if type(left) is not int or type(top) is not int or type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ToolExecutionError("The Finder menu target has no usable bounds.", context={"stage": "finder_menu_proof"})
        bounds = (left, top, width, height)
        x, y = left + width // 2, top + height // 2
        display = session.device.display_size(control=self._remaining_finder_click_control(deadline))
        if not (0 <= x < display.width and 0 <= y < display.height):
            raise ToolExecutionError("The Finder menu target is outside the supported display.", context={"stage": "finder_menu_proof"})
        try:
            suggestion = _verify_finder_search_click(title, bounds, x, y, deadline=deadline, cancellation=self._operation_cancellation)
        except Exception as exc:
            raise ToolExecutionError("The Finder menu target did not pass live AX validation.", context={"stage": "finder_menu_proof"}) from exc
        return session, (x, y) if suggestion else None

    def _require_ordinary_finder_click(self, element: Any, *, deadline: float | None) -> None:
        if deadline is not None and self._finder_menu_click_point(element, deadline=deadline)[1] is not None:
            raise ToolExecutionError("Finder search suggestions support only a single left click.", context={"stage": "finder_menu_proof"})

    @_macos_driver_tool("doubleClickOn", description="Double-click a macOS element or point.")
    def double_click_on(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._double_click_on_impl(params))

    def _double_click_on_impl(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        finder_deadline = self._finder_click_deadline(params)
        element = self._resolve_element_or_none(params)
        if element is not None:
            self._require_ordinary_finder_click(element, deadline=finder_deadline)
            center = self._element_center(element)
            try:
                element.double_click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="double_click_on")
            output: dict[str, object] = {"click_count": 2}
            if center is not None:
                output["point"] = center.model_dump(mode="json")
            return self._passed(output)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="left", double=True, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json"), "click_count": 2})

    @_macos_driver_tool("rightClickOn", description="Right-click a macOS element or point.")
    def right_click_on(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._right_click_on_impl(params))

    def _right_click_on_impl(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        modifier_flags = self._modifier_flags(params.modifiers)
        finder_deadline = self._finder_click_deadline(params)
        element = self._resolve_element_or_none(params)
        if element is not None:
            self._require_ordinary_finder_click(element, deadline=finder_deadline)
            center = self._element_center(element)
            try:
                element.right_click(
                    key_modifier_flags=modifier_flags,
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="right_click_on")
            return self._passed({"point": center.model_dump(mode="json")} if center is not None else None)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_click(point, button="right", double=False, modifier_flags=modifier_flags)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("typeText", description="Type text into the resolved or focused macOS element.")
    def type_text(self, params: MacOSTypeTextParams) -> dict[str, object]:
        return self._run_tool(lambda: self._type_text_impl(params))

    def _type_text_impl(self, params: MacOSTypeTextParams) -> dict[str, object]:
        self._require_session()
        element = self._resolve_element_or_none(params)
        if element is None and self._has_semantic_target(params):
            return self._target_missing()
        if element is not None:
            return self._type_text_into_element(element, params)

        if params.clear:
            return self._failed("action_error", "Untargeted macOS text entry cannot clear and verify an active element.")
        point = self._coordinate_only_point(params)
        if point is not None:
            self._perform_pointer_click(point, button="left", double=False, modifier_flags=None)
        self._send_macos_text(params.text, element=None)
        if point is not None:
            return self._passed({"point": point.model_dump(mode="json")})
        return self._passed()

    def _type_text_into_element(self, element: Any, params: MacOSTypeTextParams) -> dict[str, object]:
        session = self._require_session()
        pre_obs = self._inspect_obs_map(element, ("type", "id", "enabled", "displayed", "bounds"), stage="pre_entry_inspect")
        enabled_obs = pre_obs.get("enabled")
        displayed_obs = pre_obs.get("displayed")
        type_obs = pre_obs.get("type")
        elem_type = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) else None
        short_type = elem_type.removeprefix("XCUIElementType") if isinstance(elem_type, str) else ""
        bounds_tuple = self._bounds_int_tuple(pre_obs.get("bounds"))
        is_displayed = getattr(displayed_obs, "state", None) == "value" and displayed_obs.value is True
        is_enabled = getattr(enabled_obs, "state", None) == "value" and enabled_obs.value is True
        tolerates_disabled_editor = (
            is_displayed and getattr(enabled_obs, "state", None) == "value" and enabled_obs.value is False and short_type in MACOS_EDITABLE_DISABLED_TOLERANT_TYPES and bounds_tuple is not None
        )
        if not is_displayed or (not is_enabled and not tolerates_disabled_editor):
            return self._failed(
                "target_resolution_error",
                "Input target is not enabled and visible.",
                metadata={"reason": "invalid_input_target", "resolution_reason": "invalid_input_target"},
            )

        is_secure = self._is_secure_text_type(elem_type)
        id_obs = pre_obs.get("id")
        elem_id = id_obs.value if getattr(id_obs, "state", None) == "value" and isinstance(id_obs.value, str) and id_obs.value.strip() else None
        initial_value: str | None = None
        if not is_secure:
            val_obs = self._inspect_obs_map(element, ("value",), stage="pre_entry_inspect").get("value")
            val_state = getattr(val_obs, "state", None)
            if val_state == "value" and isinstance(val_obs.value, str):
                if self._is_masked_string(val_obs.value):
                    return self._failed("action_error", "Input target value is masked and cannot be verified.", metadata={"reason": "verification_unavailable", "stage": "pre_entry_inspect"})
                initial_value = val_obs.value
            elif val_state == "missing":
                initial_value = ""
            else:
                return self._failed("action_error", "Input target value is unreadable before entry.", metadata={"reason": "verification_unavailable", "stage": "pre_entry_inspect"})

        bound_element = element
        bound_by_geometry_only = False
        text_dependent = isinstance(element, _NativeAXElement) or self._target_depends_on_mutable_text(params)
        if elem_id is not None:
            bound_candidate = self._bind_element_by_id_or_geometry(session, elem_id=elem_id, elem_type=elem_type, bounds_tuple=bounds_tuple)
            if bound_candidate is not None:
                bound_element = bound_candidate
            elif text_dependent:
                return self._failed(
                    "action_error",
                    "Could not bind a text-independent identity for macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
        elif text_dependent:
            if not elem_type or bounds_tuple is None:
                return self._failed(
                    "action_error",
                    "Could not bind a text-independent geometry anchor for macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
            bound_candidate = self._bind_element_by_geometry(session, elem_type=elem_type, bounds_tuple=bounds_tuple)
            if bound_candidate is None:
                return self._failed(
                    "action_error",
                    "Could not uniquely bind target geometry before macOS text entry.",
                    metadata={"reason": "verification_unavailable", "stage": "identity_binding"},
                )
            bound_element = bound_candidate
            bound_by_geometry_only = True

        if isinstance(element, _NativeAXElement) and not isinstance(bound_element, _NativeAXElement):
            return self._failed("action_error", "Native AX text target changed session modes.", metadata={"reason": "verification_unavailable"})
        if isinstance(element, _NativeAXElement) and isinstance(bound_element, _NativeAXElement):
            original = getattr(element._node, "handle", None)
            rebound = getattr(bound_element._node, "handle", None)
            if original is not None and (rebound is None or not original.backend.equal(original.element, rebound.element)):
                return self._failed("action_error", "Native AX text target changed during identity binding.", metadata={"reason": "verification_unavailable"})

        try:
            bound_element.click(
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
            if params.clear:
                bound_element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            bound_element.send_keys(
                params.text,
                wait_timeout_seconds=self._wait_timeout(),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="type_text")

        if is_secure:
            post_secure = self._verify_post_entry_target(
                bound_element,
                expected_type=elem_type,
                expected_id=elem_id,
                expected_bounds=bounds_tuple if bound_by_geometry_only else None,
                include_value=False,
            )
            if post_secure is None:
                return self._failed(
                    "action_error",
                    "Secure text was sent, but post-entry target identity could not be verified; inspect UI state before retrying.",
                    metadata={"reason": "verification_unavailable", "stage": "post_verification"},
                )
            return self._passed({"protected": True})

        post_check = self._verify_post_entry_target(
            bound_element,
            expected_type=elem_type,
            expected_id=elem_id,
            expected_bounds=bounds_tuple if bound_by_geometry_only else None,
            include_value=True,
        )
        if post_check is None:
            return self._failed(
                "action_error",
                "Text was sent, but target geometry or identity shifted during verification; inspect UI state before re-entering text.",
                metadata={"reason": "verification_unavailable", "stage": "post_verification"},
            )
        if self._value_matches(post_check, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()
        if isinstance(bound_element, _NativeAXElement):
            return self._failed("action_error", "Native AX text readback did not match; inspect UI state before retrying.", metadata={"reason": "postcondition_failed"})

        try:
            if params.clear:
                bound_element.clear(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            self._send_macos_text(params.text, element=bound_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="type_text_fallback")

        fallback_check = self._verify_post_entry_target(
            bound_element,
            expected_type=elem_type,
            expected_id=elem_id,
            expected_bounds=bounds_tuple if bound_by_geometry_only else None,
            include_value=True,
        )
        if fallback_check is None:
            return self._failed(
                "action_error",
                "Text fallback was sent, but target geometry or identity shifted during verification; inspect UI state before re-entering text.",
                metadata={"reason": "verification_unavailable", "stage": "post_verification"},
            )
        if self._value_matches(fallback_check, params.text, clear=bool(params.clear), initial_value=initial_value):
            return self._passed()
        return self._failed(
            "action_error",
            "macOS text entry did not update the target element value after fallback.",
            metadata={"reason": "postcondition_failed", "stage": "post_verification"},
        )

    @_macos_driver_tool("pressKey", description="Send a keyboard shortcut or key to macOS.")
    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]:
        return self._run_tool(lambda: self._press_key_impl(params))

    def _verify_keyboard_foreground(self) -> None:
        self._verify_native_foreground()

    def _verify_native_foreground(self) -> int | None:
        if not self._native_ax_mode:
            return None
        if self._active_bundle_id is None:
            raise ToolExecutionError("The logical macOS application is unknown.")
        path = Path(self._session_app_path) if self._session_app_path is not None else None
        return _frontmost_pid(self._active_bundle_id, app_path=path, cancellation=self._operation_cancellation)

    def _press_key_impl(self, params: MacOSPressKeyParams) -> dict[str, object]:
        session = self._require_session()
        modifier_flags = self._modifier_flags(params.modifiers) or 0
        mapped_key = _normalize_macos_key_token(params.key)
        key_item = self._build_key_item(mapped_key, modifier_flags)
        try:
            self._verify_keyboard_foreground()
            session.device.send_keys([key_item], control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "InvalidRequestError":
                raise ConfigurationError(f"Unsupported macOS key: {params.key}.") from None
            return self._normalize_backend_exception(exc, stage="press_key")
        return self._passed({"key": params.key, "modifiers": params.modifiers or []})

    @_macos_driver_tool("hoverOn", description="Move the pointer over a macOS element or point.")
    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._hover_on_impl(params))

    def _hover_on_impl(self, params: MacOSHoverOnParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is not None:
            center = self._element_center(element)
            try:
                element.hover(
                    wait_timeout_seconds=self._wait_timeout(),
                    control=self._ActionControl(self),
                )
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                return self._normalize_backend_exception(exc, stage="hover_on")
            return self._passed({"point": center.model_dump(mode="json")} if center is not None else None)
        if self._has_semantic_target(params):
            return self._target_missing()
        point = self._coordinate_only_point(params)
        if point is None:
            return self._target_missing()
        self._perform_pointer_hover(point)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool(
        "scrollOn",
        description="Scroll on a resolved macOS element center or explicit point using Appium Mac2 wheel deltas.",
    )
    def scroll_on(self, params: MacOSScrollOnParams) -> dict[str, object]:
        return self._run_tool(lambda: self._scroll_on_impl(params))

    def _scroll_on_impl(self, params: MacOSScrollOnParams) -> dict[str, object]:
        original_session = self._require_session()
        modifier_flags = self._modifier_flags(params.modifiers)
        if self._has_semantic_target(params):
            element = self._resolve_element_or_none(params)
            if element is None:
                return self._target_missing()
            center = self._element_center(element)
            if center is None:
                return self._failed("action_error", "Resolved macOS scroll target has no usable bounds.", metadata={"stage": "scroll_on"})
        else:
            center = self._coordinate_only_point(params)
            if center is None:
                return self._target_missing()
        try:
            session = self._require_session()
            if session is not original_session and self._has_semantic_target(params):
                return self._failed("action_error", "The macOS session changed during scroll target resolution.", metadata={"reason": "session_unavailable"})
            self._verify_native_foreground()
            session.device.scroll_at(
                center.x,
                center.y,
                delta_x=float(params.delta_x),
                delta_y=float(params.delta_y),
                key_modifier_flags=modifier_flags,
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return self._normalize_backend_exception(exc, stage="scroll_on")
        return self._passed(
            {
                "point": {"x": center.x, "y": center.y},
                "delta_x": params.delta_x,
                "delta_y": params.delta_y,
                "modifiers": params.modifiers or [],
            }
        )

    @_macos_driver_tool("dragTo", description="Drag from one macOS element or point to another.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        return self._run_tool(lambda: self._drag_to_impl(params))

    def _drag_to_impl(self, params: MacOSDragToParams) -> dict[str, object]:
        source_element = self._resolve_element_or_none(params.source)
        destination_element = self._resolve_element_or_none(params.destination)
        if isinstance(source_element, _NativeAXElement) and isinstance(destination_element, _NativeAXElement) and source_element._bundle_id != destination_element._bundle_id:
            raise ToolExecutionError("Foreground application changed between native AX drag endpoints.")
        source = self._point_from_endpoint(params.source, source_element)
        destination = self._point_from_endpoint(params.destination, destination_element)
        if source is None or destination is None:
            return self._failed(
                "target_resolution_error",
                "Drag source or destination was not found.",
                metadata={"resolution_reason": "not_found", **self._safe_diagnostics(stage="drag_to")},
            )
        for element, point in ((source_element, source), (destination_element, destination)):
            if isinstance(element, _NativeAXElement) and not _verify_ax_point_hit(getattr(element._node, "handle", None), point.x, point.y):
                raise ToolExecutionError("Native AX drag endpoint no longer passes hit testing.")
        self._perform_pointer_drag(source, destination, duration_ms=params.duration_ms)
        return self._passed({"source": source.model_dump(mode="json"), "destination": destination.model_dump(mode="json")})

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot through Appium Mac2.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes | dict[str, object]:
        try:
            return self.screenshot(params)
        except ToolExecutionError as exc:
            diagnostics = exc.context.get("capture_diagnostics")
            metadata = dict(diagnostics) if isinstance(diagnostics, dict) else self._safe_diagnostics(exc, stage="take_screenshot")
            return self._failed("observation_error", "Appium Mac2 screenshot capture failed.", metadata=metadata)

    @_macos_driver_tool(
        "uiSnapshot",
        description=("Read macOS accessibility evidence as a bounded compact control tree with explicit clipping markers. No-match is not proof of invisibility."),
    )
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        return self._run_tool(lambda: self._ui_snapshot_impl(params), failure_default="observation_error")

    def _ui_snapshot_impl(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        session = self._require_session()
        window_scoped_fallback = False
        observation_source = "appium_connect_agent_tree_observation"
        observation: Any | None = None
        if self._native_ax_mode:
            try:
                observation = self._observe_native_ax_tree_or_none(max_depth=max(20, (params.max_depth or self.page_source_max_depth) + 4))
            except _NativeForegroundError:
                if (diagnosis := self._native_snapshot_rejection()) is not None:
                    return diagnosis
                raise
            if observation is None:
                if (diagnosis := self._native_snapshot_rejection()) is not None:
                    return diagnosis
                raise ToolExecutionError("No verifiable accessibility evidence for the logical macOS application.", context={"stage": "ui_snapshot"})
            observation_source = "macos_native_ax"
        if observation is None:
            session = self._require_session()
            try:
                observation = session.observe_tree(control=self._ActionControl(self))
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                if getattr(exc, "code", None) == "observation_too_large":
                    fallback_obs = self._observe_frontmost_window_tree(session)
                    if fallback_obs is not None:
                        observation = fallback_obs
                        window_scoped_fallback = True
                    else:
                        self._raise_resolution_error(exc)
                else:
                    self._raise_resolution_error(exc)
        max_depth = params.max_depth or self.page_source_max_depth
        include_attributes = bool(params.include_attributes)
        page_source, compaction_meta = self._simplify_tree_observation_with_budget(
            observation,
            max_depth=max_depth,
            include_attributes=include_attributes,
        )
        diagnostics: dict[str, object] = {
            "source": observation_source,
            "coverage": "frontmost_window_fallback" if window_scoped_fallback else getattr(observation, "coverage", "requested_scope"),
            "complete_for_requested_scope": False if window_scoped_fallback else bool(getattr(observation, "complete_for_requested_scope", True)),
            "atomic_with_screenshot": False,
            "absence_proves_invisibility": False,
        }
        if window_scoped_fallback:
            diagnostics["window_scoped_fallback"] = True
        if compaction_meta:
            diagnostics.update(compaction_meta)
        return {
            "snapshot_type": "macos_accessibility_tree",
            "page_source": page_source,
            "max_depth": max_depth,
            "include_attributes": include_attributes,
            "diagnostics": diagnostics,
        }

    def _native_snapshot_rejection(self) -> dict[str, object] | None:
        target = self._active_bundle_id
        if target is None or sys.platform != "darwin":
            return None
        deadline = time.monotonic() + 2.5
        cancellation = self._operation_cancellation
        try:
            bundle, pid, _ = _foreground_identity(deadline=deadline, cancellation=cancellation)
        except _NativeForegroundError:
            return None
        metadata = self._safe_diagnostics(stage="ui_snapshot")
        path = Path(self._session_app_path) if self._session_app_path is not None else None
        if _permission_modal_for_target(target, path, deadline=deadline, cancellation=cancellation, foreground=(bundle, pid)):
            return self._failed(
                "observation_error",
                "A macOS system permission dialog for the logical application requires user attention.",
                metadata={**metadata, "backend_code": "permission_modal"},
            )
        if bundle != target and MACOS_BUNDLE_ID_SYNTAX.fullmatch(bundle) and MACOS_BUNDLE_ID_SYNTAX.fullmatch(target):
            return self._failed(
                "observation_error",
                "The foreground macOS application does not match the logical snapshot target.",
                metadata={**metadata, "backend_code": "foreground_mismatch", "logical_bundle_id": target, "foreground_bundle_id": bundle},
            )
        return None

    def _observe_frontmost_window_tree(self, session: Any) -> Any | None:
        element_fn = getattr(session, "element", None)
        observe_fn = getattr(session, "observe_tree", None)
        if not callable(element_fn) or not callable(observe_fn):
            return None
        try:
            window_el = element_fn("class name", "XCUIElementTypeWindow")
            return observe_fn(parent=window_el, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return None

    @_macos_driver_tool(
        "inspectElement",
        description="Inspect one uniquely resolved macOS element and return unclipped attributes with SecureTextField protection.",
    )
    def inspect_element(self, params: MacOSInspectElementParams) -> dict[str, object]:
        return self._run_tool(lambda: self._inspect_element_impl(params), failure_default="observation_error")

    def _inspect_element_impl(self, params: MacOSInspectElementParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()

        requested = tuple(params.attributes)
        needs_sensitive_gate = "text" in requested or "value" in requested
        attributes_out: dict[str, dict[str, object]] = {}

        if needs_sensitive_gate:
            type_obs = self._inspect_obs_map(element, ("type",), stage="inspect_element").get("type")
            type_state = getattr(type_obs, "state", "unknown")
            type_val = type_obs.value if type_state == "value" and isinstance(type_obs.value, str) else None
            safe_non_secure = type_state == "value" and bool(type_val) and not self._is_secure_text_type(type_val)
            if "type" in requested:
                attributes_out["type"] = self._serialize_attribute_observation("type", type_obs)
            remaining = tuple(attr for attr in requested if attr != "type" and (safe_non_secure or attr not in {"text", "value"}))
            if remaining:
                inspected_map = self._inspect_obs_map(element, remaining, stage="inspect_element")
                for attr in remaining:
                    attributes_out[attr] = self._serialize_attribute_observation(attr, inspected_map.get(attr))
            if not safe_non_secure:
                for protected_attr in ("text", "value"):
                    if protected_attr in requested:
                        attributes_out[protected_attr] = {"state": "unavailable", "value": None, "reason": "protected_text"}
        else:
            inspected_map = self._inspect_obs_map(element, requested, stage="inspect_element")
            for attr in requested:
                attributes_out[attr] = self._serialize_attribute_observation(attr, inspected_map.get(attr))

        ordered = {attr: attributes_out[attr] for attr in requested}
        return self._passed({"attributes": ordered})

    @_macos_driver_tool(
        "inspectWithAI",
        description="Extract or locate visible content in one current macOS frame. Read-only; locate requires SDK-supported display mapping.",
    )
    def inspect_with_ai(self, params: MacOSInspectWithAIParams) -> dict[str, object]:
        if not isinstance(params, MacOSInspectWithAIParams):
            params = MacOSInspectWithAIParams.model_validate(params)
        return _run_visual_inspection(self, params, platform="macos")

    @_macos_driver_tool("sampleColor", description="Measure exact local RGB from a current frame point or region without uploading an image.")
    def sample_color(self, params: SampleColorParams) -> dict[str, object]:
        return self._run_tool(lambda: self._passed(_sample_color(self._require_session().device, params, control=self._ActionControl(self))), failure_default="observation_error")

    @_macos_driver_tool("assertVisible", description="Assert that a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_visible_impl(params))

    def _assert_visible_impl(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing()
        if self._element_displayed(element):
            return self._passed()
        return self._failed("assertion_error", "Target is present but not visible.")

    @_macos_driver_tool("assertNotVisible", description="Assert that a macOS element is absent or not displayed.")
    def assert_not_visible(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_not_visible_impl(params))

    def _assert_not_visible_impl(self, params: MacOSAssertNotVisibleParams) -> dict[str, object]:
        locator = self._locator_from_params(params)
        deadline = time.monotonic() + self._wait_timeout()
        last_ambiguous: _AmbiguousResolutionError | None = None
        while True:
            try:
                element = self._element_from_locator(locator)
            except _AmbiguousResolutionError as amb:
                last_ambiguous = amb
                if time.monotonic() >= deadline:
                    return self._target_ambiguous_result(amb)
                time.sleep(0.25)
                continue
            if element is None:
                return self._passed({"visible": False})
            last_ambiguous = None
            try:
                displayed_obs = self._inspect_obs_map(element, ("displayed",), stage="assert_not_visible").get("displayed")
            except ToolExecutionError as exc:
                cause = exc.context.get("cause")
                if isinstance(cause, Exception) and type(cause).__name__ == "TargetError" and self._target_error_is_definitive(cause):
                    cause_code = getattr(cause, "code", None)
                    if cause_code == "target_not_found":
                        return self._passed({"visible": False})
                    if cause_code == "target_ambiguous":
                        session = self._require_session()
                        last_ambiguous = _AmbiguousResolutionError(self._collect_ambiguous_candidates(session, locator), cause=cause)
                        if time.monotonic() >= deadline:
                            return self._target_ambiguous_result(last_ambiguous)
                        time.sleep(0.25)
                        continue
                raise
            if getattr(displayed_obs, "state", None) == "value" and displayed_obs.value is False:
                return self._passed({"visible": False})
            if time.monotonic() >= deadline:
                if getattr(displayed_obs, "state", None) != "value":
                    return self._failed("observation_error", "Target visibility could not be established.")
                return self._failed("assertion_error", "Target is visible.", output={"visible": True})
            time.sleep(0.25)
        if last_ambiguous is not None:
            return self._target_ambiguous_result(last_ambiguous)
        return self._failed("assertion_error", "Target is visible.", output={"visible": True})

    @_macos_driver_tool("assert", description="Assert existence, text, or boolean state on one uniquely located macOS element.")
    def assert_state(self, params: MacOSAssertStateParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_state_impl(params))

    def _assert_state_impl(self, params: MacOSAssertStateParams) -> dict[str, object]:
        locator = params.element.to_locator()
        element = self._poll_unique_element(locator)
        if element is None:
            return self._target_missing()

        if params.text is not None:
            return self._evaluate_text_assertion(element, params.text)

        expected_states = {field: getattr(params.element, field) for field in MACOS_STATE_ASSERTION_FIELDS if type(getattr(params.element, field)) is bool}
        if expected_states:
            ordered_fields = tuple(field for field in MACOS_STATE_ASSERTION_FIELDS if field in expected_states)
            observed_map = self._inspect_obs_map(element, ordered_fields, stage="assert_state")
            actual_states: dict[str, object] = {}
            mismatches: dict[str, dict[str, object]] = {}
            for field in ordered_fields:
                obs = observed_map.get(field)
                actual = obs.value if getattr(obs, "state", None) == "value" and type(obs.value) is bool else None
                actual_states[field] = actual
                expected = expected_states[field]
                if actual is not expected:
                    mismatches[field] = {"expected": expected, "actual": actual, "state": getattr(obs, "state", "unknown")}
            if mismatches:
                return self._failed(
                    "assertion_error",
                    "macOS element state did not match expected boolean values.",
                    output={"states": actual_states, "mismatches": mismatches},
                )
            return self._passed({"states": actual_states})

        self._inspect_obs_map(element, ("enabled",), stage="assert_state")
        return self._passed({"exists": True})

    @_macos_driver_tool("assertElementsOrder", description="Assert that macOS elements appear in the expected visual order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        return self._run_tool(lambda: self._assert_elements_order_impl(params))

    def _assert_elements_order_impl(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        missing: list[int] = []
        for index, element_ref in enumerate(params.elements):
            element = self._resolve_element_or_none(element_ref)
            if element is None:
                missing.append(index)
                continue
            rect = self._element_rect(element)
            if rect is None:
                missing.append(index)
                continue
            entries.append(
                {
                    "index": index,
                    "coordinate": self._order_coordinate(rect, params.direction),
                    "rect": rect,
                }
            )
        actual_order = [int(entry["index"]) for entry in sorted(entries, key=lambda entry: float(entry["coordinate"]))]
        expected_order = params.expected_order or list(range(len(params.elements)))
        if not params.require_all:
            expected_order = [index for index in expected_order if index in actual_order]
        output = {
            "direction": params.direction,
            "elements_found": len(entries),
            "elements_total": len(params.elements),
            "actual_order": actual_order,
            "expected_order": expected_order,
            "positions": entries,
        }
        if missing and params.require_all:
            return self._failed(
                "target_resolution_error",
                "One or more ordered elements were not found.",
                output=output,
                metadata={"missing_indexes": missing},
            )
        if self._order_matches(entries, expected_order, params.tolerance or 0.0):
            return self._passed(output)
        return self._failed(
            "assertion_error",
            "Elements are not in the expected order.",
            output=output,
        )

    @_macos_driver_tool("assertWithAI", description="Evaluate a scoped macOS image-space visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        return self._capture_screenshot(params, allow_native_fallback=True)

    def capture_visual_screenshot(self, params: object | None = None) -> bytes:
        return self._capture_screenshot(params, allow_native_fallback=False)

    def _capture_screenshot(self, params: object | None, *, allow_native_fallback: bool) -> bytes:
        cancellation_check = None
        timeout_seconds: float | None = None
        if isinstance(params, dict):
            raw_check = params.get("cancellation_check")
            if callable(raw_check):
                cancellation_check = raw_check
            raw_timeout = params.get("timeout_seconds")
            if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool) and raw_timeout > 0:
                timeout_seconds = float(raw_timeout)
        cancellation_event = Event() if cancellation_check is not None else None
        failures: list[BaseException] = []
        backend_errors: list[BaseException] = []

        def check_on_caller() -> None:
            if cancellation_check is not None:
                try:
                    cancellation_check()
                except BaseException as err:
                    failures.append(err)
                    raise

        def run_capture() -> bytes:
            try:
                return self._screenshot_impl(timeout_seconds=timeout_seconds, allow_native_fallback=allow_native_fallback)
            except BaseException as err:
                backend_errors.append(err)
                raise

        try:
            data = self._run_on_worker(
                run_capture,
                cancellation_check=check_on_caller if cancellation_check is not None else None,
                cancellation_event=cancellation_event,
            )
            if cancellation_check is not None:
                check_on_caller()
        except BaseException as error:
            if cancellation_check is not None and not failures:
                with suppress(BaseException):
                    check_on_caller()
            if isinstance(error, ConfigurationError):
                raise
            diag_source = backend_errors[0] if backend_errors and isinstance(backend_errors[0], Exception) else (error if isinstance(error, Exception) else None)
            self._note_operation_timeout(diag_source)
            diagnostics = {key: value for key, value in self._safe_diagnostics(diag_source, stage="screenshot").items() if key in {"backend_code", "request_state", "session_state", "stage", "reason"}}
            if failures:
                interrupted = failures[0]
                interrupted.fsq_capture_diagnostics = diagnostics  # type: ignore[attr-defined]
                raise interrupted from None
            if isinstance(error, CancelledError) or type(error).__name__ == "OperationCancelledError" or getattr(error, "code", None) == "operation_cancelled":
                cancelled = CancelledError()
                cancelled.fsq_capture_diagnostics = diagnostics  # type: ignore[attr-defined]
                raise cancelled from None
            if isinstance(error, ToolExecutionError) and "capture_diagnostics" in error.context:
                raise
            if not isinstance(error, Exception):
                raise
            raise ToolExecutionError(
                "Appium Mac2 screenshot capture failed.",
                context={"capture_diagnostics": diagnostics},
            ) from None
        return data

    def _screenshot_impl(self, *, timeout_seconds: float | None = None, allow_native_fallback: bool = True) -> bytes:
        session = self._require_session()
        budget = timeout_seconds if timeout_seconds is not None else max(5.0, float(self.action_timeout_seconds) + 5.0)
        fallback_allowed = allow_native_fallback and self._can_use_native_screencapture_fallback(timeout_seconds=timeout_seconds)
        deadline = time.monotonic() + budget
        control = self._operation_control(budget / 2 if fallback_allowed else budget, cancellation=self._operation_cancellation)
        try:
            data = session.device.take_screenshot(control=control)
            if isinstance(data, bytes) and data:
                return data
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            if fallback_allowed:
                fallback_png = self._capture_native_macos_screenshot(timeout_seconds=deadline - time.monotonic())
                if fallback_png is not None:
                    if self._session_state(session) == "uncertain" and getattr(exc, "code", None) == "operation_timeout":
                        self._uncertain_from_timeout = True
                    return fallback_png
            raise
        if fallback_allowed:
            fallback_png = self._capture_native_macos_screenshot(timeout_seconds=deadline - time.monotonic())
            if fallback_png is not None:
                return fallback_png
        raise ToolExecutionError(
            "Appium Mac2 screenshot capture returned no image.",
            context={"capture_diagnostics": self._safe_diagnostics(stage="screenshot")},
        )

    def _can_use_native_screencapture_fallback(self, *, timeout_seconds: float | None) -> bool:
        if not self._native_ax_mode or self._borrowed_session or sys.platform != "darwin":
            return False
        return timeout_seconds is None or timeout_seconds >= 1.0

    def _capture_native_macos_screenshot(self, *, timeout_seconds: float) -> bytes | None:
        screencapture_exe = "/usr/sbin/screencapture"
        if timeout_seconds <= 0 or (self._operation_cancellation is not None and self._operation_cancellation.is_set()):
            return None
        effective_timeout = min(timeout_seconds, 15.0)
        fd, tmp_name = tempfile.mkstemp(prefix="fsq-macos-shot-", suffix=".png")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            proc = subprocess.run(  # noqa: S603 - fixed system executable without shell.
                [str(screencapture_exe), "-x", "-t", "png", str(tmp_path)],
                capture_output=True,
                timeout=effective_timeout,
                check=False,
            )
            if proc.returncode != 0 or not tmp_path.is_file():
                return None
            payload = tmp_path.read_bytes()
        except (OSError, subprocess.TimeoutExpired):
            return None
        else:
            return payload if len(payload) > 8 and payload.startswith(b"\x89PNG\r\n\x1a\n") else None
        finally:
            with suppress(OSError):
                tmp_path.unlink()

    def _ensure_session(self, params: MacOSLaunchAppParams | None = None) -> Any:
        if self._closed:
            raise ToolExecutionError("Appium Mac2 driver is closed.", context={"reason": "closed"})
        params = params or MacOSLaunchAppParams()
        if self._cleanup_pending and not params.new_session:
            raise ToolExecutionError(
                "Appium Mac2 session requires cleanup before reuse.",
                context={"reason": "cleanup_pending", **self._safe_diagnostics(stage="ensure_session")},
            )
        if self._session is not None and not self._session_is_active(self._session):
            if not params.new_session:
                if not self._try_recover_uncertain_owned_session():
                    if not self._borrowed_session and not self._recoverable_session_loss:
                        self._cleanup_pending = True
                    raise ToolExecutionError(
                        "Appium Mac2 session is no longer active.",
                        context={"reason": "session_unavailable", **self._safe_diagnostics(stage="ensure_session")},
                    )
            else:
                self._require_creation_identity(params, replacing_borrowed=self._borrowed_session)
                if self._borrowed_session:
                    self._session = None
                else:
                    self._close_owned_resources_for_recreation()

        if self._session is None:
            if self._cleanup_pending:
                self._close_owned_resources_for_recreation()
            if self._recoverable_session_loss and not params.new_session and not params.arguments and self._resolve_activation_identity(params) == (self._active_bundle_id, self._session_app_path):
                return self._require_session()
            return self._create_session(params)
        has_launch_arguments = bool(params.arguments)
        if not params.new_session and not has_launch_arguments and not self._borrowed_session:
            requested_bundle, requested_path = self._resolve_activation_identity(params)
            mode_changed = (requested_bundle != self._active_bundle_id or self._native_ax_mode) and (
                self._can_use_finder_anchored_native_ax(effective_arguments=None, inferred_target_bundle=requested_bundle) != self._native_ax_mode
            )
            bound_path = self._session_app_path
            if self._native_ax_mode and not mode_changed and requested_bundle is not None and requested_bundle == self._active_bundle_id and requested_path is not None and bound_path is None:
                bound_path = str(_running_macos_app_path(requested_bundle, cancellation=self._operation_cancellation))
            installation_changed = requested_bundle is not None and requested_path is not None and requested_path != bound_path
            target_changed_with_kill_enabled = not self.skip_app_kill and requested_bundle is not None and requested_bundle != self._active_bundle_id
            replace = mode_changed or installation_changed or target_changed_with_kill_enabled
            if replace or (self._native_ax_mode and requested_path is not None and not self._probe_session_healthy_for_same_bundle_reuse(self._session)):
                params = params.model_copy(update={"new_session": True, "bundle_id": requested_bundle, "app_path": requested_path})
        if params.new_session:
            target_bundle, target_app_path = self._require_creation_identity(params, replacing_borrowed=self._borrowed_session)
            if (
                self._native_ax_mode
                and not self._borrowed_session
                and not has_launch_arguments
                and target_app_path is None
                and target_bundle is not None
                and self._can_use_finder_anchored_native_ax(effective_arguments=None, inferred_target_bundle=target_bundle)
                and self._probe_session_healthy_for_same_bundle_reuse(self._session)
            ):
                self._activate_bundle_in_session(target_bundle)
                return self._session
            if (
                not has_launch_arguments
                and target_bundle is not None
                and self._active_bundle_id == target_bundle
                and not self._native_ax_mode
                and (target_app_path is None or target_app_path == self._session_app_path)
                and self._probe_session_healthy_for_same_bundle_reuse(self._session)
            ):
                self._activate_bundle_in_session(target_bundle, app_path=target_app_path)
                return self._session
            if self._borrowed_session:
                quit_fn = getattr(self._session, "quit", None)
                if callable(quit_fn):
                    quit_fn()
                self._session = None
            else:
                self._close_owned_resources_for_recreation()
            self._pending_creation_identity = (target_bundle, target_app_path)
            try:
                return self._create_session(params)
            finally:
                self._pending_creation_identity = None
        if has_launch_arguments:
            raise ConfigurationError("arguments require new_session=true when a Mac2 session already exists.")
        bundle_id, app_path = self._resolve_activation_identity(params)
        self._ensure_alive_if_idle(self._session)
        activate_timeout = max(DEFAULT_MACOS_ACTIVATE_TIMEOUT_SECONDS, float(self.action_timeout_seconds) + 5.0)
        if bundle_id is not None:
            self._activate_bundle_in_session(bundle_id, app_path=app_path, timeout_seconds=activate_timeout)
        elif app_path is not None:
            self._activate_unbundled_app_path(app_path, timeout_seconds=activate_timeout)
        return self._session

    def _probe_session_healthy_for_same_bundle_reuse(self, session: Any) -> bool:
        if session is None or self._cleanup_pending or not self._session_is_active(session):
            return False
        if self._borrowed_session:
            return True
        ensure_alive = getattr(session, "ensure_alive", None)
        if not callable(ensure_alive):
            return True
        try:
            ensure_alive(control=self._operation_control(DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation))
            self._last_operation_finished_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._note_operation_timeout(exc)
            return False
        return self._session_is_active(session)

    def _activate_bundle_in_session(self, bundle_id: str, *, app_path: str | None = None, timeout_seconds: float | None = None) -> None:
        effective_timeout = timeout_seconds if timeout_seconds is not None else max(DEFAULT_MACOS_ACTIVATE_TIMEOUT_SECONDS, float(self.action_timeout_seconds) + 5.0)
        if self._native_ax_mode or (app_path is not None and sys.platform == "darwin"):
            try:
                _activate_macos_app_native(
                    bundle_id,
                    app_path=Path(app_path) if app_path is not None else None,
                    timeout_seconds=effective_timeout,
                    cancellation=self._operation_cancellation,
                )
            except Exception as exc:
                raise ToolExecutionError("macOS native application activation failed.", context={"stage": "launch_app", "cause": exc}) from exc
            self._active_bundle_id = bundle_id
            self._session_app_path = app_path
            return
        try:
            self._session.device.activate_app(
                bundle_id,
                control=self._operation_control(effective_timeout, cancellation=self._operation_cancellation),
            )
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            self._note_operation_timeout(exc)
            raise ToolExecutionError(
                "macOS application activation failed.",
                context={"stage": "launch_app", "cause": exc},
            ) from exc
        self._active_bundle_id = bundle_id
        self._session_app_path = app_path

    def _activate_unbundled_app_path(self, app_path: str, *, timeout_seconds: float) -> None:
        open_cmd = shutil.which("open") or "/usr/bin/open"
        try:
            proc = subprocess.run(  # noqa: S603 - explicit validated existing app_path passed without shell.
                [open_cmd, "-a", str(Path(app_path).expanduser())],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(
                "macOS unbundled app_path activation timed out.",
                context={"stage": "launch_app", "reason": "operation_timeout"},
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(
                "macOS unbundled app_path activation failed.",
                context={"stage": "launch_app", "cause": exc},
            ) from exc
        if proc.returncode != 0:
            raise ToolExecutionError(
                "macOS unbundled app_path activation failed via open -a.",
                context={"stage": "launch_app", "returncode": proc.returncode},
            )
        self._session_app_path = app_path

    @staticmethod
    def _session_state(session: Any) -> str:
        info_fn = getattr(session, "session_info", None)
        if not callable(info_fn):
            return "active"
        state = getattr(info_fn(), "state", "active")
        return state if isinstance(state, str) else "active"

    @classmethod
    def _session_is_active(cls, session: Any) -> bool:
        return cls._session_state(session) == "active"

    def _sdk_supports_skip_app_kill(self) -> bool:
        try:
            from appium_connect_agent.automation import create_mac2_session
        except ImportError:
            return False
        parameters = inspect.signature(create_mac2_session).parameters
        return "skip_app_kill" in parameters or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())

    def _can_use_finder_anchored_native_ax(self, *, effective_arguments: list[str] | None, inferred_target_bundle: str | None) -> bool:
        if not self.skip_app_kill or self._borrowed_session or sys.platform != "darwin" or effective_arguments is not None or inferred_target_bundle not in self._native_ax_fallback_bundles:
            return False
        return _macos_ax_process_trusted()

    def _note_operation_timeout(self, exc: BaseException | None) -> None:
        if self._borrowed_session or self._session is None or exc is None:
            return
        code = getattr(exc, "code", None)
        cause = exc.context.get("cause") if isinstance(exc, ToolExecutionError) else None
        if code is None and isinstance(cause, BaseException):
            code = getattr(cause, "code", None)
        if code == "operation_timeout" and self._session_state(self._session) == "uncertain":
            self._uncertain_from_timeout = True

    def _owned_resources_cleanly_released(self) -> bool:
        return self._session is None and self._managed_scope is None and self._runtime is None and self._connection is None and not self._cleanup_pending

    def _try_recover_session_from_saved_identity(self) -> bool:
        recovery_bundle = self._active_bundle_id or (None if self.bundle_id in {None, "auto"} else self.bundle_id)
        recovery_app_path = self._session_app_path
        if recovery_bundle is None and recovery_app_path is None:
            return False
        try:
            if self._session is not None or self._managed_scope is not None or self._runtime is not None or self._connection is not None:
                self._close_owned_resources_for_recreation()
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._cleanup_pending = True
            self._recoverable_session_loss = False
            return False
        try:
            self._create_session(MacOSLaunchAppParams(bundle_id=recovery_bundle, app_path=recovery_app_path, new_session=True), recovering=True)
        except Exception as exc:  # noqa: BLE001
            if self.skip_app_kill and self._owned_resources_cleanly_released():
                self._recoverable_session_loss = True
                self._cleanup_pending = False
            else:
                self._cleanup_pending = True
                self._recoverable_session_loss = False
            self._propagate_if_cancelled(exc)
            return False
        if self._session is not None and self._session_is_active(self._session):
            self._recoverable_session_loss = False
            self._last_operation_finished_at = time.monotonic()
            return True
        return False

    def _try_recover_uncertain_owned_session(self) -> bool:
        session = self._session
        if self._borrowed_session or self._cleanup_pending or session is None or not self._uncertain_from_timeout or self._session_state(session) != "uncertain":
            return False
        ensure_alive = getattr(session, "ensure_alive", None)
        if callable(ensure_alive):
            try:
                ensure_alive(control=self._operation_control(DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation))
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
            else:
                if self._session_is_active(session):
                    self._uncertain_from_timeout = False
                    return True
        if not self.skip_app_kill or not self._sdk_supports_skip_app_kill():
            return False
        if self._runtime is None and self._managed_scope is None and self._connection is None:
            return False
        return self._try_recover_session_from_saved_identity()

    def _create_session(self, params: MacOSLaunchAppParams, *, recovering: bool = False) -> Any:
        pending_identity = getattr(self, "_pending_creation_identity", None)
        bundle_id, app_path = pending_identity if pending_identity is not None else self._require_creation_identity(params)
        resolved_app_path = Path(app_path) if app_path is not None else None
        try:
            from appium_connect_agent.automation import create_mac2_session
            from appium_connect_agent.contracts import LocalSettings
            from appium_connect_agent.runtime import OperationControl, Runtime, ServiceLifecycleError
        except ImportError as exc:
            raise ConfigurationError(
                "appium-connect-agent is required for AppiumConnectMacOSDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc

        effective_new_command_timeout = 0.0 if self.new_command_timeout_seconds is None else float(self.new_command_timeout_seconds)
        if isinstance(self.new_command_timeout_seconds, bool) or not (0.0 <= effective_new_command_timeout <= 86_400.0):
            raise ConfigurationError("macOS new_command_timeout_seconds must be between 0 and 86400 seconds or None.")
        if type(self.skip_app_kill) is not bool:
            raise ConfigurationError("macOS skip_app_kill must be a boolean.")
        effective_arguments = params.arguments if params.arguments else None
        had_startup_arguments = bool(effective_arguments) or (recovering and self._session_had_startup_arguments)
        use_finder_anchored_ax = not had_startup_arguments and self._can_use_finder_anchored_native_ax(
            effective_arguments=effective_arguments,
            inferred_target_bundle=bundle_id,
        )

        session_kwargs: dict[str, Any] = {}
        parameters = inspect.signature(create_mac2_session).parameters
        has_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
        supports_skip_app_kill = "skip_app_kill" in parameters or has_var_kw
        if supports_skip_app_kill:
            session_kwargs["skip_app_kill"] = self.skip_app_kill
        elif self.skip_app_kill is False:
            raise ConfigurationError("Installed appium-connect-agent does not support macOS skip_app_kill=False; upgrade appium-connect-agent.")

        local_settings = _resolve_managed_local_settings(LocalSettings)
        runtime = Runtime(local_settings)
        self._borrowed_session = False
        self._runtime = runtime
        scope: AbstractContextManager[Any] | None = None
        connection: Any | None = None
        last_port_error: BaseException | None = None
        for _ in range(MAX_PORT_BIND_ATTEMPTS):
            port = _allocate_loopback_port()
            candidate_scope = runtime.start_managed(
                host="127.0.0.1",
                port=port,
                base_path="",
                control=OperationControl(timeout_seconds=DEFAULT_MANAGED_STARTUP_TIMEOUT_SECONDS, cancellation=self._operation_cancellation),
            )
            self._managed_scope = candidate_scope
            self._scope_exit_attempted = False
            try:
                connection = candidate_scope.__enter__()
                scope = candidate_scope
                self._connection = connection
                break
            except BaseException as exc:
                self._cleanup_pending = True
                self._scope_exit_attempted = True
                if isinstance(exc, ServiceLifecycleError) and exc.code == "service_port_in_use":
                    try:
                        runtime.cleanup_managed(control=self._operation_control(DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                    except BaseException as cleanup_error:  # noqa: BLE001 - preserve startup and cleanup failures.
                        raise BaseExceptionGroup("Managed Appium startup and cleanup failed.", [exc, cleanup_error]) from None
                    self._managed_scope = None
                    self._scope_exit_attempted = False
                    self._cleanup_pending = False
                    last_port_error = exc
                    continue
                if not runtime.managed_cleanup_pending:
                    self._managed_scope = None
                    self._runtime = None
                    self._scope_exit_attempted = False
                    self._cleanup_pending = False
                raise

        if scope is None or connection is None:
            self._runtime = None
            if last_port_error is not None:
                raise last_port_error
            raise ConfigurationError("Failed to bind a managed Appium loopback port.")

        wda_bundle_id = MACOS_FALLBACK_FRONTMOST_BUNDLE if use_finder_anchored_ax else (bundle_id if resolved_app_path is None else None)
        wda_app_path = None if use_finder_anchored_ax else resolved_app_path
        if not use_finder_anchored_ax and resolved_app_path is None and effective_arguments is None and bundle_id is not None and _is_macos_bundle_running(bundle_id):
            _preactivate_running_macos_bundle(bundle_id)
        try:
            session = create_mac2_session(
                connection,
                bundle_id=wda_bundle_id,
                app_path=wda_app_path,
                arguments=effective_arguments,
                no_reset=True,
                new_command_timeout_seconds=effective_new_command_timeout,
                control=OperationControl(timeout_seconds=DEFAULT_SESSION_CREATE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation),
                **session_kwargs,
            )
        except BaseException as exc:
            uncertain_session = getattr(exc, "session", None)
            if uncertain_session is not None:
                self._borrowed_session = False
                self._runtime = runtime
                self._managed_scope = scope
                self._connection = connection
                self._session = uncertain_session
                self._uncertain_from_timeout = False
                self._recoverable_session_loss = False
                self._cleanup_pending = True
                raise
            try:
                self._scope_exit_attempted = True
                scope.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException as cleanup_exc:
                self._cleanup_pending = True
                logging.getLogger(__name__).warning(
                    "Managed Appium scope cleanup failed after Mac2 session creation error (%s)",
                    type(cleanup_exc).__name__,
                )
                raise
            if bool(getattr(runtime, "managed_cleanup_pending", False)):
                try:
                    runtime.cleanup_managed(control=OperationControl(timeout_seconds=DEFAULT_SESSION_QUIT_TIMEOUT_SECONDS))
                except BaseException as cleanup_exc:
                    self._cleanup_pending = True
                    logging.getLogger(__name__).debug("Secondary managed cleanup did not complete (%s)", type(cleanup_exc).__name__)
                    raise
            self._managed_scope = None
            self._connection = None
            self._runtime = None
            self._scope_exit_attempted = False
            self._cleanup_pending = False
            raise

        self._borrowed_session = False
        self._runtime = runtime
        self._managed_scope = scope
        self._connection = connection
        self._session = session
        self._wda_host_bundle_id = MACOS_FALLBACK_FRONTMOST_BUNDLE if use_finder_anchored_ax else bundle_id
        self._native_ax_mode = use_finder_anchored_ax
        self._active_bundle_id = bundle_id
        self._session_app_path = app_path
        self._session_had_startup_arguments = had_startup_arguments
        self._uncertain_from_timeout = False
        self._recoverable_session_loss = False
        self._session_settled = False
        self._scope_exit_attempted = False
        self._cleanup_primary = None
        self._cleanup_pending = False

        if bundle_id is not None and (use_finder_anchored_ax or (resolved_app_path is not None and effective_arguments is None and sys.platform == "darwin")):
            # Activate only after WDA initialization, which can foreground its anchor.
            try:
                self._activate_bundle_in_session(bundle_id, app_path=app_path)
            except BaseException as activation_error:
                try:
                    self._close_owned_resources_for_recreation()
                except BaseException as cleanup_error:  # noqa: BLE001 - preserve both teardown and activation failures, including cancellation.
                    raise BaseExceptionGroup("macOS target activation and owned-resource cleanup failed.", [activation_error, cleanup_error]) from None
                raise
        return session

    def _require_creation_identity(
        self,
        params: MacOSLaunchAppParams,
        *,
        replacing_borrowed: bool = False,
    ) -> tuple[str | None, str | None]:
        bundle_id, app_path = self._resolve_creation_identity(params, replacing_borrowed=replacing_borrowed)
        if app_path is not None:
            return self._validated_installation(app_path, expected_bundle=bundle_id)
        return bundle_id, None

    @staticmethod
    def _validated_installation(app_path: str, *, expected_bundle: str | None = None) -> tuple[str, str]:
        path = _resolve_app_bundle_path(app_path).resolve()
        bundle = _bundle_id_from_app_path(path)
        if bundle is None or MACOS_BUNDLE_ID_SYNTAX.fullmatch(bundle) is None:
            raise ConfigurationError("macOS app_path must resolve to an existing application bundle with a valid CFBundleIdentifier (bundle_id).")
        if expected_bundle is not None and expected_bundle != bundle:
            raise ConfigurationError("The supplied macOS bundle_id conflicts with the app_path CFBundleIdentifier.")
        return bundle, str(path)

    def _resolve_creation_identity(self, params: MacOSLaunchAppParams, *, replacing_borrowed: bool) -> tuple[str | None, str | None]:
        raw_param_bundle = params.bundle_id.strip() if isinstance(params.bundle_id, str) and params.bundle_id.strip() else None
        app_path = params.app_path if raw_param_bundle is not None else self._effective_app_path(params.app_path)
        if raw_param_bundle is not None:
            if raw_param_bundle.casefold() == "auto":
                return (None if app_path is not None else _detect_frontmost_macos_bundle_id()), app_path
            resolved_named = _resolve_installed_bundle_identifier(raw_param_bundle)
            if resolved_named is not None:
                return resolved_named, app_path or self._pinned_path_for_bundle(resolved_named)
            if MACOS_BUNDLE_ID_SYNTAX.fullmatch(raw_param_bundle) and "." in raw_param_bundle:
                return raw_param_bundle, app_path or self._pinned_path_for_bundle(raw_param_bundle)
            if MACOS_BUNDLE_ID_SYNTAX.fullmatch(raw_param_bundle) and app_path is None:
                return raw_param_bundle, None
            raise ConfigurationError(f"Could not resolve macOS application identity '{raw_param_bundle}' to a valid bundle_id.")

        if app_path is not None:
            configured_bundle = None if self.bundle_id in {None, "auto"} else self.bundle_id
            return configured_bundle, app_path

        if self.bundle_id == "auto":
            return _detect_frontmost_macos_bundle_id(), None
        if self.bundle_id is not None:
            return self.bundle_id, None
        if self._active_bundle_id is not None and not replacing_borrowed:
            return self._active_bundle_id, self._session_app_path
        if replacing_borrowed:
            raise ConfigurationError("macOS Appium Mac2 session replacement requires bundle_id or app_path.")
        return _detect_frontmost_macos_bundle_id(), None

    def _resolve_activation_identity(self, params: MacOSLaunchAppParams) -> tuple[str | None, str | None]:
        param_app_path = params.app_path.strip() if isinstance(params.app_path, str) and params.app_path.strip() else None
        param_bundle = params.bundle_id.strip() if isinstance(params.bundle_id, str) and params.bundle_id.strip() else None
        if param_app_path is not None:
            candidate_path = _resolve_app_bundle_path(param_app_path)
            if candidate_path.suffix.casefold() == ".app":
                expected = None if param_bundle is None or param_bundle == "auto" else (_resolve_installed_bundle_identifier(param_bundle) or param_bundle)
                return self._validated_installation(param_app_path, expected_bundle=expected)
            if candidate_path.exists() and param_bundle is None:
                return None, param_app_path
            raise ConfigurationError(
                "Could not resolve CFBundleIdentifier or existing application path from macOS app_path for in-session activation.",
                context={"app_path": param_app_path},
            )
        if param_bundle is not None:
            if param_bundle.casefold() == "auto":
                return _detect_frontmost_macos_bundle_id(), None
            resolved_installed = _resolve_installed_bundle_identifier(param_bundle)
            if resolved_installed is not None:
                return resolved_installed, self._pinned_path_for_bundle(resolved_installed)
            if MACOS_BUNDLE_ID_SYNTAX.fullmatch(param_bundle) and "." in param_bundle:
                return param_bundle, self._pinned_path_for_bundle(param_bundle)
            raise ConfigurationError(
                f"Could not resolve macOS application '{param_bundle}' to an installed CFBundleIdentifier.",
            )
        fallback_bundle = self._active_bundle_id or (None if self.bundle_id in {None, "auto"} else self.bundle_id)
        if fallback_bundle is not None:
            return fallback_bundle, self._session_app_path
        if self.app_path is not None:
            return self._validated_installation(self.app_path)
        if self.bundle_id == "auto":
            return _detect_frontmost_macos_bundle_id(), None
        raise ConfigurationError("macOS application activation requires bundle_id, app_path, or a configured bundle id.")

    def _pinned_path_for_bundle(self, bundle_id: str) -> str | None:
        return self._session_app_path if bundle_id == self._active_bundle_id else None

    def _concrete_kill_bundle_id(self, bundle_id: str | None) -> str | None:
        if isinstance(bundle_id, str) and bundle_id.strip():
            cleaned = bundle_id.strip()
            if cleaned.casefold() == "auto":
                return self._active_bundle_id
            resolved = _resolve_installed_bundle_identifier(cleaned)
            if resolved is not None:
                return resolved
            return cleaned
        return self._active_bundle_id or (None if self.bundle_id in {None, "auto"} else self.bundle_id)

    def _effective_bundle_id(self, bundle_id: str | None) -> str | None:
        if self._active_bundle_id is not None and (bundle_id is None or not bundle_id.strip() or bundle_id.strip().casefold() == "auto"):
            return self._active_bundle_id
        if isinstance(bundle_id, str) and bundle_id.strip():
            stripped = bundle_id.strip()
            if self._active_bundle_id is not None and self._active_bundle_id != stripped:
                resolved = _resolve_installed_bundle_identifier(stripped)
                if resolved is not None:
                    return resolved
            return stripped
        return None if self.bundle_id == "auto" else self.bundle_id

    def _effective_app_path(self, app_path: str | None) -> str | None:
        if isinstance(app_path, str) and app_path.strip():
            return app_path.strip()
        return self.app_path

    def _require_session(self) -> Any:
        if self._closed:
            raise ToolExecutionError("Appium Mac2 driver is closed.", context={"reason": "closed"})
        if self._cleanup_pending:
            raise ToolExecutionError(
                "Appium Mac2 session requires cleanup and cannot execute actions.",
                context={"reason": "cleanup_pending", **self._safe_diagnostics(stage="require_session")},
            )
        if self._session is None:
            if self._recoverable_session_loss and not self._borrowed_session and self.skip_app_kill and self._sdk_supports_skip_app_kill():
                if self._try_recover_session_from_saved_identity() and self._session is not None:
                    return self._session
                raise ToolExecutionError(
                    "Appium Mac2 session is unavailable after failed recovery.",
                    context={"reason": "session_unavailable", **self._safe_diagnostics(stage="require_session")},
                )
            raise ConfigurationError(
                "Appium Mac2 session is not available. Call launchApp before macOS Appium actions.",
                context={"resolution_reason": "session_unavailable"},
            )
        if not self._session_is_active(self._session):
            if not self._try_recover_uncertain_owned_session():
                if not self._borrowed_session and not self._recoverable_session_loss:
                    self._cleanup_pending = True
                raise ToolExecutionError(
                    "Appium Mac2 session is no longer active.",
                    context={"reason": "session_unavailable", **self._safe_diagnostics(stage="require_session")},
                )
        self._ensure_alive_if_idle(self._session)
        return self._session

    def _ensure_alive_if_idle(self, session: Any) -> None:
        if self._borrowed_session:
            return
        if self._last_operation_finished_at is None or (time.monotonic() - self._last_operation_finished_at) < DEFAULT_IDLE_ENSURE_ALIVE_SECONDS:
            return
        ensure_alive = getattr(session, "ensure_alive", None)
        if not callable(ensure_alive):
            return
        try:
            ensure_alive(control=self._operation_control(DEFAULT_ENSURE_ALIVE_TIMEOUT_SECONDS, cancellation=self._operation_cancellation))
            self._last_operation_finished_at = time.monotonic()
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            self._note_operation_timeout(exc)
            if self.skip_app_kill and self._sdk_supports_skip_app_kill() and self._try_replace_failed_idle_session():
                self._last_operation_finished_at = time.monotonic()
                return
            if not self._recoverable_session_loss:
                self._cleanup_pending = True
            raise ToolExecutionError(
                "Appium Mac2 session liveness probe failed; session requires cleanup or replacement.",
                context={"reason": "session_unavailable", "stage": "ensure_alive", "cause": exc},
            ) from exc

    def _try_replace_failed_idle_session(self) -> bool:
        if self._runtime is None and self._managed_scope is None and self._connection is None:
            return False
        return self._try_recover_session_from_saved_identity()

    @staticmethod
    def _has_semantic_target(params: BaseModel) -> bool:
        data = params.model_dump(mode="python", exclude_none=True)
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return True
        locator = data.get("locator")
        if isinstance(locator, dict):
            return any(bool(str(value).strip()) for key, value in locator.items() if key != "point" and value is not None)
        return False

    @staticmethod
    def _coordinate_only_point(params: BaseModel) -> MacOSPoint | None:
        data = params.model_dump(mode="python", exclude_none=True)
        point = data.get("point")
        if isinstance(point, dict):
            return MacOSPoint.model_validate(point)
        locator = data.get("locator")
        if isinstance(locator, dict) and isinstance(locator.get("point"), dict):
            return MacOSPoint.model_validate(locator["point"])
        return None

    def _resolve_element_or_none(self, params: BaseModel) -> Any | None:
        data = params.model_dump(mode="python", exclude_none=True)
        locator = data.get("locator")
        if isinstance(locator, dict):
            return self._element_from_locator(MacOSLocator.model_validate(locator))
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return self._element_from_locator(MacOSLocator(name=target.strip()))
        return None

    def _element_from_locator(self, locator: MacOSLocator) -> Any | None:
        session = self._require_session()
        if not any(
            isinstance(value, str) and value.strip()
            for value in (
                locator.accessibilityId,
                locator.name,
                locator.label,
                locator.value,
                locator.title,
                locator.role,
                locator.controlType,
                locator.className,
                locator.xpath,
                locator.predicate,
            )
        ):
            return None

        if self._native_ax_mode:
            return self._resolve_native_ax_element(locator)

        by, query_value, extra_predicate = self._build_session_locator_query(locator)
        try:
            element = session.element(by, query_value, predicate=extra_predicate)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        # When xpath or role is combined with raw predicate, intersect both queries via live inspection.
        if (locator.xpath or locator.role) and locator.predicate:
            return self._intersect_xpath_and_predicate(session, element, locator)

        try:
            element.inspect(attributes=("enabled",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            if type(exc).__name__ == "TargetError" and self._target_error_is_definitive(exc):
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise _AmbiguousResolutionError(candidates, cause=exc) from None
            self._raise_resolution_error(exc)
        return element

    def _native_ax_candidate_bundles(self, *, preferred_bundle_id: str | None = None) -> tuple[str, ...]:
        bundle = self._active_bundle_id
        if bundle is None:
            raise ToolExecutionError("The logical macOS application is unknown.")
        return (bundle,) if preferred_bundle_id in {None, bundle} else ()

    def _observe_native_ax_tree_for_bundle(self, bundle_id: str, *, max_depth: int = 24, max_nodes: int = 1200) -> Any | None:
        try:
            path_options = {"app_path": Path(self._session_app_path)} if self._session_app_path is not None else {}
            return _observe_macos_ax_tree(
                bundle_id=bundle_id,
                max_nodes=max_nodes,
                max_depth=max_depth,
                control=self._ActionControl(self),
                **path_options,
            )
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            if isinstance(exc, PermissionError):
                raise ToolExecutionError("Accessibility trust was lost; explicitly relaunch the macOS session.") from exc
            if isinstance(exc, _NativeForegroundError):
                raise
            if code == "target_not_found":
                return None
            self._raise_resolution_error(exc)
        return None

    def _observe_native_ax_tree_or_none(self, *, max_depth: int = 24, max_nodes: int = 1200, preferred_bundle_id: str | None = None) -> Any | None:
        for bundle_id in self._native_ax_candidate_bundles(preferred_bundle_id=preferred_bundle_id):
            obs = self._observe_native_ax_tree_for_bundle(bundle_id, max_depth=max_depth, max_nodes=max_nodes)
            if obs is not None and getattr(obs, "roots", ()):
                return obs
        return None

    def _match_native_ax_nodes_in_observation(self, observation: Any, locator: MacOSLocator) -> tuple[list[tuple[Any, ElementTree.Element]], bool, set[int] | None]:
        roots = getattr(observation, "roots", ())
        if not roots:
            return [], False, None
        node_by_xml: dict[int, Any] = {}

        def build_mapped_xml(t_node: Any) -> ElementTree.Element:
            type_obs = getattr(t_node, "type", None)
            tag = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) and type_obs.value else "XCUIElementTypeAny"
            attribs: dict[str, str] = {}
            for attr_field, xml_name in (
                ("id", "identifier"),
                ("label", "label"),
                ("title", "title"),
                ("value", "value"),
                ("placeholder_value", "placeholderValue"),
            ):
                obs = getattr(t_node, attr_field, None)
                if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and obs.value:
                    attribs[xml_name] = obs.value
            element_type_obs = getattr(t_node, "element_type", None)
            if getattr(element_type_obs, "state", None) == "value" and _is_strict_int(element_type_obs.value):
                attribs["elementType"] = str(element_type_obs.value)
            for bool_field in ("enabled", "selected", "focused"):
                obs = getattr(t_node, bool_field, None)
                if getattr(obs, "state", None) == "value" and type(obs.value) is bool:
                    attribs[bool_field] = "true" if obs.value else "false"
            visibility = getattr(t_node, "visibility", None)
            if visibility == "not_visible":
                attribs["visible"] = "false"
            elif visibility in {"visible", "partially_visible"}:
                attribs["visible"] = "true"
            bounds_obs = getattr(t_node, "bounds", None)
            bounds_val = getattr(bounds_obs, "value", None) if getattr(bounds_obs, "state", None) == "value" else None
            if bounds_val is not None and all(hasattr(bounds_val, key) for key in ("x", "y", "width", "height")):
                attribs["x"] = str(int(bounds_val.x))
                attribs["y"] = str(int(bounds_val.y))
                attribs["width"] = str(int(bounds_val.width))
                attribs["height"] = str(int(bounds_val.height))
            extra_attrs = getattr(t_node, "extra_attributes", None)
            if isinstance(extra_attrs, dict):
                for key, raw_val in extra_attrs.items():
                    if isinstance(key, str) and key not in attribs and raw_val is not None:
                        attribs[key] = str(raw_val)
            xml_el = ElementTree.Element(tag, attribs)
            raw_text = getattr(t_node, "raw_text", None)
            text_obs = getattr(t_node, "text", None)
            if getattr(text_obs, "state", None) == "value" and not self._is_secure_text_type(tag):
                raw_text = text_obs.value
            if isinstance(raw_text, str) and raw_text:
                xml_el.text = raw_text
            node_by_xml[id(xml_el)] = t_node
            for child in getattr(t_node, "children", ()):
                xml_el.append(build_mapped_xml(child))
            return xml_el

        xml_root = build_mapped_xml(roots[0])
        xpath_allowed_ids: set[int] | None = None
        xpath_possible_nodes = None
        predicate_uncertain = False
        if locator.xpath:
            source_by_xpath_node: dict[Any, ElementTree.Element] = {}

            def xpath_tree(source: ElementTree.Element) -> Any:
                node = etree.Element(source.tag, attrib=source.attrib)
                node.text = source.text
                source_by_xpath_node[node] = source
                node.extend(xpath_tree(child) for child in source)
                return node

            try:
                tree = xpath_tree(xml_root)
                results = tree.xpath(locator.xpath, smart_strings=False)
            except (etree.XPathError, ValueError) as exc:
                raise ConfigurationError("macOS element lookup failed: invalid_locator.", context={"resolution_reason": "invalid_locator"}) from exc
            if not isinstance(results, list) or any(not isinstance(node, etree._Element) or node not in source_by_xpath_node for node in results):
                raise ConfigurationError("macOS XPath must select elements.", context={"resolution_reason": "invalid_locator"})
            xpath_allowed_ids = {id(source_by_xpath_node[node]) for node in results}
            xpath_possible_nodes = {id(node_by_xml[source_id]) for source_id in xpath_allowed_ids}

        matched: list[tuple[Any, ElementTree.Element]] = []
        for xml_el in xml_root.iter():
            if xpath_allowed_ids is not None and id(xml_el) not in xpath_allowed_ids:
                continue
            t_node = node_by_xml[id(xml_el)]
            failed = getattr(t_node, "unknown_metadata", ())
            if locator.predicate and failed and _eval_macos_predicate_on_xml_node(locator.predicate, xml_el, failed=failed) is False:
                if xpath_possible_nodes is None:
                    xpath_possible_nodes = {id(node) for node in node_by_xml.values()}
                xpath_possible_nodes.discard(id(t_node))
            if not self._xml_node_matches_locator(xml_el, locator):
                continue
            if locator.predicate:
                t_node = node_by_xml[id(xml_el)]
                failed = getattr(t_node, "unknown_metadata", ())
                pred_ok = _eval_macos_predicate_on_xml_node(locator.predicate, xml_el, failed=failed)
                if pred_ok is None:
                    raise ConfigurationError("macOS element lookup failed: invalid_locator.", context={"resolution_reason": "invalid_locator"})
                if pred_ok == "unknown":
                    predicate_uncertain = True
                if pred_ok is not True:
                    continue
            t_node = node_by_xml.get(id(xml_el))
            if t_node is not None:
                matched.append((t_node, xml_el))
        return matched, predicate_uncertain, xpath_possible_nodes

    @staticmethod
    def _native_ax_role_may_have_type(role: str | None, tag: str) -> bool:
        return tag == "Any" or tag == "SecureTextField" or (role is not None and tag == role.removeprefix("AX"))

    @staticmethod
    def _native_ax_xpath_is_safe(xpath: str, unknown: frozenset[str]) -> bool:
        if not re.fullmatch(r"(?:/{1,2}XCUIElementType[A-Za-z]+(?:\[[1-9][0-9]*\])?)+", xpath):
            return False
        return "AXSubrole" not in unknown or not re.search(r"XCUIElementType(?:TextField|SecureTextField|TextArea|StaticText|Any)\b", xpath)

    @staticmethod
    def _native_ax_selector_is_uncertain(observation: Any, locator: MacOSLocator, xpath_possible_nodes: set[int] | None) -> bool:
        unknown = getattr(observation, "unknown_metadata", ())
        if not unknown:
            return False
        # Under partial metadata, only basic paths independent of failed reads are evidence.
        if locator.xpath and not AppiumConnectMacOSDriver._native_ax_xpath_is_safe(locator.xpath, unknown):
            return True
        pending = list(observation.roots)
        while pending:
            node = pending.pop()
            pending.extend(node.children)
            failed = getattr(node, "unknown_metadata", ())
            if not failed:
                continue
            role = (getattr(node, "extra_attributes", None) or {}).get("role")
            requested_types = [value.removeprefix("XCUIElementType") for value in (locator.controlType, locator.className) if value]
            if "AXSubrole" in failed and any(not AppiumConnectMacOSDriver._native_ax_role_may_have_type(role, tag) for tag in requested_types):
                continue
            type_uncertain = "AXSubrole" in failed and bool(requested_types)
            if xpath_possible_nodes is not None and id(node) not in xpath_possible_nodes:
                continue
            if locator.role and (getattr(node, "extra_attributes", None) or {}).get("role") != locator.role:
                continue
            if "AXSubrole" not in failed:
                tag = getattr(node.type, "value", None) or "XCUIElementTypeAny"
                if any(value and tag != (value if value.startswith("XCUIElementType") else f"XCUIElementType{value}") for value in (locator.controlType, locator.className)):
                    continue
            uncertain = False
            for requested, field, ax_name in (
                (locator.accessibilityId, "id", "AXIdentifier"),
                (locator.label, "label", "AXDescription"),
                (locator.title, "title", "AXTitle"),
                (locator.value, "value", "AXSubrole"),
            ):
                if requested:
                    if ax_name in failed:
                        uncertain = True
                    elif getattr(getattr(node, field), "value", None) != requested:
                        break
            else:
                name_fields = (("id", "AXIdentifier"), ("label", "AXDescription"), ("title", "AXTitle"), ("value", "AXSubrole"))
                name_uncertain = locator.name and not any(getattr(getattr(node, field), "value", None) == locator.name for field, _ in name_fields)
                if type_uncertain or uncertain or (name_uncertain and any(ax_name in failed for _, ax_name in name_fields)):
                    return True
        return False

    def _resolve_native_ax_node_once(self, locator: MacOSLocator, *, preferred_bundle_id: str | None = None) -> Any | None:
        for bundle_id in self._native_ax_candidate_bundles(preferred_bundle_id=preferred_bundle_id):
            obs = self._observe_native_ax_tree_for_bundle(bundle_id)
            if obs is None:
                continue
            matches, predicate_uncertain, xpath_possible_nodes = self._match_native_ax_nodes_in_observation(obs, locator)
            if not obs.complete_for_requested_scope or predicate_uncertain or self._native_ax_selector_is_uncertain(obs, locator, xpath_possible_nodes):
                return None
            if len(matches) == 1:
                return matches[0][0]
            if len(matches) > 1:
                return None
        return None

    def _resolve_native_ax_element(self, locator: MacOSLocator) -> _NativeAXElement | None:
        for bundle_id in self._native_ax_candidate_bundles():
            obs = self._observe_native_ax_tree_for_bundle(bundle_id)
            if obs is None or not getattr(obs, "roots", ()):
                raise ToolExecutionError("Native AX evidence is unavailable; target resolution is inconclusive.")
            matches, predicate_uncertain, xpath_possible_nodes = self._match_native_ax_nodes_in_observation(obs, locator)
            if len(matches) > 1 and (not locator.xpath or (obs.complete_for_requested_scope and self._native_ax_xpath_is_safe(locator.xpath, getattr(obs, "unknown_metadata", frozenset())))):
                confirmed = [xml for node, xml in matches if not (locator.controlType or locator.className) or "AXSubrole" not in getattr(node, "unknown_metadata", ())]
                if len(confirmed) > 1:
                    raise _AmbiguousResolutionError([candidate(xml) for xml in confirmed[:5]])
            if not obs.complete_for_requested_scope and (len(matches) < 2 or locator.xpath):
                raise ToolExecutionError("Native AX capture was incomplete; target resolution is inconclusive.")
            if predicate_uncertain or self._native_ax_selector_is_uncertain(obs, locator, xpath_possible_nodes):
                raise ToolExecutionError("Native AX selector metadata was unavailable; target resolution is inconclusive.")
            if len(matches) == 1:
                t_node, _ = matches[0]
                return _NativeAXElement(self, t_node, bundle_id=bundle_id, reanchor_locator=locator)
            if len(matches) > 1:
                candidates = [candidate(xml_el) for _, xml_el in matches[:5]]
                raise _AmbiguousResolutionError(candidates)
        return None

    def _intersect_xpath_and_predicate(self, session: Any, xpath_element: Any, locator: MacOSLocator) -> Any | None:
        if locator.predicate is None:
            return xpath_element
        if not self._probe_single_or_ambiguous(session, xpath_element, locator):
            return None
        try:
            scoped_element = session.element("-ios predicate string", locator.predicate, parent=xpath_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

        try:
            scoped_inspection = scoped_element.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError":
                code = getattr(exc, "code", None)
                if code == "target_not_found":
                    return None
                if code == "target_ambiguous":
                    return self._confirm_self_matches_predicate_among_subtree(xpath_element, locator.predicate)
            self._raise_resolution_error(exc)
        if self._element_signature(xpath_element) != self._signature_from_inspection(scoped_inspection):
            return None
        return xpath_element

    def _confirm_self_matches_predicate_among_subtree(self, xpath_element: Any, predicate: str) -> Any | None:
        xpath_sig = self._element_signature(xpath_element)
        session = self._require_session()
        clauses = [f"({predicate})"]
        elem_type, elem_id, elem_label, elem_title, elem_value, bounds_tuple = xpath_sig
        if isinstance(elem_type, str) and elem_type:
            clauses.append(f"amType == {_predicate_literal(elem_type)}")
        if isinstance(elem_id, str) and elem_id:
            clauses.append(f"identifier == {_predicate_literal(elem_id)}")
        if isinstance(elem_label, str) and elem_label:
            clauses.append(f"label == {_predicate_literal(elem_label)}")
        if isinstance(elem_title, str) and elem_title:
            clauses.append(f"title == {_predicate_literal(elem_title)}")
        if isinstance(elem_value, str) and elem_value:
            clauses.append(f"value == {_predicate_literal(elem_value)}")
        if isinstance(bounds_tuple, tuple) and len(bounds_tuple) == 4 and all(type(coord) is int for coord in bounds_tuple):
            bx, by, bw, bh = bounds_tuple
            clauses.extend((f"amRect.x == {bx}", f"amRect.y == {by}", f"amRect.width == {bw}", f"amRect.height == {bh}"))
        try:
            narrowed = session.element("-ios predicate string", " AND ".join(clauses), parent=xpath_element)
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        try:
            narrowed_inspection = narrowed.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            if type(exc).__name__ == "TargetError" and getattr(exc, "code", None) in {"target_not_found", "target_ambiguous"}:
                return None
            self._raise_resolution_error(exc)
        return xpath_element if xpath_sig == self._signature_from_inspection(narrowed_inspection) else None

    def _probe_single_or_ambiguous(self, session: Any, element: Any, locator: MacOSLocator) -> bool:
        try:
            element.inspect(attributes=("enabled",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            code = getattr(exc, "code", None)
            if type(exc).__name__ == "TargetError" and self._target_error_is_definitive(exc):
                if code == "target_not_found":
                    return False
                if code == "target_ambiguous":
                    candidates = self._collect_ambiguous_candidates(session, locator)
                    raise _AmbiguousResolutionError(candidates, cause=exc) from None
            self._raise_resolution_error(exc)
        return True

    def _element_signature(self, element: Any) -> tuple[object, ...]:
        try:
            inspection = element.inspect(
                attributes=("type", "id", "label", "title", "value", "bounds"),
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        return self._signature_from_inspection(inspection)

    @staticmethod
    def _signature_from_inspection(inspection: Any) -> tuple[object, ...]:
        attrs = {item.name: item.value if item.state == "value" else None for item in getattr(inspection, "attributes", ())}
        bounds = attrs.get("bounds")
        bounds_tuple = (bounds.x, bounds.y, bounds.width, bounds.height) if bounds is not None and hasattr(bounds, "x") else None
        return (attrs.get("type"), attrs.get("id"), attrs.get("label"), attrs.get("title"), attrs.get("value"), bounds_tuple)

    def _build_session_locator_query(self, locator: MacOSLocator) -> tuple[str, str, str | None]:
        if locator.xpath:
            constraints = self._xpath_constraints(locator)
            selector = f"({locator.xpath})"
            if constraints:
                selector += "[" + " and ".join(constraints) + "]"
            return "xpath", selector, None

        if locator.role:
            constraints = self._xpath_constraints(locator)
            selector = "//*[" + " and ".join(constraints) + "]"
            return "xpath", selector, None

        predicates = self._predicate_constraints(locator)
        if locator.predicate:
            combined_extra = " AND ".join(predicates) if predicates else None
            return "-ios predicate string", locator.predicate, combined_extra

        # Prefer exact single-field native strategies when unaccompanied.
        if locator.accessibilityId and not any((locator.name, locator.label, locator.value, locator.title, locator.controlType, locator.className)):
            return "accessibility id", locator.accessibilityId, None

        if not predicates:
            raise ConfigurationError("macOS locator requires at least one constraint.", context={"resolution_reason": "invalid_locator"})
        return "-ios predicate string", " AND ".join(predicates), None

    def _xpath_constraints(self, locator: MacOSLocator) -> list[str]:
        constraints: list[str] = []
        for field, attribute in (
            ("accessibilityId", "identifier"),
            ("label", "label"),
            ("value", "value"),
            ("title", "title"),
            ("role", "role"),
        ):
            raw = getattr(locator, field)
            if raw:
                constraints.append(f"@{attribute}={_xpath_literal(raw)}")
        if locator.name:
            literal = _xpath_literal(locator.name)
            constraints.append("(" + " or ".join(f"@{key}={literal}" for key in ("identifier", "name", "label", "value", "title")) + ")")
        for type_val in (locator.controlType, locator.className):
            if type_val:
                full = type_val if type_val.startswith("XCUIElementType") else f"XCUIElementType{type_val}"
                raw_lit = _xpath_literal(type_val)
                full_lit = _xpath_literal(full)
                constraints.append(f"(name()={raw_lit} or name()={full_lit} or @type={raw_lit})")
        return constraints

    def _predicate_constraints(self, locator: MacOSLocator) -> list[str]:
        clauses: list[str] = []
        if locator.accessibilityId:
            clauses.append(f"identifier == {_predicate_literal(locator.accessibilityId)}")
        if locator.label:
            clauses.append(f"label == {_predicate_literal(locator.label)}")
        if locator.value:
            clauses.append(f"value == {_predicate_literal(locator.value)}")
        if locator.title:
            clauses.append(f"title == {_predicate_literal(locator.title)}")
        if locator.name:
            lit = _predicate_literal(locator.name)
            clauses.append(f"(identifier == {lit} OR label == {lit} OR value == {lit} OR title == {lit})")
        for type_val in (locator.controlType, locator.className):
            if type_val:
                if type_val.startswith("XCUIElementType"):
                    clauses.append(f"amType == {_predicate_literal(type_val)}")
                else:
                    prefixed = f"XCUIElementType{type_val}"
                    clauses.append(f"(amType == {_predicate_literal(type_val)} OR amType == {_predicate_literal(prefixed)})")
        return clauses

    def _collect_ambiguous_candidates(self, session: Any, locator: MacOSLocator) -> list[dict[str, object]]:
        try:
            observation = session.observe_tree(control=self._ActionControl(self))
        except Exception:  # noqa: BLE001
            return []
        matches: list[dict[str, object]] = []
        stack = list(reversed(getattr(observation, "roots", ())))
        while stack and len(matches) < 5:
            node = stack.pop()
            xml_node = self._tree_node_to_xml_element(node)
            if self._xml_node_matches_locator(xml_node, locator):
                matches.append(candidate(xml_node))
            children = getattr(node, "children", ())
            if children:
                stack.extend(reversed(children))
        return matches

    @staticmethod
    def _xml_node_matches_locator(node: ElementTree.Element, locator: MacOSLocator) -> bool:
        attrs = node.attrib
        if locator.accessibilityId and attrs.get("identifier") != locator.accessibilityId:
            return False
        if locator.label and attrs.get("label") != locator.label:
            return False
        if locator.value and attrs.get("value") != locator.value:
            return False
        if locator.title and attrs.get("title") != locator.title:
            return False
        if locator.role and attrs.get("role") != locator.role:
            return False
        if locator.name and not any(attrs.get(key) == locator.name for key in ("identifier", "name", "label", "value", "title")):
            return False
        for type_val in (locator.controlType, locator.className):
            if type_val:
                prefixed = type_val if type_val.startswith("XCUIElementType") else f"XCUIElementType{type_val}"
                if node.tag not in {type_val, prefixed} and attrs.get("type") not in {type_val, prefixed}:
                    return False
        return True

    def _point_from_endpoint(self, endpoint: MacOSDragEndpoint, element: Any | None) -> MacOSPoint | None:
        if element is not None:
            return self._element_center(element)
        if self._has_semantic_target(endpoint):
            return None
        return self._coordinate_only_point(endpoint)

    def _perform_pointer_click(
        self,
        point: MacOSPoint,
        *,
        button: str = "left",
        double: bool = False,
        modifier_flags: int | None = None,
    ) -> None:
        session = self._require_session()
        self._verify_native_foreground()
        try:
            session.device.click_at(
                point.x,
                point.y,
                button="right" if button == "right" else "left",
                double=double,
                key_modifier_flags=modifier_flags,
                control=self._ActionControl(self),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _perform_pointer_hover(self, point: MacOSPoint) -> None:
        session = self._require_session()
        self._verify_native_foreground()
        try:
            session.device.hover_at(point.x, point.y, control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _perform_pointer_drag(self, source: MacOSPoint, destination: MacOSPoint, *, duration_ms: int | None) -> None:
        session = self._require_session()
        self._verify_native_foreground()
        effective_duration = max(1, min(duration_ms if duration_ms is not None else 250, 60_000))
        timeout_seconds = max(float(self.action_timeout_seconds), (effective_duration / 1000.0) + 5.0)
        try:
            session.device.swipe_between(
                source.x,
                source.y,
                destination.x,
                destination.y,
                duration_ms=effective_duration,
                control=self._operation_control(timeout_seconds, cancellation=self._operation_cancellation),
            )
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)

    def _send_macos_text(self, text: str, *, element: Any | None = None) -> None:
        session = self._require_session()
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_text:
            return
        keys: list[Any] = [self._build_key_item(MACOS_KEY_NAMES["ENTER"] if character == "\n" else character, 0) for character in normalized_text]
        for start in range(0, len(keys), 1024):
            chunk = keys[start : start + 1024]
            try:
                if element is not None:
                    element.send_macos_keys(
                        chunk,
                        wait_timeout_seconds=self._wait_timeout(),
                        control=self._ActionControl(self),
                    )
                else:
                    self._verify_keyboard_foreground()
                    session.device.send_keys(chunk, control=self._ActionControl(self))
            except Exception as exc:  # noqa: BLE001
                self._propagate_if_cancelled(exc)
                self._raise_resolution_error(exc)

    @staticmethod
    def _build_key_item(key: str, modifier_flags: int) -> Any:
        if modifier_flags == 0:
            return key
        try:
            from appium_connect_agent.automation import MacKeyStroke
        except ImportError:
            return {"key": key, "modifierFlags": modifier_flags}
        return MacKeyStroke(key=key, modifier_flags=modifier_flags)

    @staticmethod
    def _modifier_flags(modifiers: Sequence[str] | None) -> int | None:
        if not modifiers:
            return None
        flags = 0
        for modifier in modifiers:
            normalized = modifier.strip().upper()
            if normalized not in MACOS_KEY_MODIFIER_FLAGS:
                raise ConfigurationError(f"Unsupported macOS key modifier: {modifier}.")
            flags |= MACOS_KEY_MODIFIER_FLAGS[normalized]
        return flags

    def _element_displayed(self, element: Any) -> bool:
        obs = self._inspect_obs_map(element, ("displayed",), stage="assert_visible").get("displayed")
        if getattr(obs, "state", None) == "value" and type(obs.value) is bool:
            return bool(obs.value)
        raise ToolExecutionError("Mac2 element cannot report visibility.", context={"stage": "assert_visible"})

    def _element_center(self, element: Any | None) -> MacOSPoint | None:
        if isinstance(element, _NativeAXElement):
            x, y = element._current_bounds_center()
            return MacOSPoint(x=x, y=y)
        rect = self._element_rect(element)
        if rect is None:
            return None
        return MacOSPoint(
            x=int(rect["x"] + rect["width"] / 2.0),
            y=int(rect["y"] + rect["height"] / 2.0),
        )

    def _element_rect(self, element: Any | None) -> dict[str, float] | None:
        if element is None:
            return None
        try:
            inspection = element.inspect(attributes=("bounds",), control=self._ActionControl(self))
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._raise_resolution_error(exc)
        attrs = getattr(inspection, "attributes", ())
        if not attrs or getattr(attrs[0], "state", None) != "value":
            return None
        bounds = attrs[0].value
        if bounds is not None and all(hasattr(bounds, key) for key in ("x", "y", "width", "height")):
            return {
                "x": float(bounds.x),
                "y": float(bounds.y),
                "width": float(bounds.width),
                "height": float(bounds.height),
            }
        return None

    @staticmethod
    def _order_coordinate(rect: dict[str, float], direction: str) -> float:
        if direction == "horizontal":
            return rect["x"] + rect["width"] / 2.0
        return rect["y"] + rect["height"] / 2.0

    @staticmethod
    def _order_matches(entries: list[dict[str, object]], expected_order: list[int], tolerance: float) -> bool:
        coordinates_by_index = {int(entry["index"]): float(entry["coordinate"]) for entry in entries}
        if any(index not in coordinates_by_index for index in expected_order):
            return False
        expected_coordinates = [coordinates_by_index[index] for index in expected_order]
        return all(left <= right + tolerance for left, right in pairwise(expected_coordinates))

    def _simplify_tree_observation(
        self,
        observation: Any,
        *,
        max_depth: int,
        include_attributes: bool,
    ) -> dict[str, object]:
        page_source, _ = self._simplify_tree_observation_with_budget(
            observation,
            max_depth=max_depth,
            include_attributes=include_attributes,
        )
        return page_source

    def _simplify_tree_observation_with_budget(
        self,
        observation: Any,
        *,
        max_depth: int,
        include_attributes: bool,
    ) -> tuple[dict[str, object], dict[str, object]]:
        roots = getattr(observation, "roots", ())
        if not roots:
            return {"format": "xml", "root": None, "source_length": 0}, {}
        xml_root = self._tree_node_to_xml_element(roots[0])
        serialized = ElementTree.tostring(xml_root, encoding="unicode")
        try:
            node_count = self._element_count(xml_root)
            depth_candidates = [max_depth, *(step_depth for step_depth in (8, 6, 4) if step_depth < max_depth)]
            chosen_root: dict[str, object] | None = None
            compaction_meta: dict[str, object] = {}
            for candidate_depth in depth_candidates:
                for sibling_limit in MACOS_COMPACT_SIBLING_STEPDOWNS:
                    built_root = self._element_snapshot(
                        xml_root,
                        depth=1,
                        max_depth=candidate_depth,
                        include_attributes=include_attributes,
                        max_siblings=sibling_limit,
                    )
                    chosen_root = built_root
                    encoded_len = len(json.dumps(built_root, ensure_ascii=False, separators=(",", ":")))
                    if encoded_len <= DEFAULT_MACOS_SNAPSHOT_MAX_JSON_CHARS:
                        if candidate_depth < max_depth or sibling_limit is not None:
                            compaction_meta = {
                                "adaptive_compaction": True,
                                "effective_max_depth": candidate_depth,
                                **({"max_siblings_per_parent": sibling_limit} if sibling_limit is not None else {}),
                            }
                        return (
                            {
                                "format": "xml",
                                "source_length": len(serialized),
                                "node_count": node_count,
                                "root": built_root,
                            },
                            compaction_meta,
                        )
            if chosen_root is not None:
                compaction_meta = {
                    "adaptive_compaction": True,
                    "effective_max_depth": depth_candidates[-1],
                    "max_siblings_per_parent": MACOS_COMPACT_SIBLING_STEPDOWNS[-1],
                }
            return (
                {
                    "format": "xml",
                    "source_length": len(serialized),
                    "node_count": node_count,
                    "root": chosen_root,
                },
                compaction_meta,
            )
        except Exception as exc:  # noqa: BLE001 - preserve observation availability on unexpected local compaction failure.
            self._propagate_if_cancelled(exc)
            preview = serialized[:DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS]
            return (
                {
                    "format": "unparsed_xml",
                    "source_preview": preview,
                    "source_length": len(serialized),
                    "truncated": len(serialized) > len(preview),
                    "parse_error": type(exc).__name__,
                },
                {},
            )

    def _tree_node_to_xml_element(self, node: Any) -> ElementTree.Element:
        type_obs = getattr(node, "type", None)
        tag = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) and type_obs.value else "XCUIElementTypeAny"
        attribs: dict[str, str] = {}
        for attr_field, xml_name in (
            ("id", "identifier"),
            ("label", "label"),
            ("title", "title"),
            ("value", "value"),
            ("placeholder_value", "placeholderValue"),
        ):
            obs = getattr(node, attr_field, None)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and obs.value:
                attribs[xml_name] = obs.value

        element_type_obs = getattr(node, "element_type", None)
        if getattr(element_type_obs, "state", None) == "value" and _is_strict_int(element_type_obs.value):
            attribs["elementType"] = str(element_type_obs.value)

        for bool_field in ("enabled", "selected"):
            obs = getattr(node, bool_field, None)
            if getattr(obs, "state", None) == "value" and type(obs.value) is bool:
                attribs[bool_field] = "true" if obs.value else "false"

        visibility = getattr(node, "visibility", None)
        if visibility == "not_visible":
            attribs["visible"] = "false"
        elif visibility in {"visible", "partially_visible"}:
            attribs["visible"] = "true"

        bounds_obs = getattr(node, "bounds", None)
        bounds_val = getattr(bounds_obs, "value", None) if getattr(bounds_obs, "state", None) == "value" else None
        if bounds_val is not None and all(hasattr(bounds_val, key) for key in ("x", "y", "width", "height")):
            attribs["x"] = str(int(bounds_val.x))
            attribs["y"] = str(int(bounds_val.y))
            attribs["width"] = str(int(bounds_val.width))
            attribs["height"] = str(int(bounds_val.height))

        extra_attrs = getattr(node, "extra_attributes", None)
        if isinstance(extra_attrs, dict):
            for key, raw_val in extra_attrs.items():
                if isinstance(key, str) and key not in attribs and raw_val is not None:
                    attribs[key] = str(raw_val)

        xml_el = ElementTree.Element(tag, attribs)
        raw_text = getattr(node, "raw_text", None)
        if isinstance(raw_text, str) and raw_text:
            xml_el.text = raw_text
        for child in getattr(node, "children", ()):
            xml_el.append(self._tree_node_to_xml_element(child))
        return xml_el

    def _element_snapshot(
        self,
        element: ElementTree.Element,
        *,
        depth: int,
        max_depth: int,
        include_attributes: bool,
        max_siblings: int | None = None,
    ) -> dict[str, object]:
        snapshot = self._compact_element_snapshots(element, include_attributes=include_attributes, preserve_node=True)[0]
        self._bound_snapshot_depth(snapshot, depth=depth, max_depth=max_depth, max_siblings=max_siblings)
        return snapshot

    def _compact_element_snapshots(
        self,
        element: ElementTree.Element,
        *,
        include_attributes: bool,
        preserve_node: bool = False,
    ) -> list[dict[str, object]]:
        children = [child_snapshot for child in element for child_snapshot in self._compact_element_snapshots(child, include_attributes=include_attributes)]
        attributes = self._snapshot_attributes(element.attrib, include_attributes=include_attributes)
        text = (element.text or "").strip()[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        interactive = self._xml_name(element.tag).removeprefix("XCUIElementType") in MACOS_INTERACTIVE_SHORT_TYPES
        if not preserve_node and not interactive and not self._has_snapshot_signal(attributes, text=text):
            return children

        snapshot: dict[str, object] = {"type": self._xml_name(element.tag)}
        if attributes:
            snapshot["attributes"] = attributes
        clipped = [
            key
            for key, value in element.attrib.items()
            if self._xml_name(key) in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS
            and (include_attributes or self._xml_name(key) in DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS)
            and len(value.strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS
        ]
        if clipped:
            snapshot["truncated_attributes"] = clipped
        if len((element.text or "").strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS:
            snapshot["text_truncated"] = True
        if text:
            snapshot["text"] = text
        if children:
            snapshot["children"] = children
        return [snapshot]

    def _bound_snapshot_depth(
        self,
        snapshot: dict[str, object],
        *,
        depth: int,
        max_depth: int,
        max_siblings: int | None = None,
    ) -> None:
        children = snapshot.get("children")
        if not isinstance(children, list) or not children:
            return
        if depth >= max_depth:
            snapshot.pop("children")
            snapshot["children_truncated"] = len(children)
            return
        if max_siblings is not None and len(children) > max_siblings:
            omitted = len(children) - max_siblings
            kept = children[:max_siblings]
            snapshot["children"] = kept
            snapshot["children_truncated"] = int(snapshot.get("children_truncated", 0)) + omitted
            children = kept
        for child in children:
            if isinstance(child, dict):
                self._bound_snapshot_depth(child, depth=depth + 1, max_depth=max_depth, max_siblings=max_siblings)

    def _snapshot_attributes(self, attributes: dict[str, str], *, include_attributes: bool) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, raw_value in attributes.items():
            normalized_key = self._xml_name(key)
            value = str(raw_value).strip()
            if not value or (not include_attributes and normalized_key not in DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS):
                continue
            default_state = MACOS_SNAPSHOT_STATE_DEFAULTS.get(normalized_key)
            if default_state is not None and value.lower() == default_state:
                continue
            if normalized_key in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS:
                value = value[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
            selected[normalized_key] = value
        return selected

    def _has_snapshot_signal(self, attributes: dict[str, object], *, text: str) -> bool:
        non_default_state = any(key in MACOS_SNAPSHOT_STATE_DEFAULTS for key in attributes)
        identity_or_text = bool(text) or any(key in MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS for key in attributes)
        if self._is_zero_size(attributes) or str(attributes.get("visible", "")).lower() == "false":
            return identity_or_text or non_default_state
        return identity_or_text or non_default_state or any(key in MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS for key in attributes)

    @staticmethod
    def _is_zero_size(attributes: dict[str, object]) -> bool:
        for key in ("width", "height"):
            value = attributes.get(key)
            if value is None:
                continue
            try:
                if float(str(value)) == 0:
                    return True
            except ValueError:
                continue
        return False

    def _element_count(self, element: ElementTree.Element) -> int:
        return 1 + sum(self._element_count(child) for child in list(element))

    @staticmethod
    def _xml_name(value: str) -> str:
        return value.rsplit("}", 1)[-1] if "}" in value else value

    def _viewport_size(self, session: Any) -> tuple[int, int] | None:
        observation: Any | None = None
        if self._native_ax_mode:
            with suppress(Exception):
                observation = self._observe_native_ax_tree_or_none(max_depth=2, max_nodes=4)
        if observation is None:
            observe_fn = getattr(session, "observe_tree", None)
            if not callable(observe_fn):
                return None
            try:
                observation = observe_fn(control=self._ActionControl(self))
            except Exception:  # noqa: BLE001
                return None
        viewport = getattr(observation, "viewport", None)
        width_obs = getattr(viewport, "width", None)
        height_obs = getattr(viewport, "height", None)
        if (
            getattr(width_obs, "state", None) == "value"
            and getattr(height_obs, "state", None) == "value"
            and _is_strict_int(width_obs.value)
            and _is_strict_int(height_obs.value)
            and width_obs.value > 0
            and height_obs.value > 0
        ):
            return int(width_obs.value), int(height_obs.value)
        return None

    @staticmethod
    def _is_secure_text_type(elem_type: str | None) -> bool:
        if not isinstance(elem_type, str) or not elem_type:
            return False
        return elem_type.removeprefix("XCUIElementType") == "SecureTextField"

    @staticmethod
    def _is_masked_string(value: str) -> bool:
        return bool(value) and all(ch in MACOS_MASK_GLYPHS for ch in value)

    @staticmethod
    def _bounds_int_tuple(bounds_obs: Any) -> tuple[int, int, int, int] | None:
        if getattr(bounds_obs, "state", None) != "value":
            return None
        bounds = getattr(bounds_obs, "value", None)
        if bounds is None or not all(hasattr(bounds, key) for key in ("x", "y", "width", "height")):
            return None
        bx, by, bw, bh = int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height)
        if bw <= 0 or bh <= 0:
            return None
        return (bx, by, bw, bh)

    def _serialize_attribute_observation(self, name: str, obs: Any) -> dict[str, object]:
        state = getattr(obs, "state", "unknown") if obs is not None else "unknown"
        if state not in {"value", "unknown", "not_applicable", "missing"}:
            state = "unknown"
        if state != "value":
            return {"state": state, "value": None}
        raw_val = getattr(obs, "value", None)
        if name == "bounds":
            if raw_val is not None and all(hasattr(raw_val, key) for key in ("x", "y", "width", "height")):
                return {
                    "state": "value",
                    "value": {
                        "x": int(raw_val.x),
                        "y": int(raw_val.y),
                        "width": int(raw_val.width),
                        "height": int(raw_val.height),
                    },
                }
            return {"state": "unknown", "value": None}
        if name in {"text", "value"} and isinstance(raw_val, str) and self._is_masked_string(raw_val):
            return {"state": "unavailable", "value": None, "reason": "protected_text"}
        return {"state": "value", "value": raw_val}

    def _inspect_obs_map(self, element: Any, attributes: tuple[str, ...], *, stage: str) -> dict[str, Any]:
        try:
            inspection = element.inspect(attributes=attributes, control=self._ActionControl(self))
        except Exception as exc:
            self._propagate_if_cancelled(exc)
            raise ToolExecutionError(
                "macOS element inspection failed.",
                context={"stage": stage, "cause": exc},
            ) from exc
        return {item.name: item for item in getattr(inspection, "attributes", ())}

    @staticmethod
    def _locator_from_params(params: BaseModel) -> MacOSLocator:
        data = params.model_dump(mode="python", exclude_none=True)
        locator = data.get("locator")
        if isinstance(locator, dict):
            return MacOSLocator.model_validate(locator)
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return MacOSLocator(name=target.strip())
        raise ConfigurationError("macOS element lookup requires target or locator.", context={"resolution_reason": "invalid_locator"})

    def _poll_unique_element(self, locator: MacOSLocator) -> Any | None:
        deadline = time.monotonic() + self._wait_timeout()
        while True:
            element = self._element_from_locator(locator)
            if element is not None:
                return element
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def _evaluate_text_assertion(self, element: Any, text_assertion: Any) -> dict[str, object]:
        attr_choice = text_assertion.attribute
        actual_text: str | None = None
        selected_attr: str | None = None

        if attr_choice in {"label", "title", "placeholder_value"}:
            obs = self._inspect_obs_map(element, (attr_choice,), stage="assert_state").get(attr_choice)
            if getattr(obs, "state", None) == "value" and isinstance(obs.value, str):
                actual_text = obs.value
                selected_attr = attr_choice
            elif getattr(obs, "state", None) == "missing":
                actual_text = ""
                selected_attr = attr_choice
            else:
                return self._failed(
                    "assertion_error",
                    f"Target attribute '{attr_choice}' is unavailable for text assertion.",
                    output={"attribute": attr_choice, "text": None},
                    metadata={"reason": "protected_or_unreadable_text"},
                )
        else:
            type_obs = self._inspect_obs_map(element, ("type",), stage="assert_state").get("type")
            type_val = type_obs.value if getattr(type_obs, "state", None) == "value" and isinstance(type_obs.value, str) else None
            if not type_val or self._is_secure_text_type(type_val):
                return self._failed(
                    "assertion_error",
                    "Target text is protected or unreadable.",
                    output={"attribute": attr_choice, "text": None},
                    metadata={"reason": "protected_or_unreadable_text"},
                )
            if attr_choice in {"value", "text"}:
                obs = self._inspect_obs_map(element, (attr_choice,), stage="assert_state").get(attr_choice)
                if getattr(obs, "state", None) == "value" and isinstance(obs.value, str) and not self._is_masked_string(obs.value):
                    actual_text = obs.value
                    selected_attr = attr_choice
                elif getattr(obs, "state", None) == "missing":
                    actual_text = ""
                    selected_attr = attr_choice
                else:
                    return self._failed(
                        "assertion_error",
                        f"Target attribute '{attr_choice}' is protected or unreadable.",
                        output={"attribute": attr_choice, "text": None},
                        metadata={"reason": "protected_or_unreadable_text"},
                    )
            else:
                candidates_map = self._inspect_obs_map(element, ("value", "label", "title", "text"), stage="assert_state")
                for candidate_attr in ("value", "label", "title", "text"):
                    obs = candidates_map.get(candidate_attr)
                    if getattr(obs, "state", None) == "value" and isinstance(obs.value, str):
                        if candidate_attr in {"value", "text"} and self._is_masked_string(obs.value):
                            return self._failed(
                                "assertion_error",
                                "Target text is masked.",
                                output={"attribute": candidate_attr, "text": None},
                                metadata={"reason": "protected_or_unreadable_text"},
                            )
                        actual_text = obs.value
                        selected_attr = candidate_attr
                        break
                if actual_text is None:
                    actual_text = ""
                    selected_attr = "auto"

        matched = actual_text == text_assertion.equals if text_assertion.equals is not None else (text_assertion.contains in actual_text if text_assertion.contains is not None else False)
        if matched:
            return self._passed({"attribute": selected_attr, "text": actual_text})
        return self._failed(
            "assertion_error",
            "macOS element text did not match expectation.",
            output={"attribute": selected_attr, "text": actual_text},
        )

    @staticmethod
    def _target_depends_on_mutable_text(params: MacOSTypeTextParams) -> bool:
        if isinstance(params.target, str) and params.target.strip():
            return True
        loc = params.locator
        if loc is None:
            return False
        if any(isinstance(val, str) and val.strip() for val in (loc.name, loc.value, loc.label, loc.title)):
            return True
        return any(isinstance(selector, str) and bool(MACOS_MUTABLE_TEXT_SELECTOR_TOKEN.search(selector)) for selector in (loc.xpath, loc.predicate))

    def _bind_element_by_id_or_geometry(
        self,
        session: Any,
        *,
        elem_id: str,
        elem_type: str | None,
        bounds_tuple: tuple[int, int, int, int] | None,
    ) -> Any | None:
        if self._native_ax_mode:
            by_id_loc = MacOSLocator(accessibilityId=elem_id, controlType=elem_type)
            try:
                bound_native = self._resolve_native_ax_element(by_id_loc)
                if bound_native is not None:
                    return bound_native
            except _AmbiguousResolutionError:
                pass
            if elem_type and bounds_tuple is not None:
                bx, by, bw, bh = bounds_tuple
                pred = (
                    f"identifier == {_predicate_literal(elem_id)} AND amType == {_predicate_literal(elem_type)} "
                    f"AND amRect.x == {bx} AND amRect.y == {by} AND amRect.width == {bw} AND amRect.height == {bh}"
                )
                try:
                    return self._resolve_native_ax_element(MacOSLocator(predicate=pred))
                except _AmbiguousResolutionError:
                    return None
            return None
        try:
            candidate_el = session.element("accessibility id", elem_id)
            obs_map = self._inspect_obs_map(candidate_el, ("type", "id", "bounds"), stage="identity_binding")
            obs_id = obs_map.get("id")
            obs_type = obs_map.get("type")
            if getattr(obs_id, "state", None) == "value" and obs_id.value == elem_id:
                if elem_type is None or (getattr(obs_type, "state", None) == "value" and obs_type.value == elem_type):
                    return candidate_el
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
        if elem_type and bounds_tuple is not None:
            bx, by, bw, bh = bounds_tuple
            predicate = (
                f"identifier == {_predicate_literal(elem_id)} AND amType == {_predicate_literal(elem_type)} "
                f"AND amRect.x == {bx} AND amRect.y == {by} AND amRect.width == {bw} AND amRect.height == {bh}"
            )
            return self._probe_bound_predicate_element(session, predicate, expected_id=elem_id, expected_type=elem_type, expected_bounds=bounds_tuple)
        return None

    def _bind_element_by_geometry(
        self,
        session: Any,
        *,
        elem_type: str,
        bounds_tuple: tuple[int, int, int, int],
    ) -> Any | None:
        bx, by, bw, bh = bounds_tuple
        predicate = f"amType == {_predicate_literal(elem_type)} AND amRect.x == {bx} AND amRect.y == {by} AND amRect.width == {bw} AND amRect.height == {bh}"
        if self._native_ax_mode:
            try:
                return self._resolve_native_ax_element(MacOSLocator(predicate=predicate))
            except _AmbiguousResolutionError:
                return None
        return self._probe_bound_predicate_element(session, predicate, expected_id=None, expected_type=elem_type, expected_bounds=bounds_tuple)

    def _probe_bound_predicate_element(
        self,
        session: Any,
        predicate: str,
        *,
        expected_id: str | None,
        expected_type: str,
        expected_bounds: tuple[int, int, int, int],
    ) -> Any | None:
        try:
            candidate_el = session.element("-ios predicate string", predicate)
            obs_map = self._inspect_obs_map(candidate_el, ("type", "id", "bounds"), stage="identity_binding")
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return None
        obs_type = obs_map.get("type")
        if getattr(obs_type, "state", None) != "value" or obs_type.value != expected_type:
            return None
        if expected_id is not None:
            obs_id = obs_map.get("id")
            if getattr(obs_id, "state", None) != "value" or obs_id.value != expected_id:
                return None
        obs_bounds = self._bounds_int_tuple(obs_map.get("bounds"))
        if obs_bounds != expected_bounds:
            return None
        return candidate_el

    def _verify_post_entry_target(
        self,
        bound_element: Any,
        *,
        expected_type: str | None,
        expected_id: str | None,
        expected_bounds: tuple[int, int, int, int] | None,
        include_value: bool,
    ) -> str | None:
        attrs = ("type", "id", "enabled", "value", "bounds") if include_value else ("type", "id", "enabled", "bounds")
        try:
            obs_map = self._inspect_obs_map(bound_element, attrs, stage="post_verification")
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            return None
        if expected_type is not None:
            obs_type = obs_map.get("type")
            if getattr(obs_type, "state", None) == "value" and obs_type.value != expected_type:
                return None
        if expected_id is not None:
            obs_id = obs_map.get("id")
            if getattr(obs_id, "state", None) == "value" and obs_id.value != expected_id:
                return None
        if expected_bounds is not None:
            if self._bounds_int_tuple(obs_map.get("bounds")) != expected_bounds:
                return None
        if not include_value:
            return ""
        val_obs = obs_map.get("value")
        val_state = getattr(val_obs, "state", None)
        if val_state == "value" and isinstance(val_obs.value, str) and not self._is_masked_string(val_obs.value):
            return val_obs.value
        if val_state == "missing":
            return ""
        return None

    @staticmethod
    def _value_matches(actual: str, expected_text: str, *, clear: bool, initial_value: str | None) -> bool:
        if clear:
            return actual == expected_text
        return actual == f"{initial_value}{expected_text}" if initial_value is not None else actual.endswith(expected_text)

    def _raise_resolution_error(self, error: Exception) -> None:
        cls_name = type(error).__name__
        code = getattr(error, "code", None)
        if cls_name in {"ElementValidationError", "InvalidRequestError"} or code in {
            "invalid_selector",
            "invalid_argument",
            "unsupported_operation",
        }:
            raise ConfigurationError("macOS element lookup failed: invalid_locator.", context={"resolution_reason": "invalid_locator"}) from None
        if cls_name == "InvalidSessionStateError" or code in {
            "session_closed",
            "session_disposed",
            "session_invalid",
            "session_lost",
            "connection_closed",
            "connection_closing",
        }:
            if not self._borrowed_session:
                self._cleanup_pending = True
            raise ToolExecutionError(
                "macOS session is unavailable.",
                context={"reason": "session_unavailable", **self._safe_diagnostics(error, stage="element_lookup")},
            ) from None
        raise ToolExecutionError(
            "macOS backend operation failed.",
            context={"stage": "element_lookup", "cause": error},
        ) from error

    def _run_tool(
        self,
        func: Callable[[], dict[str, object]],
        *,
        failure_default: str = "action_error",
        allow_cleanup_pending: bool = False,
    ) -> dict[str, object]:
        try:
            return self._run_on_worker(func, allow_cleanup_pending=allow_cleanup_pending)
        except _AmbiguousResolutionError as amb:
            return self._target_ambiguous_result(amb)
        except ValidationError:
            return self._failed("configuration_error", "Invalid macOS automation request parameters.")
        except ConfigurationError as exc:
            if exc.context.get("resolution_reason") == "invalid_locator":
                return self._failed("configuration_error", str(exc), metadata={"resolution_reason": "invalid_locator", **self._safe_diagnostics()})
            raise
        except ToolExecutionError as exc:
            self._note_operation_timeout(exc)
            cause = exc.context.get("cause")
            if isinstance(cause, Exception):
                self._propagate_if_cancelled(cause)
                return self._normalize_backend_exception(cause, stage=str(exc.context.get("stage") or "tool"), failure_default=failure_default)
            return self._failed(failure_default, str(exc), metadata=self._safe_diagnostics(exc))
        except BaseExceptionGroup:
            raise
        except Exception as exc:  # noqa: BLE001
            self._propagate_if_cancelled(exc)
            self._note_operation_timeout(exc)
            return self._normalize_backend_exception(exc, failure_default=failure_default)

    def _normalize_backend_exception(
        self,
        exc: Exception,
        *,
        stage: str | None = None,
        failure_default: str = "action_error",
    ) -> dict[str, object]:
        if isinstance(exc, ConfigurationError):
            raise exc
        if isinstance(exc, _AmbiguousResolutionError):
            return self._target_ambiguous_result(exc)
        code = getattr(exc, "code", None)
        cls_name = type(exc).__name__
        metadata = self._safe_diagnostics(exc, stage=stage)
        if isinstance(exc, _NativeForegroundError):
            return self._failed(failure_default, str(exc), metadata=metadata)
        if cls_name in {"InvalidRequestError", "ElementValidationError"} or code in {"invalid_selector", "invalid_argument", "unsupported_operation"}:
            return self._failed("configuration_error", "Invalid macOS automation request parameters.", metadata={"resolution_reason": "invalid_locator", **metadata})
        if cls_name == "TargetError" and self._target_error_is_definitive(exc):
            if code == "target_ambiguous":
                return self._failed(
                    "target_resolution_error",
                    "macOS locator matches multiple elements; refine the locator.",
                    metadata={"resolution_reason": "ambiguous", **metadata},
                )
            if code == "target_not_found":
                return self._failed("target_resolution_error", "Target was not found.", metadata={"resolution_reason": "not_found", **metadata})
        if (
            cls_name == "InvalidSessionStateError"
            or code
            in {
                "session_closed",
                "session_disposed",
                "session_invalid",
                "session_lost",
                "connection_closed",
                "connection_closing",
            }
            or stage == "ensure_alive"
        ):
            if not self._borrowed_session and not self._recoverable_session_loss:
                self._cleanup_pending = True
            metadata["reason"] = "session_unavailable"
        return self._failed(failure_default, "macOS backend operation failed.", metadata=metadata)

    def _target_ambiguous_result(self, amb: _AmbiguousResolutionError) -> dict[str, object]:
        diagnostics = self._safe_diagnostics(amb.cause, stage="element_lookup")
        return self._failed(
            "target_resolution_error",
            "macOS locator matches multiple elements; refine the locator.",
            metadata={
                "resolution_reason": "ambiguous",
                "match_count": max(len(amb.candidates), 2),
                "candidates": amb.candidates[:5],
                **diagnostics,
            },
        )

    def _target_error_is_definitive(self, exc: Exception) -> bool:
        if getattr(exc, "request_state", None) == "response_unknown":
            return False
        return self._safe_diagnostics(exc).get("session_state") == "active"

    def _safe_diagnostics(self, exc: Exception | None = None, *, stage: str | None = None) -> dict[str, object]:
        metadata: dict[str, object] = {}
        if stage is not None:
            metadata["stage"] = stage
        if self._closed:
            metadata["reason"] = "closed"
        elif self._cleanup_pending:
            metadata["reason"] = "cleanup_pending"
        if exc is not None:
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code in MACOS_SAFE_BACKEND_CODES:
                metadata["backend_code"] = code
            request_state = getattr(exc, "request_state", None)
            if isinstance(request_state, str) and request_state in {"not_sent", "response_received", "response_unknown"}:
                metadata["request_state"] = request_state
            operation_id = getattr(exc, "operation_id", None)
            if isinstance(operation_id, str) and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None:
                metadata["operation_id"] = operation_id
            steps = getattr(exc, "completed_steps", None)
            if isinstance(steps, (tuple, list)):
                metadata["completed_steps"] = list(dict.fromkeys(step for step in steps[: len(MACOS_SAFE_COMPLETED_STEPS)] if isinstance(step, str) and step in MACOS_SAFE_COMPLETED_STEPS))
            if isinstance(exc, ToolExecutionError):
                if stage is None and isinstance(exc.context.get("stage"), str):
                    metadata["stage"] = exc.context["stage"]
                reason = exc.context.get("reason")
                if reason in {"closed", "cleanup_pending", "session_unavailable"}:
                    metadata["reason"] = reason
        try:
            if self._session is not None:
                info_fn = getattr(self._session, "session_info", None)
                state = getattr(info_fn(), "state", "active") if callable(info_fn) else "active"
                if isinstance(state, str) and state in MACOS_SESSION_STATES:
                    metadata["session_state"] = state
        except BaseException:  # noqa: BLE001
            return metadata
        return metadata

    @staticmethod
    def _propagate_if_cancelled(exc: BaseException) -> None:
        if type(exc).__name__ == "OperationCancelledError" or getattr(exc, "code", None) == "operation_cancelled":
            import asyncio

            raise asyncio.CancelledError from exc

    def _wait_timeout(self) -> float:
        return max(0.5, float(self.action_timeout_seconds))

    @staticmethod
    def _ActionControl(driver: AppiumConnectMacOSDriver) -> Any:
        return driver._operation_control(max(5.0, float(driver.action_timeout_seconds) + 5.0), cancellation=driver._operation_cancellation)

    @staticmethod
    def _operation_control(timeout_seconds: float, *, cancellation: Event | None = None) -> Any:
        try:
            from appium_connect_agent.runtime import OperationControl
        except ImportError:
            return None
        return OperationControl(timeout_seconds=timeout_seconds, cancellation=cancellation)

    def _run_on_worker(
        self,
        func: Callable[[], _T],
        *,
        cancellation_check: Callable[[], None] | None = None,
        cancellation_event: Event | None = None,
        allow_cleanup_pending: bool = False,
    ) -> _T:
        if self._closed or (self._cleanup_pending and not allow_cleanup_pending):
            return func()

        def run_operation() -> _T:
            previous_cancellation = self._operation_cancellation
            self._operation_cancellation = cancellation_event
            try:
                return func()
            finally:
                self._operation_cancellation = previous_cancellation
                if self._session is not None:
                    self._last_operation_finished_at = time.monotonic()

        if self._borrowed_session and self._executor is None and cancellation_check is None:
            return run_operation()
        self._ensure_executor()
        try:
            return self._run_sync(run_operation, cancellation_check=cancellation_check, cancellation_event=cancellation_event)
        except BaseException:
            if not self._cleanup_pending and self._session is None and self._managed_scope is None and self._runtime is None:
                self._shutdown_executor()
            raise

    def _run_sync(
        self,
        func: Callable[[], _T],
        *,
        cancellation_check: Callable[[], None] | None = None,
        cancellation_event: Event | None = None,
    ) -> _T:
        if self._executor is None:
            return func()
        future = self._executor.submit(func)
        if cancellation_check is None:
            return future.result()
        while True:
            try:
                return future.result(timeout=0.025)
            except TimeoutError:
                if future.done():
                    return future.result()
                try:
                    cancellation_check()
                except BaseException:
                    if cancellation_event is not None:
                        cancellation_event.set()
                    with suppress(BaseException):
                        future.result()
                    raise

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-macos-driver")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True)
            self._executor = None

    def _target_missing(self) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target was not found.",
            metadata={"resolution_reason": "not_found", **self._safe_diagnostics()},
        )

    @staticmethod
    def _passed(output: object | None = None) -> dict[str, object]:
        return {"status": "passed", "output": output}

    @staticmethod
    def _failed(
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
