# `mempalace.ratings`

Source: [`mempalace/ratings.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/ratings.py)

Feedback ratings for search results (#159, Tier 1).

Ratings are an opt-in *ranking signal*, never a gate. They live in drawer
metadata alongside the verbatim text — the stored content is never mutated,
honoring the verbatim-always principle. A drawer accumulates two counters:

* ``rating_useful``   — times an agent/user marked a hit as helpful
* ``rating_not_useful`` — times a hit was marked unhelpful

The net score (useful − not_useful) drives a small, bounded distance
adjustment in the searcher. The adjustment can reorder neighbors but is
capped so it can never push a relevant drawer out of the result set —
100% recall is the design requirement, so a rating reorders, never excludes.

Pure functions, no I/O. The MCP tool owns the read-modify-write; the
searcher owns applying the score.

## Functions

### `extract_rating_from_metadata`

```python
def extract_rating_from_metadata(meta: dict | None) -> tuple[int, int]
```

Return ``(useful, not_useful)`` counters from a drawer's metadata.

### `apply_rating_to_metadata`

```python
def apply_rating_to_metadata(meta: dict, useful: bool) -> dict
```

Increment the appropriate counter in ``meta`` in place and return it.

Mutates only the two rating keys — every other metadata field, and the
drawer's stored content, are left untouched.

### `net_rating`

```python
def net_rating(meta: dict | None) -> int
```

Net rating signal: useful − not_useful (can be negative).

### `rating_distance_adjustment`

```python
def rating_distance_adjustment(meta: dict | None) -> float
```

Bounded cosine-distance shift for a rated drawer.

Positive net → negative shift (drawer moves *up* toward distance 0).
Negative net → positive shift (drawer moves *down*). Magnitude is
``net * RATING_DISTANCE_STEP`` clamped to ``±RATING_DISTANCE_CAP``.

Returns a value to be *added* to the effective distance, so a useful
drawer (positive net) yields a negative number.
