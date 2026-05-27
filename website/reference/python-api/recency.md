# `mempalace.recency`

Source: [`mempalace/recency.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/recency.py)

Recency weighting for search results (#158).

Recency is an opt-in *ranking signal*, never a gate. A drawer's age — the
span between its ``filed_at`` timestamp and now — produces a small, bounded
distance adjustment in the searcher: newer drawers get nudged up, older ones
left where they are. The adjustment is capped so it can reorder neighbors but
can never push a relevant drawer out of the result set — 100% recall is the
design requirement, so recency reorders, never excludes.

The signal is exponential decay (half-life based), the same shape upstream
tracks for Weibull decay in MemPalace/mempalace#1032: a drawer one half-life
old keeps half its maximum boost, two half-lives old a quarter, and so on. A
drawer with no parseable timestamp gets zero adjustment (treated as ageless,
never penalized).

Pure functions, no I/O. The searcher owns reading ``filed_at`` from metadata
and applying the score; this module owns the math.

## Functions

### `age_days`

```python
def age_days(meta: dict | None, now: datetime | None = None) -> float | None
```

Age of a drawer in days from its ``filed_at``, or ``None`` if unknown.

A negative span (future-dated row) clamps to 0.0 — a clock skew shouldn't
invert the signal into a penalty.

### `recency_distance_adjustment`

```python
def recency_distance_adjustment(meta: dict | None, now: datetime | None = None, halflife_days: float = RECENCY_HALFLIFE_DAYS, max_shift: float = RECENCY_DISTANCE_MAX) -> float
```

Bounded cosine-distance shift for a drawer's recency.

Exponential decay: a drawer ``halflife_days`` old keeps half the maximum
shift, ``2*halflife_days`` a quarter, and so on. The result is always in
``[-max_shift, 0.0]`` — a value to be *added* to the effective distance,
so a fresh drawer (large boost) yields a more-negative number and moves
*up*. A drawer with no parseable timestamp yields 0.0 (ageless).

``max_shift <= 0`` or ``halflife_days <= 0`` disables the signal (returns
0.0) so a misconfigured weight can't invert ranking.
