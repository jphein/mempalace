# `mempalace.sources.aider`

Source: [`mempalace/sources/aider.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/aider.py)

Aider source adapter (RFC 002).

Ingests Aider chat history files (``.aider.chat.history.md``). Format:
- ``# aider chat started at YYYY-MM-DD HH:MM:SS`` — session headers
- ``#### &lt;text>`` — user turns (H4 headers)
- ``> &lt;text>`` — system/aider output (blockquotes)
- Plain text — assistant responses

Each session (delimited by ``# aider chat started at``) becomes one
source_file. Real test data at
``~/Projects/openwrt/openwrt-backups/.aider.chat.history.md``.

## Classes

### `class AiderSourceAdapter(BaseSourceAdapter)`

Ingest Aider chat history files.

#### `ingest`

```python
def ingest(self, *, source: SourceRef, palace: PalaceContext) -> Iterator[DrawerRecord]
```

#### `is_current`

```python
def is_current(self, *, item: SourceItemMetadata, existing_metadata: Optional[dict]) -> bool
```

#### `describe_schema`

```python
def describe_schema(self) -> AdapterSchema
```

#### `source_summary`

```python
def source_summary(self, *, source: SourceRef) -> SourceSummary
```
