# `mempalace.tag_extraction`

Source: [`mempalace/tag_extraction.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/tag_extraction.py)

TF-IDF auto-tag extraction for drawer write time (#201).

Auto-tags are an opt-in convenience layer on top of the manual tag system
defined in ``mempalace.tags``. Explicit caller-supplied tags always win;
this module only contributes when the caller passes ``tags=None``.

The extractor is hand-rolled (no sklearn) to keep the dep footprint flat
and the cold-import time negligible. Given mempalace's read-heavy access
pattern, the cost we care about is the per-write extraction time and the
amortised cost of rebuilding the IDF table when the corpus shifts.

Public surface
--------------

``extract_tags`` — given a content string and an IDF table, return a
normalised list of 3-8 tag candidates ordered by descending score.

``build_idf`` — given an iterable of drawer-content strings (the corpus
snapshot), compute the document-frequency table and produce IDF weights.

``IdfCache`` — process-local cache that holds at most one IDF table per
``(wing, room)`` scope. Callers ask for the table they need; the cache
either returns the warm copy or runs ``build_idf`` against the supplied
corpus builder. TTL is intentionally generous because the corpus only
shifts when drawers are written, and the per-write extraction tolerates
slightly stale weights — they only rank existing tokens, not invent them.

## Classes

### `class IdfCache`

Thread-safe TTL cache of IDF tables, keyed by ``(wing, room)``.

Re-running ``build_idf`` on every write would force a full corpus
scan per drawer; the cache amortises that across writes within the
same wing/room. Entries expire after ``ttl_seconds`` (default 5 min)
so a write-heavy session eventually sees fresh weights without
requiring explicit invalidation calls from every write path.

The cache holds *snapshots*, not handles to the backend, so callers
pass a ``corpus_builder`` callable that yields the current corpus
when the cache decides a refresh is due. This keeps the extractor
decoupled from the storage backend (postgres / chroma / tests).

#### `__init__`

```python
def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 32)
```

#### `get`

```python
def get(self, wing: str, room: str, corpus_builder: Callable[[], Iterable[str]]) -> dict[str, float]
```

#### `invalidate`

```python
def invalidate(self, wing: Optional[str] = None, room: Optional[str] = None) -> None
```

Drop one entry, all entries for a wing, or the whole cache.

## Functions

### `build_idf`

```python
def build_idf(corpus: Iterable[str]) -> dict[str, float]
```

Compute IDF weights for a corpus snapshot.

Each input string is one document. IDF uses the standard smoothed
formula ``log((N + 1) / (df + 1)) + 1`` so terms appearing in every
document still earn a positive (if small) weight.

Returns an empty dict when the corpus is empty so callers can treat
the empty case as "no extraction possible" rather than crashing.

### `extract_tags`

```python
def extract_tags(content: str, idf: Optional[dict[str, float]] = None, k: int = _DEFAULT_K) -> list[str]
```

Return up to ``k`` normalised tag candidates for ``content``.

``idf`` is the IDF table from ``build_idf``. Pass ``None`` for a
cold-start corpus — the extractor falls back to raw term frequency
(every term gets IDF=1.0), which is poor but deterministic.

Output is already normalised via ``mempalace.tags.normalise_tag``,
so callers can pipe directly into ``apply_tags_to_metadata``.
