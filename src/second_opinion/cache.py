"""Disk cache for tool responses.

Why: external APIs are rate-limited, flaky, and non-deterministic. Caching every tool call to JSON
makes demos reproducible, evals repeatable, and lets the whole system run offline (SO_OFFLINE=1)
from either the runtime cache (data/cache) or committed fixtures (fixtures/).

Lookup order: data/cache -> fixtures -> (live call, then write to data/cache) -> raise if offline.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .config import settings


class CacheMiss(Exception):
    """Raised in offline mode when neither cache nor fixture has the requested key."""


def _key_to_filename(namespace: str, key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    if len(safe) > 80:
        safe = safe[:60] + "_" + hashlib.sha1(key.encode()).hexdigest()[:12]
    return f"{namespace}__{safe}.json"


def _paths(namespace: str, key: str) -> tuple[Path, Path]:
    fname = _key_to_filename(namespace, key)
    return settings.data_dir / "cache" / fname, settings.fixtures_dir / fname


def read(namespace: str, key: str, max_age_s: float | None = None) -> Any | None:
    """Return cached payload or None. Fixtures never expire; runtime cache honours max_age_s."""
    runtime, fixture = _paths(namespace, key)
    if runtime.exists():
        doc = json.loads(runtime.read_text(encoding="utf-8"))
        if max_age_s is None or (time.time() - doc.get("_ts", 0)) <= max_age_s:
            return doc["payload"]
    if fixture.exists():
        return json.loads(fixture.read_text(encoding="utf-8"))["payload"]
    return None


def write(namespace: str, key: str, payload: Any) -> None:
    runtime, _ = _paths(namespace, key)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(json.dumps({"_ts": time.time(), "_key": key, "payload": payload}, indent=1, default=str),
                       encoding="utf-8")


def cached(namespace: str, key: str, fetch: Callable[[], Any], max_age_s: float | None = 6 * 3600) -> Any:
    """Serve from cache/fixture; otherwise call fetch() and cache the result. Offline mode never fetches."""
    hit = read(namespace, key, max_age_s=None if settings.offline else max_age_s)
    if hit is not None:
        return hit
    if settings.offline:
        raise CacheMiss(f"offline and no cache/fixture for {namespace}:{key}")
    payload = fetch()
    write(namespace, key, payload)
    return payload
