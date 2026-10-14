# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==[^;\s]+(?:\s*;.*)?$")


def _load_workflow(name: str) -> tuple[str, dict[str, Any]]:
    raw = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    assert isinstance(workflow, dict)
    assert isinstance(workflow.get("jobs"), dict)
    for job_name, job in workflow["jobs"].items():
        assert isinstance(job, dict), job_name
        assert isinstance(job.get("steps"), list), job_name
        assert all(isinstance(step, dict) for step in job["steps"]), job_name
    return raw, workflow


def _workflow_step(workflow: dict[str, Any], job_name: str, step_name: str) -> dict[str, Any]:
    matches = [step for step in workflow["jobs"][job_name]["steps"] if step.get("name") == step_name]
    assert len(matches) == 1, (job_name, step_name)
    return matches[0]


def _delimited_python(script: str, opener: str, terminator: str) -> str:
    lines = script.splitlines()
    starts = [index for index, line in enumerate(lines) if opener in line]
    assert len(starts) == 1, opener
    start = starts[0]
    end = next((index for index in range(start + 1, len(lines)) if lines[index] == terminator), None)
    assert end is not None, terminator
    return "\n".join(lines[start + 1 : end]) + "\n"


def test_python_dependencies_are_lock_free_public_and_exactly_versioned() -> None:
    pyproject_path = ROOT / "pyproject.toml"
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    project = tomllib.loads(pyproject_text)

    ignored_paths = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "uv.lock" in ignored_paths
    assert "tool" not in project or "uv" not in project["tool"]
    assert "packagefeedproxy.microsoft.io" not in pyproject_text
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirement in project["build-system"]["requires"])
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirement in project["project"]["dependencies"])
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirements in project["project"]["optional-dependencies"].values() for requirement in requirements)


def test_agent_engine_distribution_does_not_require_agents_sdk() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = project["project"]
    requirements = [*metadata["dependencies"], *project["build-system"]["requires"]]
    for optional in metadata["optional-dependencies"].values():
        requirements.extend(optional)
    assert "openai-agents" not in {canonicalize_name(Requirement(requirement).name) for requirement in requirements}
    assert "openai==2.34.0" in metadata["dependencies"]
    assert "google-genai==2.21.0" in metadata["dependencies"]
    assert "httpx==0.28.1" in metadata["dependencies"]
    assert "OpenAI Agents SDK" not in metadata["description"]


def test_default_distribution_contract() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = project["project"]

    assert metadata["name"] == "fsq-agent"
    assert metadata["version"] == "0.1.9"
    version = Version(metadata["version"])
    assert version.local is None
    dependencies = metadata["dependencies"]
    for name in ("uiautomator2", "playwright", "pywinauto", "pillow", "Appium-Python-Client"):
        assert any(dependency.startswith(f"{name}==") for dependency in dependencies)
    assert set(metadata["optional-dependencies"]) == {"dev"}
    assert metadata["scripts"] == {
        "fsq": "fsq_agent.adapters.cli:main",
        "fsq-agent": "fsq_agent.adapters.cli:main",
    }
    assert metadata["license"] == "MIT"
    assert metadata["authors"] == [{"name": "Microsoft Corporation"}]
    assert metadata["urls"] == {
        "Documentation": "https://github.com/FSQ-lab/FSQ#readme",
        "Issues": "https://github.com/FSQ-lab/FSQ/issues",
        "Repository": "https://github.com/FSQ-lab/FSQ",
    }
    classifiers = set(metadata["classifiers"])
    assert "Operating System :: OS Independent" in classifiers
    assert "Intended Audience :: Developers" in classifiers
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers


def test_sdist_includes_public_release_documentation_and_example() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    includes = set(project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])

    assert {
        "/CHANGELOG.md",
        "/README.zh-CN.md",
        "/docs/assets/fsq-workflow.svg",
        "/docs/assets/social-preview.png",
        "/docs/assets/social-preview.svg",
        "/docs/case-format.md",
        "/docs/cli-reference.md",
        "/docs/getting-started.md",
        "/docs/getting-started.zh-CN.md",
        "/docs/media",
        "/docs/platform-prerequisites.md",
        "/docs/releases",
        "/docs/support-and-stability.md",
        "/examples",
    } <= includes


def test_release_workflow_is_manual_safe_and_uses_oidc_trusted_publishing() -> None:
    raw, workflow = _load_workflow("release.yml")
    trigger = workflow[True]  # PyYAML 1.1 parses the unquoted GitHub Actions `on` key as true.
    dispatch = trigger["workflow_dispatch"]
    assert set(trigger) == {"workflow_dispatch"}
    assert dispatch["inputs"]["publish"] == {
        "description": "Publish the verified distributions to PyPI",
        "required": True,
        "type": "boolean",
        "default": False,
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"verify", "install-smoke", "publish"}
    publish = workflow["jobs"]["publish"]
    assert publish["if"] == "${{ inputs.publish }}"
    assert publish["needs"] == ["verify", "install-smoke"]
    assert publish["environment"] == "pypi"
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert any(step.get("uses", "").startswith("actions/download-artifact@") for step in publish["steps"])
    assert publish["steps"][-1]["run"] == "uv publish --trusted-publishing always dist/*"
    install_smoke = workflow["jobs"]["install-smoke"]
    assert install_smoke["needs"] == "verify"
    assert set(install_smoke["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert any(step.get("uses", "").startswith("actions/download-artifact@") for step in install_smoke["steps"])
    install_smoke_commands = "\n".join(str(step.get("run", "")) for step in install_smoke["steps"])
    for command in (
        "python -m venv",
        'python -c "import fsq_agent"',
        "fsq",
        "fsq-agent",
    ):
        assert command in install_smoke_commands
    assert "load_platform_settings" in install_smoke_commands
    verify = workflow["jobs"]["verify"]
    assert any(step.get("uses", "").startswith("actions/upload-artifact@") for step in verify["steps"])
    commands = "\n".join(str(step.get("run", "")) for step in verify["steps"])
    for command in (
        "ruff check .",
        "ruff format --check .",
        "python -m pytest",
        "npm run typecheck",
        "npm test",
        "npm run build",
        "uv build",
        "twine check dist/*",
        "tests/test_distribution_contract.py",
        '"$RUNNER_TEMP/fsq-release-smoke/bin/fsq" --help',
        '"$RUNNER_TEMP/fsq-release-smoke/bin/fsq-agent" --help',
        "load_platform_settings",
    ):
        assert command in commands
    assert "uv sync --extra dev --reinstall-package fsq-agent" in commands
    assert "--frozen" not in commands
    assert "--locked" not in commands
    assert "uv run --no-sync ruff check ." in commands
    assert "uv run --no-sync ruff format --check ." in commands
    assert "uv run --no-sync python -m pytest" in commands
    assert "PYPI_API_TOKEN" not in raw
    assert "password:" not in raw


def test_workflow_embedded_python_is_syntactically_valid() -> None:
    _, ci = _load_workflow("ci.yml")
    for job_name, step_name in (
        ("quality", "Verify clean checkout package resource ownership"),
        ("tests", "Verify clean checkout package resource ownership"),
        ("package", "Verify distribution contracts"),
    ):
        step = _workflow_step(ci, job_name, step_name)
        assert step["shell"] == "python"
        compile(step["run"], f"ci.yml:{job_name}:{step_name}", "exec")

    _, release = _load_workflow("release.yml")
    verify_script = _workflow_step(release, "verify", "Install wheel in a clean environment")["run"]
    compile(_delimited_python(verify_script, "<<'PY'", "PY"), "release.yml:verify:installed-wheel", "exec")
    matrix_script = _workflow_step(release, "install-smoke", "Install wheel and verify console scripts")["run"]
    compile(_delimited_python(matrix_script, "$resourceSmoke = @'", "'@"), "release.yml:install-smoke:resources", "exec")


def test_release_install_smoke_checks_every_native_command_exit() -> None:
    _, release = _load_workflow("release.yml")
    script = _workflow_step(release, "install-smoke", "Install wheel and verify console scripts")["run"]
    lines = script.splitlines()

    for command in (
        "python -m venv $venv",
        "& $python -m pip install --upgrade pip",
        "& $python -m pip install $wheels[0].FullName",
        '& $python -c "import fsq_agent"',
        "& $fsq --help",
        "& $fsqAgent --help",
        "$resourceSmoke | & $python -",
    ):
        command_index = lines.index(command)
        assert lines[command_index + 1] == "if ($LASTEXITCODE -ne 0) {"
        assert lines[command_index + 2].strip().startswith('throw "')
        assert lines[command_index + 3] == "}"


def test_distribution_includes_only_control_plane_frontend_assets() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = "fsq_agent/adapters/control_plane/static/**"
    retired = "fsq_agent/adapters/control_plane/playground/static/**"

    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert set(wheel["artifacts"]) == {expected}
    assert retired not in wheel["artifacts"]
    assert "force-include" not in wheel
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert set(sdist["artifacts"]) == {expected}
    assert retired not in sdist["artifacts"]
    assert "force-include" not in sdist


def test_frontend_distribution_script_does_not_mutate_python_resources() -> None:
    script = (ROOT / "scripts" / "distribute-frontend-build.mjs").read_text(encoding="utf-8")

    assert "fsq_agent/resources" not in script
    assert "knowledge/skills" not in script


def test_sdist_uses_tracked_package_runtime_resources() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    includes = set(sdist["include"])

    assert "/fsq_agent" in includes
    assert not {"/config.android.yaml", "/config.web.yaml", "/config.windows.yaml", "/config.macos.yaml", "/knowledge/skills"} & includes
    for path in (
        ROOT / "fsq_agent/config/config.android.yaml",
        ROOT / "fsq_agent/config/config.web.yaml",
        ROOT / "fsq_agent/config/config.windows.yaml",
        ROOT / "fsq_agent/config/config.macos.yaml",
        ROOT / "fsq_agent/config/config.example.yaml",
        ROOT / "fsq_agent/resources/skills/android-harness.md",
        ROOT / "fsq_agent/resources/skills/web-harness.md",
        ROOT / "fsq_agent/resources/skills/windows-harness.md",
        ROOT / "fsq_agent/resources/skills/macos-harness.md",
        ROOT / "fsq_agent/resources/skills/automation-basics.md",
        ROOT / "fsq_agent/resources/skills/fsq/SKILL.md",
        ROOT / "fsq_agent/resources/skills/fsq/assets/getting-started.web.fsq.yaml",
    ):
        assert path.is_file()


def test_canonical_skill_resources_are_checked_out_with_lf() -> None:
    resources = (
        "fsq_agent/resources/skills/fsq/SKILL.md",
        "fsq_agent/resources/skills/fsq/assets/getting-started.web.fsq.yaml",
    )
    result = subprocess.run(  # noqa: S603 - Fixed Git query against repository-owned paths.
        ["git", "check-attr", "-z", "text", "eol", "--", *resources],  # noqa: S607 - Git is provided by CI.
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    fields = result.stdout.decode("utf-8").split("\0")
    assert fields.pop() == ""
    attributes = {(path, attribute): value for path, attribute, value in zip(fields[::3], fields[1::3], fields[2::3], strict=True)}

    for resource in resources:
        assert attributes[(resource, "text")] == "set"
        assert attributes[(resource, "eol")] == "lf"


def test_concise_readmes_cover_the_same_install_and_ui_contract() -> None:
    readmes = {
        "en": (ROOT / "README.md").read_text(encoding="utf-8"),
        "zh": (ROOT / "README.zh-CN.md").read_text(encoding="utf-8"),
    }

    for language, readme in readmes.items():
        assert len(readme.splitlines()) <= 105
        for required in (
            "https://github.com/FSQ-lab/FSQ",
            "python -m pip install --upgrade fsq-agent",
            "fsq ui",
            "docs/media/01-describe-goal.png",
            "aa9d0a12-2f93-4894-8349-52a013424939",
            "QqCahxGDdS0",
        ):
            assert required in readme
        assert readme.index("FSQ Lab") < readme.index("##")
        documentation_heading = {"en": "## Documentation", "zh": "## 文档"}[language]
        assert readme.index("> [!IMPORTANT]") < readme.index(documentation_heading)


def test_bilingual_readmes_present_two_complete_quick_start_paths() -> None:
    contracts = {
        "en": {
            "path": ROOT / "README.md",
            "quick_start": "## Quick start",
            "agent": "### 1. Coding Agent",
            "ui": "### 2. FSQ UI",
            "reload": "reload or start a new Coding Agent session",
            "prompt": "> Guide me through one FSQ test.",
            "workspace": "Workspace",
            "platform": "platform",
            "target": "exact target",
            "credentials": "credentials",
            "replay": "separately approving any replay",
        },
        "zh": {
            "path": ROOT / "README.zh-CN.md",
            "quick_start": "## 快速开始",
            "agent": "### 1. Coding Agent",
            "ui": "### 2. FSQ UI",
            "reload": "重新加载 Coding Agent 或启动新 session",
            "prompt": "> 引导我完成一个 FSQ 测试。",
            "workspace": "Workspace",
            "platform": "平台",
            "target": "准确目标",
            "credentials": "凭据",
            "replay": "另行决定是否批准重放",
        },
    }

    for contract in contracts.values():
        readme = contract["path"].read_text(encoding="utf-8")
        quick_start = readme[readme.index(contract["quick_start"]) :]
        agent_section = quick_start[quick_start.index(contract["agent"]) : quick_start.index(contract["ui"])]
        ui_section = quick_start[quick_start.index(contract["ui"]) :]

        assert agent_section.index("https://github.com/FSQ-lab/FSQ") < agent_section.index("fsq init --agent skill")
        assert agent_section.index("fsq init --agent skill") < agent_section.index(contract["reload"]) < agent_section.index(contract["prompt"])
        assert all(value in agent_section for value in (contract["workspace"], contract["platform"], contract["target"], contract["credentials"]))
        assert contract["replay"] in quick_start
        assert all(value in ui_section for value in ("python -m pip install --upgrade fsq-agent", "fsq ui", "Settings", contract["workspace"], contract["platform"]))


def test_bilingual_getting_started_guides_follow_the_guided_skill_protocol() -> None:
    contracts = {
        "en": {
            "path": ROOT / "docs" / "getting-started.md",
            "reload": "Reload or start a new Coding Agent session",
            "guided": "## Guided AI Explore",
            "scope": "platform and exact current target",
            "workspace": "exact registered Workspace status and root",
            "goal": "Goal and observable success criteria",
            "permissions": "permissions",
            "provider": "Provider handoff",
            "result": "result first",
            "returned": "exact returned Case",
            "static": "fsq case format",
            "confirmation": "separate explicit confirmation",
            "fallback": "Provider-free Web troubleshooting fallback",
        },
        "zh": {
            "path": ROOT / "docs" / "getting-started.zh-CN.md",
            "reload": "重新加载或启动新的 Coding Agent session",
            "guided": "## 引导式 AI Explore",
            "scope": "平台和准确的当前目标",
            "workspace": "准确注册的 Workspace 状态与 root",
            "goal": "Goal 和可观察的成功标准",
            "permissions": "权限",
            "provider": "Provider 交接",
            "result": "先总结结果",
            "returned": "准确返回的 Case",
            "static": "fsq case format",
            "confirmation": "另行明确确认",
            "fallback": "无需 Provider 的 Web 故障排查后备流程",
        },
    }

    for contract in contracts.values():
        guide = " ".join(contract["path"].read_text(encoding="utf-8").split())
        install_markers = (
            "python -m pip install --upgrade fsq-agent",
            "fsq init --agent skill",
            contract["reload"],
        )
        install_positions = [guide.index(marker) for marker in install_markers]
        assert install_positions == sorted(install_positions)

        protocol = guide[guide.index(contract["guided"]) :]
        scope_markers = (
            contract["scope"],
            contract["workspace"],
            contract["goal"],
            contract["permissions"],
        )
        scope_positions = [protocol.index(marker) for marker in scope_markers]
        ordered_markers = (
            contract["provider"],
            "case create",
            contract["result"],
            contract["returned"],
            contract["static"],
            contract["confirmation"],
            "case test",
            contract["fallback"],
        )
        positions = [protocol.index(marker) for marker in ordered_markers]
        assert positions == sorted(positions)
        assert max(scope_positions) < positions[0]
        assert "assets/getting-started.web.fsq.yaml" in protocol[positions[-1] :]

        for obsolete_claim in (
            "installed Skill cannot",
            "Skill cannot configure",
            "cannot run Explore",
            "relative Control Plane route",
            "安装后的 Skill 不可以",
            "Skill 也不能配置",
            "运行 Explore/suggestion",
            "相对 Control Plane 路由",
        ):
            assert obsolete_claim not in guide


def test_changelog_keeps_released_skill_behavior_historical() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog[changelog.index("## [Unreleased]") : changelog.index("## [0.1.6]")]
    release_015 = changelog[changelog.index("## [0.1.5]") : changelog.index("## [0.1.0]")]

    assert "platform-neutral AI testing workflow" in unreleased
    assert ("Package-owned, host-neutral FSQ Agent Skill onboarding for a provider-free Web Strict self-check and exact Run handoff to the local Control Plane.") in release_015
    assert "platform-neutral guided AI testing workflow" not in release_015


def test_ci_verifies_all_runtime_package_resources() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for resource in (
        "fsq_agent/config/config.android.yaml",
        "fsq_agent/config/config.web.yaml",
        "fsq_agent/config/config.windows.yaml",
        "fsq_agent/config/config.macos.yaml",
        "fsq_agent/config/config.example.yaml",
        "fsq_agent/resources/skills/android-harness.md",
        "fsq_agent/resources/skills/web-harness.md",
        "fsq_agent/resources/skills/windows-harness.md",
        "fsq_agent/resources/skills/macos-harness.md",
        "fsq_agent/resources/skills/automation-basics.md",
        "fsq_agent/agent/templates/agent_instructions.j2",
        "fsq_agent/agent/templates/task_input.j2",
    ):
        assert resource in workflow

    for contract in (
        "Verify clean checkout package resource ownership",
        "uv.lock must not be tracked",
        "uv build --wheel dist/*.tar.gz --out-dir rebuilt-dist",
        "Wheel rebuilt from sdist has different runtime package resources",
        "Sdist contains retired root resource",
    ):
        assert contract in workflow
    assert "uv sync --extra dev --reinstall-package fsq-agent" in workflow
    assert "uv sync --all-extras --reinstall-package fsq-agent" in workflow
    assert "cache-dependency-glob: pyproject.toml" in workflow
    assert "cache-dependency-glob: uv.lock" not in workflow
    assert "--frozen" not in workflow
    assert "--locked" not in workflow
    assert "uv run --no-sync ruff check ." in workflow
    assert "uv run --no-sync ruff format --check ." in workflow
    assert "uv run --no-sync python -m pytest" in workflow


def test_readme_uses_current_installation_and_cli_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    headline = "Describe what to test. Let AI help to test the user flow."
    assert readme.index(headline) < readme.index("FSQ Lab")
    assert "python -m pip install --upgrade fsq-agent" in readme
    assert "## Quick start" in readme
    assert "### 1. Coding Agent" in readme
    assert "### 2. FSQ UI" in readme
    assert "fsq init --agent skill" in readme
    assert "configure the FSQ LLM Provider and model in **Settings**" in readme
    assert "never ask me to put credentials in chat" in readme
    assert "Workspace, platform, and exact target" in readme
    assert "separately and explicitly approve replay" in readme
    assert "README.zh-CN.md" in readme
    assert "docs/getting-started.zh-CN.md" in readme
    assert "![FSQ Test Runner for describing a test goal](docs/media/01-describe-goal.png)" in readme
    assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in readme
    assert "https://youtu.be/QqCahxGDdS0" in readme
    assert "Autoplay demo page" not in readme
    assert "English captions" not in readme
    assert "docs/demo.html" not in readme
    assert "Bilibili" not in readme
    assert "fsq ui" in readme
    for obsolete in (
        "fsq-agent run",
        "run --strict",
        "fsq-agent control-plane",
        "fsq-agent report",
        "fsq-agent playground",
        "init --name",
        "fsq-agent[web]",
        "fsq-agent[android]",
        "fsq-agent[windows]",
        "fsq-agent[macos]",
    ):
        assert obsolete not in readme


def test_chinese_readme_uses_current_installation_and_cli_contract() -> None:
    readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    headline = "描述你要测试什么\uff0c让 AI 帮你测试用户流程。"
    assert readme.index(headline) < readme.index("FSQ Lab")
    assert "python -m pip install --upgrade fsq-agent" in readme
    assert "## 快速开始" in readme
    assert "### 1. Coding Agent" in readme
    assert "### 2. FSQ UI" in readme
    assert "fsq init --agent skill" in readme
    assert "在 **Settings** 中配置 FSQ 使用的 LLM Provider 和模型" in readme
    assert "不要要求我在聊天中提供凭据" in readme
    assert "Workspace、平台和准确目标" in readme
    assert "另行明确批准重放" in readme
    assert "![用于描述测试目标的 FSQ Test Runner](docs/media/01-describe-goal.png)" in readme
    assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in readme
    assert "https://youtu.be/QqCahxGDdS0" in readme
    assert "自动播放演示页" not in readme
    assert "中文字幕" not in readme
    assert "Bilibili" not in readme
    assert "fsq ui" in readme
    for obsolete in (
        "fsq-agent run",
        "run --strict",
        "fsq-agent control-plane",
        "fsq-agent report",
        "fsq-agent playground",
        "init --name",
        "fsq-agent[web]",
        "fsq-agent[android]",
        "fsq-agent[windows]",
        "fsq-agent[macos]",
    ):
        assert obsolete not in readme


def test_release_demo_gif_is_lightweight_and_committed() -> None:
    demo_gif = ROOT / "docs" / "media" / "fsq-v0.1.0-android-demo-preview.gif"

    assert demo_gif.is_file()
    assert demo_gif.stat().st_size < 1_000_000


def test_v010_release_materials_cover_public_launch_contract() -> None:
    notes = (ROOT / "docs" / "releases" / "v0.1.0.md").read_text(encoding="utf-8")
    release_copy = (ROOT / "docs" / "releases" / "v0.1.0-github-release.md").read_text(encoding="utf-8")

    for text in (notes, release_copy):
        assert "python -m pip install fsq-agent" in text
        assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in text
        assert "https://youtu.be/QqCahxGDdS0" in text
        assert "alpha" in text.lower()
        assert "Playwright" in text
        assert "uiautomator2" in text
        assert "pywinauto" in text
        assert "Appium" in text
        assert "token" in text.lower()
        assert "API key" in text
        assert "/Users/" not in text
        assert "production ready" not in text.lower()
