# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from typing import Any

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.core import EvidenceRecorder, StepRunner, StepSequenceRunner
from fsq_agent.models import (
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    StepPhase,
)

FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Manifest Smoke Case
platform: android
appId: com.microsoft.emmx
---
- launchApp
- tapOn:
    target: Search box
    locator:
      resourceId: com.microsoft.emmx:id/search_box_text
- killApp
"""


class SmokeHarness:
    def get_context(self) -> HarnessContext:
        return HarnessContext(platform="android", session_id="session-1")

    def action_space(self) -> dict[str, Any]:
        return {}

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        return None

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        return HarnessActionResult(status="passed", action_name=step.action_name)

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        return None

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{kind}",
            kind=kind,
            path=Path(f"artifacts/{kind}/{step_id}-{phase}-{reason}.{kind}"),
        )

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "unknown"


def test_fsq_steps_sequence_runner_and_recorder_write_evidence_manifest(tmp_path: Path) -> None:
    case_path = tmp_path / "manifest_smoke.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry()
    steps = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot()).to_executable_steps(case)
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path / "run-1")
    bundle = StepSequenceRunner(
        step_runner=StepRunner(
            harness=SmokeHarness(),
            capability_registry=registry,
        ),
        evidence_recorder=recorder,
    ).run_steps(
        run_id="run-1",
        steps=steps,
    )

    manifest_path = recorder.write_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert bundle.run_id == "run-1"
    assert manifest_path == tmp_path / "run-1" / "evidence-manifest.json"
    assert manifest["run_id"] == "run-1"
    assert [step["step_id"] for step in manifest["steps"]] == [
        "manifest_smoke-step-001",
        "manifest_smoke-step-002",
        "manifest_smoke-step-003",
    ]
    assert manifest["steps"][1]["source_ref"] == {
        "source_type": "fsq",
        "source_id": str(case_path),
        "step_index": 1,
        "metadata": {"case_name": "Manifest Smoke Case", "platform": "android"},
    }
    assert [event["event_type"] for event in manifest["events"]].count("artifact_captured") == 3
    artifact_reasons = [event["payload"]["reason"] for event in manifest["events"] if event["event_type"] == "artifact_captured"]
    assert artifact_reasons.count("before-action") == 1
    assert artifact_reasons.count("after-action") == 2
    assert [artifact["kind"] for artifact in manifest["artifacts"]] == ["screenshot"] * 3
    assert manifest["artifacts"][0]["path"] == "artifacts/screenshot/manifest_smoke-step-001-finalize-after-action.screenshot"
    assert manifest["artifacts"][1]["path"] == "artifacts/screenshot/manifest_smoke-step-002-finalize-after-action.screenshot"
    assert manifest["artifacts"][2]["path"] == "artifacts/screenshot/manifest_smoke-step-003-prepare-before-action.screenshot"
