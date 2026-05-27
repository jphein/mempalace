"""Feedback ratings for search results (#159, Tier 1).

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
"""

from __future__ import annotations

USEFUL_KEY = "rating_useful"
NOT_USEFUL_KEY = "rating_not_useful"

# Per-step distance shift for one net rating point, in cosine-distance units.
# Closet boosts run 0.04–0.40; we sit deliberately below the weakest closet
# rung so an explicit rating nudges order without overpowering semantic match.
RATING_DISTANCE_STEP = 0.03

# Hard cap on the cumulative shift regardless of how lopsided the counters
# get. Keeps a heavily-rated drawer from collapsing to distance 0 (or
# ballooning past a relevant neighbor) — the signal saturates.
RATING_DISTANCE_CAP = 0.12


def _coerce_count(value) -> int:
    """Read a counter from metadata, tolerating missing/garbage values.

    Chroma metadata round-trips ints, but partially-written or
    externally-edited rows may carry strings or ``None``. Anything that
    isn't a non-negative int reads as 0.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def extract_rating_from_metadata(meta: dict | None) -> tuple[int, int]:
    """Return ``(useful, not_useful)`` counters from a drawer's metadata."""
    if not meta:
        return (0, 0)
    return (_coerce_count(meta.get(USEFUL_KEY)), _coerce_count(meta.get(NOT_USEFUL_KEY)))


def apply_rating_to_metadata(meta: dict, useful: bool) -> dict:
    """Increment the appropriate counter in ``meta`` in place and return it.

    Mutates only the two rating keys — every other metadata field, and the
    drawer's stored content, are left untouched.
    """
    u, n = extract_rating_from_metadata(meta)
    if useful:
        meta[USEFUL_KEY] = u + 1
    else:
        meta[NOT_USEFUL_KEY] = n + 1
    return meta


def net_rating(meta: dict | None) -> int:
    """Net rating signal: useful − not_useful (can be negative)."""
    u, n = extract_rating_from_metadata(meta)
    return u - n


def rating_distance_adjustment(meta: dict | None) -> float:
    """Bounded cosine-distance shift for a rated drawer.

    Positive net → negative shift (drawer moves *up* toward distance 0).
    Negative net → positive shift (drawer moves *down*). Magnitude is
    ``net * RATING_DISTANCE_STEP`` clamped to ``±RATING_DISTANCE_CAP``.

    Returns a value to be *added* to the effective distance, so a useful
    drawer (positive net) yields a negative number.
    """
    net = net_rating(meta)
    if net == 0:
        return 0.0
    raw = -net * RATING_DISTANCE_STEP
    if raw > RATING_DISTANCE_CAP:
        return RATING_DISTANCE_CAP
    if raw < -RATING_DISTANCE_CAP:
        return -RATING_DISTANCE_CAP
    return raw
