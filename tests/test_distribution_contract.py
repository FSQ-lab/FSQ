# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
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
    assert metadata["version"] == "0.3.1"
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


def _markdown_section(document: str, heading: str) -> str:
    start = re.search(rf"(?m)^{re.escape(heading)}$", document)
    assert start is not None, heading
    level = len(heading.split(" ", 1)[0])
    rest = document[start.end() :]
    end = re.search(rf"(?m)^#{{1,{level}}} ", rest)
    return rest[: end.start()] if end else rest


def test_readmes_share_current_media_and_resolvable_local_destinations() -> None:
    current_video = "https://github.com/user-attachments/assets/3acfecf4-bf48-4c76-8245-2eeef0dedb1f"
    current_youtube = "https://youtu.be/f1sobvVHNg0"
    expected_images = {f"docs/media/quick-test-{name}.png" for name in ("task", "execution", "result")}
    for name in ("README.md", "README.zh-CN.md"):
        path = ROOT / name
        readme = path.read_text(encoding="utf-8")
        assert current_video in readme
        assert current_youtube in readme
        demo_heading = "## See a complete test" if name == "README.md" else "## 看一次完整测试"
        demo = _markdown_section(readme, demo_heading)
        assert demo.splitlines().count(current_video) == 1
        assert re.search(rf"(?<!!)\[[^\]\n]+\]\({re.escape(current_youtube)}\)", demo)
        assert "![" not in demo
        assert "<img" not in demo.lower()
        assert "aa9d0a12-2f93-4894-8349-52a013424939" not in readme
        assert "QqCahxGDdS0" not in readme
        images = re.findall(r"!\[([^\]]+)\]\((docs/media/[^)]+)\)", readme)
        assert {target for _, target in images} == expected_images
        for alt, target in images:
            assert alt.strip()
            assert (ROOT / target).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert "docs/media/quick-test.en.srt" in readme
        assert "docs/media/quick-test-demo-provenance.md" in readme
        assert "https://fsq-lab.github.io/FSQ/" in readme
        assert "Web" in readme
        assert "Android" in readme
        assert readme.index("FSQ Lab") < readme.index("##")
        assert "> [!IMPORTANT]" in readme

        # Check actual linked files and heading fragments, not just expected strings.
        targets = re.findall(r'\]\(([^)\s]+)\)|(?:href|src|srcset)="([^"]+)"', readme)
        assert targets
        for markdown_target, html_target in targets:
            target = markdown_target or html_target
            if target.startswith(("https://", "http://")):
                continue
            local, _, fragment = target.partition("#")
            destination = path.parent / local if local else path
            assert destination.is_file(), target
            if fragment:
                headings = re.findall(r"(?m)^#{1,6} (.+)$", destination.read_text(encoding="utf-8"))
                anchors = {re.sub(r"[^\w\s-]", "", heading).lower().replace(" ", "-") for heading in headings}
                assert fragment in anchors, target

    srt = (ROOT / "docs/media/quick-test.en.srt").read_text(encoding="utf-8")
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> ", srt)
    expected_vtt = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
    assert (ROOT / "docs/media/quick-test.en.vtt").read_text(encoding="utf-8") == expected_vtt


def test_bilingual_readmes_present_two_complete_quick_start_paths() -> None:
    contracts = (
        ("README.md", "## Quick start", "reload or start a new Coding Agent session", "one approval", "does not initialize a Workspace, configure a Provider, or run a test"),
        ("README.zh-CN.md", "## 快速开始", "重新加载 Coding Agent 或启动新 session", "一次批准请求", "不初始化 Workspace、不配置 Provider、不运行测试"),
    )
    for name, heading, reload, approval, setup_boundary in contracts:
        readme = (ROOT / name).read_text(encoding="utf-8")
        quick_start = _markdown_section(readme, heading)
        assert not re.search(r"(?m)^### \d+[.)]\s*(?:Coding Agent|FSQ UI)", quick_start)
        sections = {title: _markdown_section(quick_start, f"### {title}") for title in ("Coding Agent", "FSQ UI")}
        for title, section in sections.items():
            details = re.findall(r"<details>\s*<summary>[^<]+</summary>(.*?)</details>", section, re.DOTALL)
            assert len(details) == 1
            assert section[: section.index("<details>")].strip(), "Keep a concise entry before setup details"
            assert "Quick Test" in section
            anchor = "coding-agent" if title == "Coding Agent" else "fsq-ui"
            assert re.search(rf"\]\(docs/getting-started(?:\.zh-CN)?\.md#{anchor}\)", section)
        agent = sections["Coding Agent"]
        assert agent.index("https://github.com/FSQ-lab/FSQ") < agent.index("fsq init --agent skill") < agent.index(reload)
        assert approval in agent
        assert setup_boundary in agent
        assert "Skill" in agent
        assert "Web" in agent
        ui = sections["FSQ UI"]
        ui_details = re.search(r"<details>(.*?)</details>", ui, re.DOTALL)
        assert ui_details is not None
        assert "python -m pip install --upgrade fsq-agent" in ui_details[1]
        assert "fsq ui" in ui_details[1]
        assert all(marker in ui_details[1] for marker in ("Settings", "Provider", "Web", "Start Quick Test", "Open report"))

        goals = re.findall(r"```text\n(.*?)\n```", quick_start, re.DOTALL)
        assert len(goals) == 1
        goal = goals[0]
        assert goal.index("FSQ-lab/FSQ") < goal.index("About") < goal.index("Describe a goal.") < goal.index("Test the real app.")
        assert "[describe the outcome]" not in goal
        assert "[描述期望结果]" not in goal
        if name == "README.md":
            assert all(
                marker in goal
                for marker in ("search", "selected browser", "website link", "captured page evidence", "Only browse public pages", "Do not sign in", "submit forms", "Stop and report a blocker")
            )
        else:
            assert all(marker in goal for marker in ("搜索", "已选择的浏览器", "官网链接", "页面证据", "只浏览公开页面", "不登录", "不提交表单", "停止并报告阻碍"))
        guide_name = "getting-started.zh-CN.md" if "zh-CN" in name else "getting-started.md"
        guide = (ROOT / "docs" / guide_name).read_text(encoding="utf-8")
        assert goal in guide, "README and detailed guide must share the complete first goal"


def test_bilingual_getting_started_guides_follow_the_guided_skill_protocol() -> None:
    contracts = (
        (
            "getting-started.md",
            "## Review results before choosing advanced workflows",
            "## Provider-free Web troubleshooting fallback",
            "No registered project Workspace, manual project initialization, or project Doctor is required",
            "authorizes one normal Quick Test without another FSQ-layer run confirmation",
            "never automatically resubmits",
            "Only your explicit confirmation after that disclosure",
            "not the primary experience or a prerequisite",
        ),
        (
            "getting-started.zh-CN.md",
            "## 检查结果\uff0c再选择进阶操作",
            "## 无需 Provider 的 Web 故障排查后备流程",
            "无需注册或初始化项目 Workspace\uff0c也无需先运行项目 Doctor",
            "授权一次正常 Quick Test\uff0c无需另一层 FSQ 执行确认",
            "不会自动重提",
            "你明确确认后才执行一次 Strict Replay",
            "不是主要体验\uff0c也不是用户 Goal 的前置步骤",
        ),
    )
    for name, review_heading, fallback_heading, no_project_setup, one_request, no_retry, replay_confirmation, fallback_boundary in contracts:
        guide = (ROOT / "docs" / name).read_text(encoding="utf-8")
        first_use = guide[: guide.index(review_heading)]
        assert "~/.fsq/quick-test/" in first_use
        assert no_project_setup in first_use
        assert "<project-root>/fsq_workspace" not in first_use
        agent = _markdown_section(guide, "## Coding Agent")
        assert agent.index("fsq init --agent skill") < agent.index(".agents/skills/fsq/")
        assert agent.index("quick-test context") < agent.index("quick-test run")
        assert one_request in agent
        assert no_retry in agent
        assert all(marker in agent for marker in ("JSONL", "Settings", "Provider"))
        assert "case create" not in agent
        assert "case test" not in agent
        if name == "getting-started.md":
            assert all(
                marker in agent
                for marker in (
                    "Never put keys, tokens, or secrets in chat",
                    "failure, timeout, interruption, lost output, or malformed output",
                    "authoritative execution status",
                    "whole-goal verification",
                )
            )
        else:
            assert all(marker in agent for marker in ("不要将 Key、token 或 secret 放进聊天", "失败、超时、中断、输出丢失或格式错误", "权威执行状态", "整体验证结论"))
        ui = _markdown_section(guide, "## FSQ UI")
        assert all(marker in ui for marker in ("python -m pip install --upgrade fsq-agent", "fsq ui", "Quick Test", "Settings", "Start Quick Test", "Open report"))
        advanced = _markdown_section(guide, review_heading)
        assert "FSQ Case Manager" in advanced
        assert "Doctor" in advanced
        assert replay_confirmation in advanced
        fallback = " ".join(_markdown_section(guide, fallback_heading).split())
        assert "assets/getting-started.web.fsq.yaml" in fallback
        assert fallback_boundary in fallback
        assert guide.index(review_heading) < guide.index(fallback_heading) < guide.index("case create")


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


@pytest.mark.parametrize(
    ("name", "headline", "quick_heading", "advanced_heading", "feedback_heading", "safety_markers"),
    [
        (
            "README.md",
            "Describe a goal. Test the real app.",
            "## Quick start",
            "## Go further when you need to",
            "## Help shape FSQ",
            (
                "supported configured Provider and explicitly selected model",
                "never paste them into Coding Agent chat",
                "no manual project Workspace setup",
                "does not save a formal Case",
                "does not perform Strict Replay",
                "Evidence is stored locally",
                "may contain private app data",
                "review it before sharing",
                "alpha",
            ),
        ),
        (
            "README.zh-CN.md",
            "描述测试目标\uff0c让 AI 测试真实应用。",
            "## 快速开始",
            "## 需要时再进阶",
            "## 一起完善 FSQ",
            (
                "已配置的受支持 Provider 与明确选择的模型",
                "不要发送到 Coding Agent 聊天",
                "无需手动初始化项目 Workspace",
                "不保存正式 Case",
                "不执行 Strict Replay",
                "证据保存在本地",
                "可能包含私有应用数据",
                "审查",
                "Alpha",
            ),
        ),
    ],
    ids=("english", "chinese"),
)
def test_readme_uses_current_installation_and_cli_contract(name: str, headline: str, quick_heading: str, advanced_heading: str, feedback_heading: str, safety_markers: tuple[str, ...]) -> None:
    readme = (ROOT / name).read_text(encoding="utf-8")
    assert readme.index(headline) < readme.index("FSQ Lab")
    assert "Python 3.11+" in _markdown_section(readme, quick_heading)
    assert all(marker in readme for marker in safety_markers)
    advanced = _markdown_section(readme, advanced_heading)
    assert "FSQ Case Manager" in advanced
    assert "Strict Replay" in advanced
    if name == "README.md":
        assert "A run may produce a Case" in advanced
        assert "separately request Strict Replay" in advanced
        assert "Quick Test never runs it automatically" in advanced
    else:
        assert "Run 可能产生 Case" in advanced
        assert "单独请求 Strict Replay" in advanced
        assert "Quick Test 不会自动重放" in advanced
    assert readme.index(quick_heading) < readme.index(advanced_heading) < readme.index(feedback_heading)
    assert "https://github.com/FSQ-lab/FSQ/issues/new/choose" in _markdown_section(readme, feedback_heading)
    for link in (
        "README.md" if "zh-CN" in name else "README.zh-CN.md",
        "docs/getting-started.zh-CN.md",
        "docs/platform-prerequisites.md",
        "docs/support-and-stability.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "LICENSE",
    ):
        assert re.search(rf"(?:\]\(|href=\"){re.escape(link)}(?:\)|\")", readme)
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
