"""TF-IDF auto-tag extraction for drawer write time (#201).

Auto-tags are an opt-in convenience layer on top of the manual tag system
defined in ``mempalace.tags``. Explicit caller-supplied tags always win;
this module only contributes when the caller passes ``tags=None``.

The extractor is hand-rolled (no sklearn) to keep the dep footprint flat
and the cold-import time negligible. Given mempalace's read-heavy access
pattern, the cost we care about is the per-write extraction time and the
amortised cost of rebuilding the IDF table when the corpus shifts.

Public surface
--------------

``extract_tags`` — given a content string and an IDF table, return a
normalised list of 3-8 tag candidates ordered by descending score.

``build_idf`` — given an iterable of drawer-content strings (the corpus
snapshot), compute the document-frequency table and produce IDF weights.

``IdfCache`` — process-local cache that holds at most one IDF table per
``(wing, room)`` scope. Callers ask for the table they need; the cache
either returns the warm copy or runs ``build_idf`` against the supplied
corpus builder. TTL is intentionally generous because the corpus only
shifts when drawers are written, and the per-write extraction tolerates
slightly stale weights — they only rank existing tokens, not invent them.
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Callable, Iterable, Optional

from .tags import normalise_tag

# Token shape: 3-30 char alphanumeric run, may carry interior . _ -.
# We deliberately reject pure-numeric tokens (e.g. "2026", "123") — they
# rarely make for useful tags and bloat the IDF table.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-.]{2,29}")

# Minimum token frequency for a candidate to even be considered. Keeps
# one-off typos and noise out of the extraction.
_MIN_TF = 1

# Per-call extraction bounds. The issue calls for "3-8 tags"; we emit at
# most ``_MAX_TAGS`` (default 5) and accept short content yielding fewer
# than the lower bound rather than padding with low-signal noise.
_DEFAULT_K = 5

# Conservative English-only stopword list. We keep it inline rather than
# pulling NLTK; the goal is "drop obvious filler", not "perfect linguistic
# coverage". Extend cautiously — every entry costs a tag a user might
# actually want to surface.
_STOPWORDS = frozenset(
    """
    the and for that with this from have but not are was were been being
    will would could should can may might must shall about above after
    against all also any because before below between both each few how
    into more most much only other over same some such than then these
    they those through under until very what when where which while who
    whom why your yours their them then theirs ours ourselves yourselves
    himself herself itself themselves does did done having here there
    just like make made make made many one two three four five six seven
    eight nine ten own its his her hers them they our you yours its lets
    let off out per via etc upon onto off ago between among across
    inside outside around against amongst within without behind beyond
    along throughout meanwhile however therefore thus hence whereas
    notwithstanding regarding concerning anything everything something
    nothing everyone anyone someone nobody anybody somebody whoever
    whatever whichever whenever wherever however whomever
    """.split()
)


def _tokenize(text: str) -> list[str]:
    """Lower-case, strip punctuation, drop stopwords, dedupe by position."""
    if not isinstance(text, str) or not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(lowered):
        tok = match.group(0)
        if tok in _STOPWORDS:
            continue
        # Strip trailing punctuation the regex's interior class allowed.
        tok = tok.strip("._-")
        if len(tok) < 3:
            continue
        tokens.append(tok)
    return tokens


def build_idf(corpus: Iterable[str]) -> dict[str, float]:
    """Compute IDF weights for a corpus snapshot.

    Each input string is one document. IDF uses the standard smoothed
    formula ``log((N + 1) / (df + 1)) + 1`` so terms appearing in every
    document still earn a positive (if small) weight.

    Returns an empty dict when the corpus is empty so callers can treat
    the empty case as "no extraction possible" rather than crashing.
    """
    df: dict[str, int] = {}
    n = 0
    for doc in corpus:
        n += 1
        seen = set(_tokenize(doc))
        for tok in seen:
            df[tok] = df.get(tok, 0) + 1
    if n == 0:
        return {}
    return {tok: math.log((n + 1) / (count + 1)) + 1.0 for tok, count in df.items()}


def _score_tokens(content: str, idf: dict[str, float]) -> list[tuple[str, float]]:
    """Return ``(token, tf-idf score)`` pairs for the candidate ranking pass.

    Tokens absent from the IDF table get a neutral IDF of 1.0 — this lets
    the extractor degrade gracefully when the cache is cold or the corpus
    is brand-new (the IDF table is empty), instead of silently returning
    zero tags forever until a backfill runs.
    """
    tokens = _tokenize(content)
    if not tokens:
        return []
    tf: dict[str, int] = {}
    for tok in tokens:
        tf[tok] = tf.get(tok, 0) + 1
    scored: list[tuple[str, float]] = []
    for tok, count in tf.items():
        if count < _MIN_TF:
            continue
        weight = idf.get(tok, 1.0)
        scored.append((tok, count * weight))
    return scored


def extract_tags(
    content: str,
    idf: Optional[dict[str, float]] = None,
    k: int = _DEFAULT_K,
) -> list[str]:
    """Return up to ``k`` normalised tag candidates for ``content``.

    ``idf`` is the IDF table from ``build_idf``. Pass ``None`` for a
    cold-start corpus — the extractor falls back to raw term frequency
    (every term gets IDF=1.0), which is poor but deterministic.

    Output is already normalised via ``mempalace.tags.normalise_tag``,
    so callers can pipe directly into ``apply_tags_to_metadata``.
    """
    if k < 1:
        return []
    scored = _score_tokens(content, idf or {})
    if not scored:
        return []
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    seen: dict[str, None] = {}
    for tok, _score in scored:
        norm = normalise_tag(tok)
        if not norm or norm in seen:
            continue
        seen[norm] = None
        if len(seen) >= k:
            break
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Process-local IDF cache
# ---------------------------------------------------------------------------


class IdfCache:
    """Thread-safe TTL cache of IDF tables, keyed by ``(wing, room)``.

    Re-running ``build_idf`` on every write would force a full corpus
    scan per drawer; the cache amortises that across writes within the
    same wing/room. Entries expire after ``ttl_seconds`` (default 5 min)
    so a write-heavy session eventually sees fresh weights without
    requiring explicit invalidation calls from every write path.

    The cache holds *snapshots*, not handles to the backend, so callers
    pass a ``corpus_builder`` callable that yields the current corpus
    when the cache decides a refresh is due. This keeps the extractor
    decoupled from the storage backend (postgres / chroma / tests).
    """

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 32):
        self._ttl = max(1.0, ttl_seconds)
        self._max = max(1, max_entries)
        self._lock = threading.Lock()
        self._store: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}

    def get(
        self,
        wing: str,
        room: str,
        corpus_builder: Callable[[], Iterable[str]],
    ) -> dict[str, float]:
        key = (wing or "", room or "")
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and now - entry[0] < self._ttl:
                return entry[1]
        # Build outside the lock — corpus iteration may touch I/O.
        idf = build_idf(corpus_builder())
        with self._lock:
            if len(self._store) >= self._max:
                # Evict the oldest entry. Tiny cache, linear scan is fine.
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                self._store.pop(oldest_key, None)
            self._store[key] = (now, idf)
        return idf

    def invalidate(self, wing: Optional[str] = None, room: Optional[str] = None) -> None:
        """Drop one entry, all entries for a wing, or the whole cache."""
        with self._lock:
            if wing is None:
                self._store.clear()
                return
            for key in list(self._store.keys()):
                if key[0] != wing:
                    continue
                if room is not None and key[1] != room:
                    continue
                self._store.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
