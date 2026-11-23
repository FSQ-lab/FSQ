# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

DEFAULT_SNAPSHOT_TEXT_LIMIT_CHARS = 160
DEFAULT_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_SNAPSHOT_STATE_DEFAULTS: Mapping[str, str] = {
    "enabled": "true",
    "visible": "true",
    "selected": "false",
}

_INVALID_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_VALID_XML_TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")


def sanitize_xml_text(value: str) -> str:
    return _INVALID_XML_CHARS_RE.sub("", value)


def sanitize_xml_tag(raw_tag: str | None, *, fallback: str = "Control") -> tuple[str, bool]:
    cleaned = sanitize_xml_text((raw_tag or "").strip())
    if cleaned and _VALID_XML_TAG_RE.fullmatch(cleaned) is not None and not cleaned.lower().startswith("xml"):
        return cleaned, False
    if not cleaned:
        return fallback, False
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("._-")
    if not candidate or not (candidate[0].isalpha() or candidate[0] == "_") or candidate.lower().startswith("xml"):
        candidate = f"{fallback}_{candidate}" if candidate else fallback
    return (candidate if _VALID_XML_TAG_RE.fullmatch(candidate) else fallback), True


def xml_local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1] if "}" in value else value


def count_xml_elements(element: ElementTree.Element) -> int:
    return 1 + sum(count_xml_elements(child) for child in list(element))


@dataclass(frozen=True)
class TreeCompactionPolicy:
    empty_root_tag: str
    interactive_tags: frozenset[str]
    identity_attribute_keys: frozenset[str]
    semantic_attribute_keys: frozenset[str]
    text_attribute_keys: frozenset[str]
    default_attribute_keys: frozenset[str] | None = None
    state_defaults: Mapping[str, str] = field(default_factory=lambda: DEFAULT_SNAPSHOT_STATE_DEFAULTS)
    text_limit_chars: int = DEFAULT_SNAPSHOT_TEXT_LIMIT_CHARS
    source_preview_chars: int = DEFAULT_SNAPSHOT_SOURCE_PREVIEW_CHARS
    tag_prefix_to_strip: str = ""
    secure_tag_predicate: Callable[[str, Mapping[str, str]], bool] | None = None


@dataclass(frozen=True)
class CompactedXmlResult:
    xml: str
    source_nodes: int
    retained_nodes: int
    layout_nodes_removed: int
    clipped_attributes: int
    offscreen_nodes_omitted: int
    children_truncated: int
    effective_max_depth: int | None
    max_siblings_per_parent: int | None


def empty_compacted_envelope(
    *,
    empty_root_tag: str,
    max_depth: int | None,
    text_limit_chars: int = DEFAULT_SNAPSHOT_TEXT_LIMIT_CHARS,
) -> dict[str, object]:
    safe_tag, _ = sanitize_xml_tag(empty_root_tag, fallback="Control")
    xml_str = f"<{safe_tag} />"
    return {
        "format": "xml",
        "xml": xml_str,
        "node_count": 0,
        "coverage": {
            "status": "unknown",
            "reason": "empty_tree",
            "source_nodes": 0,
            "retained_nodes": 0,
            "retained_characters": 0,
        },
        "compaction": {
            "applied": True,
            "text_attribute_limit": text_limit_chars,
            "max_depth": max_depth,
            "layout_nodes_removed": 0,
            "clipped_attributes": 0,
            "offscreen_nodes_omitted": 0,
        },
    }


def unparsed_fallback_envelope(
    *,
    serialized_source: str,
    source_nodes: int,
    max_depth: int | None,
    error: BaseException,
    preview_chars: int = DEFAULT_SNAPSHOT_SOURCE_PREVIEW_CHARS,
) -> dict[str, object]:
    preview = sanitize_xml_text(serialized_source)[:preview_chars]
    return {
        "format": "unparsed_xml",
        "xml": preview,
        "source_preview": preview,
        "source_length": len(serialized_source),
        "node_count": source_nodes,
        "truncated": len(serialized_source) > len(preview),
        "parse_error": type(error).__name__,
        "coverage": {
            "status": "unknown",
            "reason": "uncompacted_tree_fallback",
            "source_nodes": source_nodes,
            "retained_nodes": source_nodes,
            "retained_characters": len(preview),
        },
        "compaction": {
            "applied": False,
            "text_attribute_limit": None,
            "max_depth": max_depth,
            "layout_nodes_removed": 0,
            "clipped_attributes": 0,
            "offscreen_nodes_omitted": 0,
        },
    }


def build_compacted_envelope(
    result: CompactedXmlResult,
    *,
    policy: TreeCompactionPolicy,
    requested_max_depth: int | None,
    complete_for_scope: bool = True,
    upstream_reason: str = "requested_scope",
) -> dict[str, object]:
    is_partial = not complete_for_scope or result.layout_nodes_removed > 0 or result.clipped_attributes > 0 or result.offscreen_nodes_omitted > 0 or result.children_truncated > 0
    if not complete_for_scope:
        reason = upstream_reason
    elif is_partial:
        reason = "semantic_compaction"
    else:
        reason = "complete_tree"

    compaction: dict[str, object] = {
        "applied": True,
        "text_attribute_limit": policy.text_limit_chars,
        "max_depth": requested_max_depth,
        "layout_nodes_removed": result.layout_nodes_removed,
        "clipped_attributes": result.clipped_attributes,
        "offscreen_nodes_omitted": result.offscreen_nodes_omitted,
    }
    if result.children_truncated > 0:
        compaction["children_truncated"] = result.children_truncated
    if result.effective_max_depth is not None and requested_max_depth is not None and result.effective_max_depth < requested_max_depth:
        compaction["effective_max_depth"] = result.effective_max_depth
    if result.max_siblings_per_parent is not None:
        compaction["max_siblings_per_parent"] = result.max_siblings_per_parent

    return {
        "format": "xml",
        "xml": result.xml,
        "node_count": result.source_nodes,
        "coverage": {
            "status": "partial" if is_partial else "complete",
            "reason": reason,
            "source_nodes": result.source_nodes,
            "retained_nodes": result.retained_nodes,
            "retained_characters": len(result.xml),
        },
        "compaction": compaction,
    }


def compact_xml_element_tree(
    xml_root: ElementTree.Element,
    *,
    policy: TreeCompactionPolicy,
    max_depth: int | None = None,
    include_attributes: bool = False,
    include_offscreen: bool = False,
    max_xml_chars: int | None = None,
    depth_stepdowns: Sequence[int] = (),
    sibling_stepdowns: Sequence[int | None] = (None,),
) -> CompactedXmlResult:
    working_root = _clone_sanitized_element(xml_root, fallback_tag=policy.empty_root_tag)
    source_nodes = count_xml_elements(working_root)
    offscreen_omitted = 0 if include_offscreen else _prune_offscreen_descendants(working_root, policy=policy)
    _ = source_nodes - offscreen_omitted

    if max_depth is not None:
        candidate_depths: list[int | None] = [max_depth, *(step for step in depth_stepdowns if step < max_depth)]
    else:
        candidate_depths = [None]

    chosen: CompactedXmlResult | None = None
    for depth_limit in candidate_depths:
        for sibling_limit in sibling_stepdowns:
            compacted_entries, layout_removed, clipped_count = _compact_subtree(
                working_root,
                policy=policy,
                include_attributes=include_attributes,
                preserve_node=True,
            )
            compacted_node = compacted_entries[0]
            children_truncated = _bound_tree_depth_and_siblings(
                compacted_node,
                depth=1,
                max_depth=depth_limit,
                max_siblings=sibling_limit,
            )
            compact_xml = ElementTree.tostring(compacted_node, encoding="unicode", short_empty_elements=True)
            retained_nodes = count_xml_elements(compacted_node)
            candidate = CompactedXmlResult(
                xml=compact_xml,
                source_nodes=source_nodes,
                retained_nodes=retained_nodes,
                layout_nodes_removed=layout_removed,
                clipped_attributes=clipped_count,
                offscreen_nodes_omitted=offscreen_omitted,
                children_truncated=children_truncated,
                effective_max_depth=depth_limit,
                max_siblings_per_parent=sibling_limit,
            )
            chosen = candidate
            if max_xml_chars is None or len(compact_xml) <= max_xml_chars:
                return candidate

    if chosen is None:
        msg = "Tree compaction produced no candidate result"
        raise RuntimeError(msg)
    return chosen


def _clone_sanitized_element(element: ElementTree.Element, *, fallback_tag: str) -> ElementTree.Element:
    raw_local_tag = xml_local_name(element.tag)
    safe_tag, was_sanitized = sanitize_xml_tag(raw_local_tag, fallback=fallback_tag)
    attribs: dict[str, str] = {}
    if was_sanitized and raw_local_tag.strip():
        attribs["control_type"] = sanitize_xml_text(raw_local_tag.strip())
    for raw_key, raw_val in element.attrib.items():
        local_key = xml_local_name(str(raw_key))
        safe_key, _ = sanitize_xml_tag(local_key, fallback="attr")
        attribs[safe_key] = sanitize_xml_text(str(raw_val))
    cloned = ElementTree.Element(safe_tag, attribs)
    if element.text:
        cloned.text = sanitize_xml_text(element.text)
    for child in element:
        cloned.append(_clone_sanitized_element(child, fallback_tag=fallback_tag))
    return cloned


def _short_tag(tag: str, policy: TreeCompactionPolicy) -> str:
    local = xml_local_name(tag)
    if policy.tag_prefix_to_strip and local.startswith(policy.tag_prefix_to_strip):
        return local[len(policy.tag_prefix_to_strip) :]
    return local


def _is_secure_node(tag: str, attribs: Mapping[str, str], policy: TreeCompactionPolicy) -> bool:
    if policy.secure_tag_predicate is not None:
        return policy.secure_tag_predicate(tag, attribs)
    return False


def _has_non_positive_dimension(attribs: Mapping[str, str]) -> bool:
    for key in ("width", "height"):
        raw = attribs.get(key)
        if raw is not None:
            try:
                if float(raw) <= 0:
                    return True
            except ValueError:
                continue
    bounds = attribs.get("bounds")
    if bounds:
        match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds.strip())
        if match is not None:
            left, top, right, bottom = (int(group) for group in match.groups())
            if right - left <= 0 or bottom - top <= 0:
                return True
    return False


def _is_dead_or_offscreen_leaf(element: ElementTree.Element, *, policy: TreeCompactionPolicy) -> bool:
    if element.get("visible", "").strip().lower() == "false":
        return True
    if _short_tag(element.tag, policy) in policy.interactive_tags:
        return False
    is_secure = _is_secure_node(element.tag, element.attrib, policy)
    if not is_secure and (element.text or "").strip():
        return False
    for key in policy.identity_attribute_keys:
        if is_secure and key == "value":
            continue
        if element.attrib.get(key, "").strip():
            return False
    for state_key, default_val in policy.state_defaults.items():
        raw_state = element.attrib.get(state_key, "").strip().lower()
        if raw_state and raw_state != default_val:
            return False
    return _has_non_positive_dimension(element.attrib)


def _prune_offscreen_descendants(element: ElementTree.Element, *, policy: TreeCompactionPolicy) -> int:
    retained: list[ElementTree.Element] | None = None
    omitted = 0
    for index, child in enumerate(element):
        omitted += _prune_offscreen_descendants(child, policy=policy)
        if len(child) == 0 and _is_dead_or_offscreen_leaf(child, policy=policy):
            if retained is None:
                retained = element[:index]
            omitted += 1
        elif retained is not None:
            retained.append(child)
    if retained is not None:
        element[:] = retained
    return omitted


def _select_and_clip_attributes(
    tag: str,
    raw_attribs: Mapping[str, str],
    *,
    policy: TreeCompactionPolicy,
    include_attributes: bool,
) -> tuple[dict[str, str], int]:
    is_secure = _is_secure_node(tag, raw_attribs, policy)
    selected: dict[str, str] = {}
    clipped = 0
    for raw_key, raw_value in raw_attribs.items():
        key = xml_local_name(raw_key)
        if key in {"is_password", "_secure"}:
            continue
        if is_secure and key == "value":
            continue
        value = sanitize_xml_text(str(raw_value)).strip()
        if not value:
            continue
        if not include_attributes and policy.default_attribute_keys is not None and key not in policy.default_attribute_keys:
            continue
        default_state = policy.state_defaults.get(key)
        if default_state is not None and value.lower() == default_state:
            continue
        if key in policy.text_attribute_keys and len(value) > policy.text_limit_chars:
            value = value[: policy.text_limit_chars]
            clipped += 1
        selected[key] = value
    return selected, clipped


def _has_semantic_signal(
    selected_attribs: Mapping[str, str],
    *,
    text: str,
    policy: TreeCompactionPolicy,
) -> bool:
    if text:
        return True
    if any(key in policy.state_defaults for key in selected_attribs):
        return True
    if any(key in policy.identity_attribute_keys for key in selected_attribs):
        return True
    if _has_non_positive_dimension(selected_attribs) or selected_attribs.get("visible", "").lower() == "false":
        return False
    return any(key in policy.semantic_attribute_keys for key in selected_attribs)


def _compact_subtree(
    element: ElementTree.Element,
    *,
    policy: TreeCompactionPolicy,
    include_attributes: bool,
    preserve_node: bool = False,
) -> tuple[list[ElementTree.Element], int, int]:
    child_elements: list[ElementTree.Element] = []
    layout_removed = 0
    clipped_total = 0
    for child in element:
        compacted_children, child_removed, child_clipped = _compact_subtree(
            child,
            policy=policy,
            include_attributes=include_attributes,
            preserve_node=False,
        )
        child_elements.extend(compacted_children)
        layout_removed += child_removed
        clipped_total += child_clipped

    is_secure = _is_secure_node(element.tag, element.attrib, policy)
    selected_attribs, attr_clipped = _select_and_clip_attributes(
        element.tag,
        element.attrib,
        policy=policy,
        include_attributes=include_attributes,
    )
    raw_text = "" if is_secure else sanitize_xml_text(element.text or "").strip()
    text_clipped = 1 if len(raw_text) > policy.text_limit_chars else 0
    clipped_text = raw_text[: policy.text_limit_chars]

    interactive = _short_tag(element.tag, policy) in policy.interactive_tags
    if not preserve_node and not interactive and not _has_semantic_signal(selected_attribs, text=clipped_text, policy=policy):
        return child_elements, layout_removed + 1, clipped_total

    clipped_total += attr_clipped + text_clipped
    retained_el = ElementTree.Element(element.tag, selected_attribs)
    if clipped_text:
        retained_el.text = clipped_text
    retained_el.extend(child_elements)
    return [retained_el], layout_removed, clipped_total


def _bound_tree_depth_and_siblings(
    element: ElementTree.Element,
    *,
    depth: int,
    max_depth: int | None,
    max_siblings: int | None,
) -> int:
    children = list(element)
    if not children:
        return 0
    if max_depth is not None and depth >= max_depth:
        truncated_count = sum(count_xml_elements(child) for child in children)
        element[:] = []
        element.set("children_truncated", str(len(children)))
        return truncated_count
    truncated_total = 0
    if max_siblings is not None and len(children) > max_siblings:
        omitted_children = children[max_siblings:]
        kept_children = children[:max_siblings]
        truncated_total += sum(count_xml_elements(child) for child in omitted_children)
        element[:] = kept_children
        element.set("children_truncated", str(len(omitted_children)))
        children = kept_children
    for child in children:
        truncated_total += _bound_tree_depth_and_siblings(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            max_siblings=max_siblings,
        )
    return truncated_total
