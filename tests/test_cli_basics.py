# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

import click
import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import main

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _walk_commands(command: click.Command, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    yield path, command
    if isinstance(command, click.Group):
        for name, subcommand in sorted(command.commands.items()):
            yield from _walk_commands(subcommand, (*path, name))


@pytest.mark.parametrize("flag", ["--version", "-V"])
@pytest.mark.parametrize("prog_name", ["fsq", "fsq-agent"])
def test_version_human_output(monkeypatch: pytest.MonkeyPatch, flag: str, prog_name: str) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main._distribution_version", lambda: "0.4.2")
    monkeypatch.setattr("fsq_agent.adapters.cli._main.require_initialized_workspace", lambda *a: pytest.fail("No Workspace required"))

    result = CliRunner().invoke(main, [flag], prog_name=prog_name)

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"{prog_name} 0.4.2"


@pytest.mark.parametrize("output_mode", ["json", "jsonl"])
@pytest.mark.parametrize(
    "argv_factory",
    [
        lambda mode: ["--output", mode, "--version"],
        lambda mode: [f"--output={mode}", "-V"],
        lambda mode: ["--version", "--output", mode],
    ],
)
def test_version_machine_output(monkeypatch: pytest.MonkeyPatch, output_mode: str, argv_factory) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main._distribution_version", lambda: "0.4.2")
    monkeypatch.setattr("fsq_agent.adapters.cli._main.require_initialized_workspace", lambda *a: pytest.fail("No Workspace required"))

    result = CliRunner().invoke(main, argv_factory(output_mode), prog_name="fsq")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == "fsq.machine/v1"
    assert record["type"] == "result"
    assert record["operation"] == "version"
    assert record["status"] == "success"
    assert record["result"] == {"version": "0.4.2"}


def test_version_falls_back_to_unknown_when_package_metadata_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(_name: str) -> str:
        raise PackageNotFoundError("fsq-agent")

    monkeypatch.setattr("importlib.metadata.version", _missing)

    human = CliRunner().invoke(main, ["--version"], prog_name="fsq")
    assert human.exit_code == 0, human.output
    assert human.output.strip() == "fsq unknown"

    machine = CliRunner().invoke(main, ["--output", "json", "--version"], prog_name="fsq")
    assert machine.exit_code == 0, machine.output
    record = json.loads(machine.output)
    assert record["operation"] == "version"
    assert record["status"] == "success"
    assert record["result"] == {"version": "unknown"}


def test_version_works_outside_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fsq_agent.adapters.cli._main._distribution_version", lambda: "0.4.2")

    result = CliRunner().invoke(main, ["--version"], prog_name="fsq")

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "fsq 0.4.2"


def test_subcommand_rejects_version_option() -> None:
    result = CliRunner().invoke(main, ["doctor", "--version"], prog_name="fsq")

    assert result.exit_code == 2


def test_short_and_long_help_work_on_every_command() -> None:
    runner = CliRunner()
    commands = list(_walk_commands(main))
    assert len(commands) > 1

    for path, _command in commands:
        short_result = runner.invoke(main, [*path, "-h"], prog_name="fsq")
        long_result = runner.invoke(main, [*path, "--help"], prog_name="fsq")

        assert short_result.exit_code == 0, (path, short_result.output)
        assert long_result.exit_code == 0, (path, long_result.output)
        assert short_result.output == long_result.output
        assert "-h, --help" in short_result.output


def test_all_public_commands_and_options_define_help_text() -> None:
    missing_command_help: list[str] = []
    missing_option_help: list[str] = []

    for path, command in _walk_commands(main):
        label = " ".join(("fsq", *path))
        if not (command.help and command.help.strip()):
            missing_command_help.append(label)
        missing_option_help.extend(f"{label} {'/'.join(param.opts)}" for param in command.params if isinstance(param, click.Option) and not (param.help and param.help.strip()))

    assert not missing_command_help, f"Commands missing help text: {missing_command_help}"
    assert not missing_option_help, f"Options missing help text: {missing_option_help}"
