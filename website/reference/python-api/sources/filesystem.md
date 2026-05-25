# `mempalace.sources.filesystem`

Source: [`mempalace/sources/filesystem.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/filesystem.py)

Filesystem source adapter (RFC 002 §9).

Thin wrapper around ``mempalace.miner`` — the existing filesystem mining
pipeline. Delegates scanning, chunking, room detection, and metadata
construction to miner internals so adapter users get the same behavior as
``mempalace mine`` without coupling to the miner's function signatures.

This adapter does NOT replace ``miner.mine()``; it provides an alternative
entry point via the adapter plugin contract. Both paths coexist.

## Classes

### `class FilesystemSourceAdapter(BaseSourceAdapter)`

Ingest project files from a local directory tree.

Wraps ``miner.scan_project()``, ``miner.chunk_text()``, and
``miner.detect_room()`` so the full filesystem mining pipeline is
available via the adapter contract.

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
