# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

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


def sanitize_urls(text: str) -> str:
    def safe_parameters(value: str) -> str:
        parameters = parse_qsl(value, keep_blank_values=True)
        if not any(_is_url_parameter_credential_key(key) for key, _ in parameters):
            return value
        return urlencode([(key, "[REDACTED]" if _is_url_parameter_credential_key(key) else item) for key, item in parameters])

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            safe_query = safe_parameters(parsed.query)
            safe_fragment = parsed.fragment
            if "=" in parsed.fragment:
                # A '?' before the first assignment separates a route query;
                # after an assignment it may be part of the credential value.
                route, separator, parameters = parsed.fragment.partition("?")
                safe_fragment = route + separator + safe_parameters(parameters) if separator and "=" not in route else safe_parameters(parsed.fragment)
            if "@" not in parsed.netloc and safe_query == parsed.query and safe_fragment == parsed.fragment:
                return raw
            return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, safe_query, safe_fragment))
        except ValueError:
            return "[REDACTED_URL]"

    return re.sub(r"https?://[^\s<>\"']+", replace, text, flags=re.I)


def redact_text(
    text: str,
    secrets: tuple[str, ...] = (),
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


def redact_structure(
    value: object,
    secrets: tuple[str, ...] = (),
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
                return {
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
    redacted_str = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted_str = redacted_str.replace(secret, replacement)
    stripped = redacted_str.strip()
    if stripped.startswith(("{", "[", '"')):
        try:
            decoded = json.loads(redacted_str)
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
    return redact_text(redacted_str, secrets, replacement=replacement, auth_replacement=auth_replacement)
