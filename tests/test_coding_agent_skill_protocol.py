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


def _parser_arguments(template: list[str], tmp_path: Path) -> list[str]:
    values = {
        "<platform>": "web",
        "<provider>": "openai",
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
        "confirmation is consumed when submission begins",
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

    assert skill.index("## Discover before asking") < skill.index("## Initialize only with permission")
    assert skill.index("## Provider handoff") < skill.index("## Confirm and create once")
    assert skill.index("## Interpret and report the terminal result") < skill.index("## Optional exact-Case review")


def test_skill_discovers_exact_current_candidate_before_initialization() -> None:
    skill = " ".join(_skill().split())

    doctor = "`fsq --output json --non-interactive doctor`"
    unresolved = "ask for either an exact registered Workspace root or an exact directory to initialize"

    assert "Use the exact current working directory as the first and only initial Workspace candidate" in skill
    assert "Before asking for any Workspace, platform, target, or readiness fact Doctor can provide" in skill
    assert unresolved in skill
    assert "Do not search parent or sibling directories, scan broadly, or try guessed roots" in skill
    assert "obtain explicit permission, and initialize exactly once" in skill
    assert "actual returned Workspace root" in skill
    assert skill.index(doctor) < skill.index(unresolved)


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
            "separate mutation boundaries",
            (
                "Initialization, one create submission, and replay are three separate mutation boundaries",
                "Do not ask for future replay permission during Explore",
            ),
            (),
        ),
        (
            "minimal duplicate prevention",
            (
                "minimal in-session state",
                "does not provide cross-session exactly-once behavior",
                "Never submit the same confirmation twice",
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
