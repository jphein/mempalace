# `mempalace.sources.opencode`

Source: [`mempalace/sources/opencode.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/opencode.py)

OpenCode source adapter (RFC 002).

Ingests OpenCode AI-coding-CLI session transcripts out of OpenCode's local
SQLite store (default ``~/.local/share/opencode/opencode.db``) into the
palace as :class:`DrawerRecord` instances.

Each OpenCode session becomes one ``source_file`` of the shape
``opencode://&lt;absolute-db-path>#session=&lt;sid>``. The drawers under that
``source_file`` are exchange-pair chunks of the session transcript,
formatted to match the existing ``convo_miner`` shape so downstream
ranking, search, and closet-building behave identically.

Reverse-engineering credit: the SQLite schema, ``json_extract`` paths,
tool-echo / file-injection skip filters, and same-role merge originated in
@JakobSachs's PR #23 (``feat: add OpenCode SQLite session database
support``). This adapter rebuilds those same primitives on the RFC 002
contract so it can ship as a registered adapter rather than a normalize.py
branch.

## Classes

### `class OpenCodeSourceAdapter(BaseSourceAdapter)`

Mine OpenCode AI-coding-CLI sessions into the palace (RFC 002 §1).

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

Shape: ``opencode://&lt;absolute-db-path>#session=&lt;sid>``. Stable across
re-ingests, used as the ChromaDB ``where=&#123;"source_file": ...}`` key and
by ``is_current`` to look up existing drawers.
