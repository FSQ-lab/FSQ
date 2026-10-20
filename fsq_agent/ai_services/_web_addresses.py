# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

# Detect supplied syntax before validating it, including unsupported hosts.
_NAVIGATION = re.compile(r"[a-z][a-z0-9+.-]*[:\uff1a]//|(?:https?|ftp|ftps|file|javascript|data|about|mailto|ws|wss)[:\uff1a]|https?[/\\]|//(?=\S)", re.IGNORECASE)
_CHINESE_PAIRS = dict(zip("\uff08\uff3b【《〈「『“\u2018\uff02\uff07", "\uff09\uff3d】》〉」』”\u2019\uff02\uff07", strict=True))
_QUOTES = {'"': '"', "'": "'", "`": "`", "<": ">", **_CHINESE_PAIRS}
_PROSE_END = "\uff0c。\uff1b\uff01\uff1f、\uff1a…—" + "".join(_CHINESE_PAIRS.values())
_CLOSING = ")]}>\"'`" + "".join(_CHINESE_PAIRS.values())
_LABEL = re.compile(r"(?:目标网址|目标地址|网站地址|网址|地址|链接|URL|URI)[:\uff1a]\s*$", re.IGNORECASE)


class QuickTestWebAddressParser:
    """Extract exact navigation references, without repairing ambiguous input."""

    def validate(self, address: str) -> str:
        """Validate one complete structured URL without extracting or rewriting it."""
        _validate(address)
        return address

    def extract(self, goal: str) -> tuple[str, ...]:
        addresses, _ = _scan(goal, 0)
        return tuple(dict.fromkeys(addresses))

    def contains(self, goal: str, reference: str) -> bool:
        """Accept only an entire reference with exactly the supplied spelling."""
        return reference in self.extract(goal)


def _check_prefix(text: str, start: int) -> None:
    label = _LABEL.search(text, 0, start)
    if label is not None:
        _check_prefix(text, label.start())
        return
    if start:
        before = text[start - 1]
        cjk = unicodedata.name(before, "").startswith(("CJK ", "HIRAGANA ", "KATAKANA ", "HANGUL "))
        if (before.isalnum() and not cjk) or before in "*_./:\uff1a\\-+@%?=&#!~" or unicodedata.category(before).startswith("M"):
            raise ValueError("Embedded navigation reference.")


def _check_suffix(text: str, end: int) -> None:
    # Punctuation outside an established enclosure can end a sentence, but
    # cannot hide a continuing path, query, fragment or unseparated prose.
    if end < len(text) and text[end] in ".,;!":
        punctuation_end = end
        while punctuation_end < len(text) and text[punctuation_end] in ".,;!":
            punctuation_end += 1
        if punctuation_end == len(text) or text[punctuation_end].isspace() or text[punctuation_end] in _PROSE_END:
            return
    if end < len(text) and text[end].isascii() and (text[end].isalnum() or text[end] in "-._~:/?#@!$&*+,;=%([\\"):
        raise ValueError("Ambiguous enclosing boundary.")


def _enclosed(text: str, start: int) -> tuple[str, int]:
    closing = _QUOTES[text[start]]
    opening = text[start]
    depth = 1
    for end in range(start + 1, len(text)):
        char = text[end]
        if char == closing:
            depth -= 1
            if not depth:
                return text[start + 1 : end], end + 1
        elif opening != closing and char == opening:
            depth += 1
    raise ValueError("Unclosed navigation reference.")


def _reference_at(text: str, position: int) -> bool:
    """Recognize only adjacent presentation wrappers around navigation syntax."""
    while position < len(text):
        if _NAVIGATION.match(text, position):
            return True
        if text.startswith(("**", "__"), position):
            position += 2
        elif text[position] in _QUOTES or text[position] in "([{":
            position += 1
        else:
            break
    return False


def _scan(text: str, position: int, closing: str | None = None) -> tuple[list[str], int]:
    """Scan prose recursively, keeping matched context separate from URL data."""
    addresses = []
    while position < len(text):
        char = text[position]
        if closing is not None and char == closing:
            return addresses, position + 1
        if char == "[":
            link = _markdown_link(text, position)
            if link is not None:
                address, position = link
                if address is not None:
                    addresses.append(address)
                continue
        if _reference_at(text, position):
            _check_prefix(text, position)
            bare = _NAVIGATION.match(text, position) is not None
            found, position = _presentation(text, position)
            addresses.extend(found)
            following, position = _following(text, position, closing, external_closers=bare)
            addresses.extend(following)
            continue
        if closing is not None:
            if char in _CHINESE_PAIRS:
                found, position = _scan(text, position + 1, _CHINESE_PAIRS[char])
                addresses.extend(found)
                continue
            if char in _CHINESE_PAIRS.values():
                raise ValueError("Mismatched enclosing boundary.")
        position += 1
    if closing is not None:
        raise ValueError("Unclosed enclosing boundary.")
    return addresses, position


def _presentation(text: str, start: int, marker: str | None = None) -> tuple[list[str], int]:
    if text.startswith(("**", "__"), start):
        strong = text[start : start + 2]
        addresses, end = _presentation(text, start + 2, strong)
        if not text.startswith(strong, end):
            raise ValueError("Unclosed strong navigation wrapper.")
        return addresses, end + 2
    if _NAVIGATION.match(text, start):
        address, end = _bare(text, start, marker)
        _validate(address)
        return [address], end
    opening = text[start]
    if opening in _QUOTES and _NAVIGATION.match(text, start + 1):
        # Explicitly enclosed URL data stays opaque to prose scanning.
        address, end = _enclosed(text, start)
        _validate(address)
        return [address], end
    closing = _QUOTES.get(opening) or {"(": ")", "[": "]", "{": "}"}[opening]
    return _scan(text, start + 1, closing)


def _following(text: str, end: int, closing: str | None, *, external_closers: bool = False) -> tuple[list[str], int]:
    addresses = []
    while end < len(text):
        char = text[end]
        if char == closing:
            return addresses, end
        if char == "<":
            raise ValueError("Ambiguous navigation delimiter.")
        if char in _CHINESE_PAIRS:
            found, end = _scan(text, end + 1, _CHINESE_PAIRS[char])
            addresses.extend(found)
        elif char in _CLOSING:
            # Bare references retain external prose delimiters, but cannot
            # consume a known enclosing context or hide URL continuation.
            if closing is not None or not external_closers:
                raise ValueError("Mismatched enclosing boundary.")
            end += 1
        else:
            break
    _check_suffix(text, end)
    return addresses, end


def _bare(text: str, start: int, marker: str | None = None) -> tuple[str, int]:
    end = start
    pairs = []
    while end < len(text):
        char = text[end]
        if char.isspace():
            break
        if _control(char):
            raise ValueError("Control character in navigation reference.")
        if marker is not None and text.startswith(marker, end):
            break
        if text.startswith(("**", "__"), end):
            raise ValueError("Ambiguous strong navigation wrapper.")
        if char == "\uff1a" and re.fullmatch(r"https?://[^/?#]*", text[start:end], re.IGNORECASE):
            raise ValueError("Fullwidth colon in navigation authority.")
        if char in _PROSE_END or char in _QUOTES or char == ">":
            break
        if not char.isascii():
            raise ValueError("Unicode navigation data requires explicit boundaries.")
        if char in "([":
            pairs.append(char)
        elif char == "}":
            break
        elif char in ")]":
            if not pairs:
                break
            if pairs.pop() != ("(" if char == ")" else "["):
                raise ValueError("Mismatched navigation brackets.")
        end += 1
    if pairs:
        raise ValueError("Unclosed navigation brackets.")
    address = text[start:end]
    for match in re.finditer(r",[a-z][a-z0-9_-]*", address, re.IGNORECASE):
        if not address[match.end() :].startswith("="):
            raise ValueError("Ambiguous prose after navigation reference.")
    return address, end


def _markdown_link(text: str, start: int) -> tuple[str | None, int] | None:
    # Consume the whole link so neither labels nor optional titles are URLs.
    depth = 1
    end = start + 1
    while end < len(text) and depth:
        if text[end] == "\\":
            end += 2
            continue
        if text[end] == "[":
            depth += 1
        elif text[end] == "]":
            depth -= 1
        end += 1
    if depth or end >= len(text) or text[end] != "(":
        return None
    position = end + 1
    while position < len(text) and text[position] == " ":
        position += 1
    destination_start = position
    if position < len(text) and text[position] == "<":
        destination, position = _enclosed(text, position)
    else:
        begin = position
        depth = 0
        while position < len(text):
            char = text[position]
            if char.isspace() or (char == ")" and depth == 0):
                break
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            position += 1
        destination = text[begin:position]
    # Keep the source boundary visible to lookahead, including a closer after //.
    has_address = _NAVIGATION.search(text, destination_start, position + 1) is not None
    if has_address:
        _validate(destination)
    title_allowed = position < len(text) and text[position].isspace()
    while position < len(text) and text[position].isspace():
        position += 1
    if title_allowed and position < len(text) and text[position] in {'"', "'", "("}:
        closing = ")" if text[position] == "(" else text[position]
        position += 1
        while position < len(text) and text[position] != closing:
            position += 2 if text[position] == "\\" else 1
        if position >= len(text):
            raise ValueError("Unclosed Markdown title.")
        position += 1
        while position < len(text) and text[position].isspace():
            position += 1
    if position >= len(text) or text[position] != ")":
        raise ValueError("Unclosed Markdown destination.")
    return destination if has_address else None, position + 1


def _control(char: str) -> bool:
    return unicodedata.category(char) in {"Cc", "Cf", "Cs"}


def _validate(address: str) -> None:
    if not re.match(r"https?://", address, re.IGNORECASE) or any(_control(char) or char.isspace() or char in "\\<>`" for char in address):
        raise ValueError("Invalid HTTP(S) navigation reference.")
    if re.search(r"%(?![0-9a-f]{2})", address, re.IGNORECASE):
        raise ValueError("Malformed navigation escape.")
    parsed = urlsplit(address)
    authority = parsed.netloc
    if not parsed.hostname or "@" in authority:
        raise ValueError("Invalid navigation authority.")
    if authority.startswith("["):
        bracket = authority.find("]")
        ipaddress.IPv6Address(authority[1:bracket])
        tail = authority[bracket + 1 :]
        if tail and not tail.startswith(":"):
            raise ValueError("Invalid IPv6 navigation authority.")
    else:
        host, separator, port = authority.partition(":")
        if any(not (char.isalnum() or char in ".-_") for char in host):
            raise ValueError("Invalid navigation host.")
        tail = separator + port
    if tail and (not re.fullmatch(r":[0-9]+", tail) or not 1 <= int(tail[1:]) <= 65535):
        raise ValueError("Invalid navigation port.")
    pairs = []
    for char in parsed.path + parsed.query + parsed.fragment:
        if char in "([":
            pairs.append(char)
        elif char in ")]" and (not pairs or pairs.pop() != ("(" if char == ")" else "[")):
            raise ValueError("Mismatched navigation brackets.")
    if pairs:
        raise ValueError("Unclosed navigation brackets.")
