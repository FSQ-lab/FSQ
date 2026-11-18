# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest

from fsq_agent.agent import CodingAgentPolicy
from fsq_agent.models import AgentFinalOutput, AgentPromptConfig, StepResult, Task


def _task_envelope(prompt):
    return json.JSONDecoder().raw_decode(prompt.removeprefix("Structured task input:\n"))[0]


@pytest.mark.parametrize("verification_goal", [None, "", "  "])
def test_missing_goal_uses_same_request_grounded_criterion_in_execution_and_verification(verification_goal):
    request = "Inspect the currently visible Downloads entries; report only titles, without editing or deleting anything."
    task = Task(id="inspect", name="Inspect Downloads", description=request, verification_goal=verification_goal)
    policy = CodingAgentPolicy()

    main_input = _task_envelope(policy.build_task_prompt(AgentPromptConfig(), task))
    verifier_input = json.loads(policy.build_verification_input(task, []))

    assert main_input["verification_goal"] == verifier_input["verification_goal"]
    assert request in main_input["verification_goal"]
    assert main_input["key_actions"] == []
    assert main_input["task"]["description"] == request
    assert task.verification_goal == verification_goal


@pytest.mark.parametrize(
    "goal",
    [
        'Save exactly "8 servings by" in extensionless file "Recipe", with no extra entries.',
        "Create the requested playlist with a total duration from 45:00 through 50:00 and no extras.",
        "Return only the integer distance in meters, rounded to the nearest meter.",
    ],
)
def test_objective_goals_are_unchanged_across_main_and_verifier_inputs(goal):
    policy = CodingAgentPolicy()
    task = Task(id="objective", description="Perform the requested operation.", verification_goal=goal)

    main_input = _task_envelope(policy.build_task_prompt(AgentPromptConfig(), task))
    verifier_input = json.loads(policy.build_verification_input(task, []))

    assert main_input["verification_goal"] == goal
    assert verifier_input["verification_goal"] == goal


@pytest.mark.parametrize("summary", ["6510", "First title\nSecond title"])
def test_supported_answer_survives_structured_uncertainty_and_verification_evidence(summary):
    policy = CodingAgentPolicy()
    output = AgentFinalOutput(status="inconclusive", summary=summary, evidence=["Read the visible source values."], errors=["Coverage of off-screen entries remains unverified."])
    results = [StepResult(step_id=1, status="success", tool_name="agent_runtime.runner", actual_outcome=policy.serialize_agent_final_output(output))]

    bundle = json.loads(policy.build_verification_input(Task(id="answer", description="Report the requested observed answer."), results))
    parsed = policy.coerce_agent_final_output(results[0].actual_outcome)

    assert parsed.summary == summary
    assert parsed.status == "inconclusive"
    assert bundle["agent_claims"]["summary"] == summary
    assert bundle["agent_claims"]["errors"] == output.errors
    assert bundle["agent_claims"]["status"] == "inconclusive"


def test_fallback_preserves_reference_description_and_explicit_constraints():
    policy = CodingAgentPolicy()
    task = Task(
        id="preserve-scope",
        name="Inspect files",
        description="Leave the document unsaved and return only complete filenames.",
        planning_reference_kind="goal",
        planning_reference_text="Browse Downloads without modifying the collection.",
        acceptance_criteria=["Do not submit or rename anything."],
    )

    envelope = _task_envelope(policy.build_task_prompt(AgentPromptConfig(), task))
    goal = envelope["verification_goal"]

    assert task.description in goal
    assert task.planning_reference_text in goal
    assert task.acceptance_criteria[0] in goal
    assert envelope["key_actions"] == []
