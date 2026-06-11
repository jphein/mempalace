# `mempalace.collision_scan`

Source: [`mempalace/collision_scan.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/collision_scan.py)

Pre-mining defense against drawer_id collisions.

Runs immediately before a batched chromadb upsert. Computes the union of
incoming drawer_ids and existing drawer_ids that share a key with the
batch; raises ``CollisionError`` if any drawer_id appears more than once
in that union with conflicting ``(source_file, chunk_index)`` metadata.

Under the v2 hash recipe (see :mod:`mempalace.ids`) accidental collisions
are vanishingly rare — SHA-256 truncated to 24 hex chars makes random
collision ~2^-96. The scan exists for two reasons:

1. Catch upstream bugs that emit duplicate ``(source_file, chunk_index)``
   pairs in the same batch with conflicting content. ChromaDB would
   silently let the last-write win; the scan surfaces it as an
   actionable error naming both call sites.
2. Catch the astronomical-but-possible SHA-256 hash collision with a
   clear message instead of a silent overwrite at upsert time.

The scan does NOT fire on idempotent re-mines — when an incoming drawer
matches an existing one with the SAME ``(source_file, chunk_index)``
metadata, that is normal re-write behavior, not collision.

## Classes

### `class CollisionError(Exception)`

Raised by :func:`assert_no_collisions` when the pre-mining scan
detects a drawer_id that would silently overwrite existing content
or duplicate within a batch with conflicting metadata.

The exception message names every colliding ``drawer_id`` and the
full set of ``(source_file, chunk_index)`` pairs producing each one,
so a user fixing one collision does not have to rediscover the next
by re-running the mine.

## Functions

### `assert_no_collisions`

```python
def assert_no_collisions(proposed: list[tuple[str, dict]], collection) -> None
```

Abort the mine via ``CollisionError`` if any proposed drawer_id
collides with itself or with an existing drawer in ``collection``.

Args:
    proposed: list of ``(drawer_id, metadata)`` tuples for the
        chunks about to be upserted. ``metadata`` must carry at
        least ``source_file``; ``chunk_index`` is used when
        present.
    collection: a ChromaDB-shaped collection with ``get(ids=...)``
        returning a dict with ``ids`` and ``metadatas`` keys.

Raises:
    CollisionError: when a drawer_id maps to two or more distinct
        ``(source_file, chunk_index)`` tuples in the union of
        incoming and existing rows.
