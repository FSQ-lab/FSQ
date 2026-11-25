# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

if TYPE_CHECKING:
    from collections.abc import Collection

MAX_REDACTION_DEPTH = 128

_CREDENTIAL_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "private_values",
        "credentials",
    }
)
_URL_QUERY_ONLY_CREDENTIAL_KEYS = frozenset({"signature", "sig"})


def is_credential_key(key: object) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote(str(key)).strip()).lower().replace("-", "_")
    return normalized in _CREDENTIAL_KEYS


def _is_url_parameter_credential_key(key: str) -> bool:
    return is_credential_key(key) or key.strip().casefold() in _URL_QUERY_ONLY_CREDENTIAL_KEYS


def _safe_url_parameters(value: str) -> str:
    parameters = parse_qsl(value, keep_blank_values=True)
    if not any(_is_url_parameter_credential_key(key) for key, _ in parameters):
        return value
    return urlencode([(key, "[REDACTED]" if _is_url_parameter_credential_key(key) else item) for key, item in parameters])


def _sanitize_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
        safe_query = _safe_url_parameters(parsed.query)
        safe_fragment = parsed.fragment
        if "=" in parsed.fragment:
            # A '?' before the first assignment separates a route query;
            # after an assignment it may be part of the credential value.
            route, separator, parameters = parsed.fragment.partition("?")
            safe_fragment = route + separator + _safe_url_parameters(parameters) if separator and "=" not in route else _safe_url_parameters(parsed.fragment)
        if "@" not in parsed.netloc and safe_query == parsed.query and safe_fragment == parsed.fragment:
            return raw
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, safe_query, safe_fragment))
    except ValueError:
        return "[REDACTED_URL]"


def sanitize_urls(text: str, *, whole_url: bool = False) -> str:
    if whole_url:
        return _sanitize_url(text)
    return re.sub(r"https?://[^\s<>\"']+", lambda match: _sanitize_url(match.group(0)), text, flags=re.I)


def redact_text(
    text: str,
    secrets: Collection[str] = (),
    *,
    replacement: str = "***",
    auth_replacement: str | None = None,
) -> str:
    xml = _redact_xml(text, secrets)
    if xml is not None:
        return xml
    return _redact_plain_text(text, secrets, replacement=replacement, auth_replacement=auth_replacement)


def _redact_plain_text(
    text: str,
    secrets: Collection[str],
    *,
    replacement: str = "***",
    auth_replacement: str | None = None,
) -> str:
    effective_auth_replacement = f"Bearer {replacement}" if auth_replacement is None else auth_replacement
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, replacement)
    text = re.sub(
        r"(?im)((?:proxy[-_]authorization|authorization|set[-_]cookie|cookie)\s*[:=]\s*)[^\r\n]+",
        lambda match: f"{match.group(1)}{replacement}",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\r\n]+",
        lambda _match: effective_auth_replacement,
        text,
    )
    if replacement == "[REDACTED]":
        text = re.sub(
            r"(?im)((?:authorization|cookie|password|passwd|pwd|api[_-]?key|client[_-]?secret|secret|(?:access[_-]?|refresh[_-]?|id[_-]?)?token)[\"']?\s*[:=]\s*)[^\r\n]+",
            lambda match: f"{match.group(1)}{replacement}",
            text,
        )
    else:
        text = re.sub(
            r"(?i)(\b(?:password|passwd|pwd|api[_-]?key|client[_-]?secret|secret|(?:access[_-]?|refresh[_-]?|id[_-]?)?token)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            lambda match: f"{match.group(1)}{replacement}",
            text,
        )
    text = re.sub(
        r"(?i)([?&](?:token|access_token|refresh_token|id_token|api[_-]?key|apikey|client[_-]?secret|secret|password|passwd|pwd|signature|sig|authorization)=)[^&#\s\"']*",
        r"\1[REDACTED]",
        text,
    )
    return sanitize_urls(text)


def _redact_xml(text: str, secrets: Collection[str]) -> str | None:
    """Redact XML as decoded values, never as attribute syntax or entity spellings."""
    if not text.lstrip().startswith("<") or not text.rstrip().endswith(">"):
        return None
    if "<!DOCTYPE" in text:
        return "<Redacted />"
    try:
        root = ElementTree.fromstring(text)  # noqa: S314 - DTDs are rejected before parsing.
    except (ElementTree.ParseError, LookupError, ValueError):
        return None

    # ElementTree discards comments and processing instructions; don't return
    # the original source with their unchecked contents still present.
    changed = "<!--" in text or "<?" in text
    # XML-bearing consumers share the producer's marker so another persistence
    # layer does not rewrite an already-redacted tree with a different marker.
    for element in root.iter():
        for key, raw in element.attrib.items():
            safe = "***" if is_credential_key(key.rsplit("}", 1)[-1]) else _redact_plain_text(raw, secrets)
            if safe != raw:
                element.set(key, safe)
                changed = True
        if element.text:
            safe = _redact_plain_text(element.text, secrets)
            if safe != element.text:
                element.text = safe
                changed = True
        if element.tail:
            safe = _redact_plain_text(element.tail, secrets)
            if safe != element.tail:
                element.tail = safe
                changed = True
    if not changed:
        return text
    try:
        return ElementTree.tostring(root, encoding="unicode")
    except RecursionError:
        return "<Redacted />"


def _maintain_xml_envelope(original: dict, redacted: dict) -> None:
    if original.get("format") not in ("xml", "unparsed_xml"):
        return
    compaction = redacted.get("compaction")
    xml = redacted.get("xml")
    if not isinstance(compaction, dict) or not isinstance(xml, str) or original.get("xml") == xml:
        return
    limit = compaction.get("max_xml_chars")
    if type(limit) is not int or limit < 4:
        return
    coverage = redacted.get("coverage")
    if len(xml) > limit:
        # Redaction can expand XML (for example when replacing a one-character
        # credential absent from earlier settings). Never publish an oversized
        # result or clip through element syntax during late persistence.
        xml = redacted["xml"] = "<r/>"
        compaction["size_limit_applied"] = True
        if isinstance(coverage, dict):
            coverage["retained_nodes"] = 0
            if coverage.get("status") == "complete":
                coverage.update(status="partial", reason="semantic_compaction")
        if original.get("format") == "unparsed_xml":
            redacted["truncated"] = True
    if original.get("source_preview") == original.get("xml"):
        redacted["source_preview"] = xml
    if isinstance(coverage, dict):
        coverage["retained_characters"] = len(xml)


def redact_structure(
    value: object,
    secrets: Collection[str] = (),
    *,
    replacement: str = "***",
    auth_replacement: str | None = None,
    depth: int = 0,
    active_ids: set[int] | None = None,
) -> object:
    if depth > MAX_REDACTION_DEPTH:
        return "[REDACTED: nesting limit]"
    if isinstance(value, (dict, list, tuple)):
        if active_ids is None:
            active_ids = set()
        obj_id = id(value)
        if obj_id in active_ids:
            return "[REDACTED: cycle]"
        active_ids.add(obj_id)
        try:
            if isinstance(value, dict):
                redacted = {
                    key: replacement
                    if is_credential_key(key)
                    else redact_structure(
                        item,
                        secrets,
                        replacement=replacement,
                        auth_replacement=auth_replacement,
                        depth=depth + 1,
                        active_ids=active_ids,
                    )
                    for key, item in value.items()
                }
                _maintain_xml_envelope(value, redacted)
                return redacted
            return [
                redact_structure(
                    item,
                    secrets,
                    replacement=replacement,
                    auth_replacement=auth_replacement,
                    depth=depth + 1,
                    active_ids=active_ids,
                )
                for item in value
            ]
        finally:
            active_ids.discard(obj_id)
    if not isinstance(value, str):
        return value
    xml = _redact_xml(value, secrets)
    if xml is not None:
        return xml
    redacted_str = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted_str = redacted_str.replace(secret, replacement)
    stripped = redacted_str.strip()
    if stripped.startswith(("{", "[", '"')):
        try:
            decoded = json.loads(redacted_str)
        except RecursionError:
            return "[REDACTED: nesting limit]"
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)) and decoded != redacted_str:
            safe = redact_structure(
                decoded,
                secrets,
                replacement=replacement,
                auth_replacement=auth_replacement,
                depth=depth + 1,
                active_ids=active_ids,
            )
            if safe != decoded:
                return json.dumps(safe, ensure_ascii=False)
    return _redact_plain_text(redacted_str, secrets, replacement=replacement, auth_replacement=auth_replacement)
