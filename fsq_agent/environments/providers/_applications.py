# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import platform
import plistlib
import re
import time
from pathlib import Path

from fsq_agent.environments.providers._adb_transport import inventory, package_inventory, package_status, valid_package
from fsq_agent.environments.providers._web import web_candidate_paths
from fsq_agent.models import ApplicationCandidate, ApplicationLookupHint, web_executable_matches_channel

_LIMIT = 2048
_PLIST_LIMIT = 262144
_BUNDLE_ID = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")


def _match(name: str, *values: str) -> bool:
    def normalize(value):
        return "".join(char for char in value.casefold() if char.isalnum())

    query = normalize(name)
    return bool(query) and any(query in normalize(value) for value in values)


def _budget(deadline: float, count: int) -> None:
    if time.monotonic() > deadline:
        raise TimeoutError
    if count > _LIMIT:
        raise ValueError("Discovery limit exceeded.")


def discover_candidates(selected: str, name: str, *, explicit_reference: str | None, target_id: str | None, inferred_hint: ApplicationLookupHint | None = None) -> list[ApplicationCandidate]:
    deadline = time.monotonic() + 5
    # The public boundary retains provenance. Only the concrete validation
    # scope is shared here; an inferred reference is never relabeled explicit.
    reference = inferred_hint.value if inferred_hint is not None else explicit_reference
    if selected == "macos" and inferred_hint is not None:
        if inferred_hint.kind == "bundle_id" and not _BUNDLE_ID.fullmatch(inferred_hint.value):
            raise ValueError("Invalid bundle reference.")
        if inferred_hint.kind == "app_path" and not Path(inferred_hint.value).expanduser().is_absolute():
            raise ValueError("An absolute application path is required.")
    candidates = _discover(selected, name, reference, target_id, deadline)
    _budget(deadline, len(candidates))
    return candidates


def _discover(selected: str, name: str, reference: str | None, target_id: str | None, deadline: float) -> list[ApplicationCandidate]:
    if selected == "android":
        if not target_id or not any(device.serial == target_id and device.state == "device" for device in inventory(deadline)):
            raise ValueError("Selected device is unavailable.")
        if reference is not None:
            if not valid_package(reference):
                raise ValueError("Invalid package reference.")
            _budget(deadline, 0)
            status = package_status(target_id, reference, timeout_seconds=max(0.01, deadline - time.monotonic()))
            if status == "installed":
                return [ApplicationCandidate(identity=reference, label=reference, app_id=reference)]
            if status == "absent":
                return []
            if status == "timeout":
                raise TimeoutError
            raise ValueError("Package validation failed.")
        packages = package_inventory(target_id, deadline)
        _budget(deadline, len(packages))
        return [ApplicationCandidate(identity=package, label=package, app_id=package) for package in sorted(packages) if _match(name, package)]
    if selected == "web":
        if not target_id:
            raise ValueError("Browser selection is required.")
        return [
            ApplicationCandidate(identity=str(path.resolve()), label=target_id, app_path=path.resolve())
            for path in web_candidate_paths(target_id)
            if path.is_file() and web_executable_matches_channel(target_id, path)
        ]
    if selected == "macos" and platform.system() == "Darwin":
        return _macos(name, reference, deadline)
    if selected == "windows" and platform.system() == "Windows":
        return _windows(name, reference, deadline)
    raise ValueError("Unsupported host.")


def _mac_candidate(path: Path) -> ApplicationCandidate:
    path = path.expanduser().resolve(strict=True)
    if path.is_file():
        if not os.access(path, os.X_OK):
            raise ValueError("Not executable.")
        bundle = next((parent for parent in path.parents if parent.suffix.casefold() == ".app"), None)
        if bundle is None:
            return ApplicationCandidate(identity=str(path), label=path.name, app_path=path)
        path = bundle
    if not path.is_dir() or path.suffix.casefold() != ".app":
        raise ValueError("Not an application.")
    with (path / "Contents" / "Info.plist").open("rb") as stream:
        raw = stream.read(_PLIST_LIMIT + 1)
    if len(raw) > _PLIST_LIMIT:
        raise ValueError("Metadata limit exceeded.")
    metadata = plistlib.loads(raw)
    if not isinstance(metadata, dict):
        raise TypeError("Invalid application metadata.")
    bundle_id = metadata.get("CFBundleIdentifier")
    label = metadata.get("CFBundleDisplayName") or metadata.get("CFBundleName") or path.stem
    if ("CFBundleIdentifier" in metadata and (not isinstance(bundle_id, str) or not _BUNDLE_ID.fullmatch(bundle_id))) or not isinstance(label, str):
        raise ValueError("Invalid application metadata.")
    return ApplicationCandidate(identity=str(path), label=label, app_path=path, bundle_id=bundle_id)


def _macos(name: str, reference: str | None, deadline: float) -> list[ApplicationCandidate]:
    if reference and (reference.startswith(("/", "~/"))):
        path = Path(reference).expanduser()
        return [_mac_candidate(path)] if path.exists() else []
    if reference is not None and not _BUNDLE_ID.fullmatch(reference):
        raise ValueError("Invalid bundle reference.")
    candidates = []
    count = 0
    for root in _macos_roots():
        if not root.exists():
            continue
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    _budget(deadline, count)
                    path = Path(entry.path)
                    if path.suffix.casefold() == ".app":
                        candidate = _mac_candidate(path)
                        if candidate.bundle_id == reference if reference else _match(name, candidate.label, path.stem, candidate.bundle_id or ""):
                            candidates.append(candidate)
                    elif depth < 1 and entry.is_dir(follow_symlinks=False):
                        pending.append((path, depth + 1))
    return candidates


def _macos_roots() -> tuple[Path, ...]:
    return (Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications")


def _windows(name: str, reference: str | None, deadline: float) -> list[ApplicationCandidate]:
    if reference:
        path = Path(reference)
        if not path.is_absolute() or path.suffix.casefold() != ".exe":
            raise ValueError("An absolute executable is required.")
        return [_windows_candidate(path)] if path.is_file() else []
    # App Paths is bounded installed executable metadata, not a filesystem scan.
    import winreg

    candidates = []
    count = 0
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                root = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with root:
                keys = winreg.QueryInfoKey(root)[0]
                for index in range(keys):
                    count += 1
                    _budget(deadline, count)
                    key_name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, key_name) as key:
                        raw, kind = winreg.QueryValueEx(key, "")
                    if kind != winreg.REG_SZ or not isinstance(raw, str) or len(raw) > 4096:
                        raise ValueError("Unsupported application metadata.")
                    path = Path(raw.strip().strip('"'))
                    if _match(name, key_name, path.stem):
                        candidates.append(_windows_candidate(path))
    return candidates


def _windows_candidate(path: Path) -> ApplicationCandidate:
    if not path.is_absolute() or path.suffix.casefold() != ".exe" or not path.is_file():
        raise ValueError("Application executable is unavailable.")
    path = path.resolve(strict=True)
    return ApplicationCandidate(identity=os.path.normcase(str(path)), label=path.stem, app_path=path)
