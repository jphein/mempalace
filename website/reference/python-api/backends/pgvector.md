# `mempalace.backends.pgvector`

Source: [`mempalace/backends/pgvector.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/pgvector.py)

Postgres + pgvector backend for MemPalace.

pgvector is an opt-in external-service backend, the SQL counterpart to the
Qdrant REST backend. Chroma remains the default; this adapter only runs when
the user explicitly selects ``pgvector`` via config, env, or CLI/MCP flag.
Embeddings are still produced locally by MemPalace through the core embedding
wrapper before vectors are written to Postgres.

Why a second external backend: it exercises the storage contract on a
fundamentally different substrate (SQL + JSONB + the pgvector ``<=>`` operator)
than Qdrant's REST/dict model, proving the ``BaseBackend`` / ``BaseCollection``
surface is not accidentally shaped around one vendor.

Isolation model (RFC 001 isolation contract): one table per
``namespace`` + ``palace`` + ``collection``. The namespace contributes to the
table name, so this backend advertises ``supports_namespace_isolation`` and
satisfies the cross-namespace conformance arm.

Dependency posture: the live client needs the optional ``psycopg`` dependency
(``pip install mempalace[pgvector]``), imported lazily so the package imports
fine without it. CI runs against an in-memory fake client; the live Postgres
round-trip is gated behind ``MEMPALACE_PGVECTOR_LIVE_URL``.

## Classes

### `class PgVectorCollection(BaseCollection)`

#### `__init__`

```python
def __init__(self, *, backend: 'PgVectorBackend', client: _PgVectorClient, config: _PgVectorConfig, palace: PalaceRef, collection_name: str, table: str)
```

#### `get_stored_embedder_identity`

```python
def get_stored_embedder_identity(self)
```

#### `set_embedder_identity`

```python
def set_embedder_identity(self, identity) -> None
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
def count(self) -> int
```

#### `lexical_search`

```python
def lexical_search(self, *, query: str, n_results: int = 10, where: Optional[dict] = None)
```

#### `close`

```python
def close(self) -> None
```

#### `health`

```python
def health(self) -> HealthStatus
```

#### `maintenance_state`

```python
def maintenance_state(self) -> dict
```

#### `run_maintenance`

```python
def run_maintenance(self, kind: str)
```

### `class PgVectorBackend(BaseBackend)`

#### `__init__`

```python
def __init__(self)
```

#### `get_collection`

```python
def get_collection(self, *args, **kwargs) -> PgVectorCollection
```

#### `close_palace`

```python
def close_palace(self, palace: PalaceRef | str) -> None
```

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

#### `create_collection`

```python
def create_collection(self, palace_path: str, collection_name: str) -> PgVectorCollection
```

#### `get_or_create_collection`

```python
def get_or_create_collection(self, palace_path: str, collection_name: str)
```

#### `delete_collection`

```python
def delete_collection(self, palace_path: str, collection_name: str) -> None
```
