# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest
from pydantic import ValidationError

from fsq_agent import models
from fsq_agent.models import (
    AgentFinalOutput,
    AgentRuntimeSettings,
    AgentTaskInput,
    AndroidHarnessSettings,
    AndroidInputTextParams,
    AndroidSwipeParams,
    ExecutionStep,
    GoalPrePlan,
    HarnessSettings,
    LocalToolOutputSettings,
    MacOSClickOnParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSPressKeyParams,
    PageKnowledgeIndex,
    PageKnowledgePage,
    SkillConfig,
    Task,
    WaitMsParams,
    WebWaitForParams,
    WindowsClickOnParams,
)


def test_task_defaults() -> None:
    task = Task(description="Do a thing")

    assert task.id == "task"
    assert task.name == "Task"
    assert task.acceptance_criteria == []
    assert task.planning_reference_kind is None
    assert task.planning_reference_text is None
    assert task.key_actions == []
    assert task.verification_goal is None
    assert task.timeout_seconds == 300
    assert task.max_retries == 3
    assert task.knowledge_refs == []


def test_execution_step_requires_positive_id() -> None:
    step = ExecutionStep(
        step_id=1,
        action="write",
        tool="file.write",
        tool_input={"path": "out.txt", "content": "ok"},
        expected_outcome="file written",
    )

    assert step.step_id == 1


def test_agent_final_output_defaults_schema_version() -> None:
    output = AgentFinalOutput(status="success", summary="Done")

    assert output.schema_version == "task_run_v1"
    assert output.pre_plan == []


def test_agent_task_input_wraps_task_contract() -> None:
    task = Task(
        id="task-1",
        description="Do a thing",
        key_actions=["Key action 1: tap button"],
        verification_goal="Verify that doing the thing is complete.",
    )
    task_input = AgentTaskInput(
        task=task,
        acceptance_criteria=task.acceptance_criteria,
        key_actions=task.key_actions,
        verification_goal=task.verification_goal,
        acceptance_policy="Use provided criteria.",
    )

    assert task_input.schema_version == "task_input_v1"
    assert task_input.output_contract == "task_run_v1"
    assert task_input.task.id == "task-1"
    assert task_input.key_actions == ["Key action 1: tap button"]
    assert task_input.verification_goal == "Verify that doing the thing is complete."


def test_agent_runtime_settings_defaults_to_safe_offline_mode() -> None:
    settings = AgentRuntimeSettings()

    assert settings.provider is None
    assert settings.model == ""
    assert settings.base_url == ""
    assert settings.api_key == ""
    assert settings.tracing_enabled is True
    assert not hasattr(settings.prompt, "custom_instructions")
    assert not hasattr(settings.prompt, "custom_instructions_path")
    assert settings.prompt.agent_template_path is None
    assert settings.prompt.task_template_path is None
    assert settings.prompt.variables == {}
    assert settings.local_tool_output.always_write_artifact is True
    assert settings.local_tool_output.full_output_max_chars == 30000
    assert settings.local_tool_output.total_inline_output_max_chars == 60000


def test_harness_settings_default_to_android_appium_connect_agent() -> None:
    settings = HarnessSettings()

    assert settings.platform == "android"
    assert settings.android.backend == "appium_connect_agent"
    assert settings.android.app_id is None
    assert settings.android.serial is None


def test_skill_config_defaults_to_markdown() -> None:
    skill = SkillConfig(name="browser-testing", path="browser-testing.md")

    assert skill.kind == "markdown"
    assert skill.required is False


def test_models_public_surface_does_not_export_removed_tool_execution_settings() -> None:
    assert "ShellSettings" not in models.__all__
    assert "CLIToolConfig" not in models.__all__
    assert "DeprecatedToolSettings" not in models.__all__
    assert "VerificationCriterion" not in models.__all__
    assert "VerificationMode" not in models.__all__
    assert "VerificationSettings" not in models.__all__
    assert not hasattr(models, "ShellSettings")
    assert not hasattr(models, "CLIToolConfig")
    assert not hasattr(models, "DeprecatedToolSettings")
    assert not hasattr(models, "VerificationCriterion")
    assert not hasattr(models, "VerificationMode")
    assert not hasattr(models, "VerificationSettings")


def test_capability_parameter_schemas_include_llm_facing_guidance() -> None:
    wait_schema = WaitMsParams.model_json_schema()
    android_text_schema = AndroidInputTextParams.model_json_schema()
    android_swipe_schema = AndroidSwipeParams.model_json_schema()
    web_wait_schema = WebWaitForParams.model_json_schema()
    windows_click_schema = WindowsClickOnParams.model_json_schema()
    macos_click_schema = MacOSClickOnParams.model_json_schema()
    macos_launch_schema = MacOSLaunchAppParams.model_json_schema()
    macos_kill_schema = MacOSKillAppParams.model_json_schema()
    macos_press_key_schema = MacOSPressKeyParams.model_json_schema()

    assert "Wait without touching platform state" in wait_schema["description"]
    assert "milliseconds" in wait_schema["properties"]["duration_ms"]["description"]

    assert "target or non-empty locator" in android_text_schema["description"]
    assert "runtimeSecret" in android_text_schema["properties"]["textType"]["description"]
    assert "text to enter" in android_text_schema["properties"]["text"]["description"]
    assert "semantic target" in android_text_schema["properties"]["target"]["description"]
    assert "structured Android locator" in android_text_schema["properties"]["locator"]["description"]

    assert "exactly one" in android_swipe_schema["description"]
    assert "reference_screen_size" not in android_swipe_schema["properties"]

    assert "exactly one locator or url" in web_wait_schema["description"]
    assert "condition timeout" in web_wait_schema["properties"]["timeout_ms"]["description"]

    assert "non-empty locator" in windows_click_schema["description"]
    assert "descriptive" in windows_click_schema["properties"]["target"]["description"]
    assert "Windows control locator" in windows_click_schema["properties"]["locator"]["description"]

    assert "target, non-empty locator, or point" in macos_click_schema["description"]
    assert "macOS screen point" in macos_click_schema["properties"]["point"]["description"]

    assert MacOSLaunchAppParams().new_session is False
    assert macos_launch_schema["properties"]["new_session"]["default"] is False
    assert "existing Mac2 session" in macos_launch_schema["properties"]["new_session"]["description"]
    assert "session creation" in macos_launch_schema["properties"]["arguments"]["description"]
    assert "configured bundle id" in macos_launch_schema["properties"]["bundle_id"]["description"]
    assert "environment" not in macos_launch_schema["properties"]
    assert "retain" in macos_kill_schema["properties"]["close_session"]["description"]
    assert "Enter" in macos_press_key_schema["properties"]["key"]["description"]
    assert "COMMAND" in macos_press_key_schema["properties"]["modifiers"]["description"]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MacOSLaunchAppParams(environment={"APP_MODE": "test"})


def test_android_replay_references_are_hidden_but_validated_and_serialized() -> None:
    context = models.HarnessContext(platform="android", screen_size=(1080, 2400))
    for model, payload in (
        (models.AndroidTapAtParams, {"point": {"x": 1, "y": 2}}),
        (AndroidSwipeParams, {"start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}}),
    ):
        assert "reference_screen_size" not in model.model_json_schema()["properties"]
        params = model.model_validate({**payload, "reference_screen_size": {"width": 540, "height": 1200}})
        assert params.model_dump(mode="json")["reference_screen_size"] == {"width": 540, "height": 1200}
        assert params.replay_params(context)["reference_screen_size"] == {"width": 540, "height": 1200}
        assert model.model_validate(payload).replay_params(context)["reference_screen_size"] == {"width": 1080, "height": 2400}
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "reference_screen_size": {"width": 0, "height": 1200}})


@pytest.mark.parametrize("percent", [None, 1, 0.5, 0.0001])
def test_android_element_swipe_accepts_fraction_without_replay_reference(percent: float | None) -> None:
    params = AndroidSwipeParams(locator={"resourceId": "list"}, direction="up", percent=percent)
    assert params.percent == percent
    assert "reference_screen_size" not in params.replay_params(models.HarnessContext(platform="android", screen_size=(1080, 2400)))
    assert AndroidSwipeParams(target="List", direction="down").percent is None
    assert AndroidSwipeParams(direction="left", percent=None).direction == "left"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"start": {"x": 1, "y": 2}},
        {"direction": "up", "start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}},
        {"target": "List"},
        {"locator": {}, "direction": "up"},
        {"target": "List", "start": {"x": 1, "y": 2}, "end": {"x": 3, "y": 4}},
        {"direction": "up", "percent": 0.5},
        {"direction": "up", "reference_screen_size": {"width": 10, "height": 20}},
        {"target": "List", "direction": "up", "reference_screen_size": {"width": 10, "height": 20}},
        *({"target": "List", "direction": "up", "percent": value} for value in [True, False, 0, -0.5, 1.01, float("nan"), float("inf"), "0.5"]),
        *({"direction": "up", "duration": value} for value in [True, 0, 60001, 1.5]),
    ],
)
def test_android_swipe_rejects_conflicts_invalid_fractions_and_durations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AndroidSwipeParams.model_validate(payload)


def test_android_inspect_attributes_are_safe_explicit_and_duplicate_free() -> None:
    model = models.AndroidInspectElementParams
    assert model(target="Path").attributes == ("text", "desc")
    allowed = ("id", "text", "desc", "type", "package", "bounds", "enabled", "displayed", "checked", "selected", "clickable", "focused")
    assert model(locator={"resourceId": "path"}, attributes=allowed).attributes == allowed
    for attributes in ([], ["text", "text"], ["password"], ["value"], ["arbitrary"]):
        with pytest.raises(ValidationError):
            model(target="Path", attributes=attributes)
    with pytest.raises(ValidationError):
        model(locator={}, attributes=["text"])


def test_web_parameter_models_enforce_semantic_replay_contract() -> None:
    locator_schema = models.WebLocator.model_json_schema()
    scope_schema = models.WebLocatorScope.model_json_schema()
    click_schema = models.WebClickOnParams.model_json_schema()
    select_schema = models.WebSelectOptionParams.model_json_schema()

    assert set(locator_schema["properties"]) == {"role", "name", "within", "index"}
    assert locator_schema["required"] == ["role"]
    assert set(scope_schema["properties"]) == {"role", "name"}
    assert scope_schema["required"] == ["role"]
    assert "locator" in click_schema["required"]
    assert "target" not in click_schema["properties"]
    assert set(select_schema["properties"]) == {"locator", "labels"}

    locator = models.WebLocator(role="button", name="Edit", within={"role": "row", "name": "Alice"}, index=0)
    assert locator.model_dump(mode="json", exclude_none=True) == {
        "role": "button",
        "name": "Edit",
        "within": {"role": "row", "name": "Alice"},
        "index": 0,
    }

    for invalid_locator in ({"role": ""}, {"role": "button", "ref": "e1"}, {"role": "button", "within": {"role": "row", "index": 0}}):
        with pytest.raises(ValueError, match=r"non-empty role|Extra inputs are not permitted"):
            models.WebLocator.model_validate(invalid_locator)

    with pytest.raises(ValueError, match="unique"):
        models.WebSelectOptionParams(locator={"role": "combobox"}, labels=["Newest", "Newest"])
    with pytest.raises(ValueError, match="exactly one locator or url"):
        models.WebWaitForParams(locator={"role": "main"}, url="**/search**")
    with pytest.raises(ValueError, match="index"):
        models.WebWaitForParams(locator={"role": "status", "index": 0}, state="hidden")
    with pytest.raises(ValueError, match="index"):
        models.WebAssertNotVisibleParams(locator={"role": "status", "index": 0})
    with pytest.raises(ValueError, match="exactly one"):
        models.WebTextAssertion(contains="done", equals="done")
    with pytest.raises(ValueError, match="exactly one"):
        models.WebTextAssertion()


def test_local_tool_output_rejects_artifact_subdir_escape() -> None:
    with pytest.raises(ValueError, match="artifact_subdir"):
        LocalToolOutputSettings(artifact_subdir="../outside")


@pytest.mark.parametrize("field", ["full_output_max_chars", "total_inline_output_max_chars"])
def test_local_tool_output_rejects_nonpositive_character_budgets(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        LocalToolOutputSettings(**{field: 0})


def test_local_tool_output_allows_no_recent_inline_outputs() -> None:
    settings = LocalToolOutputSettings(recent_inline_output_count=0)

    assert settings.recent_inline_output_count == 0


def test_page_knowledge_page_uses_semantic_identifiers_and_reference_locators() -> None:
    page = PageKnowledgePage.model_validate(
        {
            "page_id": "edge_android_new_tab_page",
            "name": "New Tab Page",
            "identifiers": [{"name": "Account menu visible", "description": "Account entry is visible."}],
            "images": [{"path": "../assets/pages/ntp.png", "description": "Typical NTP."}],
            "elements": [
                {
                    "name": "Browser menu",
                    "role": "button",
                    "reference_locators": [
                        {
                            "strategy": "id",
                            "selector": "com.microsoft.emmx:id/overflow_button_bottom",
                            "confidence": "high",
                            "notes": "Observed in bottom toolbar mode.",
                        }
                    ],
                    "operations": [
                        {
                            "operation": "tap",
                            "result": {
                                "type": "navigate",
                                "to_page_id": "edge_android_overflow_menu",
                                "description": "Opens the overflow menu.",
                            },
                        }
                    ],
                }
            ],
        }
    )

    assert page.schema_version == "page_knowledge_page_v1"
    assert page.identifiers[0].model_dump() == {"name": "Account menu visible", "description": "Account entry is visible."}
    assert page.elements[0].reference_locators[0].confidence == "high"
    assert page.elements[0].operations[0].result.to_page_id == "edge_android_overflow_menu"


def test_page_knowledge_index_and_goal_pre_plan_defaults() -> None:
    index = PageKnowledgeIndex(
        product="Microsoft Edge",
        platform="Android",
        pages=[
            {
                "page_id": "edge_android_new_tab_page",
                "file": "pages/edge_android_new_tab_page.md",
                "name": "New Tab Page",
                "intents": ["new tab", "search"],
            }
        ],
    )
    plan = GoalPrePlan(
        goal="Open downloads",
        key_actions=[{"step_id": 1, "action": "Open browser menu", "source_page_ids": ["edge_android_new_tab_page"]}],
        verification_goal="Verify that Downloads can be opened from the browser menu.",
    )

    assert index.schema_version == "page_knowledge_index_v1"
    assert index.pages[0].page_id == "edge_android_new_tab_page"
    assert plan.schema_version == "goal_pre_plan_v1"
    assert plan.key_actions[0].step_id == 1
    assert plan.verification_goal == "Verify that Downloads can be opened from the browser menu."


class _IdleInt(int):
    pass


@pytest.mark.parametrize("value", [None, 0, 1000, 9_007_199_254_740_991])
def test_android_initial_idle_policy_preserves_exact_values(value: int | None) -> None:
    settings = AndroidHarnessSettings(uiautomator2_wait_for_idle_timeout_ms=value)
    assert settings.uiautomator2_wait_for_idle_timeout_ms == value
    assert type(settings.uiautomator2_wait_for_idle_timeout_ms) is type(value)


@pytest.mark.parametrize("value", [True, False, _IdleInt(1000), 0.0, 1000.0, "1000", "", -1, 9_007_199_254_740_992, 2**63 - 1])
def test_android_initial_idle_policy_rejects_coercion_and_inexact_values(value: object) -> None:
    with pytest.raises(ValidationError) as failed:
        AndroidHarnessSettings.model_validate({"uiautomator2_wait_for_idle_timeout_ms": value})
    assert failed.value.errors()[0]["loc"] == ("uiautomator2_wait_for_idle_timeout_ms",)


@pytest.mark.parametrize("duration", [1, 60000])
def test_android_swipe_duration_boundaries_and_nullable_point_fraction(duration: int) -> None:
    params = AndroidSwipeParams(start={"x": 1, "y": 2}, end={"x": 3, "y": 4}, percent=None, duration=duration)
    assert params.duration == duration
    assert params.percent is None
