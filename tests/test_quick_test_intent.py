# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fsq_agent.agent_engine import EngineError, ModelResult
from fsq_agent.ai_services import QuickTestIntent, QuickTestIntentAnalyzer, build_quick_test_intent_analyzer
from fsq_agent.config import load_platform_settings
from fsq_agent.models import ApplicationCandidate


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
    return json.dumps(
        {
            "sufficient": True,
            "reason": "sufficient",
            "target_name": "Calculator",
            "candidate_id": "c001",
            "explicit_reference": None,
            "base_url": None,
            "candidate_ids": [],
            "explanation": None,
            **updates,
        }
    )


CANDIDATES = (
    ApplicationCandidate(
        candidate_id="c001",
        identity="com.vendor.calculator",
        label="Calculator",
        aliases=("计算器",),
        app_id="com.vendor.calculator",
        source="android_package",
    ),
    ApplicationCandidate(
        candidate_id="c002",
        identity="com.android.vending",
        label="Google Play Store",
        aliases=("应用商店",),
        app_id="com.android.vending",
        source="android_package",
    ),
)

WINDOWS_CANDIDATES = (
    ApplicationCandidate(
        candidate_id="c001",
        identity="C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        label="Microsoft Edge",
        aliases=("edge",),
        app_path=Path("C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"),
        window_title_re=".*Microsoft Edge.*",
        source="windows_catalogue",
    ),
)


def test_candidate_selection_uses_single_tool_free_call_and_hides_host_fields():
    session = Session(output(target_name="Microsoft Edge", candidate_id="c001"))
    result = QuickTestIntentAnalyzer(session).analyze(
        platform="windows",
        goal="打开微软浏览器\uff0c访问 example.com 并检查标题",
        candidates=WINDOWS_CANDIDATES,
    )
    assert result == QuickTestIntent(
        sufficient=True,
        reason="sufficient",
        target_name="Microsoft Edge",
        candidate_id="c001",
    )
    assert len(session.requests) == 1
    request = session.requests[0]
    assert "window_title_re" not in request.input
    assert "launch_args" not in request.input
    assert "window_title_re" not in request.output.schema["properties"]
    assert "launch_args" not in request.output.schema["properties"]
    context = json.loads(request.input.splitlines()[-1])
    assert context["candidates"][0]["candidate_id"] == "c001"
    assert "window_title_re" not in context["candidates"][0]
    assert session.closed


@pytest.mark.parametrize("reference", ["com.example.app", "com.example.ipa", "/Applications/Test Product.app"])
def test_ios_verbatim_bundle_or_simulator_path(reference):
    session = Session(output(candidate_id=None, explicit_reference=reference))
    result = QuickTestIntentAnalyzer(session).analyze(platform="ios", goal=f'Open "{reference}" and read the header', candidates=())
    assert result.explicit_reference == reference
    assert len(session.requests) == 1
    assert not {"xcode_org_id", "xcode_signing_id", "updated_wda_bundle_id"}.intersection(session.requests[0].output.schema["properties"])


@pytest.mark.parametrize(
    ("goal", "reference"),
    [
        ("Open Notes", "com.example.notes"),
        ("Open /Applications/Notes.ipa", "/Applications/Notes.ipa"),
        ("Open ./Notes.app", "./Notes.app"),
        ('Open "~/Applications/Notes.app"', "/Applications/Notes.app"),
        ('Open "/Applications/Notes.app/Subdirectory"', "/Applications/Notes.app"),
        ('Open "/Applications/Old.app copies/New.app" and inspect it', "/Applications/Old.app"),
        ("打开「/Applications/Old.app copies/New.app」并检查", "/Applications/Old.app"),
    ],
)
def test_ios_rejects_guessed_or_unsupported_explicit_references(goal, reference):
    session = Session(output(candidate_id=None, explicit_reference=reference))
    with pytest.raises(EngineError, match="verbatim"):
        QuickTestIntentAnalyzer(session).analyze(platform="ios", goal=goal, candidates=())


def test_cross_language_alias_and_entry_app_selection_without_literal_grounding():
    session = Session(output(target_name="Google Play Store", candidate_id="c002", explanation="Install target app via Play Store first."))
    result = QuickTestIntentAnalyzer(session).analyze(
        platform="android",
        goal="先去应用商店安装微信\uff0c然后检查安装结果",
        candidates=CANDIDATES,
    )
    assert result.sufficient
    assert result.candidate_id == "c002"
    assert result.target_name == "Google Play Store"
    assert result.explanation == "Install target app via Play Store first."
    assert session.closed


@pytest.mark.parametrize("bad_id", ["c999", "invented", "C001", ""])
def test_unknown_or_invented_candidate_id_is_rejected(bad_id):
    session = Session(output(candidate_id=bad_id))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"
    assert session.closed


def test_explicit_reference_allowed_only_when_verbatim_in_goal():
    goal = "In com.custom.calculator verify 1+1=2"
    session = Session(output(candidate_id=None, explicit_reference="com.custom.calculator"))
    result = QuickTestIntentAnalyzer(session).analyze(platform="android", goal=goal, candidates=CANDIDATES)
    assert result.sufficient
    assert result.candidate_id is None
    assert result.explicit_reference == "com.custom.calculator"


def test_ungrounded_explicit_reference_is_rejected():
    session = Session(output(candidate_id=None, explicit_reference="com.invented.calculator"))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"
    assert session.closed


@pytest.mark.parametrize("actual", ["com.tencent.mm2", "com.tencent.mm.evil", "evilcom.tencent.mm", "com.tencent.mm-suffix"])
def test_explicit_identity_does_not_accept_larger_identifier_prefix(actual):
    session = Session(output(candidate_id=None, explicit_reference="com.tencent.mm"))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal=f"用{actual}搜索联系人并验证结果", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize(("platform", "reference"), [("android", "com.vendor.calculator"), ("macos", "/Applications/Calculator.app"), ("windows", r"C:\Apps\Calculator.exe")])
def test_native_reference_surrounding_whitespace_is_normalized(platform, reference):
    session = Session(output(candidate_id=None, explicit_reference=f"\n\t {reference} \r\n"))
    result = QuickTestIntentAnalyzer(session).analyze(platform=platform, goal=f"Open {reference} and verify 1+1=2")
    assert result.explicit_reference == reference
    assert session.closed


@pytest.mark.parametrize("reference", ["com.vendor.\ncalculator", "com.vendor.\tcalculator", "com.vendor.\x00calculator"])
def test_native_reference_embedded_controls_are_rejected(reference):
    session = Session(output(candidate_id=None, explicit_reference=reference))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal=f"Open {reference} and verify 1+1=2")
    assert error.value.category == "invalid_output"
    assert session.closed


def test_native_sufficient_requires_candidate_or_explicit_reference():
    session = Session(output(candidate_id=None, explicit_reference=None))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"


def test_application_not_found_and_ambiguous_preserve_structured_diagnostics():
    not_found = QuickTestIntentAnalyzer(Session(output(sufficient=False, reason="application_not_found", target_name="WeChat", candidate_id=None, explanation="WeChat is not installed."))).analyze(
        platform="android", goal="打开微信发消息", candidates=CANDIDATES
    )
    assert not not_found.sufficient
    assert not_found.reason == "application_not_found"
    assert not_found.target_name == "WeChat"
    assert not_found.explanation == "WeChat is not installed."

    ambiguous = QuickTestIntentAnalyzer(
        Session(output(sufficient=False, reason="ambiguous_application", target_name="App", candidate_id=None, candidate_ids=["c001", "c002"], explanation="Two candidates match."))
    ).analyze(platform="android", goal="打开应用并检查", candidates=CANDIDATES)
    assert not ambiguous.sufficient
    assert ambiguous.reason == "ambiguous_application"
    assert ambiguous.candidate_ids == ("c001", "c002")


def test_ambiguous_rejects_unknown_diagnostic_candidate_ids():
    session = Session(output(sufficient=False, reason="ambiguous_application", candidate_id=None, candidate_ids=["c001", "c999"]))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="打开应用并检查", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"


def test_candidate_id_takes_priority_and_clears_explicit_reference():
    session = Session(output(candidate_id="c001", explicit_reference="com.vendor.calculator"))
    result = QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Open com.vendor.calculator and verify 1+1=2", candidates=CANDIDATES)
    assert result.candidate_id == "c001"
    assert result.explicit_reference is None


@pytest.mark.parametrize(("platform", "bad_ref"), [("android", "Calculator"), ("macos", "Calculator"), ("windows", "notepad.exe")])
def test_malformed_native_explicit_reference_is_rejected(platform, bad_ref):
    session = Session(output(candidate_id=None, explicit_reference=bad_ref))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform=platform, goal=f"Open {bad_ref} and verify result")
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize("bad_url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.test", "https://user:pass@example.test", "https://example.test:0"])
def test_analyzer_rejects_malformed_model_extracted_web_url(bad_url):
    session = Session(output(reason="sufficient", target_name="site", candidate_id=None, base_url=bad_url))
    with pytest.raises(ValueError, match=r"HTTP\(S\) URL|URL|port|credentials|scheme|host"):
        QuickTestIntentAnalyzer(session).analyze(platform="web", goal=f"Open {bad_url} and verify heading")
    assert session.closed


@pytest.mark.parametrize(
    ("goal", "completed_url", "target_name"),
    [
        ("please open x.com and check trending posts", "https://x.com", "x.com"),
        ("open localhost:3000 and check login button", "http://localhost:3000", "localhost:3000"),
        ("visit 127.0.0.1:8080/health and verify status ok", "http://127.0.0.1:8080/health", "127.0.0.1:8080"),
    ],
)
def test_web_bare_domain_and_localhost_scheme_completion(goal, completed_url, target_name):
    session = Session(output(reason="sufficient", target_name=target_name, candidate_id=None, base_url=completed_url))
    result = QuickTestIntentAnalyzer(session).analyze(platform="web", goal=goal)
    assert result.sufficient
    assert result.reason == "sufficient"
    assert result.base_url == completed_url
    assert "bare domain" in session.requests[0].input
    assert "`https://` for public domains" in session.requests[0].input
    assert "`http://` for `localhost`" in session.requests[0].input
    assert session.closed


@pytest.mark.parametrize(
    "goal",
    ["搜索 FSQ github\uff0c并且帮我找到FSQ 的官网地址", "Goal: search for FSQ on GitHub and find its official website"],
)
def test_address_free_web_search_succeeds_without_base_url(goal):
    session = Session(output(reason="web_search", target_name="FSQ", candidate_id=None, base_url=None))
    result = QuickTestIntentAnalyzer(session).analyze(platform="web", goal=goal)
    assert result.sufficient
    assert result.reason == "web_search"
    assert result.target_name == "FSQ"
    assert result.base_url is None
    assert session.closed


def test_web_goal_extracts_base_url_without_rigid_punctuation_state_machine():
    session = Session(output(reason="sufficient", target_name="example.com", candidate_id=None, base_url="https://example.com"))
    result = QuickTestIntentAnalyzer(session).analyze(
        platform="web",
        goal="在 Chrome 中打开精确网址 https://example.com\uff08不得替换为其他站点\uff09\uff0c检查页面标题",
    )
    assert result.sufficient
    assert result.reason == "sufficient"
    assert result.base_url == "https://example.com"


def test_web_search_rejects_non_null_base_url():
    session = Session(output(reason="web_search", target_name="FSQ", candidate_id=None, base_url="https://example.com"))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="web", goal="Search FSQ and find its official website")
    assert error.value.category == "invalid_output"


def test_web_sufficient_requires_base_url_when_no_caller_url():
    session = Session(output(reason="sufficient", target_name="example", candidate_id=None, base_url=None))
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="web", goal="Open example and verify heading")
    assert error.value.category == "invalid_output"


@pytest.mark.parametrize("reason", ["insufficient", "missing_application", "application_not_found", "ambiguous_application"])
def test_insufficient_reasons_are_distinct_from_model_failure(reason):
    session = Session(output(sufficient=False, reason=reason, target_name="App", candidate_id=None))
    result = QuickTestIntentAnalyzer(session).analyze(platform="android", goal="App", candidates=CANDIDATES)
    assert not result.sufficient
    assert result.reason == reason


@pytest.mark.parametrize("text", ["", "[]", "null", "```json\n{}\n```", output(sufficient="true"), output(extra="secret"), output(reason="invented"), output(target_name=" ")])
def test_strict_output_rejects_malformed(text):
    session = Session(text)
    with pytest.raises(EngineError) as error:
        QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
    assert error.value.category == "invalid_output"
    assert session.closed


def test_parsed_output_required_and_refusal_preserved():
    for session in [Session(output(), parsed=False), Session(EngineError("refusal", "Refused."))]:
        with pytest.raises(EngineError):
            QuickTestIntentAnalyzer(session).analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
        assert session.closed


@pytest.mark.parametrize("provider", ["openai", "azure_openai", "deepseek", "google_gemini", "kimi", "github_copilot"])
def test_factory_uses_configured_neutral_session_without_workspace(monkeypatch, tmp_path, provider):
    settings = load_platform_settings("android", user_config_root=tmp_path)
    settings.agent_runtime.provider = provider
    session = Session(output())
    seen = []
    monkeypatch.setattr("fsq_agent.ai_services._factory.build_model_provider_session", lambda current: seen.append(current) or session)
    analyzer = build_quick_test_intent_analyzer(settings)
    result = analyzer.analyze(platform="android", goal="Calculator: 1+1=2", candidates=CANDIDATES)
    assert result.sufficient
    assert seen == [settings]
    assert session.requests[0].reasoning_effort == settings.agent_runtime.reasoning_effort
