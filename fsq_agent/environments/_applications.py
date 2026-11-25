# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import Future
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock, Thread
from typing import TYPE_CHECKING

from fsq_agent.environments.providers._applications import collect_inventory, discover_candidates
from fsq_agent.environments.providers._ios import _inventory_device
from fsq_agent.models import ApplicationCandidate, ApplicationInventoryResult, ApplicationLookupHint, ApplicationLookupResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsq_agent.models import WindowsApplicationConfig
_LOOKUP_TIMEOUT = 5.0
_INVENTORY_TTL_SECONDS = 60.0
_LOOKUP_SLOTS = BoundedSemaphore(2)
_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class _CachedInventory:
    candidates: tuple[ApplicationCandidate, ...]
    fingerprint: str
    expires_at: float


_INVENTORY_CACHE: dict[tuple[str, str | None], _CachedInventory] = {}


def clear_application_inventory_cache() -> None:
    """Clear process-local environment inventory snapshots (for tests or explicit reset)."""
    with _CACHE_LOCK:
        _INVENTORY_CACHE.clear()


def _inventory_fingerprint(candidates: Sequence[ApplicationCandidate]) -> str:
    payload = [
        {
            "identity": item.identity,
            "label": item.label,
            "aliases": list(item.aliases),
            "app_id": item.app_id,
            "bundle_id": item.bundle_id,
            "app_path": str(item.app_path) if item.app_path is not None else None,
            "window_title_re": item.window_title_re,
            "source": item.source,
        }
        for item in candidates
    ]
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run_bounded(worker):
    # Filesystem/registry calls cannot always be interrupted. Bound the caller's
    # wait and the number of lingering read-only probes, with no queued retries.
    if not _LOOKUP_SLOTS.acquire(blocking=False):
        raise RuntimeError("Application discovery capacity is unavailable.")
    result = Future()

    def run():
        try:
            result.set_result(worker())
        except BaseException as error:  # noqa: BLE001 -- transfer worker failures to the caller without relabeling them.
            result.set_exception(error)
        finally:
            _LOOKUP_SLOTS.release()

    try:
        Thread(target=run, name="fsq-application-discovery", daemon=True).start()
    except BaseException:
        _LOOKUP_SLOTS.release()
        raise
    return result.result(timeout=_LOOKUP_TIMEOUT)


def _bounded_candidates(platform, name, explicit_reference, target_id, inferred_hint):
    return _run_bounded(lambda: discover_candidates(platform, name, explicit_reference=explicit_reference, target_id=target_id, inferred_hint=inferred_hint))


def _bounded_inventory(platform, target_id, windows_catalogue):
    return _run_bounded(lambda: collect_inventory(platform, target_id=target_id, windows_catalogue=windows_catalogue))


def _windows_catalogue_digest(windows_catalogue: Sequence[WindowsApplicationConfig] | None) -> str:
    if not windows_catalogue:
        return ""
    payload = [{"id": entry.id, "name": entry.name, "paths": list(entry.path_candidates), "title": entry.window_title_re} for entry in windows_catalogue]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def list_applications(
    *,
    platform: str,
    target_id: str | None = None,
    windows_catalogue: Sequence[WindowsApplicationConfig] | None = None,
    force_refresh: bool = False,
) -> ApplicationInventoryResult:
    if not isinstance(platform, str) or platform not in {"android", "windows", "macos", "ios"}:
        return ApplicationInventoryResult(status="failed", code="invalid_query")
    if target_id is not None and (not isinstance(target_id, str) or not target_id.strip() or len(target_id) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in target_id)):
        return ApplicationInventoryResult(status="failed", code="invalid_query")
    if platform in {"android", "ios"} and not target_id:
        return ApplicationInventoryResult(status="failed", code="invalid_query")
    scope_key = target_id if platform in {"android", "ios"} else (_windows_catalogue_digest(windows_catalogue) if platform == "windows" else None)
    cache_key = (platform, scope_key)
    with _CACHE_LOCK:
        cached = None if force_refresh else _INVENTORY_CACHE.get(cache_key)
        if platform == "ios" and force_refresh:
            _INVENTORY_CACHE.pop(cache_key, None)
    try:
        if cached is not None and cached.expires_at > time.monotonic():
            if platform == "ios":
                try:
                    device = _run_bounded(lambda: _inventory_device(target_id, time.monotonic() + _LOOKUP_TIMEOUT))
                    if device.kind == "device":
                        return ApplicationInventoryResult(status="unsupported", code="unsupported")
                except Exception:
                    with _CACHE_LOCK:
                        if _INVENTORY_CACHE.get(cache_key) is cached:
                            del _INVENTORY_CACHE[cache_key]
                    raise
            return ApplicationInventoryResult(
                status="complete",
                candidates=cached.candidates,
                from_cache=True,
                fingerprint=cached.fingerprint,
            )
        candidates = _bounded_inventory(platform, target_id, windows_catalogue)
        if candidates is None:
            return ApplicationInventoryResult(status="unsupported", code="unsupported")
        if len(candidates) > 2048:
            return ApplicationInventoryResult(status="failed", code="discovery_failed")
        unique = tuple({item.identity: item for item in candidates}.values())
        fingerprint = _inventory_fingerprint(unique)
        with _CACHE_LOCK:
            _INVENTORY_CACHE[cache_key] = _CachedInventory(
                candidates=unique,
                fingerprint=fingerprint,
                expires_at=time.monotonic() + _INVENTORY_TTL_SECONDS,
            )
        return ApplicationInventoryResult(
            status="complete",
            candidates=unique,
            from_cache=False,
            fingerprint=fingerprint,
        )
    except TimeoutError:
        return ApplicationInventoryResult(status="failed", code="discovery_timeout")
    except Exception:  # noqa: BLE001 -- host failures must never imply an empty inventory.
        return ApplicationInventoryResult(status="failed", code="discovery_failed")


def lookup_application(*, platform: str, name: str, explicit_reference: str | None = None, target_id: str | None = None, inferred_hint: ApplicationLookupHint | None = None) -> ApplicationLookupResult:
    values = ((name, 512), (explicit_reference, 2048), (target_id, 256))
    if not isinstance(platform, str) or platform not in {"android", "web", "windows", "macos", "ios"} or not isinstance(name, str) or not name.strip() or (platform == "ios" and not target_id):
        return ApplicationLookupResult(status="failed", code="invalid_query")
    if inferred_hint is not None:
        allowed_hints = {"android": {"app_id"}, "ios": set(), "macos": {"bundle_id", "app_path"}, "windows": {"app_path"}, "web": set()}
        if not isinstance(inferred_hint, ApplicationLookupHint) or inferred_hint.kind not in allowed_hints[platform] or explicit_reference is not None:
            return ApplicationLookupResult(status="failed", code="invalid_query")
    if any(value is not None and (not isinstance(value, str) or not value.strip() or len(value) > bound or any(ord(char) < 32 or ord(char) == 127 for char in value)) for value, bound in values):
        return ApplicationLookupResult(status="failed", code="invalid_query")
    try:
        candidates = _bounded_candidates(platform, name, explicit_reference, target_id, inferred_hint)
        if len(candidates) > 2048:
            return ApplicationLookupResult(status="failed", code="discovery_failed")
        unique = {item.identity: item for item in candidates}
        return ApplicationLookupResult(status="complete", candidates=tuple(unique.values()))
    except TimeoutError:
        return ApplicationLookupResult(status="failed", code="discovery_timeout")
    except Exception:  # noqa: BLE001 -- host failures must never imply an empty or unique match.
        return ApplicationLookupResult(status="failed", code="discovery_failed")
