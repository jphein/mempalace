# `mempalace.dedup`

Source: [`mempalace/dedup.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/dedup.py)

dedup.py — Detect and remove near-duplicate drawers
====================================================

When the same files are mined multiple times, near-identical drawers
accumulate. This module finds drawers from the same source_file that
are too similar (cosine distance < threshold), keeps the longest/richest
version, and deletes the rest.

No API calls — uses ChromaDB's built-in embedding similarity.

Usage (standalone):
    python -m mempalace.dedup                          # dedup all
    python -m mempalace.dedup --dry-run                # preview only
    python -m mempalace.dedup --threshold 0.10         # stricter (near-identical only)
    python -m mempalace.dedup --threshold 0.35         # looser (catches paraphrased content)
    python -m mempalace.dedup --wing my_project        # scope to one wing
    python -m mempalace.dedup --stats                  # stats only
    python -m mempalace.dedup --source "my_project"    # filter by source

Usage (from CLI):
    mempalace dedup [--dry-run] [--threshold 0.15] [--stats]

## Functions

### `get_source_groups`

```python
def get_source_groups(col, min_count = MIN_DRAWERS_TO_CHECK, source_pattern = None, wing = None)
```

Group drawers by source_file, return groups with min_count+ entries.

If wing is specified, only considers drawers in that wing. This catches
cross-wing duplicates when the same source was mined into multiple wings.

### `dedup_source_group`

```python
def dedup_source_group(col, drawer_ids, threshold = DEFAULT_THRESHOLD, dry_run = True)
```

Dedup drawers within one source_file group.

Greedy: sort by doc length (longest first), keep if not too similar
to any already-kept drawer. Returns (kept_ids, deleted_ids).

### `show_stats`

```python
def show_stats(palace_path = None)
```

Show duplication statistics without making changes.

### `dedup_palace`

```python
def dedup_palace(palace_path = None, threshold = DEFAULT_THRESHOLD, dry_run = True, source_pattern = None, min_count = MIN_DRAWERS_TO_CHECK, wing = None)
```

Main entry point: deduplicate near-identical drawers across the palace.
