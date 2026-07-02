"""TTL cache for the periodic depth-refresh injection.

The depth signal fires a deterministic query ("session context <wing>")
whose daemon round-trip costs most of a second — well over the hook
latency budget — while its results barely change within a session. Repeat
fires are served from this small on-disk cache; only the first fire per
TTL window pays the daemon call.

Failure posture: every path fails open (a broken/corrupt/unwritable cache
just means the daemon is queried as before).
"""

import hashlib
import json
import os
import time
from typing import Optional

_DEFAULT_CACHE_PATH = os.path.expanduser("~/.mempalace/auto_query/depth_cache.json")


def default_cache_path() -> str:
    """Cache location: env ``AUTO_QUERY_DEPTH_CACHE_PATH`` > default."""
    return os.environ.get("AUTO_QUERY_DEPTH_CACHE_PATH", "").strip() or _DEFAULT_CACHE_PATH


def cache_key(query: str, wing: str, limit: int) -> str:
    """Stable key over the fields that define a depth query."""
    raw = "\x1f".join((query or "", wing or "", str(limit)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read(cache_path: str) -> dict:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_cached_injection(
    key: str, ttl_seconds: int, cache_path: Optional[str] = None
) -> Optional[str]:
    """Return the cached injection for ``key`` if younger than ``ttl_seconds``."""
    if ttl_seconds <= 0:
        return None
    if cache_path is None:
        cache_path = default_cache_path()
    entry = _read(cache_path).get(key)
    if not isinstance(entry, dict):
        return None
    ts = entry.get("ts")
    injection = entry.get("injection")
    if not isinstance(ts, (int, float)) or not isinstance(injection, str):
        return None
    if time.time() - ts > ttl_seconds:
        return None
    return injection


def store_injection(key: str, injection: str, cache_path: Optional[str] = None) -> None:
    """Store ``injection`` under ``key``, pruning stale siblings. Fails open."""
    if cache_path is None:
        cache_path = default_cache_path()
    try:
        data = _read(cache_path)
        now = time.time()
        # opportunistic prune: drop anything older than a day so the file
        # can't grow unboundedly across wings/sessions.
        data = {
            k: v
            for k, v in data.items()
            if isinstance(v, dict)
            and isinstance(v.get("ts"), (int, float))
            and now - v["ts"] < 86400
        }
        data[key] = {"injection": injection, "ts": now}
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass
