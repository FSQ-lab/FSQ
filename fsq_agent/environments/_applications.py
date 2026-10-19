# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from concurrent.futures import Future
from threading import BoundedSemaphore, Thread

from fsq_agent.environments.providers._applications import discover_candidates
from fsq_agent.models import ApplicationLookupHint, ApplicationLookupResult

_LOOKUP_TIMEOUT = 5.0
_LOOKUP_SLOTS = BoundedSemaphore(2)


def _bounded_candidates(platform, name, explicit_reference, target_id, inferred_hint):
    # Filesystem/registry calls cannot always be interrupted. Bound the caller's
    # wait and the number of lingering read-only probes, with no queued retries.
    if not _LOOKUP_SLOTS.acquire(blocking=False):
        raise RuntimeError("Application discovery capacity is unavailable.")
    result = Future()

    def discover():
        try:
            result.set_result(discover_candidates(platform, name, explicit_reference=explicit_reference, target_id=target_id, inferred_hint=inferred_hint))
        except BaseException as error:  # noqa: BLE001 -- transfer worker failures to the caller without relabeling them.
            result.set_exception(error)
        finally:
            _LOOKUP_SLOTS.release()

    try:
        Thread(target=discover, name="fsq-application-discovery", daemon=True).start()
    except BaseException:
        _LOOKUP_SLOTS.release()
        raise
    return result.result(timeout=_LOOKUP_TIMEOUT)


def lookup_application(*, platform: str, name: str, explicit_reference: str | None = None, target_id: str | None = None, inferred_hint: ApplicationLookupHint | None = None) -> ApplicationLookupResult:
    values = ((name, 512), (explicit_reference, 2048), (target_id, 256))
    if not isinstance(platform, str) or platform not in {"android", "web", "windows", "macos"} or not isinstance(name, str) or not name.strip():
        return ApplicationLookupResult(status="failed", code="invalid_query")
    if inferred_hint is not None:
        allowed_hints = {"android": {"app_id"}, "macos": {"bundle_id", "app_path"}, "windows": {"app_path"}, "web": set()}
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
