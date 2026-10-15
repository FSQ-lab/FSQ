# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml
from click.testing import CliRunner

from fsq_agent.adapters.cli import main

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "fsq_agent" / "resources" / "skills" / "fsq" / "SKILL.md"
EXPECTED_DESCRIPTION = "Guide FSQ UI testing when users want to explore an app, test a UI, verify a workflow, or create an FSQ Case from a Goal, with readiness discovery and user-confirmed execution."


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter(skill: str) -> dict[str, str]:
    assert skill.startswith("---\n")
    _, raw, _ = skill.split("---\n", 2)
    value = yaml.safe_load(raw)
    assert isinstance(value, dict)
    return value


def _command_templates(skill: str) -> list[list[str]]:
    return [shlex.split(value) for value in re.findall(r"`(fsq [^`\n]+)`", skill)]


def _section(skill: str, heading: str, next_heading: str) -> str:
    start = skill.index(heading)
    end = skill.index(next_heading, start)
    return " ".join(skill[start:end].split())


def _parser_arguments(template: list[str], tmp_path: Path) -> list[str]:
    values = {
        "<platform>": "web",
        "<provider>": "openai",
        "<app-id>": "com.example.app",
        "<confirmed-goal>": "Verify the exact outcome; allow only review; forbid purchases.",
        "<exact-case-path>": str(tmp_path / "case with spaces.fsq.yaml"),
        "<run-id>": "run-exact-1",
    }
    return [values.get(value, value) for value in template[1:]]


def test_skill_frontmatter_is_portable_and_package_owned() -> None:
    skill = _skill()

    assert _frontmatter(skill) == {"name": "fsq", "description": EXPECTED_DESCRIPTION}
    assert skill.count("\n---\n") == 1
    assert "{{" not in skill


def test_skill_protocol_contains_confirmed_workflow_boundaries() -> None:
    # Structural prompt regressions only; these checks do not prove model compliance.
    skill = " ".join(_skill().split())

    for text in (
        "Coding Agent owns the dialogue and orchestration",
        "FSQ remains a passive CLI/Application",
        "Provider API keys, tokens, and authentication secrets must not enter chat",
        "user's request authorizes one normal Goal run",
        "FSQ has no new structured status",
        "published_case_path",
        "candidate_case_path",
        "unpublished",
        "Dynamic success does not prove strict replay success",
        "user-operated alternative",
        "package upgrade or host reload alone does not update an existing copied Skill",
        "`--no-overwrite` preserves the old file",
    ):
        assert text in skill

    assert skill.index("## Start from the goal and discover before asking") < skill.index("## Recommend setup once")
    assert skill.index("## Provider handoff") < skill.index("## Run the Goal once")
    assert skill.index("## Interpret and report the terminal result") < skill.index("## Optional exact-Case review")


def test_skill_distinguishes_project_root_from_workspace_root() -> None:
    skill = " ".join(_skill().split())

    doctor = "`fsq --output json --non-interactive doctor`"
    default_root = "exact `<project-root>/fsq_workspace`"

    assert "exact opened Coding Agent project root" in skill
    assert "current working directory is only a candidate" in skill
    assert "ask the user to resolve the project-root ambiguity" in skill
    assert "project root and Workspace root are distinct" in skill
    assert "Run Doctor from the exact project root first" in skill
    assert default_root in skill
    assert "Do not search parent or sibling directories or scan broadly" in skill
    assert "actual returned Workspace root" in skill
    assert skill.index(doctor) < skill.index(default_root)


def test_skill_guards_default_workspace_path_state_matrix() -> None:
    skill = " ".join(_skill().split())

    for required in (
        "valid registered Workspace at the project root",
        "valid registered Workspace at the default path",
        "absent",
        "existing empty ordinary directory",
        "symlink",
        "non-directory",
        "unreadable",
        "indeterminate",
        "non-empty",
        "host-native filesystem capability",
        "one empty directory",
    ):
        assert required in skill

    assert "Never follow or replace a symlink" in skill
    assert "Never overwrite, delete, move, migrate" in skill
    assert "nested Workspace" in skill
    assert "Do not prescribe or assume `mkdir` or any shell" in skill


def test_skill_only_confirms_absent_default_directory_creation() -> None:
    recommendation = _section(_skill(), "## Recommend setup once", "## Provider handoff")
    for required in (
        "exact project root",
        "exact proposed Workspace root",
        "platform",
        "exact target",
        "fact source",
        "unverified candidate",
        "exact existing `fsq init` argv",
        "filesystem",
        "registry",
        "remain unauthorized",
        "only FSQ-layer setup confirmation",
        "absent default path",
        "refusal means FSQ cannot be used for this request",
        "existing empty ordinary directory",
        "without an FSQ-layer confirmation",
        "actual returned Workspace root",
        "Doctor",
    ):
        assert required in recommendation
    assert "initialization approval" not in recommendation
    refusal = "Refusal stops the FSQ journey without mutation, fallback search, or a failure-shaped Run result."
    assert refusal in recommendation
    assert recommendation.index("refusal means FSQ cannot be used for this request") < recommendation.index(refusal)
    assert "try another Workspace" not in recommendation


def test_skill_keeps_facts_candidates_and_user_decisions_distinct() -> None:
    skill = " ".join(_skill().split())

    for required in (
        "FSQ-discovered facts",
        "host facts",
        "user decisions",
        "unverified model candidates",
        "smallest cohesive group of unresolved user-owned decisions",
        "application ID",
        "Browser targets",
        "executable path",
        "bundle ID",
        "device",
        "account",
        "cannot enter argv unless established",
    ):
        assert required in skill


def test_skill_resolves_android_app_id_from_bounded_facts_and_updates_visibly() -> None:
    skill = " ".join(_skill().split())
    android = _section(_skill(), "### Resolve an Android application ID", "## Provider handoff")

    for required in (
        "bounded read-only host and connected-device facts",
        "normalized and deduplicated by exact application ID",
        "one reasonable candidate",
        "select it without confirmation",
        "multiple reasonable candidates",
        "one user choice",
        "request an exact application ID",
        "model memory",
        "unrelated existing Workspace target",
        "report the old and new application IDs",
        '`fsq init --platform android --app-id "<app-id>" --update-existing`',
        "without an FSQ-layer confirmation",
    ):
        assert required in skill
    update = '`fsq init --platform android --app-id "<app-id>" --update-existing`'
    stop = "A failed or uncertain update stops before Run allocation"
    assert update in android
    assert stop in android
    assert android.index(update) < android.index(stop)
    assert "continue with the Goal after a failed or uncertain update" not in android


def test_cleanup_success_excludes_publication_and_routes_review_to_returned_candidate() -> None:
    terminal = _section(_skill(), "## Interpret and report the terminal result", "## Optional exact-Case review")
    review = _section(_skill(), "## Optional exact-Case review", "## User-initiated strict replay")

    successful_deletion = "After confirmed successful deletion"
    not_retained = "the returned publication is not retained"
    candidate = "exact non-empty returned `candidate_case_path`"
    assert successful_deletion in terminal
    assert not_retained in terminal
    assert candidate in terminal
    assert terminal.index(successful_deletion) < terminal.index(not_retained) < terminal.index(candidate)
    assert "must not be selected for review or replay" in terminal

    assert "post-cleanup retained-path state" in review
    assert "successfully deleted publication must not be selected" in review
    assert candidate in review
    assert "identify it as an unpublished candidate" in review
    assert "use a non-empty returned `published_case_path`; otherwise" not in review
    assert "Never construct a path" in review


def test_cleanup_uncertainty_is_reported_once_without_changing_execution_conclusion() -> None:
    terminal = _section(_skill(), "## Interpret and report the terminal result", "## Optional exact-Case review")

    uncertainty = "If deletion fails or is uncertain"
    report = "report that uncertainty"
    no_retry = "never retry deletion"
    preserved = "does not change the authoritative execution conclusion"
    conclusion = "Report the authoritative execution conclusion and short summary first."
    assert uncertainty in terminal
    assert report in terminal
    assert no_retry in terminal
    assert preserved in terminal
    assert "do not claim the publication absent" in terminal
    assert terminal.index(uncertainty) < terminal.index(report) < terminal.index(no_retry)
    assert terminal.index(conclusion) < terminal.index(uncertainty)
    assert "treat the publication as absent" not in terminal


def test_skill_goal_defaults_only_reduce_authority_and_provider_resume_is_stable() -> None:
    skill = " ".join(_skill().split())

    for required in (
        "Automatic defaults may only reduce authority or stop on ambiguity",
        "account, recipient, exact content, amount, device, or irreversible action",
        "Never expand authority",
        "exact literals and side effects",
        "one complete Goal",
        "user's request authorizes one normal Goal run",
        "does not require a separate create or run confirmation",
        "Host, operating-system, or Coding Agent approval",
        "resume the same Workspace, target, and Goal draft",
        "without repeating settled questions unless facts changed",
    ):
        assert required in skill


def test_skill_behavior_scenarios_follow_confirmed_policy_table() -> None:
    # Deterministic text checks guard policy wording; they do not prove model compliance.
    skill = " ".join(_skill().split())
    scenarios = (
        (
            "read-only batch disclosure",
            (
                "one compact batch disclosure",
                "exact working directory, argv, purpose, and read-only effect",
                "Do not ask for separate approval for each ordinary read-only command",
            ),
            (),
        ),
        (
            "FSQ confirmation boundaries",
            (
                "Only creation of the absent default Workspace directory gets an FSQ-layer confirmation",
                "Do not ask for future replay permission during Explore",
            ),
            (),
        ),
        (
            "minimal duplicate prevention",
            (
                "minimal in-session state",
                "does not provide cross-session exactly-once behavior",
                "Never submit the same authorized Goal run twice",
            ),
            (),
        ),
        (
            "conditional publication cleanup",
            (
                "publication_outcome=created",
                "exact non-empty `published_case_path`",
                "host-native exact-file deletion",
                "Never derive a deletion path",
                "never delete the candidate or Run evidence",
                "Do not delete for `unchanged`, `conflict`, `failed`, or a missing path",
                "never retry deletion",
            ),
            (),
        ),
        (
            "valuable publication and replay boundary",
            (
                "every observable acceptance criterion is supported",
                "replay value",
                "report the exact returned `published_case_path`",
                "Never automatically replay",
            ),
            (),
        ),
        (
            "buffered progress silence",
            (
                "Report submission exactly once",
                "at most one unsolicited",
                "wait silently",
                "When the user asks for status",
                "Do not invent an action or stage",
            ),
            (),
        ),
        (
            "minimal machine interpretation",
            (
                "`fsq.machine/v1`",
                "operation matches",
                "structured nonzero",
                "unknown remainder",
                "without retry",
            ),
            (),
        ),
        (
            "result-first report",
            (
                "authoritative execution conclusion and short summary first",
                "observable acceptance criterion",
                "recording",
                "publication",
                "warnings",
            ),
            (),
        ),
        (
            "optional exact-path review",
            (
                "Review only when the user asks",
                "read-only",
                "Never construct a path",
            ),
            (),
        ),
        (
            "two-decision replay",
            (
                "Only a user request starts replay consideration",
                "does not authorize execution",
                "second explicit confirmation",
                "known intermediate failure, recovery, adjusted execution, or repeated corrective mutation",
                "does not hard-block replay",
                "at most one unchanged `case test` submission",
            ),
            ("`--suggest`",),
        ),
    )

    for scenario, required, forbidden in scenarios:
        for text in required:
            assert text in skill, (scenario, text)
        for text in forbidden:
            assert text not in " ".join(" ".join(command) for command in _command_templates(_skill())), (scenario, text)


def test_every_literal_skill_command_template_matches_the_real_cli_parser(tmp_path: Path) -> None:
    templates = _command_templates(_skill())
    assert templates

    for template in templates:
        result = CliRunner().invoke(main, [*_parser_arguments(template, tmp_path), "--help"])
        assert result.exit_code == 0, (template, result.output)
        assert "Usage:" in result.output


def test_skill_command_surface_excludes_forbidden_operations_and_secret_arguments() -> None:
    skill = _skill()
    commands = [" ".join(template) for template in _command_templates(skill)]

    assert "fsq --output json --non-interactive doctor" in commands
    assert "fsq --output json --non-interactive providers status" in commands
    assert "fsq init --platform android --app-id <app-id> --update-existing" in commands
    assert "fsq --output json --non-interactive case create --platform <platform> --goal <confirmed-goal>" in commands
    assert "fsq case format <exact-case-path> --check --json" in commands
    assert "fsq --output json --non-interactive case test --platform <platform> <exact-case-path>" in commands
    assert "fsq --output json --non-interactive runs show <run-id> --platform <platform>" in commands
    assert "fsq --output json --non-interactive runs logs <run-id> --platform <platform> --limit 50" in commands
    assert "fsq init --agent skill" in commands
    assert not any("runs list" in command for command in commands)
    assert not any("--suggest" in command for command in commands)
    assert not any("--write" in command for command in commands)
    assert not any("--api-key" in command for command in commands)
    assert not any(command == "fsq ui" or command.startswith("fsq ui ") for command in commands)


def test_bundled_web_case_remains_an_explicit_provider_free_fallback() -> None:
    skill = _skill()

    assert "## Provider-free Web troubleshooting fallback" in skill
    assert "assets/getting-started.web.fsq.yaml" in skill
    assert "commands.case_test.status" in skill
    assert "valid=true" in skill
    assert "at most one execution" in skill
    assert "exact returned Run ID" in skill
    assert "Never retry" in skill
