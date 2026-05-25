# `mempalace.sync`

Source: [`mempalace/sync.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sync.py)

sync.py — Gitignore-aware drawer prune (#1252).

Removes drawers whose source files are now gitignored, deleted, or moved
out of the project. Reuses the same GitignoreMatcher infrastructure that
the miner uses on the way in, so the same rules that block ingest also
drive the corresponding cleanup.

Usage:
    from mempalace.sync import sync_palace
    report = sync_palace(palace_path, project_dirs=["/repo"], dry_run=True)

## Classes

### `class SyncReport(TypedDict)`

## Functions

### `sync_palace`

```python
def sync_palace(palace_path: str, project_dirs: Optional[list] = None, wing: Optional[str] = None, dry_run: bool = True, batch_size: int = _BATCH, wal_log: Optional[Callable] = None) -> SyncReport
```

Prune drawers whose source files are gitignored, missing, or moved.

Returns a SyncReport with bucket counts. Dry-run by default; pass
dry_run=False to actually delete drawers and matching closets.

Holds ``mine_palace_lock`` for the whole call so the classify pass and
the apply branch see the same drawer snapshot. Raises
``MineAlreadyRunning`` if another mine is in progress on this palace.

On apply (``dry_run=False``), at least one of ``wing`` or
``project_dirs`` must be set so a caller cannot accidentally prune
every wing in a multi-project palace via auto-detected roots.
