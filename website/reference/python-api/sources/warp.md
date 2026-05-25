# `mempalace.sources.warp`

Source: [`mempalace/sources/warp.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/warp.py)

Warp terminal source adapter.

Ingests Warp terminal command history and AI queries from Warp's local
SQLite database (default ``~/.local/state/warp-terminal/warp.sqlite``)
into the palace as :class:`DrawerRecord` instances.

Warp stores two classes of valuable data:

1. **Commands** — shell command history grouped by ``session_id``. Each
   session becomes one ``source_file`` of the shape
   ``warp://&lt;absolute-db-path>#session=&lt;session_id>``. Commands within a
   session are rendered chronologically as a terminal transcript and
   chunked for palace storage.

2. **AI queries** — Warp AI conversations keyed by ``conversation_id``.
   Each conversation becomes one ``source_file`` of the shape
   ``warp://&lt;absolute-db-path>#ai=&lt;conversation_id>``. Query/response
   pairs are rendered as exchange-pair markdown.

Both sources support incremental ingest via file mtime versioning.

## Classes

### `class WarpSourceAdapter(BaseSourceAdapter)`

Mine Warp terminal command history and AI queries into the palace.

#### `__init__`

```python
def __init__(self) -> None
```

#### `describe_schema`

```python
def describe_schema(self) -> AdapterSchema
```

#### `ingest`

```python
def ingest(self, *, source: SourceRef, palace: PalaceContext) -> Iterator[object]
```

#### `is_current`

```python
def is_current(self, *, item: SourceItemMetadata, existing_metadata: Optional[dict]) -> bool
```

#### `source_summary`

```python
def source_summary(self, *, source: SourceRef) -> SourceSummary
```

#### `close`

```python
def close(self) -> None
```

## Functions

### `session_source_file`

```python
def session_source_file(db_path: str, session_id: str) -> str
```

Construct the stable per-session ``source_file`` identifier.

### `ai_source_file`

```python
def ai_source_file(db_path: str, conversation_id: str) -> str
```

Construct the stable per-AI-conversation ``source_file`` identifier.
