# `mempalace.sources.codex`

Source: [`mempalace/sources/codex.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/codex.py)

Codex CLI source adapter (RFC 002).

Ingests OpenAI Codex CLI session transcripts from the JSONL format at
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``. Extracts ``event_msg``
entries (user_message / agent_message) which are the canonical conversation
turns. Parsing logic extracted from ``normalize._try_codex_jsonl()``.

## Classes

### `class CodexSourceAdapter(BaseSourceAdapter)`

Ingest Codex CLI conversation sessions.

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
