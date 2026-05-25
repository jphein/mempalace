# `mempalace.palace`

Source: [`mempalace/palace.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/palace.py)

palace.py — Shared palace operations.

Consolidates collection access patterns used by both miners and the MCP server.

## Classes

### `class MineAlreadyRunning(RuntimeError)`

Raised when another `mempalace mine` already holds the per-palace lock.

### `class MineValidationError(RuntimeError)`

Raised at end of mine when PRAGMA quick_check on the palace reports errors.

#### `__init__`

```python
def __init__(self, palace_path: str, errors: list[str]) -> None
```

## Functions

### `get_collection`

```python
def get_collection(palace_path: str, collection_name: Optional[str] = None, create: bool = True)
```

Get the palace collection through the backend layer.

`collection_name=None` (the fork-side convention) and
`collection_name=DEFAULT_COLLECTION_NAME` (#665's convention) both mean
"use the configured default collection." Either resolves through
`MempalaceConfig().collection_name` (env `MEMPALACE_COLLECTION_NAME`
overrides the config file). Fork-side callers in `searcher.py`,
`convo_miner.py`, `sweeper.py`, etc. pass None explicitly; #665 was
written assuming all callers omitted the keyword and got the default,
which broke the None path. Accepting both forms is the minimal shim
until fork-side callers migrate to the new convention.

### `get_closets_collection`

```python
def get_closets_collection(palace_path: str, create: bool = True)
```

Get the closets collection — the searchable index layer.

### `build_closet_lines`

```python
def build_closet_lines(source_file, drawer_ids, content, wing, room, drawer_metas = None)
```

Build compact closet pointer lines from drawer content.

Returns a LIST of lines (not joined). Each line is one complete topic
pointer — never split across closets.

Legacy format (3 segments): ``topic|entities|→drawer_ids``
Tier 6a format (4 segments): ``topic|entities|YYYY-MM-DD:Lstart-Lend|→drawer_ids``

When ``drawer_metas`` is provided and the first meta carries both
``line_start``/``line_end`` plus a parseable ``filed_at``, the 4-segment
form is emitted so retrieval can jump to the right span. Otherwise the
legacy 3-segment form is used — backward compat for drawers filed before
Tier 6a and for direct callers that don't have metadata handy.

### `purge_file_closets`

```python
def purge_file_closets(closets_col, source_file: str) -> None
```

Delete every closet associated with ``source_file``.

Call this before ``upsert_closet_lines`` on a re-mine so stale topics
from a prior schema/version don't survive in the closet collection.
Mirrors the drawer-purge step in process_file().

### `upsert_closet_lines`

```python
def upsert_closet_lines(closets_col, closet_id_base, lines, metadata)
```

Write topic lines to closets, packed greedily without splitting a line.

Closets are deterministically numbered (``..._01``, ``..._02``, …) and
each ``upsert`` fully overwrites the prior content at that ID. Callers
are expected to ``purge_file_closets`` first when re-mining a source
file so stale-numbered closets from larger prior runs don't leak.

Returns the number of closets written.

### `mine_lock`

```python
def mine_lock(source_file: str)
```

Cross-platform file lock for mine operations.

Prevents multiple agents from mining the same file simultaneously,
which causes duplicate drawers when the delete+insert cycle interleaves.

### `mine_palace_lock`

```python
def mine_palace_lock(palace_path: str)
```

Per-palace non-blocking lock around the full `mine` pipeline.

The per-file `mine_lock` only protects delete+insert interleave for a
single source; it does not prevent N copies of `mempalace mine <dir>`
from being spawned concurrently by hooks. When that happens, each copy
drives ChromaDB HNSW inserts in parallel against the same palace,
which (combined with chromadb's multi-threaded ParallelFor) can
corrupt the HNSW graph and produce sparse link_lists.bin blowups.

The lock file is keyed by sha256(palace_path) so mines against
*different* palaces can still run in parallel — we only serialize
writes into the same palace, which is the correctness boundary.

The key is derived from a fully normalized form of the path:
`realpath` resolves symlinks and `..` segments, and `normcase` folds
case on Windows (which has a case-insensitive filesystem). Without
normcase, `C:\Palace` and `c:\palace` would hash to different keys
on Windows and let two concurrent mines touch the same on-disk palace.

Non-blocking: if another `mine` is already writing to this palace,
raise MineAlreadyRunning so the caller can exit cleanly instead of
piling up as a waiting worker.

Re-entrant: if the current thread already holds the lock for the same
palace, the context manager passes through without re-acquiring. This
lets ChromaCollection write methods (which acquire the lock themselves
to protect MCP/direct callers) compose with miner.mine() (which holds
the outer lock for the entire mine pipeline) without self-deadlock.

### `file_already_mined`

```python
def file_already_mined(collection, source_file: str, check_mtime: bool = False, extract_mode: Optional[str] = None) -> bool
```

Check if a file has already been filed in the palace.

Returns False (so the file gets re-mined) when:
  - no drawers exist for this source_file
  - the stored `normalize_version` is missing or older than the current
    schema (triggers silent rebuild after a normalization upgrade)
  - `check_mtime=True` and the file's mtime differs from the stored one

When check_mtime=True (used by project miner), also re-mines on content
change. When check_mtime=False (used by convo miner), transcripts are
assumed immutable, so only the version gate triggers a rebuild.

When extract_mode is set (used by convo miner), idempotency is scoped to
that extraction mode so exchange-mode and general-mode drawers can coexist
for the same source transcript. Legacy drawers without extract_mode are
treated as exchange-mode drawers.

### `bulk_check_mined`

```python
def bulk_check_mined(collection) -> dict[str, float]
```

Pre-fetch source_file/source_mtime pairs for all documents in the collection.

Returns a dict mapping source_file -> source_mtime (as float) for every
document that has both fields.  Callers can check membership and compare
mtimes locally instead of issuing one ChromaDB query per file.

Fetches the full collection in paginated batches (like palace_graph.py)
since a WHERE-IN filter on thousands of paths is not supported by ChromaDB.

### `prefetch_mined_set`

```python
def prefetch_mined_set(collection, extract_mode: Optional[str] = None) -> set[str]
```

Pre-fetch the set of source_files already mined at the current NORMALIZE_VERSION.

Mirrors file_already_mined()'s version-gate semantics (check_mtime=False
branch) but in one bulk pass instead of one ChromaDB query per file.
Returns a set of source_file paths whose stored drawers are at or above
NORMALIZE_VERSION; callers do `if path in result_set: skip`.

When extract_mode is set, mirrors file_already_mined(..., extract_mode=...)
so conversation mines skip per extraction mode rather than per source file.

The convo miner walks thousands of transcript files; per-file
`collection.get(where={"source_file": X})` costs ~2s on a 150k-drawer
palace, making a 2000-file sweep take >1h of pure skip-checking. This
helper drops that to a single paginated scan plus O(1) lookups.
