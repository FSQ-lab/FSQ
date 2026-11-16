# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
from pathlib import Path

import pytest

from fsq_agent.agent_engine import ToolCall, ToolInputFailure
from fsq_agent.models import (
    AgentToolCall,
    LocalToolOutputSettings,
    RunEvent,
    RuntimeSecretSettings,
    ToolExecutionError,
)
from fsq_agent.tools import AgentToolAdapter, AgentToolExecutor, AgentToolRegistry, DefaultAgentToolProvider, FileOps, ToolArtifactStore


def _provider(tmp_path: Path, **kwargs) -> DefaultAgentToolProvider:
    return DefaultAgentToolProvider(FileOps(tmp_path), **kwargs)


def _registry(provider: DefaultAgentToolProvider) -> AgentToolRegistry:
    return AgentToolRegistry.from_providers([provider])


@pytest.mark.asyncio
async def test_agent_tool_file_ops_are_scoped(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    executor = AgentToolExecutor(_registry(provider))

    result = await executor.execute(AgentToolCall(tool_name="write_file", arguments={"path": "nested/out.txt", "content": "hello"}))

    assert result.status == "success"
    assert (tmp_path / "nested" / "out.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_bootstrap_provider_forwards_workspace_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent._capability_bootstrap import build_agent_tool_provider

    workspace = tmp_path / "W"
    cases = workspace / "cases" / "android"
    cases.mkdir(parents=True)
    (cases / "076.fsq.yaml").write_text("bootstrap case", encoding="utf-8")
    provider = build_agent_tool_provider(workspace_base=workspace, read_roots=[cases], write_root=workspace / ".fsq" / "runs" / "android" / "run-1" / "artifacts")
    adapter = AgentToolAdapter(_registry(provider))
    read_tool = next(tool for tool in adapter.build_tools() if tool.name == "read_file")
    monkeypatch.chdir(tmp_path)

    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "cases/android/076.fsq.yaml"}, call_id="workspace-read"))

    assert json.loads(output)["result"]["output"]["output"] == "bootstrap case"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["034.fsq.yaml", "076.fsq.yaml"])
async def test_file_reads_use_workspace_paths_after_cwd_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    workspace = tmp_path / "W"
    cases = workspace / "cases" / "android"
    cases.mkdir(parents=True)
    target = cases / filename
    target.write_text("canonical case", encoding="utf-8")
    file_ops = FileOps(workspace_base=workspace, read_roots=[cases], write_root=workspace / ".fsq" / "runs" / "android" / "run-1" / "artifacts")
    monkeypatch.chdir(tmp_path)

    relative = await file_ops.read_text({"path": f"cases/android/{filename}"})
    absolute = await file_ops.read_text({"path": str(target)})

    assert relative.status == absolute.status == "success"
    assert relative.output == absolute.output == "canonical case"


@pytest.mark.asyncio
async def test_file_reads_do_not_search_roots_or_invent_missing_prefixes(tmp_path: Path) -> None:
    workspace = tmp_path / "W"
    cases = workspace / "cases" / "android"
    knowledge = workspace / "knowledge" / "android"
    for root, content in [(cases, "case copy"), (knowledge, "knowledge copy")]:
        root.mkdir(parents=True)
        (root / "shared.md").write_text(content, encoding="utf-8")
    file_ops = FileOps(workspace_base=workspace, read_roots=[knowledge, cases])

    with pytest.raises(ToolExecutionError, match="outside the allowed read roots"):
        await file_ops.read_text({"path": "shared.md"})
    assert (await file_ops.read_text({"path": "cases/android/shared.md"})).output == "case copy"
    assert (await file_ops.read_text({"path": "knowledge/android/shared.md"})).output == "knowledge copy"
    missing = await file_ops.read_text({"path": "cases/android/missing.yaml"})
    assert missing.status == "failed"
    assert str(cases / "missing.yaml") in missing.error
    assert str(knowledge / "cases") not in missing.error


@pytest.mark.asyncio
async def test_file_read_base_does_not_authorize_config_traversal_or_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "W"
    cases = workspace / "cases" / "android"
    cases.mkdir(parents=True)
    config = workspace / ".fsq" / "config" / "config.android.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("private configuration", encoding="utf-8")
    (workspace / "root.txt").write_text("private root", encoding="utf-8")
    (cases / "escape.yaml").symlink_to(config)
    file_ops = FileOps(workspace_base=workspace, read_roots=[cases], write_root=cases / "output")

    for path in ["root.txt", ".fsq/config/config.android.yaml", "cases/android/../../root.txt", "cases/android/escape.yaml", str(config)]:
        with pytest.raises(ToolExecutionError, match="outside the allowed read roots"):
            await file_ops.read_text({"path": path})
    with pytest.raises(ToolExecutionError, match="outside the allowed write root"):
        await file_ops.write_text({"path": "../unauthorized.txt", "content": "blocked"})


@pytest.mark.asyncio
async def test_file_default_base_is_cwd_not_first_read_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cases = tmp_path / "cases" / "android"
    cases.mkdir(parents=True)
    (cases / "034.fsq.yaml").write_text("case", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    file_ops = FileOps(read_roots=[cases])
    monkeypatch.chdir(cases)

    assert (await file_ops.read_text({"path": "cases/android/034.fsq.yaml"})).output == "case"
    with pytest.raises(ToolExecutionError, match="outside the allowed read roots"):
        await file_ops.read_text({"path": "034.fsq.yaml"})
    assert (await FileOps(root=cases).read_text({"path": "034.fsq.yaml"})).output == "case"


def test_artifact_paths_remain_run_relative_and_reject_wrong_run_ids(tmp_path: Path) -> None:
    runs_dir = tmp_path / "W" / ".fsq" / "runs" / "android"
    store = ToolArtifactStore(runs_dir, "069_correctID", LocalToolOutputSettings())
    artifact = store.write("ui_snapshot", "saved observation")
    assert artifact is not None
    relative = str(artifact.relative_to(runs_dir / "069_correctID"))
    for path in [str(artifact), relative]:
        assert store.read_slice(path, 0, 1000)["content"] == store.read_text(str(artifact))
        assert len(store.search(path, "saved observation", True, 1, 10)["matches"]) == 1
    wrong = runs_dir / "069_wrongID" / relative
    wrong.parent.mkdir(parents=True)
    wrong.write_text("other run", encoding="utf-8")
    escaped = runs_dir / "069_correctID" / "escape.json"
    escaped.symlink_to(wrong)
    for path in [str(wrong), f"../069_wrongID/{relative}", str(escaped)]:
        with pytest.raises(ToolExecutionError, match="current run directory"):
            store.read_slice(path, 0, 100)
        with pytest.raises(ToolExecutionError, match="current run directory"):
            store.search(path, "other", True, 1, 10)


@pytest.mark.asyncio
async def test_agent_tool_file_writes_are_scoped_to_active_run(tmp_path: Path) -> None:
    runs_dir = tmp_path / "cases"
    provider = _provider(tmp_path / "shared-artifacts", runs_dir=runs_dir)
    provider.configure_run("run-1")

    result = await provider.invoke(AgentToolCall(tool_name="write_file", arguments={"path": "notes.txt", "content": "run local"}))

    assert result.status == "success"
    assert (runs_dir / "run-1" / "artifacts" / "notes.txt").read_text(encoding="utf-8") == "run local"
    assert not (tmp_path / "shared-artifacts" / "notes.txt").exists()


def test_agent_tool_registry_lists_only_dynamic_tools(tmp_path: Path) -> None:
    names = {definition.name for definition in _registry(_provider(tmp_path)).list_tools()}

    assert names == {
        "read_file",
        "write_file",
        "search_artifact",
        "read_artifact_slice",
    }
    assert "get_runtime_secret" not in names
    assert "wait_ms" not in names
    assert "run_cli_tool" not in names
    assert "submit_visual_assertion" not in names
    assert "publish_progress" not in names
    assert "shell" not in names


def test_common_tool_compatibility_aliases_are_not_public() -> None:
    from fsq_agent import models, tools

    for name in ("CommonToolProvider", "CommonToolRegistry", "CommonToolExecutor", "DefaultCommonToolProvider", "AgentsCommonToolAdapter"):
        assert name not in tools.__all__
        assert not hasattr(tools, name)
    for name in ("CommonToolCall", "CommonToolResult"):
        assert name not in models.__all__
        assert not hasattr(models, name)


def test_agent_tool_adapter_passes_tool_strictness(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    adapter = AgentToolAdapter(_registry(provider))

    tools = adapter.build_tools()

    assert {tool.name: tool.strict for tool in tools} == {
        "read_file": True,
        "write_file": True,
        "search_artifact": True,
        "read_artifact_slice": True,
    }


async def test_invalid_agent_tool_input_uses_existing_failure_result(tmp_path: Path) -> None:
    adapter = AgentToolAdapter(_registry(_provider(tmp_path)))
    tool = next(binding for binding in adapter.build_tools() if binding.name == "write_file")
    response = json.loads(await tool.on_invalid_input(ToolInputFailure(name=tool.name, call_id="invalid-write", message="Tool arguments must be a JSON object.")))
    assert response["tool_name"] == "write_file"
    assert response["status"] == "failed"
    assert response["result"]["status"] == "failed"
    assert response["result"]["error"] == "Tool arguments must be a JSON object."
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.asyncio
async def test_direct_harness_execution_is_not_common_tool_owned(tmp_path: Path) -> None:
    executor = AgentToolExecutor(_registry(_provider(tmp_path)))

    with pytest.raises(ToolExecutionError, match="Unknown AgentTool"):
        await executor.execute(AgentToolCall(tool_name="tap_on", arguments={}))


@pytest.mark.asyncio
async def test_agent_tool_adapter_emits_agent_tool_origin_without_replay(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    events: list[RunEvent] = []
    adapter = AgentToolAdapter(_registry(_provider(tmp_path)))
    read_tool = next(tool for tool in adapter.build_tools(run_id="run-1", task_id="task-1", event_sink=events.append) if tool.name == "read_file")

    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "file.txt"}, call_id="call_read"))

    payload = json.loads(output)
    assert payload["result"]["output"]["output"] == "hello"
    assert events[-1].payload["tool_origin"] == "agent_tool"
    assert events[-1].payload["executor_kind"] == "agent_tool"
    assert "replay" not in events[-1].payload


@pytest.mark.asyncio
async def test_agent_tool_writes_full_output_artifact_and_returns_inline(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    provider = _provider(
        tmp_path,
        local_tool_output_settings=LocalToolOutputSettings(full_output_max_chars=1000),
        runs_dir=runs_dir,
        run_id="run-1",
    )
    adapter = AgentToolAdapter(_registry(provider), local_tool_output_settings=LocalToolOutputSettings(full_output_max_chars=1000))
    read_target = tmp_path / "file.txt"
    read_target.write_text("hello", encoding="utf-8")
    read_tool = next(tool for tool in adapter.build_tools(run_id="run-1", task_id="task-1") if tool.name == "read_file")

    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "file.txt"}, call_id="call_read"))

    payload = json.loads(output)
    artifact_path = Path(payload["artifact"]["path"])
    artifact_text = await asyncio.to_thread(artifact_path.read_text, encoding="utf-8")
    artifact = json.loads(artifact_text)
    assert payload["model_output"] == "full"
    assert payload["result"]["output"]["output"] == "hello"
    assert artifact["metadata"] == {"path": "file.txt"}
    assert '"output": "hello"' in artifact["content"]


@pytest.mark.asyncio
async def test_agent_tool_large_output_uses_artifact_search_and_slice(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    settings = LocalToolOutputSettings(
        full_output_max_chars=3000,
        historical_preview_chars=20,
        model_response_max_chars=500,
    )
    provider = _provider(tmp_path, local_tool_output_settings=settings, runs_dir=runs_dir, run_id="run-1")
    adapter = AgentToolAdapter(_registry(provider), local_tool_output_settings=settings)
    (tmp_path / "large.txt").write_text("alpha beta gamma " * 1000, encoding="utf-8")
    tools = adapter.build_tools(run_id="run-1", task_id="task-1")
    read_tool = next(tool for tool in tools if tool.name == "read_file")
    search_tool = next(tool for tool in tools if tool.name == "search_artifact")
    slice_tool = next(tool for tool in tools if tool.name == "read_artifact_slice")

    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "large.txt"}, call_id="call_read"))
    payload = json.loads(output)

    assert payload["model_output"] == "artifact_reference"
    assert "result" not in payload
    search_output = await search_tool.invoke(
        ToolCall(name="search_artifact", arguments={"artifact_path": payload["artifact"]["path"], "query": "gamma", "max_matches": 1, "context_chars": 20}, call_id="call_search")
    )
    search_payload = json.loads(search_output)
    assert search_payload["result"]["output"]["matches"][0]["offset"] >= 0

    slice_output = await slice_tool.invoke(
        ToolCall(
            name="read_artifact_slice",
            call_id="call_slice",
            arguments={
                "artifact_path": payload["artifact"]["path"],
                "offset": search_payload["result"]["output"]["matches"][0]["offset"],
                "length": 30,
            },
        ),
    )
    assert "gamma" in json.loads(slice_output)["result"]["output"]["content"]


def test_artifact_search_changed_content_between_queries_resets_prior_windows(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "field before")
    assert path is not None
    saved_text = path.read_text(encoding="utf-8")
    first = store.search(str(path), "field", False, max_matches=1, context_chars=5)
    path.write_text(saved_text.replace("before", "after!"), encoding="utf-8")
    changed_query = store.search(str(path), "after!", False, max_matches=1, context_chars=5)
    path.write_text(saved_text, encoding="utf-8")
    restored = store.search(str(path), "field", False, max_matches=1, context_chars=5)

    assert first["repeated_search"] is False
    assert changed_query["repeated_search"] is False
    assert restored["repeated_search"] is False
    assert restored["matches"] == first["matches"]


@pytest.mark.parametrize(
    ("content", "query", "case_sensitive"),
    [
        ('İ 前缀 😀 "Field": "line\\nvalue\\\\tail"', "field", False),
        ("😀 İ dotted and İ", "İ", False),
        ("😀 field field", "field", True),
        ("literal .* [field] .*", ".*", False),
        ('😀 "field" line', r"\"field\"", True),
    ],
)
def test_artifact_search_offsets_address_saved_serialized_text(tmp_path: Path, content: str, query: str, case_sensitive: bool) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", content)
    assert path is not None
    saved_text = path.read_text(encoding="utf-8")
    result = store.search(str(path), query, case_sensitive, max_matches=1, context_chars=7)

    expected_offset = saved_text.index(query if case_sensitive else query.replace("field", "Field"))
    match = result["matches"][0]
    assert match["offset"] == expected_offset
    assert match["context_start"] == max(0, expected_offset - 7)
    assert match["context_end"] == min(len(saved_text), expected_offset + len(query) + 7)
    assert match["preview"] == saved_text[match["context_start"] : match["context_end"]]
    assert result["total_chars"] == len(saved_text)
    assert store.read_slice(str(path), match["offset"], len(query))["content"] == saved_text[expected_offset : expected_offset + len(query)]


@pytest.mark.parametrize(
    ("content", "query", "source_match"),
    [
        ("😀 İ separated İ", "İ", "İ"),
        ("😀 İ separated İ", "i\u0307", "İ"),
        ("😀 i\u0307 separated i\u0307", "İ", "i\u0307"),
        ("İ Field middle İ Field", "field", "Field"),
    ],
)
def test_artifact_search_multiple_matches_preserve_unicode_source_spans(tmp_path: Path, content: str, query: str, source_match: str) -> None:
    store = ToolArtifactStore(tmp_path, "unicode-run", LocalToolOutputSettings())
    path = store.write("snapshot", content)
    assert path is not None
    saved_text = path.read_text(encoding="utf-8")
    first = saved_text.index(source_match)
    second = saved_text.index(source_match, first + len(source_match))
    result = store.search(str(path), query, False, max_matches=2, context_chars=7)
    assert result["truncated"] is False
    assert [match["offset"] for match in result["matches"]] == [first, second]
    for match, offset in zip(result["matches"], (first, second), strict=True):
        start, end = max(0, offset - 7), min(len(saved_text), offset + len(source_match) + 7)
        assert (match["context_start"], match["context_end"]) == (start, end)
        assert match["preview"] == saved_text[start:end]
        assert store.read_slice(str(path), offset, len(source_match))["content"] == source_match


def test_artifact_search_guides_field_and_context_recovery(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", '"field": "' + "value " * 40 + '" field')
    assert path is not None
    result = store.search(str(path), "field", True, max_matches=1, context_chars=8)
    match = result["matches"][0]

    assert result["truncated"] is True
    assert f"read_artifact_slice(artifact_path, offset={match['offset']}, length=12000)" in result["instructions"]
    assert f"read_artifact_slice(artifact_path, offset={match['context_end']}, length=12000)" in result["instructions"]
    assert "refine" in result["instructions"].lower()
    assert "max_matches" in result["instructions"]
    assert "identical" in result["instructions"].lower()
    recovered = store.read_slice(str(path), match["offset"], 12000)
    assert recovered["content"].startswith('field\\":')


def test_artifact_search_repeat_preserves_matches_across_resolved_paths(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "field value " * 10)
    assert path is not None
    saved_text = path.read_text(encoding="utf-8")
    first = store.search(str(path), "field", False, max_matches=1, context_chars=5)
    repeated = store.search(str(path.relative_to(tmp_path / "run-1")), "field", False, max_matches=1, context_chars=5)

    assert first["repeated_search"] is False
    assert repeated["repeated_search"] is True
    assert repeated["matches"] == first["matches"]
    assert repeated["truncated"] == first["truncated"]
    assert repeated["artifact_path"] == first["artifact_path"]
    assert "no new content" in repeated["diagnostic"].lower()
    assert "read_artifact_slice" in repeated["instructions"]
    assert path.read_text(encoding="utf-8") == saved_text


def test_artifact_search_recognizes_six_interleaved_repeats(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    paths = [store.write("snapshot", f"field value {index}") for index in range(6)]
    assert all(path is not None for path in paths)
    first_results = [store.search(str(path), "field", False, max_matches=1, context_chars=4) for path in paths]
    for path, first in zip(paths, first_results, strict=True):
        repeated = store.search(str(path), "field", False, max_matches=1, context_chars=4)
        assert first["repeated_search"] is False
        assert repeated["repeated_search"] is True
        assert repeated["matches"] == first["matches"]
        assert "no new content" in repeated["diagnostic"].lower()


@pytest.mark.parametrize(
    "changed_arguments",
    [
        {"query": "FIELD"},
        {"case_sensitive": True},
        {"max_matches": 2},
        {"context_chars": 6},
    ],
)
def test_artifact_search_different_parameters_are_not_repeats(tmp_path: Path, changed_arguments: dict) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "field value field")
    assert path is not None
    arguments = {"query": "field", "case_sensitive": False, "max_matches": 1, "context_chars": 5}
    first = store.search(str(path), **arguments)
    changed = store.search(str(path), **(arguments | changed_arguments))
    repeated = store.search(str(path), **arguments)

    assert first["repeated_search"] is False
    assert changed["repeated_search"] is False
    assert repeated["repeated_search"] is True


def test_artifact_search_content_change_is_not_an_unchanged_repeat(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "field before")
    assert path is not None
    store.search(str(path), "field", False, max_matches=1, context_chars=20)
    path.write_text(path.read_text(encoding="utf-8").replace("before", "after!"), encoding="utf-8")
    changed = store.search(str(path), "field", False, max_matches=1, context_chars=20)
    repeated = store.search(str(path), "field", False, max_matches=1, context_chars=20)

    assert changed["repeated_search"] is False
    assert "after!" in changed["matches"][0]["preview"]
    assert repeated["repeated_search"] is True
    assert repeated["matches"] == changed["matches"]


@pytest.mark.asyncio
async def test_artifact_search_run_history_is_isolated_in_neutral_bindings(tmp_path: Path) -> None:
    provider = _provider(tmp_path, runs_dir=tmp_path / "runs", run_id="run-1")
    adapter = AgentToolAdapter(_registry(provider))
    for run_id in ("run-1", "run-2"):
        provider.configure_run(run_id)
        assert provider.artifact_store is not None
        path = provider.artifact_store.write("snapshot", "field value")
        assert path is not None
        tool = next(tool for tool in adapter.build_tools(run_id=run_id, task_id="task-1") if tool.name == "search_artifact")
        call = ToolCall(name="search_artifact", arguments={"artifact_path": str(path), "query": "field", "max_matches": 1, "context_chars": 4}, call_id="search")
        first = json.loads(await tool.invoke(call))["result"]["output"]
        repeated = json.loads(await tool.invoke(call))["result"]["output"]
        assert first["repeated_search"] is False
        assert repeated["repeated_search"] is True
        assert repeated["matches"] == first["matches"]
        assert "no new content" in repeated["diagnostic"].lower()


def test_repeated_artifact_search_without_matches_guides_refinement(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "field value")
    assert path is not None
    first = store.search(str(path), "missing", False, max_matches=1, context_chars=5)
    repeated = store.search(str(path), "missing", False, max_matches=1, context_chars=5)

    assert first["matches"] == repeated["matches"] == []
    assert repeated["truncated"] is False
    assert repeated["repeated_search"] is True
    assert "no new content" in repeated["diagnostic"].lower()
    assert "refine" in repeated["instructions"].lower()


def test_artifact_slice_only_guides_continuation_before_end(tmp_path: Path) -> None:
    store = ToolArtifactStore(tmp_path, "run-1", LocalToolOutputSettings())
    path = store.write("snapshot", "😀 field value")
    assert path is not None
    first = store.read_slice(str(path), 0, 7)
    assert first["has_more_after"] is True
    assert f"read_artifact_slice(artifact_path, offset={first['offset'] + first['length']}, length=12000)" in first["instructions"]
    terminal = store.read_slice(str(path), first["offset"] + first["length"], 30000)
    assert terminal["has_more_after"] is False
    assert "read_artifact_slice(" not in terminal["instructions"]
    assert first["content"] + terminal["content"] == path.read_text(encoding="utf-8")
    empty = store.read_slice(str(path), terminal["total_chars"], 1)
    assert empty["length"] == 0
    assert empty["has_more_after"] is False
    assert "read_artifact_slice(" not in empty["instructions"]
    search = store.search(str(path), "field", True, max_matches=1, context_chars=2000)
    assert search["matches"][0]["context_end"] == search["total_chars"]
    assert search["instructions"].count("read_artifact_slice(") == 1


@pytest.mark.parametrize("content_length", [20, 40000])
@pytest.mark.parametrize("private_value", ["canary-92837", 'canary-"92837"\\secret\nvalue'])
async def test_file_helpers_redact_configured_values_before_output_artifacting(tmp_path: Path, content_length: int, private_value: str) -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})
    settings = LocalToolOutputSettings(full_output_max_chars=1000)
    provider = _provider(tmp_path, runtime_secret_settings=runtime_secrets, local_tool_output_settings=settings, runs_dir=tmp_path / "runs", run_id="run-1")
    adapter = AgentToolAdapter(_registry(provider), local_tool_output_settings=settings)
    content = f"Visible {private_value} " + "x" * content_length + " TAIL"
    source = tmp_path / "input.txt"
    source.write_text(content, encoding="utf-8")
    read_tool = next(tool for tool in adapter.build_tools(run_id="run-1") if tool.name == "read_file")

    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "input.txt"}, call_id="secret-read"))

    assert "canary-" not in output
    payload = json.loads(output)
    expected = content.replace(private_value, "***")
    if payload["model_output"] == "full":
        assert payload["result"]["output"]["output"] == expected
    artifact_text = await asyncio.to_thread(Path(payload["artifact"]["path"]).read_text, encoding="utf-8")
    assert "canary-" not in artifact_text
    assert json.loads(json.loads(artifact_text)["content"])["output"] == expected
    assert source.read_text(encoding="utf-8") == content
