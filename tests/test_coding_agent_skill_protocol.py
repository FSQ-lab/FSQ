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
EXPECTED_DESCRIPTION = "Guide one user-confirmed FSQ test from readiness through Case creation, review, and optional strict replay."


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
        "One confirmation authorizes one `case create` submission",
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

    assert skill.index("## Establish exact scope and permission") < skill.index("## Initialize only with permission")
    assert skill.index("## Provider handoff") < skill.index("## Confirm and create exactly once")
    assert skill.index("## Review the exact returned Case") < skill.index("## Optional strict replay")


def test_skill_branches_registered_workspace_before_initialization() -> None:
    skill = " ".join(_skill().split())

    registered_branch = "If an already registered usable Workspace exists"
    initialization_branch = "Otherwise, explain the initialization mutation"

    assert registered_branch in skill
    assert "change directly to its exact confirmed registered root" in skill
    assert "run Doctor there without invoking initialization" in skill
    assert initialization_branch in skill
    assert "obtain explicit permission, and initialize exactly once" in skill
    assert "actual returned Workspace root" in skill
    assert skill.index(registered_branch) < skill.index(initialization_branch)


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
