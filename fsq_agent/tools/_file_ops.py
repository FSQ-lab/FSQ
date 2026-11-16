# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import time
from pathlib import Path
from typing import Any

from fsq_agent.models import ToolExecutionError, ToolResult


class FileOps:
    def __init__(
        self,
        root: Path | None = None,
        *,
        workspace_base: Path | None = None,
        read_roots: list[Path] | None = None,
        write_root: Path | None = None,
    ) -> None:
        default_root = (root or Path.cwd()).resolve()
        self.workspace_base = (workspace_base or default_root).resolve()
        self.read_roots = [path.resolve() for path in (read_roots or [default_root])]
        self.write_root = (write_root or default_root).resolve()

    def configure_write_root(self, write_root: Path) -> None:
        self.write_root = write_root.resolve()

    def _is_inside(self, path: Path, root: Path) -> bool:
        return root in (path, *path.parents)

    def _resolve_read(self, path_value: object) -> Path:
        if not isinstance(path_value, str):
            raise ToolExecutionError("File path must be a string.")
        raw_path = Path(path_value)
        path = (raw_path if raw_path.is_absolute() else self.workspace_base / raw_path).resolve()
        if not any(self._is_inside(path, root) for root in self.read_roots):
            raise ToolExecutionError("File path is outside the allowed read roots.", context={"path": str(path)})
        return path

    def _resolve_write(self, path_value: object) -> Path:
        if not isinstance(path_value, str):
            raise ToolExecutionError("File path must be a string.")
        raw_path = Path(path_value)
        path = raw_path.expanduser().resolve() if raw_path.is_absolute() else (self.write_root / raw_path).resolve()
        if not self._is_inside(path, self.write_root):
            raise ToolExecutionError("File path is outside the allowed write root.", context={"path": str(path)})
        return path

    async def read_text(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        path = self._resolve_read(arguments.get("path"))
        try:
            content = path.read_text(encoding=str(arguments.get("encoding", "utf-8")))
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(tool_name="file.read", status="failed", error=str(exc), duration_ms=duration_ms)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(tool_name="file.read", status="success", output=content, duration_ms=duration_ms)

    async def write_text(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        path = self._resolve_write(arguments.get("path"))
        content = arguments.get("content", "")
        if not isinstance(content, str):
            raise ToolExecutionError("File content must be a string.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=str(arguments.get("encoding", "utf-8")))
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(tool_name="file.write", status="failed", error=str(exc), duration_ms=duration_ms)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(tool_name="file.write", status="success", output=str(path), duration_ms=duration_ms)
