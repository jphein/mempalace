"""
novelty.py — Gzip-based novelty scoring for drawers
====================================================

Tag drawer contents with a novelty score derived from Normalized
Compression Distance (NCD). Adapted from the True Memory paper
(arXiv:2605.04897), which reports AUC 0.788 for gzip NCD vs 0.484 for
inverted cosine similarity — embedding distance inverts the novelty
signal (short acks like "ok" land far from factual memories while
genuinely novel updates land close).

NCD(a, b) = (|C(a+b)| - min(|C(a)|, |C(b)|)) / max(|C(a)|, |C(b)|)

where |C(x)| is the byte length of gzip(x). Returns 0.0 for identical
strings, approaches 1.0 (and may slightly exceed it) for maximally
different strings.

This module is the core scoring function only. It is intentionally a
tag, not a gate — the write path never blocks based on novelty. Wiring
into the palace-daemon write hook is tracked separately (daemon #45).

No external dependencies: stdlib gzip only. Designed to score in
microseconds per comparison so it can run inline on every drawer write.

Usage:
    from mempalace.novelty import ncd, novelty_score, classify_novelty

    score = novelty_score(new_text, recent_drawer_texts)
    label = classify_novelty(score)  # "novel" | "routine" | "redundant"
"""

import gzip
from functools import lru_cache
from typing import Iterable


__all__ = ["ncd", "novelty_score", "classify_novelty"]


# Compression level 6 is gzip's default — best balance of speed and
# compression ratio for novelty discrimination. Higher levels add cost
# without changing NCD ordering meaningfully.
_GZIP_LEVEL = 6


@lru_cache(maxsize=128)
def _csize(data: bytes) -> int:
    """Compressed byte length of ``data`` under gzip.

    Cached so windowed scoring (which compresses the same ``text`` N
    times across a recent-drawer loop) only pays the gzip cost once
    per distinct input within the cache window.
    """
    return len(gzip.compress(data, compresslevel=_GZIP_LEVEL))


def ncd(a: str, b: str) -> float:
    """Normalized Compression Distance between two strings.

    Returns 0.0 when ``a`` and ``b`` are identical, and approaches 1.0
    as they become maximally different. Values slightly above 1.0 are
    possible for very short non-identical inputs because gzip framing
    overhead dominates — clamp at the call site if a strict [0, 1]
    range is required.

    Empty inputs collapse to 0.0 (two empty strings are identical) or
    1.0 (one empty, one not — maximally different by convention).
    """
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0

    a_bytes = a.encode("utf-8")
    b_bytes = b.encode("utf-8")

    ca = _csize(a_bytes)
    cb = _csize(b_bytes)
    cab = _csize(a_bytes + b_bytes)

    return (cab - min(ca, cb)) / max(ca, cb)


def novelty_score(text: str, recent_texts: Iterable[str]) -> float:
    """Score how novel ``text`` is relative to a window of recent drawers.

    Returns the mean NCD against every entry in ``recent_texts``. Higher
    values mean the text shares less compressible structure with what's
    already in the window — i.e. it is more novel.

    With an empty window the convention is 1.0: a drawer with nothing
    to compare against is treated as fully novel rather than fully
    redundant. This matches the write-path intent (first drawer in a
    fresh wing should not be flagged "redundant").
    """
    scores = [ncd(text, other) for other in recent_texts]
    if not scores:
        return 1.0
    return sum(scores) / len(scores)


def classify_novelty(score: float, threshold: float = 0.5) -> str:
    """Bucket a novelty score into ``novel``, ``routine``, or ``redundant``.

    The two boundaries straddle ``threshold``:

    - ``score >= threshold``         → ``novel``
    - ``threshold/2 <= score < threshold`` → ``routine``
    - ``score < threshold/2``        → ``redundant``

    Default threshold 0.5 puts the novel/routine boundary at the
    midpoint of the [0, 1] NCD range. Callers tuning for their corpus
    can shift it; the bucket names stay stable so downstream filters
    keep working.
    """
    if score >= threshold:
        return "novel"
    if score >= threshold / 2:
        return "routine"
    return "redundant"
