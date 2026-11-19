# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from fsq_agent.drivers.macos._appium_connect import AppiumConnectMacOSDriver
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertVisibleParams,
    MacOSClickOnParams,
    MacOSLocator,
    MacOSPoint,
    MacOSRightClickOnParams,
    MacOSUiSnapshotParams,
)


def _obs(value: object, state: str = "value") -> Any:
    return SimpleNamespace(state=state, value=value if state == "value" else None)


def _attr_obs(name: str, value: object, state: str = "value") -> Any:
    return SimpleNamespace(name=name, state=state, value=value if state == "value" else None)


def _bounds(x: int = 10, y: int = 20, width: int = 40, height: int = 30) -> Any:
    return SimpleNamespace(x=x, y=y, width=width, height=height)


class TargetError(RuntimeError):
    def __init__(self, code: str, message: str = "target error") -> None:
        super().__init__(message)
        self.code = code


class ElementValidationError(RuntimeError):
    def __init__(self, code: str = "invalid_argument", message: str = "invalid selector") -> None:
        super().__init__(message)
        self.code = code


class InvalidSessionStateError(RuntimeError):
    def __init__(self, code: str = "session_closed", message: str = "session closed") -> None:
        super().__init__(message)
        self.code = code


class CommandError(RuntimeError):
    def __init__(self, code: str = "command_failed", message: str = "backend error") -> None:
        super().__init__(message)
        self.code = code


def _xml_to_tree_node(xml_el: ElementTree.Element) -> Any:
    attribs = dict(xml_el.attrib)
    x = attribs.pop("x", None)
    y = attribs.pop("y", None)
    w = attribs.pop("width", None)
    h = attribs.pop("height", None)
    bounds = _bounds(int(x), int(y), int(w), int(h)) if all(v is not None for v in (x, y, w, h)) else None

    identifier = attribs.pop("identifier", None)
    label = attribs.pop("label", None)
    title = attribs.pop("title", None)
    value = attribs.pop("value", None)
    placeholder = attribs.pop("placeholderValue", None)
    enabled_raw = attribs.pop("enabled", None)
    selected_raw = attribs.pop("selected", None)
    visible_raw = attribs.pop("visible", None)

    visibility = "unknown"
    if visible_raw is not None:
        visibility = "visible" if visible_raw.lower() == "true" else "not_visible"

    return SimpleNamespace(
        type=_obs(xml_el.tag),
        id=_obs(identifier, "value" if identifier is not None else "missing"),
        text=_obs(value or label or title, "value" if (value or label or title) else "missing"),
        label=_obs(label, "value" if label is not None else "missing"),
        title=_obs(title, "value" if title is not None else "missing"),
        value=_obs(value, "value" if value is not None else "missing"),
        placeholder_value=_obs(placeholder, "value" if placeholder is not None else "missing"),
        element_type=_obs(None, "missing"),
        enabled=_obs(enabled_raw.lower() == "true") if enabled_raw is not None else _obs(None, "missing"),
        selected=_obs(selected_raw.lower() == "true") if selected_raw is not None else _obs(None, "missing"),
        bounds=_obs(bounds, "value" if bounds is not None else "unknown"),
        visibility=visibility,
        viewport_relation="inside",
        extra_attributes=attribs,
        raw_text=(xml_el.text or ""),
        children=tuple(_xml_to_tree_node(child) for child in list(xml_el)),
    )


class ElementStub:
    def __init__(self, identifier: str, **attributes: Any) -> None:
        self.identifier = identifier
        self.attributes = attributes

    def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
        del control
        items = []
        for name in attributes:
            if name == "id":
                items.append(_attr_obs("id", self.identifier))
            elif name == "type":
                items.append(_attr_obs("type", self.attributes.get("type", "XCUIElementTypeButton")))
            elif name == "displayed":
                items.append(_attr_obs("displayed", bool(self.attributes.get("displayed", True))))
            elif name == "enabled":
                items.append(_attr_obs("enabled", bool(self.attributes.get("enabled", True))))
            elif name == "bounds":
                items.append(_attr_obs("bounds", self.attributes.get("bounds", _bounds())))
            else:
                val = self.attributes.get(name)
                items.append(_attr_obs(name, val, "value" if val is not None else "missing"))
        return SimpleNamespace(attributes=tuple(items))

    def click(self, **kwargs: Any) -> None:
        return None

    def right_click(self, **kwargs: Any) -> None:
        return None


class _OutcomeElementProxy:
    def __init__(self, elements: tuple[ElementStub, ...], error: Exception | None) -> None:
        self.elements = elements
        self.error = error

    def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
        if self.error is not None:
            raise self.error
        if not self.elements:
            raise TargetError("target_not_found")
        if len(self.elements) > 1:
            raise TargetError("target_ambiguous")
        return self.elements[0].inspect(attributes=attributes, control=control)

    def click(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        if len(self.elements) == 1:
            self.elements[0].click(**kwargs)

    def right_click(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        if len(self.elements) == 1:
            self.elements[0].right_click(**kwargs)


class SessionStub:
    def __init__(
        self,
        elements: list[ElementStub] | tuple[ElementStub, ...] = (),
        *,
        source: str = "<XCUIElementTypeApplication/>",
        error: Exception | None = None,
    ) -> None:
        self.elements = tuple(elements)
        self.source = source
        self.error = error
        self.calls: list[tuple[str, str, str | None]] = []
        self.device = SimpleNamespace(
            click_at=lambda *args, **kwargs: pytest.fail("Unexpected coordinate click_at"),
        )

    def session_info(self) -> Any:
        return SimpleNamespace(id="mac2:stub", appium_session_id="mac2:stub", state="active", platform="macos")

    def element(self, by: str, value: str, *, parent: Any = None, predicate: str | None = None) -> Any:
        self.calls.append((by, value, predicate) if parent is None else (f"scoped:{by}", value, predicate))
        return _OutcomeElementProxy(self.elements, self.error)

    def observe_tree(self, *, control: Any = None) -> Any:
        del control
        if self.error is not None:
            raise self.error
        xml_root = ElementTree.fromstring(self.source)  # noqa: S314
        return SimpleNamespace(
            coverage="requested_scope",
            complete_for_requested_scope=True,
            viewport=SimpleNamespace(width=_obs(1440), height=_obs(900), rotation=_obs(None, "not_applicable")),
            roots=(_xml_to_tree_node(xml_root),),
        )


def test_locator_combines_value_and_type_without_first_match_fallback() -> None:
    session = SessionStub([ElementStub("one")])
    driver = AppiumConnectMacOSDriver(session=session)
    result = driver.assert_visible(MacOSAssertVisibleParams(locator=MacOSLocator(value="11", controlType="XCUIElementTypeStaticText")))
    assert result["status"] == "passed"
    assert len(session.calls) == 1
    assert session.calls[0][0] == "-ios predicate string"
    assert 'value == "11"' in session.calls[0][1]
    assert 'amType == "XCUIElementTypeStaticText"' in session.calls[0][1]
    assert " AND " in session.calls[0][1]


def test_locator_literal_apostrophe_and_quotes_are_encoded() -> None:
    session = SessionStub([ElementStub("one")])
    driver = AppiumConnectMacOSDriver(session=session)
    driver.assert_visible(MacOSAssertVisibleParams(target='Men\'s "Evo" \\ shoes'))
    assert session.calls[0][0] == "-ios predicate string"
    assert r'"Men\'s \"Evo\" \\ shoes"' in session.calls[0][1] or '"Men\'s \\"Evo\\" \\\\ shoes"' in session.calls[0][1]


def test_xpath_locator_literal_apostrophe_and_quotes_are_encoded() -> None:
    session = SessionStub([ElementStub("one")])
    driver = AppiumConnectMacOSDriver(session=session)
    driver.assert_visible(MacOSAssertVisibleParams(locator=MacOSLocator(xpath="//Button", label='Men\'s "Evo" shoes')))
    assert session.calls[0][0] == "xpath"
    assert "concat(" in session.calls[0][1]


def test_title_is_preserved_and_supported_by_locators() -> None:
    title = "Save Link As" + "x" * 50
    source = f'<XCUIElementTypeApplication><XCUIElementTypeMenuItem identifier="a" title="{title}"/><XCUIElementTypeMenuItem identifier="b" title="{title}"/></XCUIElementTypeApplication>'
    driver = AppiumConnectMacOSDriver(session=SessionStub(source=source))
    assert "title" in MacOSLocator.model_json_schema()["properties"]

    tree = driver.ui_snapshot(MacOSUiSnapshotParams())
    assert tree["page_source"]["root"]["children"][0]["attributes"]["title"] == title[:50]

    locator_session = SessionStub([ElementStub("one")])
    AppiumConnectMacOSDriver(session=locator_session).assert_visible(MacOSAssertVisibleParams(locator=MacOSLocator(title=title)))
    assert f'title == "{title}"' in locator_session.calls[0][1]

    target_session = SessionStub([ElementStub("one")])
    AppiumConnectMacOSDriver(session=target_session).assert_visible(MacOSAssertVisibleParams(target=title))
    assert f'title == "{title}"' in target_session.calls[0][1]

    amb_result = AppiumConnectMacOSDriver(session=SessionStub([ElementStub("a", title=title), ElementStub("b", title=title)], source=source)).assert_visible(MacOSAssertVisibleParams(target=title))
    assert amb_result["status"] == "failed"
    assert amb_result["failure_category"] == "target_resolution_error"
    assert amb_result["metadata"]["resolution_reason"] == "ambiguous"
    assert amb_result["metadata"]["candidates"][0]["display"]["title"] == title[:50]
    assert amb_result["metadata"]["candidates"][0]["locator"]["title"] == title


def test_ambiguous_locator_never_claims_visible() -> None:
    driver = AppiumConnectMacOSDriver(session=SessionStub([ElementStub("a"), ElementStub("b")]))
    result = driver.assert_visible(MacOSAssertVisibleParams(target="same"))
    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["resolution_reason"] == "ambiguous"


def test_backend_error_not_disguised_as_missing_target() -> None:
    driver = AppiumConnectMacOSDriver(session=SessionStub(error=RuntimeError("SECRET backend body")))
    result = driver.assert_visible(MacOSAssertVisibleParams(target="shoe"))
    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert "SECRET" not in json.dumps(result)


def test_tree_clipping_is_explicit() -> None:
    source = '<XCUIElementTypeApplication><XCUIElementTypeButton label="' + "x" * 60 + '"/></XCUIElementTypeApplication>'
    driver = AppiumConnectMacOSDriver(session=SessionStub(source=source))
    tree = driver.ui_snapshot(MacOSUiSnapshotParams())
    node = tree["page_source"]["root"]["children"][0]
    assert node["truncated_attributes"] == ["label"]


@pytest.mark.parametrize(
    ("exc", "expected_category", "expected_key", "expected_val"),
    [
        (ElementValidationError("invalid_argument", "private selector"), "configuration_error", "resolution_reason", "invalid_locator"),
        (InvalidSessionStateError("session_closed", "private session"), "action_error", "reason", "session_unavailable"),
        (CommandError("command_failed", "private response"), "action_error", "backend_code", "command_failed"),
    ],
)
def test_backend_failure_classification(exc: Exception, expected_category: str, expected_key: str, expected_val: str) -> None:
    session = SessionStub(error=exc)
    result = AppiumConnectMacOSDriver(session=session).assert_visible(MacOSAssertVisibleParams(target="shoe"))
    assert result["status"] == "failed"
    assert result["failure_category"] == expected_category
    assert result["metadata"][expected_key] == expected_val
    assert "private" not in json.dumps(result)


def test_no_session_is_distinct() -> None:
    with pytest.raises(ConfigurationError) as error:
        AppiumConnectMacOSDriver().ui_snapshot(MacOSUiSnapshotParams())
    assert error.value.context["resolution_reason"] == "session_unavailable"


def test_explicit_selector_and_semantic_constraints_intersect() -> None:
    session = SessionStub([ElementStub("same")])
    locator = MacOSLocator(xpath="//Button", predicate="enabled == true", label="Submit", controlType="Button")
    result = AppiumConnectMacOSDriver(session=session).assert_visible(MacOSAssertVisibleParams(locator=locator))
    assert result["status"] == "passed"
    assert session.calls[0][0] == "xpath"
    assert session.calls[0][1].startswith("(//Button)[")
    assert "@label='Submit'" in session.calls[0][1]
    assert session.calls[1] == ("scoped:-ios predicate string", "enabled == true", None)


def test_role_and_predicate_constraints_intersect_without_rejection() -> None:
    session = SessionStub([ElementStub("same")])
    locator = MacOSLocator(role="AXButton", predicate="enabled == true", label="Submit")
    result = AppiumConnectMacOSDriver(session=session).assert_visible(MacOSAssertVisibleParams(locator=locator))
    assert result["status"] == "passed"
    assert session.calls[0][0] == "xpath"
    assert "@role='AXButton'" in session.calls[0][1]
    assert "@label='Submit'" in session.calls[0][1]
    assert session.calls[1] == ("scoped:-ios predicate string", "enabled == true", None)
    mismatch_driver = AppiumConnectMacOSDriver(session=SessionStub([ElementStub("first")]))
    calls_count = 0

    def distinct_inspect(*, attributes: tuple[str, ...], control: Any = None) -> Any:
        nonlocal calls_count
        calls_count += 1
        if "type" in attributes:
            return SimpleNamespace(
                attributes=(
                    _attr_obs("type", "XCUIElementTypeButton"),
                    _attr_obs("id", f"id-{calls_count}"),
                    _attr_obs("label", "Submit"),
                    _attr_obs("title", None, "missing"),
                    _attr_obs("value", None, "missing"),
                    _attr_obs("bounds", _bounds()),
                )
            )
        return SimpleNamespace(attributes=(_attr_obs("enabled", True), _attr_obs("displayed", True)))

    mismatch_driver._session.elements[0].inspect = distinct_inspect  # type: ignore[method-assign]
    mismatch_result = mismatch_driver.assert_visible(MacOSAssertVisibleParams(locator=locator))
    assert mismatch_result["status"] == "failed"
    assert mismatch_result["failure_category"] == "target_resolution_error"


def test_xpath_or_role_with_broad_predicate_does_not_raise_false_ambiguity() -> None:
    target_button = ElementStub("save-btn", type="XCUIElementTypeButton", label="Save", enabled=True)
    other_enabled = ElementStub("cancel-btn", type="XCUIElementTypeButton", label="Cancel", enabled=True)

    class ScopedSession(SessionStub):
        def element(self, by: str, value: str, *, parent: Any = None, predicate: str | None = None) -> Any:
            self.calls.append((by if parent is None else f"scoped:{by}", value, predicate))
            if parent is None and by == "xpath":
                return _OutcomeElementProxy((target_button,), None)
            if parent is None and by == "-ios predicate string":
                return _OutcomeElementProxy((target_button, other_enabled), None)
            if parent is not None and 'label == "Save"' in value:
                return _OutcomeElementProxy((target_button,), None)
            if parent is not None:
                # Simulate container/self + descendant matching broad predicate until narrowed by signature
                return _OutcomeElementProxy((target_button, other_enabled), None)
            return _OutcomeElementProxy((), None)

    session = ScopedSession([target_button, other_enabled])
    locator = MacOSLocator(role="AXButton", label="Save", predicate="enabled == true")
    result = AppiumConnectMacOSDriver(session=session).assert_visible(MacOSAssertVisibleParams(locator=locator))
    assert result["status"] == "passed"
    assert all(call[0] != "-ios predicate string" for call in session.calls)
    assert any("amRect.x == 10" in call[1] for call in session.calls)

    name_session = SessionStub([target_button])
    AppiumConnectMacOSDriver(session=name_session).assert_visible(MacOSAssertVisibleParams(target="Save"))
    assert 'name == "Save"' not in name_session.calls[0][1]
    for attr in ("identifier", "label", "value", "title"):
        assert f'{attr} == "Save"' in name_session.calls[0][1]


@pytest.mark.parametrize("method,params_type", [("click_on", MacOSClickOnParams), ("right_click_on", MacOSRightClickOnParams)])
def test_coordinates_cannot_override_missing_semantic_target(method: str, params_type: type[Any]) -> None:
    driver = AppiumConnectMacOSDriver(session=SessionStub())
    result = getattr(driver, method)(params_type(target="Missing", point=MacOSPoint(x=10, y=20)))
    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["resolution_reason"] == "not_found"


def test_interactive_unlabelled_node_preserved() -> None:
    result = AppiumConnectMacOSDriver(session=SessionStub(source='<XCUIElementTypeApplication><Wrapper><XCUIElementTypeButton enabled="true"/></Wrapper></XCUIElementTypeApplication>')).ui_snapshot(
        MacOSUiSnapshotParams()
    )
    assert result["page_source"]["root"]["children"][0]["type"] == "XCUIElementTypeButton"


def test_ui_snapshot_schema_rejects_query() -> None:
    from fsq_agent.harnesses._macos import MacOSHarness

    with pytest.raises(ValidationError):
        MacOSUiSnapshotParams.model_validate({"query": {"text": "Save"}})
    schema = next(item for item in MacOSHarness(driver=AppiumConnectMacOSDriver()).action_space() if item.name == "ui_snapshot")
    assert "query" not in schema.params_json_schema["properties"]


@pytest.mark.parametrize("stage", ["visibility", "geometry", "click"])
def test_live_element_failures_keep_session_reason(stage: str) -> None:
    class BrokenElement(ElementStub):
        def inspect(self, *, attributes: tuple[str, ...], control: Any = None) -> Any:
            if stage == "visibility" and "displayed" in attributes:
                raise InvalidSessionStateError("session_closed", "private")
            if stage == "geometry" and "bounds" in attributes:
                raise InvalidSessionStateError("session_closed", "private")
            return super().inspect(attributes=attributes, control=control)

        def click(self, **kwargs: Any) -> None:
            if stage == "click":
                raise InvalidSessionStateError("session_closed", "private")

    driver = AppiumConnectMacOSDriver(session=SessionStub([BrokenElement("one")]))
    method, params = {
        "visibility": (driver.assert_visible, MacOSAssertVisibleParams(target="one")),
        "geometry": (driver.right_click_on, MacOSRightClickOnParams(target="one")),
        "click": (driver.click_on, MacOSClickOnParams(target="one")),
    }[stage]
    result = method(params)
    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["reason"] == "session_unavailable"
    assert "private" not in json.dumps(result)
