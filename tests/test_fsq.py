# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
from pathlib import Path

import pytest

from fsq_agent.adapters.cli._task_loader import discover_case_yaml_paths, read_raw_text_file, resolve_case_yaml_path
from fsq_agent.case_dsl import FSQ_CASE_SUFFIX, FsqCaseLoader, is_fsq_case_file
from fsq_agent.models import ConfigurationError

FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Fundamental Test bing.com website
description: Converted from Edge Android Behave BDD scenario.
platform: android
appId: com.microsoft.emmx
tags:
  - p0
    - fsq-converted
---
- launchApp
- assertVisible:
    target: New Tab Page account menu
    locator:
      accessibilityId: Account menu
    optional: false
- tapOn:
    target: Search box in NTP page
    locator:
      resourceId: com.microsoft.emmx:id/search_box_text
- inputText:
    text: bing.com
    target: Search box
    locator:
      resourceId: com.microsoft.emmx:id/url_bar
- pressKey:
    key: Enter
- assertWithAI:
    assertion: Verify the webpage appearance.
    surface: appearance
    full_screen_reason: The entire page appearance is under test.
    optional: false
- killApp
"""


def test_fsq_case_loader_loads_two_document_case(tmp_path: Path) -> None:
    case_path = tmp_path / "fundamental_test_bing_com_website.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")

    case = FsqCaseLoader().load_case(case_path)

    assert case.config.schema_version == "fsq.ai-test/v1"
    assert case.config.name == "Fundamental Test bing.com website"
    assert case.config.platform == "android"
    assert case.config.app_id == "com.microsoft.emmx"
    assert len(case.commands) == 7
    assert case.config.on_case_start == []
    assert case.config.on_case_complete == []


def test_fsq_case_loader_loads_lifecycle_hooks_preserving_action_order(tmp_path: Path) -> None:
    case_path = tmp_path / "hooks.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\n"
        "name: Hooked Case\n"
        "platform: web\n"
        "onCaseStart:\n"
        "  runShell: ./scripts/prepare.sh\n"
        "  runCase: hooks/login.fsq.yaml\n"
        "onCaseComplete:\n"
        "  - runCase: hooks/logout.fsq.yaml\n"
        "    runShell: ./scripts/cleanup.sh\n"
        "  - runShell: ./scripts/remove-temp-files.sh\n"
        "---\n"
        "[]\n",
        encoding="utf-8",
    )

    case = FsqCaseLoader().load_case(case_path)

    assert [[action.action_name, action.value] for action in case.config.on_case_start[0].actions] == [
        ["runShell", "./scripts/prepare.sh"],
        ["runCase", "hooks/login.fsq.yaml"],
    ]
    assert [[action.action_name, action.value] for action in case.config.on_case_complete[0].actions] == [
        ["runCase", "hooks/logout.fsq.yaml"],
        ["runShell", "./scripts/cleanup.sh"],
    ]
    assert [[action.action_name, action.value] for action in case.config.on_case_complete[1].actions] == [
        ["runShell", "./scripts/remove-temp-files.sh"],
    ]


def test_fsq_case_loader_accepts_explicit_empty_lifecycle_hook_list(tmp_path: Path) -> None:
    case_path = tmp_path / "empty_hooks.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Empty Hooks
platform: web
onCaseStart: []
onCaseComplete: []
---
[]
""",
        encoding="utf-8",
    )

    case = FsqCaseLoader().load_case(case_path)

    assert case.config.on_case_start == []
    assert case.config.on_case_complete == []


@pytest.mark.parametrize(
    "hook_yaml",
    [
        "onCaseStart: not-a-mapping",
        "onCaseStart:\n  unknown: value",
        "onCaseStart:\n  runCase: ''",
        "onCaseStart:\n  runShell: ''",
        "onCaseStart:\n  - []",
        "onCaseStart:\n  actions: []",
    ],
)
def test_fsq_case_loader_rejects_invalid_lifecycle_hooks(tmp_path: Path, hook_yaml: str) -> None:
    case_path = tmp_path / "bad_hooks.fsq.yaml"
    case_path.write_text(
        f"""
schemaVersion: fsq.ai-test/v1
name: Bad Hooks
platform: web
{hook_yaml}
---
[]
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid FSQ case config"):
        FsqCaseLoader().load_case(case_path)


def test_fsq_case_loader_accepts_single_document_goal_only_case(tmp_path: Path) -> None:
    case_path = tmp_path / "single_doc_goal.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Single Document Goal
platform: android
""",
        encoding="utf-8",
    )

    case = FsqCaseLoader().load_case(case_path)

    assert case.commands == []
    assert case.config.name == "Single Document Goal"


def test_fsq_case_loader_accepts_empty_command_document_goal_only_case(tmp_path: Path) -> None:
    case_path = tmp_path / "empty_commands_goal.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Empty Commands Goal
platform: android
---
[]
""",
        encoding="utf-8",
    )

    case = FsqCaseLoader().load_case(case_path)

    assert case.commands == []


def test_fsq_case_loader_rejects_too_many_documents(tmp_path: Path) -> None:
    case_path = tmp_path / "bad.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Bad\nplatform: android\n---\n[]\n---\nextra: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid FSQ case file"):
        FsqCaseLoader().load_case(case_path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("case.fsq.yaml", True),
        ("nested/case.fsq.yaml", True),
        ("case.FSQ.yaml", False),
        ("case.fsq.yml", False),
        ("case.yaml", False),
    ],
)
def test_fsq_case_suffix_contract_is_exact_and_case_sensitive(path: str, expected: bool) -> None:
    assert FSQ_CASE_SUFFIX == ".fsq.yaml"
    assert is_fsq_case_file(path) is expected


def test_fsq_case_loader_rejects_wrong_suffix_before_reading(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"\.fsq\.yaml"):
        FsqCaseLoader().load_case(tmp_path / "missing.yaml")


def test_fsq_case_loader_discovers_only_exact_lowercase_suffix(tmp_path: Path) -> None:
    expected = tmp_path / "expected.fsq.yaml"
    expected.write_text(FSQ_CASE, encoding="utf-8")
    (tmp_path / "excluded.FSQ.yaml").write_text(FSQ_CASE, encoding="utf-8")

    assert [case.path for case in FsqCaseLoader().load_cases(tmp_path)] == [expected]


def test_fsq_case_loader_rejects_discovered_symlink_escape(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    outside = tmp_path / "outside.fsq.yaml"
    outside.write_text(FSQ_CASE, encoding="utf-8")
    link = cases_dir / "linked.fsq.yaml"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    with pytest.raises(ConfigurationError, match="case directory"):
        FsqCaseLoader().load_cases(cases_dir)


def test_fsq_module_does_not_import_root_private_modules() -> None:
    package_root = Path(__file__).resolve().parents[1] / "fsq_agent" / "fsq"
    imports: set[str] = set()
    for source_path in package_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not {module for module in imports if module.startswith("fsq_agent._")}


def test_resolve_case_yaml_path_uses_cases_dir_and_requires_suffix(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_path = cases_dir / "case.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    legacy_path = cases_dir / "legacy.yaml"
    legacy_path.write_text("name: legacy\n", encoding="utf-8")

    assert resolve_case_yaml_path("case.fsq.yaml", cases_dir) == case_path.resolve()
    with pytest.raises(ConfigurationError, match=r"\.fsq\.yaml"):
        resolve_case_yaml_path("legacy.yaml", cases_dir)


def test_discover_case_yaml_paths_prefers_recursive_fsq_cases(tmp_path: Path) -> None:
    area = tmp_path / "android" / "rendering"
    area.mkdir(parents=True)
    case_path = area / "case.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    (tmp_path / "legacy.yaml").write_text("description: legacy\n", encoding="utf-8")

    assert discover_case_yaml_paths(tmp_path) == [case_path.resolve()]


def test_discover_case_yaml_paths_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"No \.fsq\.yaml"):
        discover_case_yaml_paths(tmp_path)


def test_discover_case_yaml_paths_rejects_discovered_symlink_escape(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    outside = tmp_path / "outside.fsq.yaml"
    outside.write_text(FSQ_CASE, encoding="utf-8")
    link = cases_dir / "linked.fsq.yaml"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    with pytest.raises(ConfigurationError, match="case directory"):
        discover_case_yaml_paths(cases_dir, cases_dir)


def test_read_raw_text_file_returns_invalid_yaml_without_parsing(tmp_path: Path) -> None:
    case_path = tmp_path / "case.fsq.yaml"
    case_path.write_text("not: [valid yaml", encoding="utf-8")

    path, content = read_raw_text_file(case_path)

    assert path == case_path.resolve()
    assert content == "not: [valid yaml"
