# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.ai_services import QuickTestWebAddressParser


@pytest.mark.parametrize(
    ("goal", "address"),
    [
        ("打开https://example.test/docs\uff0c确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs。确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs\uff1b确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs\uff01确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs\uff1f确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs”并确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs\u2019并确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs\uff09并确认标题", "https://example.test/docs"),
        ("打开https://example.test/docs】并确认标题", "https://example.test/docs"),
        ("Open (https://example.test/a(b(c)d)e) and verify", "https://example.test/a(b(c)d)e"),
        ("Open https://[2001:db8::1]:8443/a(b)?x=%2f#part and verify", "https://[2001:db8::1]:8443/a(b)?x=%2f#part"),
        ('Open [label https://ignored.test](https://example.test/a(b) "title https://title.test") and verify', "https://example.test/a(b)"),
        ("Open [label](<https://example.test/路径?q=值\uff0c。> 'title') and verify", "https://example.test/路径?q=值\uff0c。"),
        ("Open https://example.test/?a=1,b=2 and verify", "https://example.test/?a=1,b=2"),
        ("Open <https://example.test/a'b\"c?q=(one)&v=[two]> and verify", "https://example.test/a'b\"c?q=(one)&v=[two]"),
        ("Open https://example.test/a\nVerify the heading", "https://example.test/a"),
        ("Open https://example.test/a\tVerify the heading", "https://example.test/a"),
        ("Open (https://[::1]:8443/a(b)) and verify", "https://[::1]:8443/a(b)"),
        ('Open "https://example.test/a". Verify the heading', "https://example.test/a"),
        ("Open (https://[::1]:8443/a(b)). Verify the heading", "https://[::1]:8443/a(b)"),
    ],
)
def test_natural_language_and_markdown_boundaries(goal, address):
    parser = QuickTestWebAddressParser()
    assert parser.extract(goal) == (address,)
    assert parser.contains(goal, address)


@pytest.mark.parametrize(("left", "right"), [('"', '"'), ("'", "'"), ("“", "”"), ("\u2018", "\u2019"), ("「", "」"), ("『", "』"), ("`", "`"), ("<", ">")])
@pytest.mark.parametrize("address", ["HTTPS://Example.TEST:443/a(b(c))?x=%2f&next=https://inner.test/#片段!", "https://example.test/路径?q=值\uff0c。", "https://example.test/end.?!;,"])
def test_explicit_boundaries_preserve_all_address_text(left, right, address):
    assert QuickTestWebAddressParser().extract(f"Open {left}{address}{right} and verify") == (address,)


@pytest.mark.parametrize("suffix", [".", "?", ";", "!", ",", "#", "?#"])
@pytest.mark.parametrize("part", ["/path", "/?q=value", "/#part"])
def test_bare_ascii_terminal_punctuation_is_address_data(part, suffix):
    address = f"https://example.test{part}{suffix}"
    assert QuickTestWebAddressParser().extract(f"Open {address} and verify") == (address,)


def test_empty_query_fragment_and_ordered_exact_deduplication():
    first = "HTTPS://Example.TEST:443/a%2Fb?#"
    second = "https://[::1]:1/?next=https://inner.test"
    goal = f"Start <{first}> then `{second}` then <{first}>"
    parser = QuickTestWebAddressParser()
    assert parser.extract(goal) == (first, second)
    assert not parser.contains(goal, "https://inner.test")
    assert not parser.contains(goal, first.lower())


@pytest.mark.parametrize(
    "address",
    [
        "https:/example.test",
        "https://",
        "ftp://example.test",
        "custom://example.test",
        "javascript:alert(1)",
        "//example.test",
        "prefixhttps://example.test",
        "prefix_https://example.test",
        "file:https://example.test",
        "https://user:pass@example.test",
        "https://example.test\\path",
        "https://example.test:0/path",
        "https://example.test:65536/path",
        "https://example.test:bad/path",
        "https://example.test:/path",
        "https://example.test/%",
        "https://example.test/%2G",
        "https://[::1/path",
        "https://[not-ipv6]/path",
        "https://[::1]extra/path",
        "https://::1/path",
        "https://example.test/a(b",
        "https://example.test/path,then",
        "https://example.test/路径并确认",
        '"https://example.test/path',
        "“https://example.test/path",
        "<https://example.test/path",
        "`https://example.test/path",
        "[site](https://example.test/path",
        '[site](https://example.test/path "unterminated)',
        '"https://example.test/a\nb"',
        '"https://example.test/a\tb"',
        "https://example.test/a\x00b",
        "https://example.test/a)b",
        "https://example.test/a]b",
        "(https://example.test/path",
        "https://example.test/path<suffix",
        "https://example.test/path'fragment",
        "https://example.test/a[one",
        "https://example.test/a%2",
        "https://[::1]:/path",
        "https://[::1]:65536/path",
        "https://[::1]:bad/path",
        "https://[::1]@other.test",
        "[site](<https://example.test/path>extra)",
        "https://example.test/a\u200bb",
        "éhttps://example.test",
        "https://[::1]]/path",
        "https:///example.test",
        "https:example.test",
        "https//example.test",
        "http\\example.test",
    ],
)
def test_invalid_or_ambiguous_navigation_fails_instead_of_shortening(address):
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(f"Open {address} and verify the heading")


@pytest.mark.parametrize(
    "reference", ["https://example.test", "https://example.test/a", "https://example.test/a%2fb?x=1#part", "https://example.test/a%2Fb?x=1", "https://example.test/a%2Fb?x=1#part"]
)
def test_contains_requires_complete_exact_reference(reference):
    parser = QuickTestWebAddressParser()
    assert not parser.contains("Open <https://example.test:443/a%2Fb?x=1#part!> and verify", reference)


@pytest.mark.parametrize(
    "goal",
    [
        "Search Node.js and find its official site",
        "搜索 FSQ github\uff0c并找到官网地址",
        "Goal: check the heading",
        "Search FSQ // compare its official site",
        "Search FSQ //",
        "Search FSQ //\tcompare its official site",
    ],
)
def test_ordinary_topics_are_not_navigation_syntax(goal):
    assert QuickTestWebAddressParser().extract(goal) == ()


def test_malformed_input_also_fails_contains_instead_of_accepting_prefix():
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().contains("Open https://example.test/a(b", "https://example.test/a")


@pytest.mark.parametrize("suffix", ["?x=1", "#part", "/path", ":443", "@other.test", ";x=1", "+tail", "~tail", ".tail", "!tail", "$tail", ",x=1"])
@pytest.mark.parametrize("address", ["https://[::1]]", "https://example.test/a)", "https://example.test/a))", "https://example.test/a\uff09", "https://example.test/a”"])
def test_closing_bracket_cannot_hide_address_continuation(address, suffix):
    parser = QuickTestWebAddressParser()
    with pytest.raises(ValueError, match=r".+"):
        parser.extract(f"Open {address}{suffix} and verify")


@pytest.mark.parametrize("title", [r'"say \"hello\" https://title.test"', r"'say \'hello\' https://title.test'", r"(say \) https://title.test)", r'"https://title.test \\"'])
@pytest.mark.parametrize("angled", [False, True])
def test_markdown_title_escapes_do_not_change_destination_or_expose_metadata(title, angled):
    reference = "HTTPS://Example.TEST:443/a(b)?x=%2f#片段!"
    destination = f"<{reference}>" if angled else reference
    goal = f"Open [https://label.test]({destination} {title}) and verify"
    parser = QuickTestWebAddressParser()
    assert parser.extract(goal) == (reference,)
    assert not parser.contains(goal, "https://label.test")
    assert not parser.contains(goal, "https://title.test")


@pytest.mark.parametrize("title", [r'"say \"hello\"', r"'say \'hello\'", r"(say \)", '"say \\'])
def test_unclosed_escaped_markdown_title_fails_without_exposing_metadata(title):
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(f"Open [https://label.test](https://example.test {title}) and verify")


@pytest.mark.parametrize("host", ["example.test", "例子.test", "☃.test", "😀.test", "\u0301.test", "_.test", "%41.test", ").test", ">.test"])
@pytest.mark.parametrize("template", ["{}", '"{}"', "<{}>", "`{}`", "[site]({})"])
def test_unsupported_protocol_relative_navigation_is_an_error(host, template):
    supplied = template.format(f"//{host}")
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(f"Search FSQ {supplied} and verify")


def test_snowman_protocol_relative_address_is_rejected():
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract("//☃.test")


@pytest.mark.parametrize("address", ["https://outer.test/?next=//_.test&redirect=https://inner.test#//%41.test", "https://outer.test/?next=//☃.test&redirect=//😀.test"])
def test_accepted_http_reference_consumes_inner_protocol_relative_syntax(address):
    parser = QuickTestWebAddressParser()
    goal = f"Open <{address}> and verify"
    assert parser.extract(goal) == (address,)
    assert not parser.contains(goal, "//_.test")
    assert not parser.contains(goal, "https://inner.test")
    assert not parser.contains(goal, "//☃.test")
