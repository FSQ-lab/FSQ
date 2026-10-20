# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.agent_engine import EngineError
from fsq_agent.ai_services import QuickTestIntentAnalyzer, QuickTestWebAddressParser


@pytest.mark.parametrize(
    "address",
    [
        "HTTPS://Example.TEST:443/a%2Fb?#",
        "https://[2001:db8::1]:65535/a(b)?x=%2f#",
        "https://example.test/路径\uff08数据\uff09\uff3b值\uff3d【值】《值》〈值〉「值」『值』“值”\u2018值\u2019\uff02值\uff02\uff07值\uff07…——?x=*_#?!;,",
        "https://example.test/?next=https://inner.test/path&value=//other.test",
    ],
)
def test_validate_preserves_entire_structured_address(address):
    assert QuickTestWebAddressParser().validate(address) == address


@pytest.mark.parametrize(
    "address",
    [
        "",
        " ",
        " https://example.test",
        "https://example.test ",
        "Open https://example.test",
        "<https://example.test>",
        "ftp://example.test",
        "https\uff1a//example.test",
        "https://user@example.test",
        "https://example.test:0",
        "https://example.test:65536",
        "https://example.test:bad",
        "https://example.test:",
        "https://example.test/%",
        "https://example.test/%xz",
        "https://example.test/\\path",
        "https://example.test/a\nb",
        "https://example.test/a\u200bb",
    ],
)
def test_validate_does_not_extract_trim_or_repair(address):
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().validate(address)


CHINESE_PAIRS = list(zip("\uff08\uff3b【《〈「『“\u2018\uff02\uff07", "\uff09\uff3d】》〉」』”\u2019\uff02\uff07", strict=True))


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
def test_complete_chinese_wrapper_and_adjacent_explanation(opening, closing):
    parser = QuickTestWebAddressParser()
    address = "HTTPS://Example.TEST:443/a%2Fb?#"
    for goal in (f"打开{opening}{address}{closing}并验证标题", f"打开{address}{opening}不得替换站点{closing}\uff0c验证标题"):
        assert parser.extract(goal) == (address,)
        assert parser.contains(goal, address)
        assert not parser.contains(goal, "https://example.test")


def test_original_regression_and_nested_explanation():
    parser = QuickTestWebAddressParser()
    goal = "在 Chrome 中打开精确网址 https://example.com\uff08不得替换为其他站点\uff09\uff0c检查页面标题\uff0c并验证标题是否恰为 Example Domain\uff1b保留可支持结论的页面证据。"
    assert parser.extract(goal) == ("https://example.com",)
    assert parser.extract("打开https://example.com\uff08限制\uff08保持原网址\uff09及「当前页面」\uff09\uff0c检查标题") == ("https://example.com",)


@pytest.mark.parametrize("delimiter", ["…", "……", "—", "——"])
def test_ellipsis_and_em_dash_delimit_bare_prose(delimiter):
    assert QuickTestWebAddressParser().extract(f"打开https://example.test/path{delimiter}检查标题") == ("https://example.test/path",)


@pytest.mark.parametrize(("opening", "closing"), [*CHINESE_PAIRS, ('"', '"'), ("`", "`"), ("<", ">")])
def test_explicit_unicode_punctuation_and_emphasis_data_are_preserved(opening, closing):
    # Use a different outer quote when the URL itself contains that quote.
    punctuation = "\uff08\uff09\uff3b\uff3d【】《》〈〉「」『』“”\u2018\u2019\uff02\uff07…——*_".replace(opening, "").replace(closing, "")
    address = f"HTTPS://Example.TEST/路径{punctuation}?q=值{punctuation}#片段!"
    parser = QuickTestWebAddressParser()
    assert parser.extract(f"打开{opening}{address}{closing}\uff0c检查标题") == (address,)
    assert parser.contains(f"打开{opening}{address}{closing}", address)


@pytest.mark.parametrize("marker", ["**", "__"])
@pytest.mark.parametrize(("opening", "closing"), [("", ""), ('"', '"'), ("<", ">"), ("`", "`"), ("“", "”"), ("【", "】")])
def test_strong_wrappers_can_enclose_existing_url_wrappers(marker, opening, closing):
    address = "HTTPS://Example.TEST:443/a%2Fb?#"
    parser = QuickTestWebAddressParser()
    goal = f"打开 {marker}{opening}{address}{closing}{marker}\uff0c检查标题"
    assert parser.extract(goal) == (address,)
    assert parser.contains(goal, address)


@pytest.mark.parametrize("label", ["URL", "url", "uRl", "URI", "uri", "网址", "地址", "链接", "目标网址", "目标地址", "网站地址"])
@pytest.mark.parametrize("separator", [":", "\uff1a", ": ", "\uff1a "])
def test_recognized_labels_at_token_boundary(label, separator):
    parser = QuickTestWebAddressParser()
    address = "https://example.test/path?next=https://inner.test#"
    goal = f"打开 {label}{separator}{address}\uff0c检查标题"
    assert parser.extract(goal) == (address,)
    assert not parser.contains(goal, "https://inner.test")


def test_combined_label_strong_chinese_wrapper_and_explanation():
    goal = "打开 目标网址\uff1a**【HTTPS://Example.TEST/路径?x=值…——*_#】**\uff08不得替换\uff09\uff0c检查标题"
    assert QuickTestWebAddressParser().extract(goal) == ("HTTPS://Example.TEST/路径?x=值…——*_#",)


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
@pytest.mark.parametrize("template", ["打开{opening}https://example.test/path", "打开https://example.test/path{opening}解释"])
def test_missing_chinese_pair_fails_without_shortened_reference(opening, closing, template):
    del closing
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(template.format(opening=opening))


@pytest.mark.parametrize(
    "goal",
    [
        "打开【https://example.test/path\uff3d",
        "打开\uff08https://example.test/path】",
        "打开https://example.test/path\uff08解释\uff3d",
        "打开https://example.test/path\uff08解释\uff08嵌套\uff09",
        "打开**https://example.test/path",
        "打开__https://example.test/path",
        "打开https://example.test/path**",
        "打开https://example.test/path__",
        "打开**https://example.test/path__",
        "打开***https://example.test/path***",
        "打开___https://example.test/path___",
        "打开**<https://example.test/path>__",
        "打开https\uff1a//example.test/path",
        "打开https\uff1aexample.test/path",
        "打开prefixhttps://example.test/path",
        "打开prefix_https://example.test/path",
        "打开file:https://example.test/path",
        "打开javascript:https://example.test/path",
        "打开custom:https://example.test/path",
        "打开myURL:https://example.test/path",
        "打开x网址\uff1ahttps://example.test/path",
        "打开prefix URL\uff1ahttps\uff1a//example.test/path",
    ],
)
def test_malformed_wrappers_markers_schemes_and_unsafe_prefixes_fail(goal):
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(goal)


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
@pytest.mark.parametrize("continuation", ["/more", "?x=1", "#part", ":443", "@other.test", "suffix"])
def test_explanation_or_wrapper_cannot_hide_url_continuation(opening, closing, continuation):
    parser = QuickTestWebAddressParser()
    for goal in (f"打开https://example.test{opening}解释{closing}{continuation}", f"打开{opening}https://example.test{closing}{continuation}"):
        with pytest.raises(ValueError, match=r".+"):
            parser.extract(goal)


@pytest.mark.parametrize("authority", ["example.test\uff1a443", "[::1]\uff1a443", "example.test\uff1a/path", "example.test\uff1a@other.test"])
def test_fullwidth_authority_colon_does_not_authorize_shortened_url(authority):
    parser = QuickTestWebAddressParser()
    address = f"https://{authority}"
    with pytest.raises(ValueError, match=r".+"):
        parser.validate(address)
    with pytest.raises(ValueError, match=r".+"):
        parser.extract(f"打开{address}\uff0c检查标题")


@pytest.mark.parametrize("marker", ["**", "__"])
def test_strong_wrapper_preserves_single_internal_marker_characters(marker):
    address = "https://example.test/a_b*c?x=_*#"
    assert QuickTestWebAddressParser().extract(f"打开{marker}{address}{marker}\uff0c检查标题") == (address,)


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
def test_explanation_references_remain_ordered_and_available_for_provenance(opening, closing):
    first = "https://a.test"
    second = "HTTPS://B.TEST:443/a(b)?x=%2f#"
    goal = f"打开{first}{opening}随后访问 {second} 并检查标题{closing}\uff0c再访问 https://c.test"
    parser = QuickTestWebAddressParser()
    assert parser.extract(goal) == (first, second, "https://c.test")
    assert parser.contains(goal, second)
    assert not parser.contains(goal, "https://B.TEST")


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
def test_explanation_final_reference_does_not_consume_its_enclosing_mark(opening, closing):
    goal = f"打开https://a.test{opening}随后访问 https://b.test{closing}\uff0c检查标题"
    assert QuickTestWebAddressParser().extract(goal) == ("https://a.test", "https://b.test")


@pytest.mark.parametrize("address", ["ftp://b.test", "https://b.test:0", "https\uff1a//b.test"])
def test_invalid_reference_inside_explanation_fails_extract_contains_and_inference(address):
    goal = f"打开https://a.test\uff08随后访问 {address} 并检查标题\uff09"
    parser = QuickTestWebAddressParser()
    with pytest.raises(ValueError, match=r".+"):
        parser.extract(goal)
    with pytest.raises(ValueError, match=r".+"):
        parser.contains(goal, "https://a.test")

    class NoInferenceSession:
        closed = False

        def complete_sync(self, request):
            pytest.fail("Invalid supplied navigation reached inference/search classification")

        def close_sync(self):
            self.closed = True

    session = NoInferenceSession()
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="web", goal=goal)
    assert error.value.category == "invalid_output"
    assert session.closed


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
@pytest.mark.parametrize("inner", ["**https://example.test/path**", "__【https://example.test/path】__", "【https://example.test/path】"])
def test_nested_presentation_requires_all_matching_enclosures(opening, closing, inner):
    parser = QuickTestWebAddressParser()
    assert parser.extract(f"打开{opening}{inner}{closing}\uff0c检查标题") == ("https://example.test/path",)
    assert parser.extract(f"打开{opening}{inner}\uff0c检查标题{closing}") == ("https://example.test/path",)
    with pytest.raises(ValueError, match=r".+"):
        parser.extract(f"打开{opening}{inner}\uff0c检查标题")
    mismatch = "\uff3d" if closing != "\uff3d" else "】"
    with pytest.raises(ValueError, match=r".+"):
        parser.extract(f"打开{opening}{inner}{mismatch}\uff0c检查标题")


@pytest.mark.parametrize(("opening", "closing"), CHINESE_PAIRS)
@pytest.mark.parametrize("continuation", ["/more", "?x=1", "#part", ":443", "suffix"])
def test_nested_enclosure_and_explanation_sequence_cannot_hide_continuation(opening, closing, continuation):
    with pytest.raises(ValueError, match=r".+"):
        QuickTestWebAddressParser().extract(f"打开{opening}【https://example.test】{closing}\uff08说明\uff09{continuation}")


def test_explanation_quoted_url_data_and_markdown_metadata_keep_their_boundaries():
    address = "https://b.test/路径】?q=值\uff02#"
    goal = f'打开https://a.test\uff08随后访问 <{address}>\uff0c再访问 [https://label.test](https://c.test "https://title.test")\uff09'
    parser = QuickTestWebAddressParser()
    assert parser.extract(goal) == ("https://a.test", address, "https://c.test")
    assert not parser.contains(goal, "https://label.test")
    assert not parser.contains(goal, "https://title.test")


def test_same_chinese_pair_inside_explicit_url_is_balanced_data():
    address = "https://example.test/路径\uff08值\uff09?q=值\uff08数据\uff09#"
    assert QuickTestWebAddressParser().extract(f"打开\uff08{address}\uff09\uff0c检查标题") == (address,)
