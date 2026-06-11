# `mempalace.migrate`

Source: [`mempalace/migrate.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/migrate.py)

mempalace migrate — Recover a palace created with a different ChromaDB version.

Reads documents and metadata directly from the palace's SQLite database
(bypassing ChromaDB's API, which fails on version-mismatched palaces),
then re-imports everything into a fresh palace using the currently installed
ChromaDB version.

Since mempalace 3.2.0 (chromadb>=1.5.4), chromadb automatically migrates
0.4.1+ databases on first open — no manual migration needed for upgrades.
Use this command only when downgrading chromadb (e.g. rolling back to an
older mempalace release) or if automatic migration fails.

Usage:
    mempalace migrate                          # migrate default palace
    mempalace migrate --palace /path/to/palace  # migrate specific palace
    mempalace migrate --dry-run                # show what would be migrated

## Functions

### `extract_drawers_from_sqlite`

```python
def extract_drawers_from_sqlite(db_path: str) -> list
```

Read all drawers directly from ChromaDB's SQLite, bypassing the API.

Works regardless of which ChromaDB version created the database.
Returns list of dicts with 'id', 'document', and 'metadata' keys.

The connection is wrapped in ``contextlib.closing`` so an exception
during extraction does not leak the SQLite handle. On Windows that
would leave a file lock on ``chroma.sqlite3`` and prevent the rest
of the migration from touching the palace directory.

### `detect_chromadb_version`

```python
def detect_chromadb_version(db_path: str) -> str
```

Detect which ChromaDB version created the database by checking schema.

### `contains_palace_database`

```python
def contains_palace_database(path: str) -> bool
```

Return True when path looks like a MemPalace ChromaDB directory.

### `confirm_destructive_action`

```python
def confirm_destructive_action(operation_name: str, palace_path: str, assume_yes: bool = False) -> bool
```

Require confirmation before destructive palace operations.

### `collection_write_roundtrip_works`

```python
def collection_write_roundtrip_works(col) -> bool
```

Return True only if the collection can upsert, read, and delete.

Some ChromaDB 0.6.x -> 1.5.x migrated collections remain readable while
writes and deletes silently no-op. A plain ``count()`` probe misses that
failure mode, so migrate must verify an actual write round-trip before
deciding that no rebuild is needed.

### `migrate`

```python
def migrate(palace_path: str, dry_run: bool = False, confirm: bool = False)
```

Migrate a palace to the currently installed ChromaDB version.

### `plan_wing_renames`

```python
def plan_wing_renames(items)
```

Pure planner over ``(id, metadata)`` pairs.

Returns ``(summary, updates)`` where ``summary`` is ``&#123;(old, new): count}``
and ``updates`` is ``[(id, new_metadata), ...]`` for only the records whose
wing changes. Metadata is copied; only the ``wing`` key is rewritten.

### `migrate_wing_names`

```python
def migrate_wing_names(palace_path: str, dry_run: bool = False, confirm: bool = False) -> bool
```

Normalize legacy wing names in ``palace_path`` (strip leading/trailing
separators), so palaces built before #1675 keep their memories discoverable.

Re-keys the ``wing`` metadata on drawers and closets in place (IDs untouched)
and the ``topics_by_wing`` registry, merging collisions. Idempotent.

Returns True if anything was (or, in dry-run, would be) migrated.
