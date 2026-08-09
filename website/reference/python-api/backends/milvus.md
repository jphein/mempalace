# `mempalace.backends.milvus`

Source: [`mempalace/backends/milvus.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/milvus.py)

Milvus backend for MemPalace.

The backend uses the modern ``pymilvus.MilvusClient`` API only. By default each
local palace gets its own Milvus Lite database at ``&lt;palace>/milvus.db``. Users
can opt into Milvus server or Zilliz Cloud by configuring a URI and token.

Embeddings are supplied by MemPalace's core embedding wrapper. This backend
declares ``requires_explicit_embeddings`` and stores/query vectors directly.

## Classes

### `class MilvusCollection(BaseCollection)`

#### `__init__`

```python
def __init__(self, *, backend: 'MilvusBackend', client: Any, config: _MilvusConfig, palace: PalaceRef, collection_name: str, remote_collection: str)
```

#### `get_stored_embedder_identity`

```python
def get_stored_embedder_identity(self)
```

#### `set_embedder_identity`

```python
def set_embedder_identity(self, identity) -> None
```

#### `distance_metric`

```python
def distance_metric(self) -> str
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

#### `get_all_metadata`

```python
def get_all_metadata(self, where: Optional[dict] = None) -> list[dict]
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

### `class MilvusBackend(BaseBackend)`

#### `__init__`

```python
def __init__(self)
```

#### `get_collection`

```python
def get_collection(self, *args, **kwargs) -> MilvusCollection
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
def create_collection(self, palace_path: str, collection_name: str) -> MilvusCollection
```

#### `get_or_create_collection`

```python
def get_or_create_collection(self, palace_path: str, collection_name: str)
```

#### `delete_collection`

```python
def delete_collection(self, palace_path: str, collection_name: str) -> None
```

## Functions

### `milvus_uri_is_server`

```python
def milvus_uri_is_server(uri: Optional[str]) -> bool
```

Return whether ``uri`` targets service-managed Milvus storage.

### `translate_where`

```python
def translate_where(where: Optional[dict]) -> str
```

Translate the portable metadata where DSL into a Milvus filter string.

### `translate_where_document`

```python
def translate_where_document(where_document: Optional[dict]) -> str
```

Translate the portable document filter subset into a Milvus filter.
