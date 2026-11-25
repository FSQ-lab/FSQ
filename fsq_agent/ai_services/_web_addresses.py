# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit


def _control(char: str) -> bool:
    return unicodedata.category(char) in {"Cc", "Cf", "Cs"}


def validate_quick_test_web_url(url: str) -> str:
    """Validate one complete HTTP(S) URL and return it unchanged."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("Invalid HTTP(S) URL.")
    if not re.match(r"https?://", url, re.IGNORECASE) or any(_control(char) or char.isspace() or char in "\\<>`" for char in url):
        raise ValueError("Invalid HTTP(S) URL.")
    if re.search(r"%(?![0-9a-f]{2})", url, re.IGNORECASE):
        raise ValueError("Malformed URL percent-escape.")
    parsed = urlsplit(url)
    authority = parsed.netloc
    if not parsed.hostname or "@" in authority:
        raise ValueError("Invalid URL authority.")
    if authority.startswith("["):
        bracket = authority.find("]")
        if bracket == -1:
            raise ValueError("Invalid IPv6 URL authority.")
        ipaddress.IPv6Address(authority[1:bracket])
        tail = authority[bracket + 1 :]
        if tail and not tail.startswith(":"):
            raise ValueError("Invalid IPv6 URL authority.")
    else:
        host, separator, port = authority.partition(":")
        if not host or any(not (char.isalnum() or char in ".-_") for char in host):
            raise ValueError("Invalid URL host.")
        tail = separator + port
    if tail and (not re.fullmatch(r":[0-9]+", tail) or not 1 <= int(tail[1:]) <= 65535):
        raise ValueError("Invalid URL port.")
    return url
