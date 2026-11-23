# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from fsq_agent.config import Settings
from fsq_agent.core.evidence import ArtifactStore, EvidenceRecorder
from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run, load_run_metadata
from fsq_agent.models import EvidenceArtifactRef, EvidenceBundle, ExecutableStep, RunnerEvent, RunnerStepResult, StepPhaseReport, VerificationResult
from fsq_agent.report import RunReportService


def _run(root: Path):
    metadata = allocate_run(workspace=root, workspace_name="demo", platform="web", source_id="search", mode="strict", source=RunSource(kind="case", case_id="search"))
    return root / ".fsq/runs/web" / metadata.run_id, metadata


def test_freeze_precedes_reporting_and_processing_cannot_flip_result(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    bundle = EvidenceBundle(bundle_id="evidence", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status="passed")])
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=bundle)
    before = (run_dir / "execution-result.json").read_bytes()
    result = service.finalize(run_dir, metadata, execution_result=frozen, processing={"report": {"status": "failed", "message": "Renderer unavailable"}})
    assert result.status == "success"
    assert result.result.steps.total == result.result.steps.passed == 1
    assert (run_dir / "execution-result.json").read_bytes() == before
    assert result.processing["report"]["status"] == "failed"
    with pytest.raises(ValueError, match="immutable"):
        service.freeze(run_dir, result, bundle=bundle, status="failed")


def test_failure_counts_and_first_failure_are_frozen(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    bundle = EvidenceBundle(
        bundle_id="e",
        run_id=metadata.run_id,
        steps=[RunnerStepResult(step_id="one", status="passed"), RunnerStepResult(step_id="two", status="failed", failure_category="assertion_error", error_message="Expected heading")],
    )
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=bundle)
    result = service.finalize(run_dir, metadata, execution_result=frozen)
    assert result.result.steps.model_dump()["total"] == 2
    assert result.result.steps.failed == 1
    assert result.result.failed_step == "two"
    assert result.status == "failed"


def test_empty_execution_is_inconclusive_and_unknown_owner_is_not_dead(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id))
    assert frozen.outcome == "inconclusive"
    (run_dir / "owner.json").unlink(missing_ok=True)
    assert service.inspect_owner(run_dir)["status"] == "unknown"


def test_provenance_never_persists_or_hashes_secret_value(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    source = tmp_path / "input.fsq.yaml"
    source.write_text("name: demo\npassword: hidden-test-value\n", encoding="utf-8")
    service = RunLifecycleService()
    service.snapshot_sources(run_dir, metadata, sources={"case": source}, secret_values=("hidden-test-value",))
    persisted = load_run_metadata(run_dir)
    assert "hidden-test-value" not in json.dumps(persisted.model_dump(mode="json"))
    snapshot = run_dir / persisted.provenance["sources"][0]["path"]
    assert "hidden-test-value" not in snapshot.read_text(encoding="utf-8")


@pytest.mark.parametrize(("effort", "verification_enabled"), [("low", False), ("high", True)])
def test_provenance_captures_neutral_runtime_policy_without_credentials(tmp_path, effort, verification_enabled):
    run_dir, metadata = _run(tmp_path)
    settings = Settings()
    settings.harness.platform = "web"
    settings.agent_runtime.reasoning_effort = effort
    settings.agent_runtime.verification_enabled = verification_enabled
    settings.agent_runtime.api_key = "not-for-provenance"
    configuration = RunLifecycleService.safe_configuration(settings)
    updated = RunLifecycleService.snapshot_sources(run_dir, metadata, sources={"goal": "Inspect"}, configuration=configuration)
    policy = updated.provenance["configuration"]["agent_runtime"]
    assert policy == {"reasoning_effort": effort, "max_turns": settings.agent_runtime.max_turns, "verification_enabled": verification_enabled}
    assert "not-for-provenance" not in updated.model_dump_json()


def test_freeze_skips_finalize_required_capture_when_step_aborted_in_prepare(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    store = ArtifactStore(run_dir)
    recorder = EvidenceRecorder(run_id=metadata.run_id, output_dir=run_dir)
    step = recorder.allocate_step_identity(ExecutableStep(step_id="step-1", action_name="click_on", kind="action"))
    recorder.record_event(
        RunnerEvent(event_type="step_start", run_id=metadata.run_id, **step.model_dump(include={"step_id", "source_step_id", "step_execution_id", "invocation_path", "attempt_index"}))
    )
    prepare_ref = store.write_json(
        kind="json",
        step_id=step.step_id,
        phase="prepare",
        name="before-screenshot-unavailable",
        payload={"status": "unavailable", "reason": "windows_app_not_launched", "requested_artifact_kind": "screenshot"},
    ).model_copy(update={"step_execution_id": step.step_execution_id})
    recorder.record_step_result(
        RunnerStepResult(
            step_id=step.step_id,
            source_step_id=step.source_step_id,
            step_execution_id=step.step_execution_id,
            invocation_path=step.invocation_path,
            action_name=step.action_name,
            status="failed",
            failure_category="harness_error",
            error_message="prepare context failed",
            phase_reports=[StepPhaseReport(step_id=step.step_id, phase="prepare", status="failed", error_message="prepare context failed", artifact_refs=[prepare_ref])],
            evidence_refs=[prepare_ref.artifact_id],
            metadata={"evidence_policy": {"capture_before": True, "capture_after": True, "artifact_kinds": ["screenshot"]}},
        )
    )
    recorder.write_manifest()
    bundle = EvidenceRecorder.recover_bundle(run_dir)
    assert bundle.completeness == "complete"

    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=bundle)
    final_metadata = service.finalize(run_dir, metadata, execution_result=frozen)
    assert frozen.outcome == "failed"
    assert not any(err.get("phase") == "finalize" and err.get("reason") == "required_capture_missing" for err in frozen.evidence["errors"])
    assert frozen.evidence["status"] == "complete"

    report = RunReportService().project(run_dir, final_metadata.model_dump(mode="json"), bundle)
    assert report.evidence["status"] == "complete"
    assert report.evidence["required_missing"] == []
    assert "required_evidence_incomplete" not in report.run["gate"]["reasons"]


def test_freeze_explore_mode_recovers_superseded_failed_step_capture_into_warnings(tmp_path: Path) -> None:
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="windows", source_id="goal", mode="explore", source=RunSource(kind="goal", goal_summary="Open Notepad"))
    run_dir = tmp_path / ".fsq/runs/windows" / metadata.run_id
    store = ArtifactStore(run_dir)
    recorder = EvidenceRecorder(run_id=metadata.run_id, output_dir=run_dir)

    attempt1 = recorder.allocate_step_identity(ExecutableStep(step_id="attempt-1", action_name="launchApp", kind="action"))
    recorder.record_event(
        RunnerEvent(event_type="step_start", run_id=metadata.run_id, **attempt1.model_dump(include={"step_id", "source_step_id", "step_execution_id", "invocation_path", "attempt_index"}))
    )
    failed_ref = store.capture_failure(
        kind="screenshot",
        step_id=attempt1.step_id,
        phase="finalize",
        name="after-launch",
        error=RuntimeError("window not ready"),
    ).model_copy(update={"step_execution_id": attempt1.step_execution_id})
    recorder.record_event(
        RunnerEvent(
            event_type="artifact_failed",
            run_id=metadata.run_id,
            **attempt1.model_dump(include={"step_id", "source_step_id", "step_execution_id", "invocation_path", "attempt_index"}),
            phase="finalize",
            payload={"artifact": failed_ref.model_dump(mode="json")},
        )
    )
    recorder.record_step_result(
        RunnerStepResult(
            step_id=attempt1.step_id,
            source_step_id=attempt1.source_step_id,
            step_execution_id=attempt1.step_execution_id,
            invocation_path=attempt1.invocation_path,
            action_name="launchApp",
            status="failed",
            failure_category="artifact_error",
            error_message="window not ready",
            phase_reports=[
                StepPhaseReport(step_id=attempt1.step_id, phase="prepare", status="passed"),
                StepPhaseReport(step_id=attempt1.step_id, phase="invoke", status="failed", error_message="launch timeout"),
                StepPhaseReport(step_id=attempt1.step_id, phase="finalize", status="passed", artifact_refs=[failed_ref]),
            ],
            evidence_refs=[failed_ref.artifact_id],
            evidence_errors=[{"phase": "finalize", "kind": "screenshot", "message": "window not ready"}],
            metadata={"evidence_policy": {"capture_before": False, "capture_after": True, "artifact_kinds": ["screenshot"]}},
        )
    )

    attempt2 = recorder.allocate_step_identity(ExecutableStep(step_id="attempt-2", action_name="launchApp", kind="action"))
    recorder.record_event(
        RunnerEvent(event_type="step_start", run_id=metadata.run_id, **attempt2.model_dump(include={"step_id", "source_step_id", "step_execution_id", "invocation_path", "attempt_index"}))
    )
    passed_ref = store.write_bytes(
        kind="screenshot",
        step_id=attempt2.step_id,
        phase="finalize",
        name="after-launch",
        data=b"\x89PNG\r\n\x1a\nfake",
    ).model_copy(update={"step_execution_id": attempt2.step_execution_id})
    recorder.record_event(
        RunnerEvent(
            event_type="artifact_captured",
            run_id=metadata.run_id,
            **attempt2.model_dump(include={"step_id", "source_step_id", "step_execution_id", "invocation_path", "attempt_index"}),
            phase="finalize",
            payload={"artifact": passed_ref.model_dump(mode="json")},
        )
    )
    recorder.record_step_result(
        RunnerStepResult(
            step_id=attempt2.step_id,
            source_step_id=attempt2.source_step_id,
            step_execution_id=attempt2.step_execution_id,
            invocation_path=attempt2.invocation_path,
            action_name="launchApp",
            status="passed",
            phase_reports=[
                StepPhaseReport(step_id=attempt2.step_id, phase="prepare", status="passed"),
                StepPhaseReport(step_id=attempt2.step_id, phase="invoke", status="passed"),
                StepPhaseReport(step_id=attempt2.step_id, phase="finalize", status="passed", artifact_refs=[passed_ref]),
            ],
            evidence_refs=[passed_ref.artifact_id],
            metadata={"evidence_policy": {"capture_before": False, "capture_after": True, "artifact_kinds": ["screenshot"]}},
        )
    )
    recorder.write_manifest()
    bundle = EvidenceRecorder.recover_bundle(run_dir)
    assert bundle.completeness == "partial"

    service = RunLifecycleService()
    frozen = service.freeze(
        run_dir,
        metadata,
        bundle=bundle,
        verification=VerificationResult(status="success", summary="Notepad opened and verified."),
    )
    final_metadata = service.finalize(run_dir, metadata, execution_result=frozen)
    assert frozen.outcome == "success"
    assert frozen.primary_failure is None
    assert frozen.evidence["status"] == "complete"
    assert frozen.evidence["errors"] == []
    assert len(frozen.evidence["warnings"]) == 1
    assert "Recovered intermediate step attempt-1" in frozen.evidence["warnings"][0]
    assert frozen.steps[0]["evidence_errors"] == [{"phase": "finalize", "kind": "screenshot", "message": "window not ready"}]

    report = RunReportService().project(run_dir, final_metadata.model_dump(mode="json"), bundle)
    assert report.evidence["status"] == "complete"
    assert report.evidence["errors"] == []
    assert report.evidence["required_missing"] == []
    assert any("Recovered intermediate step attempt-1" in w for w in report.evidence["warnings"])
    assert report.run["gate"]["status"] == "passed"
    assert report.steps[0]["evidence_errors"] == [{"phase": "finalize", "kind": "screenshot", "message": "window not ready"}]


@pytest.mark.parametrize("mode", ["explore", "strict"])
def test_freeze_blocks_unrecovered_or_final_evidence_failure(tmp_path: Path, mode: str) -> None:
    source = RunSource(kind="goal", goal_summary="Open Notepad") if mode == "explore" else RunSource(kind="case", case_id="notepad")
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="windows", source_id="notepad", mode=mode, source=source)
    run_dir = tmp_path / ".fsq/runs/windows" / metadata.run_id
    failed_ref = EvidenceArtifactRef(
        artifact_id="art-final-fail",
        kind="json",
        path=Path("artifacts/data/0001-failed.json"),
        availability="failed",
        unavailable_reason="capture crashed",
        step_id="final-step",
        phase="finalize",
        metadata={"requested_kind": "screenshot"},
    )
    bundle = EvidenceBundle(
        bundle_id="evidence",
        run_id=metadata.run_id,
        completeness="complete",
        artifacts=[failed_ref],
        steps=[
            RunnerStepResult(
                step_id="final-step",
                step_execution_id="exec-final",
                action_name="assertVisible",
                status="passed",
                phase_reports=[
                    StepPhaseReport(step_id="final-step", phase="prepare", status="passed"),
                    StepPhaseReport(step_id="final-step", phase="invoke", status="passed"),
                    StepPhaseReport(step_id="final-step", phase="finalize", status="passed", artifact_refs=[failed_ref]),
                ],
                evidence_refs=[failed_ref.artifact_id],
                metadata={"evidence_policy": {"capture_before": False, "capture_after": True, "artifact_kinds": ["screenshot"]}},
            )
        ],
    )
    frozen = RunLifecycleService().freeze(
        run_dir,
        metadata,
        bundle=bundle,
        verification=VerificationResult(status="success", summary="Model claimed success."),
    )
    assert frozen.outcome == "error"
    assert frozen.evidence["status"] == "partial"
    assert frozen.evidence["warnings"] == []
