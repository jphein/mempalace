# `mempalace.sources.gemini`

Source: [`mempalace/sources/gemini.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/gemini.py)

Gemini CLI source adapter (RFC 002).

Ingests Gemini CLI session transcripts from the JSONL format at
``~/.gemini/tmp/&lt;project_hash>/chats/session-*.jsonl``. Extracts
``user``/``gemini`` entries after the ``session_metadata`` sentinel.
Parsing logic extracted from ``normalize._try_gemini_jsonl()``.

## Classes

### `class GeminiSourceAdapter(BaseSourceAdapter)`

Ingest Gemini CLI conversation sessions.

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
