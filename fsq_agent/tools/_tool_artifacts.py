# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import re
from bisect import bisect_right
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fsq_agent.models import LocalToolOutputSettings, ToolExecutionError


class ToolArtifactStore:
    def __init__(self, runs_dir: Path, run_id: str, settings: LocalToolOutputSettings) -> None:
        self.runs_dir = runs_dir
        self.run_id = run_id
        self.settings = settings
        self.call_index = 0
        self._search_history: OrderedDict[tuple[Path, str, bool, int, int], bytes] = OrderedDict()

    @property
    def root(self) -> Path:
        return self.runs_dir / self.run_id / self.settings.artifact_subdir

    def write(self, tool_name: str, content: str, metadata: dict[str, Any] | None = None) -> Path | None:
        if not self.settings.artifact_enabled or not self.settings.always_write_artifact:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        safe_tool_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool_name).strip("-._") or "tool"
        while True:
            self.call_index += 1
            path = self.root / f"{self.call_index:06d}-{safe_tool_name}.json"
            payload = {
                "tool_name": tool_name,
                "run_id": self.run_id,
                "call_index": self.call_index,
                "content_chars": len(content),
                "metadata": metadata or {},
                "content": content,
            }
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)
            try:
                with path.open("x", encoding="utf-8") as artifact:
                    artifact.write(encoded)
            except FileExistsError:
                continue
            return path

    def read_text(self, artifact_path: str) -> str:
        path = self._resolve(artifact_path)
        return path.read_text(encoding="utf-8")

    def read_slice(self, artifact_path: str, offset: int, length: int) -> dict[str, Any]:
        path = self._resolve(artifact_path)
        text = path.read_text(encoding="utf-8")
        safe_offset = min(max(offset, 0), len(text))
        safe_length = max(length, 1)
        end = min(safe_offset + safe_length, len(text))
        has_more_after = end < len(text)
        instructions = f"Continue with read_artifact_slice(artifact_path, offset={end}, length=12000)." if has_more_after else "End of saved artifact; no further content after this slice."
        return {
            "artifact_path": str(path),
            "offset": safe_offset,
            "length": end - safe_offset,
            "total_chars": len(text),
            "content": text[safe_offset:end],
            "has_more_before": safe_offset > 0,
            "has_more_after": has_more_after,
            "instructions": instructions,
        }

    def search(self, artifact_path: str, query: str, case_sensitive: bool, max_matches: int, context_chars: int) -> dict[str, Any]:
        if not query:
            raise ToolExecutionError("Artifact search query cannot be empty.")
        path = self._resolve(artifact_path)
        text = path.read_text(encoding="utf-8")
        signature = (path, query, case_sensitive, max_matches, context_chars)
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        changed_signatures = [previous for previous, previous_digest in self._search_history.items() if previous[0] == path and previous_digest != digest]
        for previous in changed_signatures:
            del self._search_history[previous]
        repeated = self._search_history.pop(signature, None) == digest
        self._search_history[signature] = digest
        if len(self._search_history) > 32:
            self._search_history.popitem(last=False)

        haystack = text if case_sensitive else text.lower()
        needle = query if case_sensitive else query.lower()
        # Only expanding lowercase characters need coordinate corrections; retain
        # the existing literal lowercase lookup without a full character map.
        expanded_positions: list[int] = []
        if len(haystack) != len(text):
            lowered_offset = 0
            for character in text:
                width = len(character.lower())
                if width > 1:
                    expanded_positions.extend(range(lowered_offset + 1, lowered_offset + width))
                lowered_offset += width
        matches: list[dict[str, Any]] = []
        start = 0
        while len(matches) < max_matches:
            lowered_index = haystack.find(needle, start)
            if lowered_index < 0:
                break
            lowered_last = lowered_index + len(needle) - 1
            index = lowered_index - bisect_right(expanded_positions, lowered_index)
            match_end = lowered_last - bisect_right(expanded_positions, lowered_last) + 1
            context_start = max(0, index - context_chars)
            context_end = min(len(text), match_end + context_chars)
            matches.append(
                {
                    "offset": index,
                    "context_start": context_start,
                    "context_end": context_end,
                    "preview": text[context_start:context_end],
                }
            )
            start = lowered_index + len(needle)
        truncated = len(matches) >= max_matches and haystack.find(needle, start) >= 0
        guidance: list[str] = []
        if matches:
            match = matches[0]
            guidance.append(f"Read the matched field with read_artifact_slice(artifact_path, offset={match['offset']}, length=12000).")
            if match["context_end"] < len(text):
                guidance.append(f"Continue beyond its context with read_artifact_slice(artifact_path, offset={match['context_end']}, length=12000).")
        else:
            guidance.append("No literal matches in the saved text; refine query.")
        if truncated:
            guidance.append("Selection truncated: refine query or increase max_matches.")
        guidance.append("Identical searches do not advance.")
        result = {
            "artifact_path": str(path),
            "query": query,
            "total_chars": len(text),
            "matches": matches,
            "truncated": truncated,
            "repeated_search": repeated,
            "instructions": " ".join(guidance),
        }
        if repeated:
            result["diagnostic"] = "Repeated search of unchanged saved text: no new content; matches and context have not advanced."
        return result

    def _resolve(self, artifact_path: str) -> Path:
        path = Path(artifact_path)
        if not path.is_absolute():
            path = self.runs_dir / self.run_id / path
        path = path.resolve()
        run_root = (self.runs_dir / self.run_id).resolve()
        if run_root != path and run_root not in path.parents:
            raise ToolExecutionError("Artifact path must stay inside the current run directory.")
        if not path.exists() or not path.is_file():
            raise ToolExecutionError(f"Artifact not found: {artifact_path}")
        return path
