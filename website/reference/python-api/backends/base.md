# `mempalace.backends.base`

Source: [`mempalace/backends/base.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/base.py)

Storage backend contract for MemPalace (RFC 001).

This module defines the surface every storage backend must implement:

* ``BaseCollection`` — the per-collection read/write interface, kwargs-only.
* ``BaseBackend`` — the per-palace factory, addressed by ``PalaceRef``.
* ``QueryResult`` / ``GetResult`` — typed result dataclasses that replace the
  Chroma dict shape as the canonical return type.
* Error classes + ``HealthStatus`` — uniform across backends.

This is the v1 cleanup from RFC 001 §10: full typed results, ``PalaceRef``,
registry-ready ABC. Embedder injection, maintenance hooks, and the full
conformance suite land in follow-up PRs.

## Classes

### `class BackendError(Exception)`

Base class for every storage-backend error raised by core.

### `class PalaceNotFoundError(BackendError, FileNotFoundError)`

Raised when ``get_collection(create=False)`` is called on a missing palace.

Subclass of ``FileNotFoundError`` so legacy callers that catch the latter
(pre-#413 seam) keep working unchanged.

### `class CollectionNotInitializedError(PalaceNotFoundError)`

Raised when the palace exists on disk but the requested collection has
never been created (e.g. ``init`` ran but ``mine`` has not).

Distinct from :class:`PalaceNotFoundError`: the palace dir and DB are
present and valid, only the collection has not been bootstrapped yet.
Subclass of :class:`PalaceNotFoundError` (and therefore
:class:`FileNotFoundError`) so legacy callers catching either parent
keep working unchanged.

### `class BackendClosedError(BackendError)`

Raised when a backend method is called after ``close()``.

### `class UnsupportedFilterError(BackendError)`

Raised when a where-clause uses an operator the backend does not implement.

Silent dropping of unknown operators is forbidden by spec (RFC 001 §1.4).

### `class DimensionMismatchError(BackendError)`

Raised when the embedding dimension on write does not match the collection.

### `class EmbedderIdentityMismatchError(BackendError)`

Raised when the stored embedder model name differs from the current one.

### `class PalaceRef`

A handle to a palace, consumed by backends.

``id`` is always present and is the key backends use to cache handles.
``local_path`` is populated for filesystem-rooted palaces.
``namespace`` is used by server-mode backends for tenant / prefix routing.

### `class HealthStatus`

#### `healthy`

```python
def healthy(cls, detail: str = '') -> 'HealthStatus'
```

#### `unhealthy`

```python
def unhealthy(cls, detail: str) -> 'HealthStatus'
```

### `class QueryResult(_DictCompatMixin)`

Typed return from ``BaseCollection.query``.

Outer list dimension = number of query vectors / texts.
Inner list dimension = hits per query (may be zero).

Fields not in ``include=`` at the call site are populated with empty lists
of the correct outer shape (never ``None``), except ``embeddings`` which
is ``None`` when not requested.

#### `empty`

```python
def empty(cls, num_queries: int = 1, embeddings_requested: bool = False) -> 'QueryResult'
```

Construct an all-empty result preserving outer dimension.

When ``embeddings_requested`` is True, ``embeddings`` preserves the outer
query dimension with empty hit lists (matching the spec's rule that fields
requested via ``include=`` carry the outer shape even when empty). When
False, ``embeddings`` stays ``None`` to signal the field was not requested.

### `class GetResult(_DictCompatMixin)`

Typed return from ``BaseCollection.get``.

#### `empty`

```python
def empty(cls) -> 'GetResult'
```

### `class BaseCollection(ABC)`

Per-collection read/write surface every backend must implement.

#### `add`

```python
def add(self, *, documents: list[str], ids: list[str], metadatas: Optional[list[dict]] = None, embeddings: Optional[list[list[float]]] = None) -> None
```

#### `upsert`

```python
def upsert(self, *, documents: list[str], ids: list[str], metadatas: Optional[list[dict]] = None, embeddings: Optional[list[list[float]]] = None) -> None
```

#### `query`

```python
def query(self, *, query_texts: Optional[list[str]] = None, query_embeddings: Optional[list[list[float]]] = None, n_results: int = 10, where: Optional[dict] = None, where_document: Optional[dict] = None, include: Optional[list[str]] = None) -> QueryResult
```

#### `get`

```python
def get(self, *, ids: Optional[list[str]] = None, where: Optional[dict] = None, where_document: Optional[dict] = None, limit: Optional[int] = None, offset: Optional[int] = None, include: Optional[list[str]] = None) -> GetResult
```

#### `delete`

```python
def delete(self, *, ids: Optional[list[str]] = None, where: Optional[dict] = None) -> None
```

#### `count`

```python
def count(self) -> int
```

#### `estimated_count`

```python
def estimated_count(self) -> int
```

#### `close`

```python
def close(self) -> None
```

#### `health`

```python
def health(self) -> HealthStatus
```

#### `update`

```python
def update(self, *, ids: list[str], documents: Optional[list[str]] = None, metadatas: Optional[list[dict]] = None, embeddings: Optional[list[list[float]]] = None) -> None
```

Default non-atomic update: get + merge + upsert.

Backends advertising ``supports_update`` MUST override with an atomic
single-round-trip implementation.

#### `rename_wing`

```python
def rename_wing(self, *, from_wing: str, to_wing: str, batch_size: int = 500) -> dict
```

Rename all drawers from one wing to another.

Default implementation iterates in batches using metadata-only
``update()`` calls.  Backends with native bulk-update support
(e.g. PostgreSQL) should override with an atomic implementation.

Returns ``&#123;"renamed": int, "errors": int}``.

### `class BaseBackend(ABC)`

Long-lived factory serving many palaces (RFC 001 §2).

Instances are lightweight on construction — no I/O, no network. All
connection work is deferred to ``get_collection``. Instances are thread-
safe for concurrent ``get_collection`` calls across different palaces.

#### `get_collection`

```python
def get_collection(self, *, palace: PalaceRef, collection_name: str, create: bool = False, options: Optional[dict] = None) -> BaseCollection
```

#### `close_palace`

```python
def close_palace(self, palace: PalaceRef) -> None
```

Evict cached handles for a single palace. Default: no-op.

#### `close`

```python
def close(self) -> None
```

Shut down the entire backend. Default: no-op.

#### `health`

```python
def health(self, palace: Optional[PalaceRef] = None) -> HealthStatus
```

#### `detect`

```python
def detect(cls, path: str) -> bool
```
