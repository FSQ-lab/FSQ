# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from fsq_agent.application import ApplicationError, CodingAgentInstallRequest, install_coding_agent
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer
from fsq_agent.core import CapabilityDefinitionFactory, CapabilityRegistry, CommonPlatformTools

ROOT = Path(__file__).resolve().parents[1]
GETTING_STARTED_DATA_URL = (
    "data:text/html,%3C%21doctype%20html%3E%3Chtml%20lang%3D%22en%22%3E"
    "%3Chead%3E%3Cmeta%20charset%3D%22utf-8%22%3E%3Ctitle%3EFSQ%20Web%20"
    "Strict%20Replay%3C%2Ftitle%3E%3C%2Fhead%3E%3Cbody%3E%3Ch1%3EFSQ%20Web"
    "%20Strict%20Replay%3C%2Fh1%3E%3C%2Fbody%3E%3C%2Fhtml%3E"
)
SKILL_DESCRIPTION = "Guide FSQ UI testing when users want to explore an app, test a UI, verify a workflow, or create an FSQ Case from a Goal, with readiness discovery and user-confirmed execution."


def _web_registry():
    definitions = [
        *CommonPlatformTools.capability_definitions(),
        *CapabilityDefinitionFactory().platform_definitions(platform="web", include_ai_assertion=True),
    ]
    return CapabilityRegistry.from_definitions(definitions)


def _install(project: Path, *, workspace: Path | None = None, no_overwrite: bool = False):
    return install_coding_agent(
        CodingAgentInstallRequest(
            project_directory=project,
            agent="codex",
            workspace_root=workspace or project / "workspace",
            no_overwrite=no_overwrite,
        )
    )


def _install_skill(project: Path, *, workspace: Path | None = None, no_overwrite: bool = False):
    return install_coding_agent(
        CodingAgentInstallRequest(
            project_directory=project,
            agent="skill",
            workspace_root=workspace or project / "workspace",
            no_overwrite=no_overwrite,
        )
    )


def _skill_files(project: Path) -> tuple[Path, Path]:
    root = project / ".agents" / "skills" / "fsq"
    return root / "SKILL.md", root / "assets" / "getting-started.web.fsq.yaml"


def test_install_skill_creates_exact_manifest_and_canonical_case(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = _install_skill(project)
    skill, case = _skill_files(project)

    assert result.agent == "skill"
    assert [item.path.relative_to(project).as_posix() for item in result.files] == [
        ".agents/skills/fsq/SKILL.md",
        ".agents/skills/fsq/assets/getting-started.web.fsq.yaml",
    ]
    assert [item.status for item in result.files] == ["created", "created"]
    resource_root = ROOT / "fsq_agent" / "resources" / "skills" / "fsq"
    assert skill.read_bytes() == (resource_root / "SKILL.md").read_bytes()
    assert case.read_bytes() == (resource_root / "assets" / case.name).read_bytes()
    parsed = FsqCaseLoader().load_case(case)
    canonical = FsqCaseSerializer(_web_registry().snapshot()).serialize(parsed)
    assert canonical == case.read_bytes()
    assert parsed.config.env == {}
    assert parsed.config.on_case_start == []
    assert parsed.config.on_case_complete == []
    assert parsed.commands == [
        {"startBrowser": {}},
        {"navigateTo": {"url": GETTING_STARTED_DATA_URL}},
        {"assertVisible": {"locator": {"role": "heading", "name": "FSQ Web Strict Replay"}}},
        {"closeBrowser": {}},
    ]
    assert not {"assertWithAI", "runCase", "runShell"} & {next(iter(command)) for command in parsed.commands}


def test_install_skill_is_idempotent_and_preserves_unmanaged_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _install_skill(project)
    unmanaged = project / ".agents" / "skills" / "fsq" / "notes.txt"
    unmanaged.write_text("keep", encoding="utf-8")

    result = _install_skill(project)

    assert [item.status for item in result.files] == ["unchanged", "unchanged"]
    assert unmanaged.read_text(encoding="utf-8") == "keep"


def test_install_skill_updates_with_unique_backup(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _install_skill(project)
    skill, _ = _skill_files(project)
    skill.write_text("old", encoding="utf-8")

    result = _install_skill(project)
    updated = result.files[0]

    assert updated.status == "updated"
    assert updated.backup_path is not None
    assert updated.backup_path.read_text(encoding="utf-8") == "old"
    assert skill.read_bytes() == (ROOT / "fsq_agent/resources/skills/fsq/SKILL.md").read_bytes()


def test_install_skill_no_overwrite_and_partial_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    skill, case = _skill_files(project)
    case.parent.mkdir(parents=True)
    skill.write_text("keep", encoding="utf-8")

    result = _install_skill(project, no_overwrite=True)

    assert [item.status for item in result.files] == ["skipped", "created"]
    assert skill.read_text(encoding="utf-8") == "keep"
    assert case.is_file()


@pytest.mark.parametrize("relative", [".agents", ".agents/skills", ".agents/skills/fsq"])
@pytest.mark.parametrize("broken", [False, True])
def test_install_skill_rejects_symlinked_or_broken_tree_directory(tmp_path: Path, relative: str, broken: bool) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "outside"
    if not broken:
        target.mkdir()
    entry = project / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.symlink_to(target, target_is_directory=True)

    with pytest.raises(ApplicationError):
        _install_skill(project)


@pytest.mark.parametrize("relative", [".agents", ".agents/skills", ".agents/skills/fsq"])
def test_install_skill_rejects_non_directory_tree_entry(tmp_path: Path, relative: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    entry = project / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ApplicationError):
        _install_skill(project)


@pytest.mark.parametrize("managed_index", [0, 1])
@pytest.mark.parametrize("broken", [False, True])
def test_install_skill_rejects_symlinked_or_broken_managed_file(tmp_path: Path, managed_index: int, broken: bool) -> None:
    project = tmp_path / "project"
    project.mkdir()
    managed = _skill_files(project)[managed_index]
    managed.parent.mkdir(parents=True)
    target = tmp_path / "outside"
    if not broken:
        target.write_text("outside", encoding="utf-8")
    managed.symlink_to(target)

    with pytest.raises(ApplicationError):
        _install_skill(project)


def test_install_skill_validates_manifest_before_target_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.application import _coding_skill_install

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(_coding_skill_install, "_validate_skill_document", lambda _content: (_ for _ in ()).throw(ValueError("invalid manifest")))

    with pytest.raises(ApplicationError):
        _install_skill(project)

    assert not (project / ".agents").exists()


def test_install_skill_detects_concurrent_target_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.application import _coding_skill_install

    project = tmp_path / "project"
    project.mkdir()
    _install_skill(project)
    skill, _ = _skill_files(project)
    skill.write_text("old", encoding="utf-8")
    original = _coding_skill_install._verify_prepared_destination
    changed = False

    def race(item):
        nonlocal changed
        if not changed and item.path == skill:
            changed = True
            skill.write_text("concurrent", encoding="utf-8")
        return original(item)

    monkeypatch.setattr(_coding_skill_install, "_verify_prepared_destination", race)

    with pytest.raises(ApplicationError):
        _install_skill(project)

    assert skill.read_text(encoding="utf-8") == "concurrent"


def test_skill_installer_uses_only_public_cross_module_dependencies() -> None:
    source = ROOT / "fsq_agent" / "application" / "_coding_skill_install.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported_modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None]

    assert all(not any(part.startswith("_") for part in module.split(".")[1:]) for module in imported_modules if module.startswith("fsq_agent."))


def test_install_skill_does_not_publish_partial_file_when_temp_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.application import _coding_skill_install

    project = tmp_path / "project"
    project.mkdir()
    temporary_paths: list[Path] = []
    original_mkstemp = _coding_skill_install.tempfile.mkstemp

    def record_mkstemp(*args, **kwargs):
        descriptor, name = original_mkstemp(*args, **kwargs)
        temporary_paths.append(Path(name))
        return descriptor, name

    def fail_temp_fsync(_descriptor: int) -> None:
        raise OSError("simulated temporary write failure")

    monkeypatch.setattr(_coding_skill_install.tempfile, "mkstemp", record_mkstemp)
    monkeypatch.setattr(_coding_skill_install.os, "fsync", fail_temp_fsync)

    with pytest.raises(ApplicationError):
        _install_skill(project)

    skill, case = _skill_files(project)
    assert temporary_paths[0].parent == skill.parent
    assert temporary_paths[0].name.startswith(f".{skill.name}.")
    assert not temporary_paths[0].exists()
    assert not skill.exists()
    assert not case.exists()
    assert list(skill.parent.glob(f".{skill.name}.*")) == []


def test_install_skill_never_overwrites_destination_created_during_atomic_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.application import _coding_skill_install

    project = tmp_path / "project"
    project.mkdir()
    skill, case = _skill_files(project)
    original_link = _coding_skill_install.os.link
    raced = False

    def race(source, destination, *args, **kwargs):
        nonlocal raced
        destination_path = Path(destination)
        if not raced and destination_path == skill:
            raced = True
            destination_path.write_bytes(b"concurrent")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(_coding_skill_install.os, "link", race)

    with pytest.raises(ApplicationError):
        _install_skill(project)

    assert skill.read_bytes() == b"concurrent"
    assert not case.exists()
    assert list(skill.parent.glob(f".{skill.name}.*")) == []


def test_packaged_skill_resources_are_available_and_portable() -> None:
    root = ROOT / "fsq_agent" / "resources" / "skills" / "fsq"
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    packaged = files("fsq_agent").joinpath("resources", "skills", "fsq")

    assert skill.startswith(f"---\nname: fsq\ndescription: {SKILL_DESCRIPTION}\n")
    assert skill.count("\n---\n") == 1
    assert "{{" not in skill
    assert (root / "assets/getting-started.web.fsq.yaml").is_file()
    assert packaged.joinpath("SKILL.md").is_file()
    assert packaged.joinpath("assets", "getting-started.web.fsq.yaml").is_file()
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) == [
        "SKILL.md",
        "assets/getting-started.web.fsq.yaml",
    ]


def test_install_codex_agents_creates_missing_agents_md_without_path_rendering(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = tmp_path / "actual-workspace"
    project.mkdir()

    result = _install(project, workspace=workspace)

    assert [item.status for item in result.files] == ["created", "created", "created"]
    for filename, expected_name in (
        ("fsq_environment_setup.toml", "fsq_environment_setup"),
        ("fsq_test_runner.toml", "fsq_test_runner"),
    ):
        path = project / ".codex" / "agents" / filename
        source = ROOT / "fsq_agent" / "resources" / "codex" / "agents" / filename
        assert path.read_bytes() == source.read_bytes()
        definition = tomllib.loads(path.read_text(encoding="utf-8"))
        assert definition["name"] == expected_name
        assert definition["sandbox_mode"] == "workspace-write"
    workflow = (project / "AGENTS.md").read_text(encoding="utf-8")
    source_workflow = (ROOT / "fsq_agent/resources/codex/AGENTS.md").read_text(encoding="utf-8")
    assert workflow.count("<!-- BEGIN FSQ CODEX WORKFLOW -->") == 1
    assert workflow.count("<!-- END FSQ CODEX WORKFLOW -->") == 1
    assert source_workflow in workflow
    assert str(project.resolve()) not in workflow
    assert str(workspace.resolve()) not in workflow
    assert result.files[-1].backup_path is None


def test_install_codex_agents_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _install(project)
    unchanged = _install(project, workspace=project / "other-workspace")

    assert [item.status for item in unchanged.files] == ["unchanged", "unchanged", "skipped"]


def test_install_codex_agents_no_overwrite_skips_existing_targets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agents_dir = project / ".codex" / "agents"
    agents_dir.mkdir(parents=True)
    existing = agents_dir / "fsq_test_runner.toml"
    existing.write_text("keep", encoding="utf-8")

    result = _install(project, no_overwrite=True)

    statuses = {item.path.name: item.status for item in result.files}
    assert statuses["fsq_test_runner.toml"] == "skipped"
    assert existing.read_text(encoding="utf-8") == "keep"
    assert statuses["fsq_environment_setup.toml"] == "created"
    assert statuses["AGENTS.md"] == "created"


@pytest.mark.parametrize("entry_kind", ["file", "directory", "symlink", "broken_symlink"])
def test_install_codex_agents_skips_any_existing_agents_md_entry(tmp_path: Path, entry_kind: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agents_md = project / "AGENTS.md"
    if entry_kind == "file":
        agents_md.write_text("<!-- BEGIN FSQ CODEX WORKFLOW -->\nmalformed", encoding="utf-8")
    elif entry_kind == "directory":
        agents_md.mkdir()
    elif entry_kind == "symlink":
        target = project / "actual-agents.md"
        target.write_text("keep", encoding="utf-8")
        agents_md.symlink_to(target)
    else:
        agents_md.symlink_to(project / "missing-agents.md")

    result = _install(project)

    assert [item.status for item in result.files] == ["created", "created", "skipped"]
    assert agents_md.is_symlink() or agents_md.exists()
    assert result.files[-1].backup_path is None


def test_install_codex_agents_rejects_symlinked_target_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".codex").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ApplicationError) as error:
        _install(project)

    assert error.value.code.value == "configuration.invalid"
    assert list(outside.iterdir()) == []


def test_packaged_codex_resources_may_retain_placeholders() -> None:
    resources = ROOT / "fsq_agent/resources/codex"
    assert "{{FSQ_WORKSPACE}}" in (resources / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{FSQ_WORKSPACE}}" in (resources / "agents/fsq_environment_setup.toml").read_text(encoding="utf-8")
