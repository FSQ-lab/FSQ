# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import platform
import plistlib
import re
import struct
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fsq_agent.environments.providers._adb_transport import inventory, package_inventory, package_status, third_party_package_inventory, valid_package
from fsq_agent.environments.providers._ios import _ios_application_inventory, _lookup_ios_application
from fsq_agent.environments.providers._web import web_candidate_paths
from fsq_agent.models import ApplicationCandidate, ApplicationLookupHint, web_executable_matches_channel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsq_agent.models import WindowsApplicationConfig
_LIMIT = 2048
_INVENTORY_LIMIT = 512
_PLIST_LIMIT = 262144
_LNK_LIMIT = 262144
_BUNDLE_ID = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
_STRINGS_ENTRY = re.compile(r'"(?P<key>CFBundleDisplayName|CFBundleName)"\s*=\s*"(?P<value>(?:\\.|[^"\\])*)"\s*;')
_ANDROID_COMMON_SYSTEM = (
    "com.android.vending",
    "com.android.settings",
    "com.android.chrome",
    "com.android.calculator2",
    "com.google.android.calculator",
    "com.android.deskclock",
    "com.android.contacts",
    "com.android.mms",
    "com.android.apps.messaging",
    "com.android.camera",
    "com.android.camera2",
    "com.android.GoogleCamera",
    "com.android.gallery3d",
    "com.android.apps.photos",
    "com.android.documentsui",
    "com.android.packageinstaller",
    "com.android.calendar",
    "com.android.dialer",
    "com.android.gm",
    "com.android.apps.maps",
    "com.android.youtube",
)
_LNK_MAGIC = b"\x4c\x00\x00\x00"
_LNK_CLSID = b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"


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
    if selected == "ios":
        candidates = _lookup_ios_application(target_id, reference, deadline)
        _budget(deadline, len(candidates))
        return candidates
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


def _safe_alias(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return None
    return text


def _parse_strings_file(path: Path) -> tuple[str, ...]:
    try:
        raw = path.read_bytes()
    except OSError:
        return ()
    if not raw or len(raw) > _PLIST_LIMIT:
        return ()
    # Binary plist format is common for compiled InfoPlist.strings on macOS.
    if raw.startswith(b"bplist") or raw.lstrip().startswith(b"<?xml"):
        try:
            data = plistlib.loads(raw)
        except Exception:  # noqa: BLE001 -- skip unreadable localization file without failing bundle scan.
            return ()
        if isinstance(data, dict):
            values = []
            for key in ("CFBundleDisplayName", "CFBundleName"):
                cleaned = _safe_alias(data.get(key))
                if cleaned:
                    values.append(cleaned)
            return tuple(values)
        return ()
    text = None
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if not text:
        return ()
    values = []
    for match in _STRINGS_ENTRY.finditer(text):
        raw_val = match.group("value").replace(r"\"", '"').replace(r"\\", "\\")
        cleaned = _safe_alias(raw_val)
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def _macos_localized_aliases(bundle_path: Path, primary_label: str) -> tuple[str, ...]:
    resources = bundle_path / "Contents" / "Resources"
    if not resources.is_dir():
        return ()
    aliases: list[str] = []
    seen = {primary_label.casefold()}
    preferred = ("zh-Hans.lproj", "zh_CN.lproj", "zh-Hant.lproj", "zh_TW.lproj", "en.lproj", "Base.lproj")
    lproj_dirs: list[Path] = []
    for name in preferred:
        candidate = resources / name
        if candidate.is_dir():
            lproj_dirs.append(candidate)
    try:
        with os.scandir(resources) as entries:
            for entry in entries:
                if entry.name.endswith(".lproj") and entry.is_dir(follow_symlinks=False):
                    path = Path(entry.path)
                    if path not in lproj_dirs:
                        lproj_dirs.append(path)
    except OSError:
        return ()
    for lproj in lproj_dirs[:16]:
        strings_path = lproj / "InfoPlist.strings"
        if not strings_path.is_file():
            continue
        for value in _parse_strings_file(strings_path):
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                aliases.append(value)
    return tuple(aliases)


def _mac_candidate(path: Path, *, include_localized: bool = False) -> ApplicationCandidate:
    path = path.expanduser().resolve(strict=True)
    if path.is_file():
        if not os.access(path, os.X_OK):
            raise ValueError("Not executable.")
        bundle = next((parent for parent in path.parents if parent.suffix.casefold() == ".app"), None)
        if bundle is None:
            return ApplicationCandidate(identity=str(path), label=path.name, app_path=path, source="explicit_path")
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
    aliases: list[str] = []
    seen = {label.casefold()}
    for extra in (metadata.get("CFBundleDisplayName"), metadata.get("CFBundleName"), path.stem):
        cleaned = _safe_alias(extra)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            aliases.append(cleaned)
    if include_localized:
        for loc in _macos_localized_aliases(path, label):
            if loc.casefold() not in seen:
                seen.add(loc.casefold())
                aliases.append(loc)
    return ApplicationCandidate(identity=str(path), label=label, aliases=tuple(aliases), app_path=path, bundle_id=bundle_id, source="applications_dir")


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


def _windows_start_menu_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return tuple(roots)


def _read_c_string(data: bytes, offset: int, *, unicode: bool = False) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    if unicode:
        end = offset
        while end + 1 < len(data):
            if data[end] == 0 and data[end + 1] == 0:
                return data[offset:end].decode("utf-16-le", errors="ignore")
            end += 2
        return None
    end = data.find(b"\x00", offset)
    if end == -1:
        return None
    return data[offset:end].decode("mbcs" if os.name == "nt" else "latin-1", errors="ignore")


def _resolve_lnk_target(lnk_path: Path) -> Path | None:
    """Parse a Windows Shell Link (.lnk) LinkInfo local base path without COM or shell execution."""
    try:
        raw = lnk_path.read_bytes()
    except OSError:
        return None
    if len(raw) < 76 or len(raw) > _LNK_LIMIT:
        return None
    if raw[:4] != _LNK_MAGIC or raw[4:20] != _LNK_CLSID:
        return None
    flags = struct.unpack_from("<I", raw, 20)[0]
    offset = 76
    has_id_list = bool(flags & 0x01)
    has_link_info = bool(flags & 0x02)
    if has_id_list:
        if offset + 2 > len(raw):
            return None
        id_list_size = struct.unpack_from("<H", raw, offset)[0]
        offset += 2 + id_list_size
    if not has_link_info or offset + 28 > len(raw):
        return None
    info_start = offset
    info_size, header_size, info_flags = struct.unpack_from("<III", raw, info_start)
    if info_size < 28 or info_start + info_size > len(raw):
        return None
    if not (info_flags & 0x01):  # VolumeIDAndLocalBasePath
        return None
    local_base_offset = struct.unpack_from("<I", raw, info_start + 16)[0]
    common_suffix_offset = struct.unpack_from("<I", raw, info_start + 24)[0]
    local_base = None
    suffix = ""
    if header_size >= 0x24 and info_start + 36 <= len(raw):
        uni_base_offset = struct.unpack_from("<I", raw, info_start + 28)[0]
        uni_suffix_offset = struct.unpack_from("<I", raw, info_start + 32)[0]
        if uni_base_offset:
            local_base = _read_c_string(raw, info_start + uni_base_offset, unicode=True)
        if uni_suffix_offset:
            suffix = _read_c_string(raw, info_start + uni_suffix_offset, unicode=True) or ""
    if not local_base and local_base_offset:
        local_base = _read_c_string(raw, info_start + local_base_offset, unicode=False)
        if common_suffix_offset:
            suffix = _read_c_string(raw, info_start + common_suffix_offset, unicode=False) or ""
    if not local_base:
        return None
    combined = local_base + suffix
    if not combined or any(ord(char) < 32 or ord(char) == 127 for char in combined):
        return None
    return Path(combined)


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


def _windows_candidate(
    path: Path,
    *,
    label: str | None = None,
    aliases: tuple[str, ...] = (),
    window_title_re: str | None = None,
    source: str = "explicit_path",
) -> ApplicationCandidate:
    if not path.is_absolute() or path.suffix.casefold() != ".exe" or not path.is_file():
        raise ValueError("Application executable is unavailable.")
    resolved = path.resolve(strict=True)
    effective_label = label or resolved.stem
    return ApplicationCandidate(
        identity=os.path.normcase(str(resolved)),
        label=effective_label,
        aliases=aliases,
        app_path=resolved,
        window_title_re=window_title_re,
        source=source,
    )


def _collect_android_inventory(target_id: str | None, deadline: float) -> list[ApplicationCandidate]:
    if not target_id or not any(device.serial == target_id and device.state == "device" for device in inventory(deadline)):
        raise ValueError("Selected device is unavailable.")
    third_party = third_party_package_inventory(target_id, deadline)
    _budget(deadline, len(third_party))
    all_packages = package_inventory(target_id, deadline)
    _budget(deadline, len(all_packages))
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pkg in sorted(third_party):
        seen.add(pkg)
        ordered.append((pkg, "android_third_party"))
    # Include common system/store/utility packages that users frequently target or use as entry apps.
    for pkg in _ANDROID_COMMON_SYSTEM:
        if pkg in all_packages and pkg not in seen:
            seen.add(pkg)
            ordered.append((pkg, "android_system"))
    # Fill remaining budget with other installed packages if total is small enough.
    if len(ordered) < _INVENTORY_LIMIT:
        for pkg in sorted(all_packages):
            if pkg not in seen:
                seen.add(pkg)
                ordered.append((pkg, "android_system"))
                if len(ordered) >= _INVENTORY_LIMIT:
                    break
    return [
        ApplicationCandidate(
            identity=pkg,
            label=pkg.rsplit(".", 1)[-1] or pkg,
            aliases=(pkg,) if pkg.rsplit(".", 1)[-1] != pkg else (),
            app_id=pkg,
            source=source,
        )
        for pkg, source in ordered[:_INVENTORY_LIMIT]
    ]


def _collect_macos_inventory(deadline: float) -> list[ApplicationCandidate]:
    candidates: dict[str, ApplicationCandidate] = {}
    count = 0
    for root in _macos_roots():
        if not root.exists():
            continue
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                count += 1
                _budget(deadline, count)
                path = Path(entry.path)
                if path.suffix.casefold() == ".app":
                    try:
                        candidate = _mac_candidate(path, include_localized=True)
                    except Exception:  # noqa: BLE001, S112 -- skip broken/unreadable individual bundles in inventory scan.
                        continue
                    candidates.setdefault(candidate.identity, candidate)
                elif depth < 1 and entry.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
    return sorted(candidates.values(), key=lambda item: (item.label.casefold(), item.identity))[:_INVENTORY_LIMIT]


def _merge_windows_candidate(bucket: dict[str, ApplicationCandidate], candidate: ApplicationCandidate) -> None:
    existing = bucket.get(candidate.identity)
    if existing is None:
        bucket[candidate.identity] = candidate
        return
    aliases: list[str] = list(existing.aliases)
    seen = {existing.label.casefold(), *(a.casefold() for a in aliases)}
    for text in (candidate.label, *candidate.aliases):
        cleaned = _safe_alias(text)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            aliases.append(cleaned)
    # Preserve preset window_title_re and source priority (preset > start_menu > app_paths).
    bucket[candidate.identity] = existing.model_copy(
        update={
            "aliases": tuple(aliases),
            "window_title_re": existing.window_title_re or candidate.window_title_re,
        }
    )


def _collect_windows_app_paths(bucket: dict[str, ApplicationCandidate], deadline: float, count: int) -> int:
    try:
        import winreg
    except ImportError:
        return count
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                root = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                try:
                    keys = winreg.QueryInfoKey(root)[0]
                except OSError:
                    continue
                for index in range(keys):
                    count += 1
                    _budget(deadline, count)
                    try:
                        key_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, key_name) as key:
                            raw, kind = winreg.QueryValueEx(key, "")
                    except OSError:
                        continue
                    allowed_kinds = {winreg.REG_SZ}
                    expand_kind = getattr(winreg, "REG_EXPAND_SZ", None)
                    if expand_kind is not None:
                        allowed_kinds.add(expand_kind)
                    if kind not in allowed_kinds or not isinstance(raw, str) or len(raw) > 4096:
                        continue
                    expanded = os.path.expandvars(raw.strip().strip('"'))
                    path = Path(expanded)
                    try:
                        stem = Path(key_name).stem
                        aliases = (stem,) if stem and stem.casefold() != path.stem.casefold() else ()
                        candidate = _windows_candidate(path, aliases=aliases, source="windows_app_paths")
                    except ValueError:
                        continue
                    _merge_windows_candidate(bucket, candidate)
    return count


def _collect_windows_start_menu(bucket: dict[str, ApplicationCandidate], deadline: float, count: int) -> int:
    for root in _windows_start_menu_roots():
        if not root.is_dir():
            continue
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                count += 1
                _budget(deadline, count)
                path = Path(entry.path)
                if path.suffix.casefold() == ".lnk" and entry.is_file(follow_symlinks=False):
                    target = _resolve_lnk_target(path)
                    if target is None:
                        continue
                    try:
                        label = _safe_alias(path.stem) or target.stem
                        aliases = (target.stem,) if target.stem.casefold() != label.casefold() else ()
                        candidate = _windows_candidate(target, label=label, aliases=aliases, source="windows_start_menu")
                    except ValueError:
                        continue
                    _merge_windows_candidate(bucket, candidate)
                elif depth < 3 and entry.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
    return count


def _collect_windows_inventory(
    windows_catalogue: Sequence[WindowsApplicationConfig] | None,
    deadline: float,
) -> list[ApplicationCandidate]:
    bucket: dict[str, ApplicationCandidate] = {}
    count = 0
    # 1. Bundled presets (highest priority, retain host-only window_title_re).
    for entry in windows_catalogue or ():
        for raw_path in entry.path_candidates:
            count += 1
            _budget(deadline, count)
            expanded = Path(os.path.expandvars(raw_path))
            try:
                aliases = tuple(a for a in (entry.id, expanded.stem) if a.casefold() != entry.name.casefold())
                candidate = _windows_candidate(
                    expanded,
                    label=entry.name,
                    aliases=aliases,
                    window_title_re=entry.window_title_re,
                    source="windows_preset",
                )
            except ValueError:
                continue
            _merge_windows_candidate(bucket, candidate)
            break
    # 2. Start Menu .lnk shortcuts (friendly labels).
    count = _collect_windows_start_menu(bucket, deadline, count)
    # 3. Registry App Paths.
    _collect_windows_app_paths(bucket, deadline, count)
    return sorted(bucket.values(), key=lambda item: (item.label.casefold(), item.identity))[:_INVENTORY_LIMIT]


def collect_inventory(
    selected: str,
    *,
    target_id: str | None = None,
    windows_catalogue: Sequence[WindowsApplicationConfig] | None = None,
) -> list[ApplicationCandidate] | None:
    deadline = time.monotonic() + 5.0
    if selected == "android":
        candidates = _collect_android_inventory(target_id, deadline)
    elif selected == "ios":
        candidates = _ios_application_inventory(target_id, deadline)
        if candidates is None:
            return None
    elif selected == "macos" and platform.system() == "Darwin":
        candidates = _collect_macos_inventory(deadline)
    elif selected == "windows" and platform.system() == "Windows":
        candidates = _collect_windows_inventory(windows_catalogue, deadline)
    else:
        raise ValueError("Unsupported host.")
    _budget(deadline, len(candidates))
    return [candidate.model_copy(update={"candidate_id": f"{selected}:{index + 1}"}) for index, candidate in enumerate(candidates)]
