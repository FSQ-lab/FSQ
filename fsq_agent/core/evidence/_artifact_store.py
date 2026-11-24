# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fsq_agent.core.runtime_secrets import RuntimeSecretStore
from fsq_agent.models import EvidenceArtifactKind, EvidenceArtifactRef, StepPhase

_ARTIFACT_DIRS: dict[EvidenceArtifactKind, str] = {
    "screenshot": "screenshots",
    "ui_tree": "ui-trees",
    "ui_snapshot": "ui-snapshots",
    "tool_call": "harness-calls",
    "log": "logs",
    "json": "raw",
    "text": "logs",
    "other": "raw",
}
_DEFAULT_EXTENSIONS: dict[EvidenceArtifactKind, str] = {
    "screenshot": "png",
    "ui_tree": "json",
    "ui_snapshot": "json",
    "tool_call": "json",
    "log": "txt",
    "json": "json",
    "text": "txt",
    "other": "bin",
}
_DEFAULT_MIME_TYPES: dict[EvidenceArtifactKind, str] = {
    "screenshot": "image/png",
    "ui_tree": "application/json",
    "ui_snapshot": "application/json",
    "tool_call": "application/json",
    "log": "text/plain",
    "json": "application/json",
    "text": "text/plain",
    "other": "application/octet-stream",
}


class ArtifactStore:
    def __init__(self, run_dir: Path, *, secret_values: tuple[str, ...] = ()) -> None:
        self.run_dir = run_dir.resolve()
        self._occurrences: dict[str, int] = {}
        self._lock = threading.Lock()
        self._secret_values = tuple(value for value in secret_values if value)
        self._capture_callback = None
        self._capture_sequence = 0

    def allocate_capture_sequence(self) -> int:
        with self._lock:
            self._capture_sequence += 1
            return self._capture_sequence

    @contextmanager
    def capture_scope(self, callback):
        previous = self._capture_callback
        self._capture_callback = callback
        try:
            yield
        finally:
            self._capture_callback = previous

    def _acknowledge(self, ref):
        if self._capture_callback is not None:
            self._capture_callback(ref)
        return ref

    def capture_failure(self, *, kind, step_id, phase, name, error):
        ref = EvidenceArtifactRef(
            artifact_id=f"{kind}-{self._slug(step_id)}-{phase}-{uuid.uuid4().hex[:12]}",
            kind=kind,
            step_id=step_id,
            step_execution_id=step_id,
            phase=phase,
            availability="failed",
            unavailable_reason=self._sanitize(str(error) or type(error).__name__),
            metadata={"capture_reason": name, "requested_kind": kind},
        )
        return self._acknowledge(ref)

    def write_json(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        payload: Any,
    ) -> EvidenceArtifactRef:
        original = payload
        payload = self._sanitize(payload)
        ref = self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension="json", data=json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"), structured=True)
        ref.metadata["redacted"] = ref.metadata.get("redacted", False) or payload != original
        if kind in {"ui_snapshot", "ui_tree"}:
            known = {key: payload[key] for key in ("coverage", "truncated", "compaction", "clipped", "snapshot") if isinstance(payload, dict) and key in payload}
            ref.metadata.update(known or {"coverage": None, "coverage_reason": "driver_did_not_report_coverage"})
        if kind == "json" and isinstance(payload, dict) and payload.get("status") == "unavailable" and payload.get("requested_artifact_kind"):
            ref = ref.model_copy(update={"availability": "not_applicable", "unavailable_reason": str(payload.get("reason") or "session_not_started")})
            ref.metadata["requested_artifact_kind"] = payload["requested_artifact_kind"]
        return self._acknowledge(ref)

    def _sanitize(self, value):
        return RuntimeSecretStore.redact_structure(value, self._secret_values)

    def write_text(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        text: str,
    ) -> EvidenceArtifactRef:
        return self._acknowledge(self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension=_DEFAULT_EXTENSIONS[kind], data=text.encode("utf-8")))

    def write_bytes(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        data: bytes,
    ) -> EvidenceArtifactRef:
        return self._acknowledge(self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension=_DEFAULT_EXTENSIONS[kind], data=data))

    def _write(self, *, kind, step_id, phase, name, extension, data, structured=False) -> EvidenceArtifactRef:
        with self._lock:
            transformed = False
            if not structured and kind not in {"screenshot", "other"}:
                safe_data = self._sanitize(data.decode("utf-8")).encode("utf-8")
                transformed = safe_data != data
                data = safe_data
            key = f"{kind}-{self._artifact_id(step_id=step_id, phase=phase, name=name)}"
            occurrence = self._occurrences.get(key, 0) + 1
            self._occurrences[key] = occurrence
            artifact_id = f"{key}-{occurrence}-{uuid.uuid4().hex[:12]}"
            relative = self._relative_path(kind=kind, artifact_id=artifact_id, extension=extension)
            path = self.run_dir / relative
            if not path.resolve().is_relative_to(self.run_dir):
                raise ValueError("Artifact path must remain contained in its Run.")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(self.run_dir):
                raise ValueError("Artifact path must remain contained in its Run.")
            descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Hard-link installation is atomic and refuses an existing destination.
                os.link(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self._artifact_ref(artifact_id=artifact_id, kind=kind, path=relative, step_id=step_id, phase=phase).model_copy(
                update={
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "capture_occurrence": occurrence,
                    "step_execution_id": step_id,
                    "metadata": {"capture_reason": name, "redacted": transformed},
                }
            )

    def _artifact_ref(
        self,
        *,
        artifact_id: str,
        kind: EvidenceArtifactKind,
        path: Path,
        step_id: str,
        phase: StepPhase,
    ) -> EvidenceArtifactRef:
        return EvidenceArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=path,
            mime_type=_DEFAULT_MIME_TYPES[kind],
            step_id=step_id,
            phase=phase,
        )

    def _relative_path(self, *, kind: EvidenceArtifactKind, artifact_id: str, extension: str) -> Path:
        return Path("artifacts") / _ARTIFACT_DIRS[kind] / f"{artifact_id}.{extension}"

    def _artifact_id(self, *, step_id: str, phase: StepPhase, name: str) -> str:
        return "-".join([self._slug(step_id), phase, self._slug(name)])

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "artifact"
