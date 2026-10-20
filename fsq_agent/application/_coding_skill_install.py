# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CodingAgentInstallFileResult,
    CodingAgentInstallRequest,
    CodingAgentInstallResult,
)
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer
from fsq_agent.core import CapabilityDefinitionFactory, CapabilityRegistry, CommonPlatformTools

_MANIFEST = ("SKILL.md", "assets/getting-started.web.fsq.yaml")
_DESCRIPTION = "Guide FSQ Quick Test UI verification from a natural-language goal, with readiness discovery, evidence, and explicit Case creation or replay when requested."


@dataclass(frozen=True)
class _DestinationFact:
    exists: bool
    stat_key: tuple[int, int, int, int] | None = None
    content: bytes | None = None


@dataclass(frozen=True)
class _PreparedFile:
    path: Path
    content: bytes
    fact: _DestinationFact
    skipped: bool = False


def install_coding_skill(request: CodingAgentInstallRequest) -> CodingAgentInstallResult:
    try:
        project = _resolve_project(request.project_directory)
        workspace = request.workspace_root.expanduser().resolve(strict=False)
        resources = _load_and_validate_resources()
        skill_root = project / ".agents" / "skills" / "fsq"
        _validate_tree(project)
        prepared = _prepare_files(skill_root, resources, request.no_overwrite)
        _ensure_tree(project)
        results = tuple(_install_file(item) for item in prepared)
        return CodingAgentInstallResult(agent="skill", project_directory=project, workspace_root=workspace, files=results)
    except ApplicationError:
        raise
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.CONFIGURATION,
            message=str(exc).splitlines()[0] or "Coding Agent Skill installation failed.",
            action="Review the project Skill targets and retry.",
        ) from exc


def _resolve_project(path: Path) -> Path:
    project = path.expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Coding Agent project directory must be an existing directory.")
    return project


def _resource_root():
    root = files("fsq_agent").joinpath("resources", "skills", "fsq")
    if not root.is_dir():
        raise ValueError("Packaged Coding Agent Skill resources are unavailable.")
    return root


def _load_and_validate_resources() -> dict[str, bytes]:
    if len(set(_MANIFEST)) != len(_MANIFEST):
        raise ValueError("Coding Agent Skill manifest paths must be unique.")
    folded: set[str] = set()
    resources: dict[str, bytes] = {}
    root = _resource_root()
    for value in _MANIFEST:
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.as_posix() != value:
            raise ValueError("Coding Agent Skill manifest path is not contained.")
        casefolded = value.casefold()
        if casefolded in folded:
            raise ValueError("Coding Agent Skill manifest paths collide by case.")
        folded.add(casefolded)
        resource = root.joinpath(*relative.parts)
        if not resource.is_file():
            raise ValueError(f"Packaged Coding Agent Skill resource is unavailable: {value}")
        resources[value] = resource.read_bytes()
    _validate_skill_document(resources["SKILL.md"])
    _validate_case(resources["assets/getting-started.web.fsq.yaml"])
    return resources


def _validate_skill_document(content: bytes) -> None:
    text = content.decode("utf-8")
    if not text.startswith("---\n"):
        raise ValueError("Coding Agent Skill must begin with portable YAML frontmatter.")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("Coding Agent Skill frontmatter is not terminated.")
    frontmatter = yaml.safe_load(text[4:marker])
    if frontmatter != {"name": "fsq", "description": _DESCRIPTION}:
        raise ValueError("Coding Agent Skill frontmatter does not match the package manifest.")
    if not text[marker + 5 :].strip():
        raise ValueError("Coding Agent Skill instructions must not be empty.")


def _validate_case(content: bytes) -> None:
    text = content.decode("utf-8")
    case = FsqCaseLoader().load_text(text, Path("assets/getting-started.web.fsq.yaml"))
    if case.config.platform != "web" or case.config.name != "fsq-web-getting-started":
        raise ValueError("Coding Agent Skill Case identity is invalid.")
    definitions = [
        *CommonPlatformTools.capability_definitions(),
        *CapabilityDefinitionFactory().platform_definitions(platform="web", include_ai_assertion=True),
    ]
    serializer = FsqCaseSerializer(CapabilityRegistry.from_definitions(definitions).snapshot())
    if serializer.serialize(case) != content:
        raise ValueError("Coding Agent Skill Case is not canonical.")


def _validate_tree(project: Path) -> None:
    current = project
    for relative in (".agents", ".agents/skills", ".agents/skills/fsq"):
        current = project / relative
        if os.path.lexists(current):
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError(f"Refusing an unsafe Skill target directory: {current}")


def _destination_fact(path: Path) -> _DestinationFact:
    if not os.path.lexists(path):
        return _DestinationFact(exists=False)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Refusing an unsafe managed Skill file: {path}")
    content = path.read_bytes()
    return _DestinationFact(exists=True, stat_key=(info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size), content=content)


def _prepare_files(skill_root: Path, resources: dict[str, bytes], no_overwrite: bool) -> tuple[_PreparedFile, ...]:
    prepared: list[_PreparedFile] = []
    for relative in _MANIFEST:
        path = skill_root.joinpath(*PurePosixPath(relative).parts)
        fact = _destination_fact(path)
        prepared.append(_PreparedFile(path=path, content=resources[relative], fact=fact, skipped=no_overwrite and fact.exists))
    return tuple(prepared)


def _ensure_tree(project: Path) -> None:
    for path in (project / ".agents", project / ".agents" / "skills", project / ".agents" / "skills" / "fsq", project / ".agents" / "skills" / "fsq" / "assets"):
        try:
            path.mkdir()
        except FileExistsError:
            pass
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(f"Refusing an unsafe Skill target directory: {path}")


def _verify_prepared_destination(item: _PreparedFile) -> None:
    current = _destination_fact(item.path)
    if current != item.fact:
        raise ValueError(f"Managed Skill target changed during preparation: {item.path}")


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _publish_exclusive(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"Managed Skill target changed during preparation: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _install_file(item: _PreparedFile) -> CodingAgentInstallFileResult:
    _verify_prepared_destination(item)
    if item.skipped:
        return CodingAgentInstallFileResult(path=item.path, status="skipped")
    if item.fact.exists and item.fact.content == item.content:
        return CodingAgentInstallFileResult(path=item.path, status="unchanged")
    if not item.fact.exists:
        _publish_exclusive(item.path, item.content)
        return CodingAgentInstallFileResult(path=item.path, status="created")
    backup = item.path.with_name(f"{item.path.name}.bak.{uuid4().hex}")
    _write_exclusive(backup, item.fact.content or b"")
    try:
        _verify_prepared_destination(item)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{item.path.name}.", dir=item.path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(item.content)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_prepared_destination(item)
        temporary.replace(item.path)
    finally:
        temporary.unlink(missing_ok=True)
    return CodingAgentInstallFileResult(path=item.path, status="updated", backup_path=backup)
