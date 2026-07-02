# `mempalace.auto_query.depth_cache`

Source: [`mempalace/auto_query/depth_cache.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_query/depth_cache.py)

TTL cache for the periodic depth-refresh injection.

The depth signal fires a deterministic query ("session context &lt;wing>")
whose daemon round-trip costs most of a second — well over the hook
latency budget — while its results barely change within a session. Repeat
fires are served from this small on-disk cache; only the first fire per
TTL window pays the daemon call.

Failure posture: every path fails open (a broken/corrupt/unwritable cache
just means the daemon is queried as before).

## Functions

### `default_cache_path`

```python
def default_cache_path() -> str
```

Cache location: env ``AUTO_QUERY_DEPTH_CACHE_PATH`` > default.

### `cache_key`

```python
def cache_key(query: str, wing: str, limit: int) -> str
```

Stable key over the fields that define a depth query.

### `load_cached_injection`

```python
def load_cached_injection(key: str, ttl_seconds: int, cache_path: Optional[str] = None) -> Optional[str]
```

Return the cached injection for ``key`` if younger than ``ttl_seconds``.

### `store_injection`

```python
def store_injection(key: str, injection: str, cache_path: Optional[str] = None) -> None
```

Store ``injection`` under ``key``, pruning stale siblings. Fails open.
