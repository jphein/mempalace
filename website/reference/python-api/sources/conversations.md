# `mempalace.sources.conversations`

Source: [`mempalace/sources/conversations.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/conversations.py)

Conversation source adapter (RFC 002 §9).

Thin wrapper around ``mempalace.convo_miner`` — the existing conversation
mining pipeline. Delegates session scanning, exchange chunking, and room
detection to convo_miner internals so adapter users get the same behavior
as ``mempalace mine --mode convos`` without coupling to function signatures.

This adapter does NOT replace ``convo_miner.mine_convos()``; it provides
an alternative entry point via the adapter plugin contract.

## Classes

### `class ConversationSourceAdapter(BaseSourceAdapter)`

Ingest AI conversation transcripts from a local directory.

Wraps ``convo_miner.scan_convos()``, ``convo_miner.chunk_exchanges()``,
and ``convo_miner.detect_convo_room()`` so the full conversation mining
pipeline is available via the adapter contract.

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
