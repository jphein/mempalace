# `mempalace.novelty`

Source: [`mempalace/novelty.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/novelty.py)

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

## Functions

### `ncd`

```python
def ncd(a: str, b: str) -> float
```

Normalized Compression Distance between two strings.

Returns 0.0 when ``a`` and ``b`` are identical, and approaches 1.0
as they become maximally different. Values slightly above 1.0 are
possible for very short non-identical inputs because gzip framing
overhead dominates — clamp at the call site if a strict [0, 1]
range is required.

Empty inputs collapse to 0.0 (two empty strings are identical) or
1.0 (one empty, one not — maximally different by convention).

### `novelty_score`

```python
def novelty_score(text: str, recent_texts: Iterable[str]) -> float
```

Score how novel ``text`` is relative to a window of recent drawers.

Returns the mean NCD against every entry in ``recent_texts``. Higher
values mean the text shares less compressible structure with what's
already in the window — i.e. it is more novel.

With an empty window the convention is 1.0: a drawer with nothing
to compare against is treated as fully novel rather than fully
redundant. This matches the write-path intent (first drawer in a
fresh wing should not be flagged "redundant").

### `classify_novelty`

```python
def classify_novelty(score: float, threshold: float = 0.5) -> str
```

Bucket a novelty score into ``novel``, ``routine``, or ``redundant``.

The two boundaries straddle ``threshold``:

- ``score >= threshold``         → ``novel``
- ``threshold/2 <= score < threshold`` → ``routine``
- ``score < threshold/2``        → ``redundant``

Default threshold 0.5 puts the novel/routine boundary at the
midpoint of the [0, 1] NCD range. Callers tuning for their corpus
can shift it; the bucket names stay stable so downstream filters
keep working.
