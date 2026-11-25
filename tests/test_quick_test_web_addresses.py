# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.ai_services import validate_quick_test_web_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com:8080/path?q=1#frag",
        "HTTPS://Example.TEST:443/a(b(c))?x=%2f&next=https://inner.test/#片段!",
        "https://[::1]:8443/a(b)?#",
        "https://例子.test/路径\uff08说明\uff09?q=中文\uff0c值#片段\uff01",
        "https://example.test/report.",
    ],
)
def test_validate_quick_test_web_url_accepts_valid_http_urls_verbatim(url):
    assert validate_quick_test_web_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        " https://example.test",
        "https://example.test ",
        "Open https://example.test",
        "<https://example.test>",
        "ftp://example.test",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "//example.test",
        "https://user:password@example.test",
        "https://example.test/%GG",
        "https://example.test:0",
        "https://example.test:65536",
        "https://[::1",
        "https://example.test/a\x00",
        "https://example.test/\\file",
        "https://example.test/a b",
    ],
)
def test_validate_quick_test_web_url_rejects_invalid_or_unsafe_urls(url):
    with pytest.raises(ValueError, match=r".+"):
        validate_quick_test_web_url(url)
