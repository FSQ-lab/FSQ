# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re

import pytest
from pydantic import ValidationError

from fsq_agent.config import load_windows_application_catalogue
from fsq_agent.models import ConfigurationError, WindowsApplicationConfig


def record(**updates):
    return {
        "id": "edge",
        "name": "Microsoft Edge",
        "description": "Microsoft's web browser",
        "path_candidates": [r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
        "window_title_re": ".*Microsoft Edge.*",
        **updates,
    }


def test_bundled_catalogue_is_loadable_without_installed_apps():
    entries = load_windows_application_catalogue()
    assert [entry.id for entry in entries] == ["edge", "notepad", "chrome", "edge-beta", "edge-canary", "calculator"]
    assert len(entries[0].path_candidates) == 2
    assert "ProgramFiles" in entries[0].path_candidates[0]
    assert "ProgramFiles(x86)" in entries[0].path_candidates[1]
    assert "SystemRoot" in entries[1].path_candidates[0]
    assert all(entry.window_title_re for entry in entries)


@pytest.mark.parametrize(
    ("app_id", "name", "paths", "window_title", "other_title"),
    [
        (
            "chrome",
            "Google Chrome",
            [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"],
            "Google - Google Chrome",
            "Google - Microsoft Edge",
        ),
        (
            "edge-beta",
            "Microsoft Edge Beta",
            [
                r"%ProgramFiles(x86)%\Microsoft\Edge Beta\Application\msedge.exe",
                r"%LOCALAPPDATA%\Microsoft\Edge Beta\Application\msedge.exe",
            ],
            "Google - Microsoft Edge Beta",
            "Google - Microsoft Edge Canary",
        ),
        (
            "edge-canary",
            "Microsoft Edge Canary",
            [r"%LOCALAPPDATA%\Microsoft\Edge SxS\Application\msedge.exe"],
            "Google - Microsoft Edge Canary",
            "Google - Microsoft Edge Beta",
        ),
    ],
)
def test_bundled_browser_entries(app_id, name, paths, window_title, other_title):
    entry = next(entry for entry in load_windows_application_catalogue() if entry.id == app_id)
    assert entry.name == name
    assert entry.description.strip()
    assert list(entry.path_candidates) == paths
    assert re.match(entry.window_title_re, window_title)
    assert not re.match(entry.window_title_re, other_title)
    assert set(entry.model_dump()) == {"id", "name", "description", "path_candidates", "window_title_re"}


def test_bundled_calculator_entry():
    entry = next(entry for entry in load_windows_application_catalogue() if entry.id == "calculator")
    assert entry.name == "Calculator"
    assert entry.description.strip()
    assert list(entry.path_candidates) == [r"%SystemRoot%\System32\calc.exe"]
    assert re.match(entry.window_title_re, "Calculator")
    assert re.match(entry.window_title_re, "计算器")
    assert not re.match(entry.window_title_re, "Untitled - Notepad")
    assert set(entry.model_dump()) == {"id", "name", "description", "path_candidates", "window_title_re"}


@pytest.mark.parametrize(
    "updates",
    [
        {"id": ""},
        {"name": " "},
        {"description": ""},
        {"path_candidates": []},
        {"path_candidates": [" "]},
        {"path_candidates": ["a"] * 9},
        {"path_candidates": "not-a-list"},
        {"window_title_re": "["},
        {"window_title_re": ""},
        {"extra": "unsupported"},
    ],
)
def test_catalogue_record_rejects_invalid_values(updates):
    with pytest.raises(ValidationError):
        WindowsApplicationConfig.model_validate(record(**updates))


@pytest.mark.parametrize(
    "source",
    [
        "",
        "[]",
        "edge: {}",
        "- id: edge\n  id: notepad",
        "- id: edge\n  extra: unknown",
        "- &entry {id: edge, name: Edge, description: Browser, path_candidates: [a.exe], window_title_re: Edge}\n- *entry",
    ],
)
def test_loader_rejects_malformed_or_duplicate_catalogue(monkeypatch, source):
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: source)
    with pytest.raises(ConfigurationError, match="catalogue"):
        load_windows_application_catalogue()


def test_missing_catalogue_is_not_empty_fallback(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("pathlib.Path.read_text", missing)
    with pytest.raises(ConfigurationError, match="catalogue"):
        load_windows_application_catalogue()
