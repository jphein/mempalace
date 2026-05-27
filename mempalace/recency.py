"""Recency weighting for search results (#158).

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
"""

from __future__ import annotations

from datetime import datetime, timezone

FILED_AT_KEY = "filed_at"

# Maximum upward nudge for a freshly-filed drawer, in cosine-distance units.
# Sits below the weakest closet rung (0.04) and at the rating step (0.03) so a
# recency signal tilts order without overpowering semantic match or an
# explicit human rating.
RECENCY_DISTANCE_MAX = 0.03

# Age (in days) at which the boost has decayed to half its maximum. Chosen so
# a drawer stays "fresh enough to nudge" for a couple of months, then fades —
# tunable via PALACE_RECENCY_HALFLIFE_DAYS.
RECENCY_HALFLIFE_DAYS = 30.0


def _parse_filed_at(value) -> datetime | None:
    """Parse a ``filed_at`` metadata value into an aware UTC datetime.

    Drawers store ``datetime.now().isoformat()`` (naive local) but
    externally-edited or imported rows may carry a ``Z`` suffix, an offset,
    or garbage. Anything unparseable returns ``None`` so the caller can treat
    the drawer as ageless rather than crashing.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # fromisoformat in 3.9 rejects a trailing 'Z'; normalize to +00:00.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(meta: dict | None, now: datetime | None = None) -> float | None:
    """Age of a drawer in days from its ``filed_at``, or ``None`` if unknown.

    A negative span (future-dated row) clamps to 0.0 — a clock skew shouldn't
    invert the signal into a penalty.
    """
    if not meta:
        return None
    dt = _parse_filed_at(meta.get(FILED_AT_KEY))
    if dt is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    span = (ref - dt).total_seconds() / 86400.0
    return span if span > 0.0 else 0.0


def recency_distance_adjustment(
    meta: dict | None,
    now: datetime | None = None,
    halflife_days: float = RECENCY_HALFLIFE_DAYS,
    max_shift: float = RECENCY_DISTANCE_MAX,
) -> float:
    """Bounded cosine-distance shift for a drawer's recency.

    Exponential decay: a drawer ``halflife_days`` old keeps half the maximum
    shift, ``2*halflife_days`` a quarter, and so on. The result is always in
    ``[-max_shift, 0.0]`` — a value to be *added* to the effective distance,
    so a fresh drawer (large boost) yields a more-negative number and moves
    *up*. A drawer with no parseable timestamp yields 0.0 (ageless).

    ``max_shift <= 0`` or ``halflife_days <= 0`` disables the signal (returns
    0.0) so a misconfigured weight can't invert ranking.
    """
    if max_shift <= 0.0 or halflife_days <= 0.0:
        return 0.0
    age = age_days(meta, now=now)
    if age is None:
        return 0.0
    decay = 0.5 ** (age / halflife_days)
    return -max_shift * decay
