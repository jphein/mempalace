"""Optional cross-encoder reranking for the retrieval path.

OPT-IN FEATURE. Default off. Gated by ``MEMPALACE_RERANK_CROSS_ENCODER=1`` or
``config.json {"cross_encoder_rerank": true}``.

Background
----------

Re-scores the top-N candidates produced by the existing hybrid pipeline
(vector + BM25 + AGE fusion) using a lightweight cross-encoder. The
True Memory comparison (`docs/research/2026-05-24-true-memory-comparison.md`)
showed a cheap reranker captures most of the rerank value — upgrading
from 22M (``ms-marco-MiniLM-L-6-v2``) to 149M (``ms-marco-MiniLM-L-12-v2``)
only moves the needle 1.3pp within the 256d subfamily. We therefore ship
the smallest effective model and let operators upgrade via env if their
workload justifies it.

Design
------

The rerank runs *after* the existing fusion (convex / RRF) — it never
replaces fusion, it reorders the already-fused top-N. This composes with
every ``candidate_strategy`` (vector, union, hybrid) and with every
``fusion_mode`` (convex, rrf). When disabled, this module imports nothing
heavy and adds zero query-time cost.

Constraints (from techempower-org/mempalace#179)
------------------------------------------------

* **Local-first.** ``sentence-transformers`` is an optional ``[rerank]``
  extra; the import is lazy. Operators who opt in install the extra.
* **CPU-only by default.** ``ms-marco-MiniLM-L-6-v2`` is 22M parameters
  — fits on CPU with a sub-200ms ceiling for top-25 rerank batches.
* **Default off.** Per JP's no-model-at-query-time default — no external
  model loads unless the env or config flag is set.

Configuration
-------------

* ``MEMPALACE_RERANK_CROSS_ENCODER=1`` (env) or
  ``"cross_encoder_rerank": true`` (config.json) — enable the stage.
* ``MEMPALACE_RERANK_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2``
  — override the model. Default is the 22M MiniLM-L-6 cross-encoder.
* ``MEMPALACE_RERANK_TOP_N=25`` — rerank ceiling. Latency scales linearly
  with this; the rerank only reorders, so the ceiling is a quality/cost
  knob, not a recall floor.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


ENV_ENABLE = "MEMPALACE_RERANK_CROSS_ENCODER"
ENV_MODEL = "MEMPALACE_RERANK_CROSS_ENCODER_MODEL"
ENV_TOP_N = "MEMPALACE_RERANK_TOP_N"

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEFAULT_TOP_N = 25

_TRUTHY = ("1", "true", "yes", "on")

# Process-wide cache: model name → callable(pairs) → list[float].
# Cached aggressively because the cross-encoder is ~90MB to load.
_MODEL_CACHE: dict[str, Callable[[list[tuple[str, str]]], list[float]]] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def is_enabled(file_config: Optional[dict] = None) -> bool:
    """True iff the cross-encoder rerank flag is set in env or config.

    Env wins over file config (matches the rest of MempalaceConfig).
    """
    env_val = os.environ.get(ENV_ENABLE, "").strip().lower()
    if env_val:
        return env_val in _TRUTHY
    if file_config is None:
        return False
    return bool(file_config.get("cross_encoder_rerank", False))


def get_model_name(file_config: Optional[dict] = None) -> str:
    """Return the cross-encoder model name to load."""
    env_val = os.environ.get(ENV_MODEL, "").strip()
    if env_val:
        return env_val
    if file_config is not None:
        cfg_val = file_config.get("cross_encoder_model")
        if cfg_val:
            return str(cfg_val).strip()
    return DEFAULT_MODEL


def get_top_n(file_config: Optional[dict] = None) -> int:
    """Return the rerank ceiling (top-N candidates to rerank)."""
    raw = os.environ.get(ENV_TOP_N, "").strip()
    if not raw and file_config is not None:
        cfg_val = file_config.get("cross_encoder_top_n")
        if cfg_val is not None:
            raw = str(cfg_val).strip()
    if not raw:
        return DEFAULT_TOP_N
    try:
        v = int(raw)
        if v <= 0:
            raise ValueError
        return v
    except ValueError:
        logger.warning(
            "cross_encoder_rerank: invalid %s=%r — falling back to %d",
            ENV_TOP_N,
            raw,
            DEFAULT_TOP_N,
        )
        return DEFAULT_TOP_N


def _build_scorer(model_name: str) -> Callable[[list[tuple[str, str]]], list[float]]:
    """Construct a callable that scores ``(query, doc)`` pairs with a cross-encoder.

    Lazy-imports ``sentence_transformers`` so the heavy import only fires
    when the feature is opted in. Raises ``ImportError`` with an actionable
    message if the extra isn't installed.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover — exercised via install paths
        raise ImportError(
            "cross-encoder rerank requires the 'rerank' extra: "
            "pip install mempalace[rerank] "
            "(or pip install sentence-transformers)"
        ) from exc

    model = CrossEncoder(model_name)

    def score(pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        # CrossEncoder.predict returns numpy floats; coerce to Python floats
        # so callers don't need numpy in their type universe.
        raw_scores = model.predict(pairs)
        try:
            return [float(s) for s in raw_scores.tolist()]
        except AttributeError:
            return [float(s) for s in raw_scores]

    return score


def get_scorer(model_name: str) -> Callable[[list[tuple[str, str]]], list[float]]:
    """Return a cached scorer for ``model_name``.

    Cross-encoder load is ~90MB and a few seconds; cache aggressively.
    The cache survives the life of the process and is process-local
    (no cross-process sharing required at this scale).
    """
    cached = _MODEL_CACHE.get(model_name)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        scorer = _build_scorer(model_name)
        _MODEL_CACHE[model_name] = scorer
        logger.info("cross_encoder_rerank: loaded model %s", model_name)
        return scorer


def reset_model_cache() -> None:
    """Drop all cached cross-encoder models. Test/eval-only."""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def rerank(
    query: str,
    hits: list[dict],
    *,
    model_name: str = DEFAULT_MODEL,
    top_n: int = DEFAULT_TOP_N,
    scorer: Optional[Callable[[list[tuple[str, str]]], list[float]]] = None,
) -> list[dict]:
    """Reorder ``hits`` by cross-encoder relevance to ``query``.

    Only the top ``top_n`` hits are rescored — anything past that keeps its
    fused position. This bounds latency on large candidate pools while
    still letting the rerank correct the head, which is where it matters
    for R@k.

    Returns a new list (does not mutate the input list). Each rescored hit
    gains a ``cross_encoder_score`` key carrying the raw model output for
    downstream observability. Hits without text are scored as ``-inf`` so
    they sink to the bottom of the reranked window — keeping recall
    invariant (no hit is dropped).

    ``scorer`` is an injection seam for tests so unit tests don't need to
    load the real model.
    """
    if not hits:
        return hits
    if top_n <= 0:
        return hits

    head = hits[:top_n]
    tail = hits[top_n:]

    pairs: list[tuple[str, str]] = []
    score_indices: list[int] = []
    for idx, hit in enumerate(head):
        text = hit.get("text") or ""
        if not text:
            continue
        pairs.append((query, text))
        score_indices.append(idx)

    if not pairs:
        return hits

    if scorer is None:
        scorer = get_scorer(model_name)

    try:
        raw_scores = scorer(pairs)
    except Exception:
        # Reranking is a quality lift, not a correctness invariant. If it
        # fails (model unavailable, OOM, etc.) we keep the fused ordering
        # so the caller still gets a usable result set. Log loud so the
        # operator sees the regression in the daemon logs.
        logger.exception("cross_encoder_rerank: scorer failed; returning fused order")
        return hits

    NEG_INF = float("-inf")
    scored: list[tuple[float, int, dict]] = []
    score_iter = iter(raw_scores)
    for idx, hit in enumerate(head):
        new_hit = dict(hit)
        if idx in score_indices:
            score = next(score_iter)
            new_hit["cross_encoder_score"] = round(float(score), 6)
            scored.append((float(score), idx, new_hit))
        else:
            scored.append((NEG_INF, idx, new_hit))

    # Sort by score desc, with original index as tiebreaker to keep the
    # sort stable when two candidates score identically.
    scored.sort(key=lambda t: (-t[0], t[1]))
    reranked_head = [new_hit for _, _, new_hit in scored]
    return reranked_head + tail


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TOP_N",
    "ENV_ENABLE",
    "ENV_MODEL",
    "ENV_TOP_N",
    "is_enabled",
    "get_model_name",
    "get_top_n",
    "get_scorer",
    "reset_model_cache",
    "rerank",
]
