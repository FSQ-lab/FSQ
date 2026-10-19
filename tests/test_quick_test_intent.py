# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from dataclasses import replace

import pytest

from fsq_agent.agent_engine import EngineError, ModelResult
from fsq_agent.ai_services import QuickTestIntentAnalyzer, build_quick_test_intent_analyzer
from fsq_agent.config import load_platform_settings


class Session:
    def __init__(self, payload, *, parsed=True):
        self.payload = payload
        self.parsed = parsed
        self.requests = []
        self.closed = False

    def complete_sync(self, request):
        self.requests.append(request)
        if isinstance(self.payload, Exception):
            raise self.payload
        result = ModelResult(text=self.payload)
        if not self.parsed:
            return result
        try:
            return replace(result, parsed_output=request.output.parse(result.text))
        except ValueError:
            raise EngineError("invalid_output", "Invalid output.") from None

    def close_sync(self):
        self.closed = True


def output(**updates):
    return json.dumps({"sufficient": True, "reason": "sufficient", "application_name": "Calculator", "explicit_reference": None, "inferred_hint": None, **updates})


@pytest.mark.parametrize("goal", ["搜索 FSQ github\uff0c并且帮我找到FSQ 的官网地址", "Goal: search for FSQ on GitHub and find its official website"])
def test_address_free_search_is_grounded_tool_free_intent(goal):
    session = Session(output(reason="web_search", application_name="FSQ"))
    result = QuickTestIntentAnalyzer(session).analyze(platform="web", goal=goal)
    assert result.sufficient
    assert result.reason == "web_search"
    assert result.application_name == "FSQ"
    assert result.explicit_reference is None
    assert result.inferred_hint is None
    assert len(session.requests) == 1
    assert "search/discovery" in session.requests[0].input
    assert session.closed


@pytest.mark.parametrize(
    "updates",
    [
        {"application_name": "Invented"},
        {"application_name": None},
        {"explicit_reference": "https://invented.test"},
        {"inferred_hint": {"kind": "app_id", "value": "com.fsq"}},
    ],
)
def test_search_output_cannot_invent_subject_address_or_native_hint(updates):
    payload = {"reason": "web_search", "application_name": "FSQ", **updates}
    with pytest.raises(EngineError):
        QuickTestIntentAnalyzer(Session(output(**payload))).analyze(platform="web", goal="Search FSQ and find its official website")


@pytest.mark.parametrize("goal", ["FSQ", "帮我测一下网页"])
def test_bare_web_topics_remain_insufficient(goal):
    result = QuickTestIntentAnalyzer(Session(output(sufficient=False, reason="insufficient", application_name=None))).analyze(platform="web", goal=goal)
    assert not result.sufficient


def test_short_actionable_goal_is_not_length_gated():
    session = Session(output())
    result = QuickTestIntentAnalyzer(session, reasoning_effort="low").analyze(platform="windows", goal="Calculator: 1+1=2")
    assert result.sufficient
    assert result.application_name == "Calculator"
    assert session.requests[0].reasoning_effort == "low"
    assert session.requests[0].output.schema["additionalProperties"] is False
    assert session.closed


@pytest.mark.parametrize("reason", ["insufficient", "missing_application", "ambiguous_application"])
def test_insufficient_intent_is_distinct_from_model_failure(reason):
    session = Session(output(sufficient=False, reason=reason, application_name=None))
    result = QuickTestIntentAnalyzer(session).analyze(platform="macos", goal="Calculator")
    assert not result.sufficient
    assert result.reason == reason


@pytest.mark.parametrize("text", ["", "[]", "null", "```json\n{}\n```", output(sufficient="true"), output(extra="secret"), output(reason="invented"), output(application_name=" ")])
def test_strict_output_rejects_malformed(text):
    session = Session(text)
    with pytest.raises(EngineError, match="output"):
        QuickTestIntentAnalyzer(session).analyze(platform="windows", goal="Calculator: 1+1=2")
    assert session.closed


def test_parsed_output_required_and_refusal_preserved():
    for session in [Session(output(), parsed=False), Session(EngineError("refusal", "Refused."))]:
        with pytest.raises(EngineError) as error:
            QuickTestIntentAnalyzer(session).analyze(platform="web", goal="Check https://example.com")
        assert error.value.category in {"invalid_output", "refusal"}
        assert session.closed


def test_model_cannot_claim_an_inferred_native_identity_is_explicit():
    session = Session(output(explicit_reference="com.vendor.calculator"))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2")
    assert error.value.category == "invalid_output"


def test_explicit_identity_is_retained_only_when_present_in_current_goal():
    session = Session(output(explicit_reference="com.vendor.calculator"))
    result = QuickTestIntentAnalyzer(session).analyze(platform="android", goal="In com.vendor.calculator check 1+1=2")
    assert result.explicit_reference == "com.vendor.calculator"


def test_model_native_hint_cannot_replace_the_application_name_query():
    session = Session(output(application_name="com.vendor.calculator"))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2")
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize(
    ("goal", "name"),
    [
        ("用微信搜索联系人张三\uff0c确认出现匹配结果", "微信"),
        ("打开计算器计算1+1并确认结果为2", "计算器"),
        ("用WeChat搜索联系人并确认匹配结果", "WeChat"),
        ("打开Calculator计算1+1并确认结果为2", "Calculator"),
        ("Calculator: verify 1+1=2", "Calculator"),
    ],
)
def test_grounding_allows_continuous_cjk_and_mixed_script_goal(goal, name):
    result = QuickTestIntentAnalyzer(Session(output(application_name=name))).analyze(platform="android", goal=goal)
    assert result.application_name == name


@pytest.mark.parametrize(
    ("goal", "name"),
    [
        ("Use SuperCalculator and verify 1+1=2", "Calculator"),
        ("Use CalculatorPro and verify 1+1=2", "Calculator"),
        ("Use Calculator2 and verify 1+1=2", "Calculator"),
        ("Use Calculator_pro and verify 1+1=2", "Calculator"),
        ("Use Calculator\u0301 and verify 1+1=2", "Calculator"),
        ("用支付宝搜索联系人并确认匹配结果", "微信"),
        ("Use WeChat and verify search", "com.tencent.mm"),
    ],
)
def test_grounding_rejects_adjacent_latin_tokens_and_unrelated_substitution(goal, name):
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(Session(output(application_name=name))).analyze(platform="android", goal=goal)
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize(
    ("goal", "reference"),
    [
        ("用com.tencent.mm搜索联系人并确认匹配结果", "com.tencent.mm"),
        ("In com.android.calculator2 verify 1+1=2", "com.android.calculator2"),
    ],
)
def test_explicit_identity_allows_cjk_sentence_adjacency(goal, reference):
    result = QuickTestIntentAnalyzer(Session(output(application_name=None, explicit_reference=reference))).analyze(platform="android", goal=goal)
    assert result.explicit_reference == reference


@pytest.mark.parametrize("actual", ["com.tencent.mm2", "com.tencent.mm.evil", "evilcom.tencent.mm", "com.tencent.mm-suffix"])
def test_explicit_identity_does_not_accept_a_larger_identifier_prefix(actual):
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(Session(output(application_name=None, explicit_reference="com.tencent.mm"))).analyze(platform="android", goal=f"用{actual}搜索联系人并验证结果")
    assert error.value.category == "invalid_output"


def test_inferred_hint_is_preserved_separately_from_explicit_provenance():
    hint = {"kind": "app_id", "value": "com.tencent.mm"}
    result = QuickTestIntentAnalyzer(Session(output(application_name="微信", inferred_hint=hint))).analyze(platform="android", goal="用微信搜索联系人张三\uff0c确认出现匹配结果")
    assert result.application_name == "微信"
    assert result.explicit_reference is None
    assert result.inferred_hint.kind == "app_id"
    assert result.inferred_hint.value == "com.tencent.mm"


@pytest.mark.parametrize(
    "hint",
    [
        {"kind": "url", "value": "https://example.com"},
        {"kind": "app_id", "value": ""},
        {"kind": "app_id", "value": 42},
        {"kind": "app_id", "value": "com.tencent.mm", "verified": True},
        {"kind": "app_id", "value": "com.tencent.mm\ncommand"},
        {"kind": "bundle_id", "value": "com.tencent.mm"},
    ],
)
def test_malformed_or_wrong_platform_hint_is_rejected(hint):
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(Session(output(application_name="WeChat", inferred_hint=hint))).analyze(platform="android", goal="Use WeChat to search and verify the match")
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize(
    ("platform", "hint"),
    [
        ("windows", {"kind": "app_path", "value": r"C:\Applications\WeChat.exe"}),
        ("macos", {"kind": "bundle_id", "value": "com.tencent.xinWeChat"}),
        ("macos", {"kind": "app_path", "value": "/Applications/WeChat.app"}),
    ],
)
def test_native_hints_remain_typed_assistance_without_explicit_identity(platform, hint):
    result = QuickTestIntentAnalyzer(Session(output(application_name="WeChat", inferred_hint=hint))).analyze(platform=platform, goal="Use WeChat to search and verify the result")
    assert result.explicit_reference is None
    assert result.inferred_hint.model_dump() == hint


def test_hint_cannot_supply_a_missing_application_name():
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(Session(output(application_name=None, inferred_hint={"kind": "app_id", "value": "com.tencent.mm"}))).analyze(
            platform="android", goal="Search for a contact and verify the result"
        )
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize("provider", ["openai", "azure_openai", "deepseek", "google_gemini", "kimi", "github_copilot"])
def test_factory_uses_configured_neutral_session_without_workspace(monkeypatch, tmp_path, provider):
    settings = load_platform_settings("android", user_config_root=tmp_path)
    settings.agent_runtime.provider = provider
    session = Session(output())
    seen = []
    monkeypatch.setattr("fsq_agent.ai_services._factory.build_model_provider_session", lambda value: seen.append(value) or session)
    analyzer = build_quick_test_intent_analyzer(settings)
    assert not session.requests
    analyzer.analyze(platform="android", goal="Calculator: 1+1=2")
    assert seen == [settings]
    assert session.requests[0].reasoning_effort == settings.agent_runtime.reasoning_effort
