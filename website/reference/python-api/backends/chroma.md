# `mempalace.backends.chroma`

Source: [`mempalace/backends/chroma.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/chroma.py)

ChromaDB-backed MemPalace storage backend (RFC 001 reference implementation).

## Classes

### `class ChromaCollection(BaseCollection)`

Thin adapter translating ChromaDB dict returns into typed results.

When ``palace_path`` is set, all write methods (``add``, ``upsert``,
``update``, ``delete``) acquire ``mine_palace_lock(palace_path)`` for the
duration of the underlying chromadb call. This serializes MCP and other
direct-backend writers against ``mempalace mine`` and against each other,
closing the race between concurrent writers that triggers ChromaDB's
multi-threaded HNSW corruption (#974/#965).

The lock is the same primitive used by ``miner.mine()`` so re-entrant
acquisition from inside the mine pipeline (mine -> _mine_body ->
collection.upsert) is short-circuited by the per-thread guard inside
``mine_palace_lock`` — no self-deadlock.

``palace_path=None`` disables the wrapping, preserving the legacy
no-lock behaviour for callers that construct a ``ChromaCollection``
directly without going through ``ChromaBackend``.

#### `__init__`

```python
def __init__(self, collection, palace_path: Optional[str] = None)
```

#### `add`

```python
def add(self, *, documents, ids, metadatas = None, embeddings = None)
```

#### `upsert`

```python
def upsert(self, *, documents, ids, metadatas = None, embeddings = None)
```

#### `update`

```python
def update(self, *, ids, documents = None, metadatas = None, embeddings = None)
```

#### `query`

```python
def query(self, *, query_texts = None, query_embeddings = None, n_results = 10, where = None, where_document = None, include = None) -> QueryResult
```

#### `get`

```python
def get(self, *, ids = None, where = None, where_document = None, limit = None, offset = None, include = None) -> GetResult
```

#### `delete`

```python
def delete(self, *, ids = None, where = None)
```

#### `count`

```python
def count(self)
```

#### `lexical_search`

```python
def lexical_search(self, *, query: str, n_results: int = 10, where: Optional[dict] = None) -> LexicalResult
```

Return lexical BM25 candidates for this collection.

This is the normal healthy-Chroma implementation behind the optional
backend capability. The HNSW-disabled fallback in ``searcher.py`` still
reads ``chroma.sqlite3`` directly and remains Chroma-only.

#### `metadata`

```python
def metadata(self) -> dict
```

Pass-through to the underlying ChromaDB collection's metadata.

Used by the searcher to detect legacy palaces that were created
without ``hnsw:space=cosine`` and therefore silently use L2
distance, which breaks cosine-based similarity interpretation.
Returns ``&#123;}`` when metadata is absent so callers can do a plain
``.get("hnsw:space")`` without None-checks.

#### `distance_metric`

```python
def distance_metric(self) -> str
```

Report this collection's actual space from ``hnsw:space``.

MemPalace sets ``hnsw:space=cosine`` on every creation path, so a
healthy palace reports ``"cosine"``. When the key is absent, empty, or
an unrecognized value, the collection is genuinely using Chroma's HNSW
default — **L2** (Euclidean) — because cosine was never set on it. We
report ``"l2"`` in that case so core ranking maps the distances
correctly; reporting ``"cosine"`` here would reintroduce the
floor-every-result-to-zero misranking this property exists to fix.

#### `get_stored_embedder_identity`

```python
def get_stored_embedder_identity(self)
```

#### `set_embedder_identity`

```python
def set_embedder_identity(self, identity) -> None
```

### `class ChromaBackend(BaseBackend)`

MemPalace's default ChromaDB backend.

Maintains two caches:

* ``self._clients`` — ``palace_path -> PersistentClient`` for callers
  using the ``PalaceRef`` / :meth:`get_collection` path.
* An inode+mtime freshness check absorbed from ``mcp_server._get_client``
  (merged via #757) ensuring a palace rebuild on disk is detected on the
  next :meth:`get_collection` call.

#### `__init__`

```python
def __init__(self)
```

#### `make_client`

```python
def make_client(palace_path: str)
```

Create a fresh ``PersistentClient`` (runs pre-open safety pass first).

Deprecated-ish: exposed for legacy long-lived callers that manage their
own client cache. New code should obtain a collection through
:meth:`get_collection` which manages caching internally.

Quarantines HNSW segments on first open and after any detected
disk change. See :attr:`_quarantined_paths` for the gate logic.

#### `backend_version`

```python
def backend_version() -> str
```

Return the installed chromadb package version string.

#### `get_collection`

```python
def get_collection(self, *args, **kwargs) -> ChromaCollection
```

Obtain a collection for a palace.

Supports two calling conventions during the RFC 001 transition:

* New (preferred): ``get_collection(palace=PalaceRef, collection_name=...,
  create=False, options=None)``.
* Legacy: ``get_collection(palace_path, collection_name, create=False)``
  — still used by callers not yet migrated.

#### `close_palace`

```python
def close_palace(self, palace) -> None
```

Drop cached handles for ``palace`` and release its SQLite file lock.

Accepts ``PalaceRef`` or legacy path str. chromadb's rust-side file
lock is held until ``PersistentClient.close()`` is called, so plain
dict eviction would leave the palace path unreopenable and
unremovable in the same process.

#### `close`

```python
def close(self) -> None
```

#### `health`

```python
def health(self, palace: Optional[PalaceRef] = None) -> HealthStatus
```

#### `detect`

```python
def detect(cls, path: str) -> bool
```

#### `get_or_create_collection`

```python
def get_or_create_collection(self, palace_path: str, collection_name: str) -> ChromaCollection
```

Legacy shim for ``get_collection(..., create=True)`` by path string.

#### `delete_collection`

```python
def delete_collection(self, palace_path: str, collection_name: str) -> None
```

Delete ``collection_name`` from the palace at ``palace_path``.

#### `create_collection`

```python
def create_collection(self, palace_path: str, collection_name: str, hnsw_space: str = 'cosine') -> ChromaCollection
```

Create (not get-or-create) ``collection_name`` with the given HNSW space.

## Functions

### `quarantine_stale_hnsw`

```python
def quarantine_stale_hnsw(palace_path: str, stale_seconds: float = 300.0) -> list[str]
```

Rename HNSW segment dirs that look unsafe to open.

This catches two classes of HNSW corruption before ChromaDB opens the
native segment reader:

1. stale-by-mtime segments whose ``index_metadata.pickle`` fails the
   existing format sniff-test;
2. structurally impossible HNSW payloads where ``link_lists.bin`` is much
   larger than ``data_level0.bin``.

The second check is intentionally not gated by mtime. A segment with a
300x link/data ratio is unsafe regardless of whether its mtime is recent;
letting Chroma open it can SIGSEGV before Python fallback code runs.

The original directory is renamed, not deleted, so recovery remains
possible if the heuristic ever misfires.

### `hnsw_capacity_status`

```python
def hnsw_capacity_status(palace_path: str, collection_name: str = 'mempalace_drawers') -> dict
```

Compare sqlite embedding count against HNSW element count.

The #1222 failure mode: ``max_elements`` froze at 16 384 while sqlite
accumulated 192 997 embeddings. Every subsequent tool call segfaulted
when chromadb tried to load the undersized HNSW. This probe runs
*before* anything touches the segment so we can warn (or fall back to
BM25) instead of crashing.

Returns a dict with:

* ``segment_id``       — VECTOR segment UUID, or ``None`` if no palace
* ``sqlite_count``     — embeddings present in chroma.sqlite3
* ``hnsw_count``       — elements chromadb's pickle knows about
* ``divergence``       — ``sqlite_count - hnsw_count`` when both known
* ``diverged``         — True when divergence exceeds the threshold
* ``status``           — ``"ok"`` | ``"diverged"`` | ``"unknown"``
* ``message``          — human-readable summary

Never raises — a probe that throws would defeat the point.

### `quarantine_invalid_hnsw_metadata`

```python
def quarantine_invalid_hnsw_metadata(palace_path: str) -> list[str]
```

Quarantine segment dirs whose ``index_metadata.pickle`` is unreadable or invalid.

Chroma's persisted HNSW metadata is untrusted disk state. If a segment has
labels but invalid or partial metadata, current Chroma versions can accept
the pickle and crash later in the Rust loader. We rename the entire segment
out of the way before ``PersistentClient`` opens so Chroma can rebuild
cleanly instead of touching known-bad metadata.
