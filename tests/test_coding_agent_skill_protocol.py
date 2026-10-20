# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
import shlex
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from fsq_agent.adapters.cli import main

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "fsq_agent/resources/skills/fsq/SKILL.md"
DESCRIPTION = "Guide FSQ Quick Test UI verification from a natural-language goal, with readiness discovery, evidence, and explicit Case creation or replay when requested."


def skill():
    return SKILL_PATH.read_text(encoding="utf-8")


def commands():
    return [shlex.split(text) for text in re.findall(r"`(fsq [^`\n]+)`", skill())]


def test_portable_skill_frontmatter():
    value = skill()
    assert value.startswith("---\n")
    assert yaml.safe_load(value.split("---\n")[1]) == {"name": "fsq", "description": DESCRIPTION}
    assert "{{" not in value


def test_every_skill_command_template_matches_real_parser(tmp_path):
    values = {
        "<platform>": "web",
        "<desktop-platform>": "macos",
        "<target-id>": "chrome",
        "<confirmed-goal>": "Verify https://example.test search",
        "<exact-url>": "https://example.test/path?q=%2f#part!",
        "<run-id>": "run-exact",
        "<exact-case-path>": str(tmp_path / "example.fsq.yaml"),
    }
    assert commands()
    for argv in commands():
        result = CliRunner().invoke(main, [*[values.get(item, item) for item in argv[1:]], "--help"])
        assert result.exit_code == 0, (argv, result.output)


def test_default_journey_uses_only_quick_test_commands():
    default = skill().split("## Explicit Case creation")[0]
    parsed = [shlex.split(text) for text in re.findall(r"`(fsq [^`\n]+)`", default)]
    assert any("quick-test" in argv and "run" in argv and "jsonl" in argv for argv in parsed)
    assert not any("case" in argv or "init" in argv or "doctor" in argv for argv in parsed)
    assert not any("--api-key" in argv or "ui" in argv or "--suggest" in argv or "--write" in argv for argv in commands())


def test_known_web_url_has_separate_target_template_and_keeps_legacy_commands():
    runs = [argv for argv in commands() if "quick-test" in argv and "run" in argv]
    explicit = [argv for argv in runs if "--url" in argv]
    assert len(explicit) == 1
    argv = explicit[0]
    assert argv[argv.index("--platform") + 1] == "web"
    assert argv[argv.index("--url") + 1] == "<exact-url>"
    assert "--goal" in argv
    assert "--target-id" in argv
    assert any("--url" not in item and "--target-id" in item for item in runs)
    assert any("--url" not in item and "--target-id" not in item for item in runs)
    text = " ".join(skill().split())
    assert "actions and checkable outcomes" in text
    assert "Never reconstruct or normalize" in text


@pytest.mark.parametrize(
    ("section", "requirements"),
    [
        (
            "## Run Quick Test once with real progress",
            ("same host process/session handle", "Never automatically resubmit", "quick-test.run", "exactly one terminal", "A cancellation request is not proof"),
        ),
        ("## Interpret and report the result", ("supported, unmet, or unknown", "Coding Agent interpretation", "unpublished", "no Run", "exact returned registered Workspace root")),
        ("## Explicit Case creation", ("separate UI execution", "publication_outcome=created", "Never delete", "Never reconstruct")),
        ("## User-initiated strict replay", ("Dynamic success does not prove strict replay", "confirmation after this disclosure", "No retry")),
        ("## Upgrade an existing copied Skill", ("fsq init --agent skill", "backup", "--no-overwrite", "resources are not")),
    ],
)
def test_skill_policy_sections_keep_essential_boundaries(section, requirements):
    # Prompt regression checks only; model compliance needs separate live evidence.
    text = " ".join(skill().split(section, 1)[1].split("\n## ", 1)[0].split())
    for requirement in requirements:
        assert requirement in text


def test_explicit_provider_free_fallback_is_not_default():
    text = skill().split("## Provider-free Web troubleshooting fallback", 1)[1]
    assert "assets/getting-started.web.fsq.yaml" in text
    assert "not a prerequisite for Quick Test" in " ".join(text.split())
    assert (SKILL_PATH.parent / "assets/getting-started.web.fsq.yaml").is_file()
