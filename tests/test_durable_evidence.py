# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import sys
from pathlib import Path

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.core import ArtifactStore, EvidenceRecorder, StepRunner, StepSequenceRunner
from fsq_agent.models import ExecutableStep, HarnessActionResult, HarnessArtifactRef, HarnessContext, RunnerEvent, RunnerStepResult, StepPhaseReport


def _step(name="tap", **updates):
    return ExecutableStep(step_id=name, kind="action", action_name="tapOn", params={"target": "Button"}, **updates)


class JournalHarness:
    def __init__(self, root: Path, *, fail_capture=False, failure=False, cancel=False, prepare_failure=False):
        self.root = root
        self.fail_capture = fail_capture
        self.failure = failure
        self.cancel = cancel
        self.prepare_failure = prepare_failure
        self.invocations = []
        self.captures = []
        self.store = ArtifactStore(root)

    def get_context(self):
        if self.prepare_failure:
            raise RuntimeError("context unavailable")
        return HarnessContext(platform="android")

    def before_action(self, step, context):
        pass

    def invoke_action(self, step, context):
        records = [json.loads(line) for line in (self.root / "evidence-events.jsonl").read_text().splitlines()]
        assert any(record.get("event", {}).get("event_type") == "step_start" for record in records)
        self.invocations.append(step)
        if self.cancel:
            raise asyncio.CancelledError
        return HarnessActionResult(
            status="failed" if self.failure else "passed",
            action_name=step.action_name,
            failure_category="assertion_error" if self.failure else None,
            error_message="Expected button missing" if self.failure else None,
        )

    def after_action(self, step, context, result):
        recovered = EvidenceRecorder.recover_bundle(self.root)
        assert any(event.event_type == "action_result" for event in recovered.events)

    def capture_artifact(self, *, kind, reason, context, step_id, phase):
        self.captures.append((kind, phase))
        if kind == "screenshot" and self.fail_capture:
            raise RuntimeError("screenshot unavailable")
        ref = self.store.write_bytes(kind=kind, step_id=step_id, phase=phase, name=reason, data=b"evidence")
        return HarnessArtifactRef(**ref.model_dump(exclude={"step_id", "step_execution_id", "phase"}))

    def classify_error(self, error, phase, step):
        return "context_error" if phase == "prepare" else "action_error"


def _runner(root, harness):
    recorder = EvidenceRecorder(run_id="run", output_dir=root)
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="android"), evidence_sink=recorder)
    return runner, recorder


def test_journal_is_durable_before_action_and_finalization(tmp_path):
    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path))
    result = runner.run_step("run", _step())
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert bundle.steps[0].step_execution_id == result.step_id
    assert result.action_status == "passed"
    assert result.started_at is not None
    assert result.ended_at >= result.started_at
    assert [phase.phase for phase in result.phase_reports] == ["prepare", "invoke", "settle", "finalize"]
    assert all(phase.duration_ms is not None and phase.metadata["timing_measured"] for phase in result.phase_reports)
    manifest = recorder.write_manifest()
    persisted = json.loads(manifest.read_text())
    assert persisted["schema_version"] == "fsq.evidence/v2"
    assert persisted["checkpoint_sequence"] > 0
    records = [json.loads(line) for line in (tmp_path / "evidence-events.jsonl").read_text().splitlines()]
    assert [record["sequence"] for record in records] == list(range(1, len(records) + 1))
    assert all(record["schema_version"] == "fsq.evidence-event/v1" for record in records)


def test_deep_phase_output_has_one_canonical_journal_and_checkpoint_representation(tmp_path):
    class DeepOutputHarness(JournalHarness):
        def invoke_action(self, step, context):
            output = {"attributes": {"value": "A"}}
            for _ in range(10):
                output = {"children": [output]}
            return HarnessActionResult(status="passed", action_name=step.action_name, output={"page_source": {"root": output}})

        def after_action(self, step, context, result):
            pass

    runner, _ = _runner(tmp_path, DeepOutputHarness(tmp_path))

    result = runner.run_step("run", ExecutableStep(step_id="snapshot", kind="observation", action_name="ui_snapshot", params={}))

    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    recovered_result = next(step for step in recovered.steps if step.step_execution_id == result.step_execution_id)
    invoke_event = next(event for event in recovered.events if event.step_execution_id == result.step_execution_id and event.event_type == "phase_finish" and event.phase == "invoke")
    event_report = StepPhaseReport.model_validate(invoke_event.payload["phase_report"])
    result_report = next(phase for phase in recovered_result.phase_reports if phase.phase == "invoke")
    assert event_report == result_report
    assert recovered_result.status == "passed"


def test_action_failure_survives_independent_capture_failure(tmp_path):
    harness = JournalHarness(tmp_path, fail_capture=True, failure=True)
    runner, _ = _runner(tmp_path, harness)
    result = runner.run_step("run", _step())
    assert result.failure_category == "assertion_error"
    assert result.error_message == "Expected button missing"
    assert result.evidence_errors
    assert harness.captures == [("screenshot", "finalize")]
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert any(ref.availability == "failed" for ref in bundle.artifacts)


def test_cancel_and_prepare_failure_preserve_partial_results(tmp_path):
    runner, _ = _runner(tmp_path, JournalHarness(tmp_path, cancel=True))
    with pytest.raises(asyncio.CancelledError):
        runner.run_step("run", _step())
    assert EvidenceRecorder.recover_bundle(tmp_path).steps[0].status == "cancelled"
    other = tmp_path / "other"
    runner, _ = _runner(other, JournalHarness(other, prepare_failure=True))
    result = runner.run_step("run", _step())
    assert result.status == "failed"
    assert result.failure_category == "context_error"
    assert EvidenceRecorder.recover_bundle(other).steps[0].failure_category == "context_error"


def test_repeated_invocations_have_unique_ids_and_artifacts(tmp_path):
    harness = JournalHarness(tmp_path)
    runner, recorder = _runner(tmp_path, harness)
    first = runner.run_step("run", _step())
    second = runner.run_step("run", _step())
    assert first.source_step_id == second.source_step_id
    assert first.step_execution_id != second.step_execution_id
    assert first.invocation_path != second.invocation_path
    refs = recorder.build_bundle().artifacts
    assert len({ref.artifact_id for ref in refs}) == len(refs)
    assert len({ref.path for ref in refs}) == len(refs)
    assert all(ref.size_bytes == 8 and ref.sha256 for ref in refs)


def test_recovery_keeps_truncated_tail_but_rejects_complete_corruption(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="one"))
    path = tmp_path / "evidence-events.jsonl"
    good = path.read_bytes()
    path.write_bytes(good + b'{"sequence":')
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert recovered.completeness == "partial"
    assert recovered.steps[0].status == "incomplete"
    assert recovered.warnings
    assert path.read_bytes() == good + b'{"sequence":'
    path.write_bytes(good + b"not-json\n")
    with pytest.raises(ValueError, match="journal"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_sequence_records_unexecuted_leaves_and_no_duplicate_events(tmp_path):
    harness = JournalHarness(tmp_path, failure=True)
    runner, recorder = _runner(tmp_path, harness)
    bundle = StepSequenceRunner(runner, recorder).run_steps("run", [_step("one"), _step("two")])
    assert [result.status for result in bundle.steps] == ["failed", "skipped"]
    assert bundle.steps[1].step_execution_id is None
    assert bundle.steps[1].blocked_by_step == bundle.steps[0].step_execution_id
    assert len(bundle.planned_steps) == 2
    assert sum(event.event_type == "step_start" for event in bundle.events) == 1


def test_journal_failure_prevents_external_action(tmp_path, monkeypatch):
    harness = JournalHarness(tmp_path)
    runner, recorder = _runner(tmp_path, harness)

    def fail(_event):
        raise OSError("disk unavailable")

    monkeypatch.setattr(recorder, "record_event", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        runner.run_step("run", _step())
    assert harness.invocations == []


def test_artifact_store_rejects_symlink_escape_and_never_overwrites(tmp_path):
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    run.mkdir()
    outside.mkdir()
    (run / "artifacts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="contain"):
        ArtifactStore(run).write_bytes(kind="screenshot", step_id="a", phase="prepare", name="before", data=b"x")
    assert not list(outside.iterdir())


def test_checkpoint_recovery_retains_phase_and_planned_unfinished_facts(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_planned_step(_step("one", invocation_path=("root", "one")))
    recorder.record_planned_step(_step("two", invocation_path=("root", "two")))
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="one", source_step_id="one", step_execution_id="one", invocation_path=("root", "one")))
    recorder.record_event(
        RunnerEvent(
            run_id="run", event_type="action_result", step_id="one", step_execution_id="one", source_step_id="one", invocation_path=("root", "one"), phase="invoke", payload={"status": "passed"}
        )
    )
    recorder.write_manifest()
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert [step.status for step in bundle.steps] == ["incomplete", "incomplete"]
    assert bundle.steps[0].action_status == "passed"
    assert bundle.steps[1].step_execution_id is None
    assert bundle.completeness == "partial"


def test_failed_checkpoint_preserves_previous_manifest(tmp_path, monkeypatch):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    path = recorder.write_manifest()
    previous = path.read_bytes()

    def fail_replace(self, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        recorder.write_manifest()
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".evidence-*.tmp"))


def test_legacy_manifest_is_read_without_invented_measurements(tmp_path):
    path = tmp_path / "evidence-manifest.json"
    path.write_text(json.dumps({"schema_version": "1.0", "bundle_id": "old-evidence", "run_id": "old", "steps": [{"step_id": "old", "status": "passed", "duration_ms": 0}]}))
    before = path.read_bytes()
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert bundle.steps[0].duration_ms is None
    assert bundle.steps[0].unavailable_reason == "legacy_unmeasured"
    assert path.read_bytes() == before


def test_checkpoint_cannot_override_durable_action_outcome(tmp_path):
    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path, failure=True))
    runner.run_step("run", _step())
    path = recorder.write_manifest()
    payload = json.loads(path.read_text())
    payload["steps"][0]["status"] = "passed"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checkpoint"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_redaction_happens_before_journal_and_text_artifact_hash(tmp_path):
    from hashlib import sha256

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path, secret_values=("private-value",))
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_error", payload={"message": "private-value"}))
    recorder.write_manifest()
    store = ArtifactStore(tmp_path, secret_values=("private-value",))
    ref = store.write_text(kind="text", step_id="one", phase="invoke", name="safe", text="value=private-value")
    assert (tmp_path / ref.path).read_text() == "value=***"
    assert ref.sha256 == sha256(b"value=***").hexdigest()
    assert "private-value" not in (tmp_path / "evidence-events.jsonl").read_text()
    assert "private-value" not in (tmp_path / "evidence-manifest.json").read_text()


def test_canonical_evidence_preserves_replay_parameters_without_sdk_events(tmp_path):
    harness = JournalHarness(tmp_path)
    runner, _ = _runner(tmp_path, harness)
    result = runner.run_step("run", _step())
    phase = next(item for item in result.phase_reports if item.phase == "invoke")
    assert phase.metadata["safe_replay_params"] == {"target": "Button"}
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert recovered.steps[0].phase_reports[1].metadata["safe_replay_params"] == {"target": "Button"}
    assert not (tmp_path / "events.jsonl").exists()


@pytest.mark.parametrize("legacy_schema", [None, "1.0"])
def test_authoritative_journal_takes_precedence_over_legacy_report_manifest(tmp_path, legacy_schema):
    runner, _ = _runner(tmp_path, JournalHarness(tmp_path))
    result = runner.run_step("run", _step())
    legacy = {"run_id": "run", "steps": [{"step_id": "synthetic-plan", "status": "passed"}]}
    if legacy_schema:
        legacy.update(schema_version=legacy_schema, bundle_id="legacy")
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text(json.dumps(legacy))
    before = manifest.read_bytes()
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert [step.step_execution_id for step in bundle.steps] == [result.step_execution_id]
    assert bundle.steps[0].phase_reports[1].metadata["safe_replay_params"] == {"target": "Button"}
    assert bundle.warnings
    assert manifest.read_bytes() == before


def test_completed_step_streams_journal_without_implicit_manifest_until_write_manifest(tmp_path):
    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path))
    runner.run_step("run", _step())
    assert (tmp_path / "evidence-events.jsonl").is_file()
    assert not (tmp_path / "evidence-manifest.json").exists()
    recovered_from_journal = EvidenceRecorder.recover_bundle(tmp_path)
    assert recovered_from_journal.steps[0].status == "passed"
    manifest_path = recorder.write_manifest()
    payload = json.loads(manifest_path.read_text())
    assert payload["schema_version"] == "fsq.evidence/v2"
    assert payload["steps"][0]["status"] == "passed"


def test_recorder_rejects_incremental_inconsistent_events_and_results_in_constant_step_checks(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    with pytest.raises(ValueError, match="start"):
        recorder.record_event(RunnerEvent(run_id="run", event_type="action_result", step_id="s1", step_execution_id="s1", phase="invoke", payload={"status": "passed"}))
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="s1", step_execution_id="s1", source_step_id="src1", invocation_path=("root", "0")))
    with pytest.raises(ValueError, match="started more than once"):
        recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="s1", step_execution_id="s1", source_step_id="src1", invocation_path=("root", "0")))
    recorder.record_event(
        RunnerEvent(
            run_id="run",
            event_type="action_result",
            step_id="s1",
            step_execution_id="s1",
            source_step_id="src1",
            invocation_path=("root", "0"),
            phase="invoke",
            payload={"status": "failed", "failure_category": "action_error"},
        )
    )
    with pytest.raises(ValueError, match="cannot pass"):
        recorder.record_step_result(
            RunnerStepResult(step_id="s1", step_execution_id="s1", source_step_id="src1", invocation_path=("root", "0"), status="passed", action_status="failed", failure_category="action_error")
        )
    recorder.record_step_result(
        RunnerStepResult(step_id="s1", step_execution_id="s1", source_step_id="src1", invocation_path=("root", "0"), status="failed", action_status="failed", failure_category="action_error")
    )
    with pytest.raises(ValueError, match="after its completed result"):
        recorder.record_event(
            RunnerEvent(
                run_id="run",
                event_type="phase_finish",
                step_id="s1",
                step_execution_id="s1",
                source_step_id="src1",
                invocation_path=("root", "0"),
                phase="finalize",
                payload={"status": "passed", "phase_report": {"step_id": "s1", "phase": "finalize", "status": "passed"}},
            )
        )
    recorder.record_event(
        RunnerEvent(
            run_id="run",
            event_type="step_finish",
            step_id="s1",
            step_execution_id="s1",
            source_step_id="src1",
            invocation_path=("root", "0"),
            payload={"status": "failed"},
        )
    )
    assert EvidenceRecorder.recover_bundle(tmp_path).steps[0].status == "failed"


def test_recorder_incremental_validation_avoids_copying_prior_artifacts_and_fsyncs_journal_on_manifest(tmp_path, monkeypatch):
    import os

    fsynced_names: list[str] = []
    open_names: dict[int, str] = {}
    real_open = Path.open
    real_fdopen = os.fdopen
    real_fsync = os.fsync

    def tracked_open(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        open_names[stream.fileno()] = self.name
        return stream

    def tracked_fdopen(fd, *args, **kwargs):
        open_names[fd] = "manifest.tmp"
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(os, "fdopen", tracked_fdopen)
    monkeypatch.setattr(os, "fsync", lambda fd: (fsynced_names.append(open_names.get(fd, "other")), real_fsync(fd))[1])
    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path))
    runner.run_step("run", _step("step-1"))
    runner.run_step("run", _step("step-2"))
    assert "evidence-events.jsonl" not in fsynced_names

    class UniteratedDict(dict):
        def __iter__(self):
            raise AssertionError("Incremental step validation must not iterate full Run dictionaries")

        def keys(self):
            raise AssertionError("Incremental step validation must not read keys() of full Run dictionaries")

        def items(self):
            raise AssertionError("Incremental step validation must not read items() of full Run dictionaries")

        def values(self):
            raise AssertionError("Incremental step validation must not read values() of full Run dictionaries")

        def copy(self):
            raise AssertionError("Incremental step validation must not copy() full Run dictionaries")

    recorder._artifacts = UniteratedDict(recorder._artifacts)
    recorder._phases = UniteratedDict(recorder._phases)
    runner.run_step("run", _step("step-3"))

    recorder._artifacts = dict(dict.items(recorder._artifacts))
    recorder._phases = dict(dict.items(recorder._phases))
    fsynced_names.clear()
    recorder.write_manifest()
    assert fsynced_names[:2] == ["evidence-events.jsonl", "manifest.tmp"]


def test_shared_redaction_preserves_35_deep_nested_structures_and_dag_siblings_while_guarding_cycles_and_128_limit(tmp_path):
    from fsq_agent.core import ArtifactStore
    from fsq_agent.execution import RunLifecycleService

    # 1. Legitimate 35-level nested structure (previously corrupted at depth > 20)
    leaf = {"label": "Deep Button", "password": "LEAF_SECRET_CANARY", "note": "Keep my_runtime_secret safe"}
    nested_35: object = leaf
    for level in range(35):
        nested_35 = {"level": level, "child": [nested_35]}

    store = ArtifactStore(run_dir=tmp_path, secret_values=("my_runtime_secret",))
    ref = store.write_json(kind="ui_snapshot", step_id="s1", phase="finalize", name="deep", payload=nested_35)
    persisted_text = (tmp_path / ref.path).read_text(encoding="utf-8")
    assert "[REDACTED: nesting limit]" not in persisted_text
    assert "LEAF_SECRET_CANARY" not in persisted_text
    assert "my_runtime_secret" not in persisted_text
    assert "Deep Button" in persisted_text

    safe_exec = RunLifecycleService.safe_value(nested_35, ("my_runtime_secret",))
    safe_exec_json = json.dumps(safe_exec)
    assert "[REDACTED: nesting limit]" not in safe_exec_json
    assert "LEAF_SECRET_CANARY" not in safe_exec_json
    assert "my_runtime_secret" not in safe_exec_json
    assert "Deep Button" in safe_exec_json

    # 2. Shared non-cyclic DAG sibling reference is NOT flagged as a cycle
    shared_node = {"title": "Shared Leaf", "apiKey": "DAG_SECRET"}
    dag_payload = {"left": shared_node, "right": shared_node}
    safe_dag = RunLifecycleService.safe_value(dag_payload)
    assert safe_dag == {
        "left": {"title": "Shared Leaf", "apiKey": "[REDACTED]"},
        "right": {"title": "Shared Leaf", "apiKey": "[REDACTED]"},
    }

    # 3. Genuine ancestor cycle returns "[REDACTED: cycle]" without RecursionError
    cyclic_dict: dict[str, object] = {"name": "root", "token": "CYCLE_SECRET"}
    cyclic_dict["self"] = cyclic_dict
    safe_cycle = RunLifecycleService.safe_value(cyclic_dict)
    assert safe_cycle == {"name": "root", "token": "[REDACTED]", "self": "[REDACTED: cycle]"}

    # 4. Pathological depth > 128 triggers "[REDACTED: nesting limit]"
    deep_135: object = {"ok": True}
    for _ in range(135):
        deep_135 = [deep_135]
    safe_deep_135 = RunLifecycleService.safe_value(deep_135)
    assert "[REDACTED: nesting limit]" in json.dumps(safe_deep_135)

    depth = sys.getrecursionlimit() + 64
    encoded = "[" * depth + '{"token":"oversized-json-canary"}' + "]" * depth
    safe_encoded = RunLifecycleService.safe_value({"json": encoded}, ())["json"]
    assert "[REDACTED: nesting limit]" in safe_encoded
    assert "oversized-json-canary" not in safe_encoded
