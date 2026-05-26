"""Unit tests for mempalace.searcher._rrf_rank — the RRF fusion mode.

These exercise the rerank wiring (vector ordering + BM25 ordering fused via
RRF) on synthetic candidate dicts. No corpus, no daemon, no embeddings —
the candidates are hand-built so the expected ordering is deterministic.

The pure RRF math is covered separately in ``test_rrf.py``; this file
verifies that ``_rrf_rank`` builds the two rank lists correctly and that
``fusion_mode`` dispatch / validation behave.
"""

from __future__ import annotations

import pytest

from mempalace.searcher import (
    _FUSION_RANKERS,
    _candidate_identity,
    _rrf_rank,
    _validate_fusion_mode,
)


def _cand(id_, text, distance, source_file=None, chunk_index=None, matched_via=""):
    """Build a minimal candidate dict shaped like a search hit."""
    d = {
        "id": id_,
        "text": text,
        "distance": distance,
        "matched_via": matched_via,
    }
    if source_file is not None:
        d["_source_file_full"] = source_file
        d["_chunk_index"] = chunk_index
        d["source_file"] = source_file.rsplit("/", 1)[-1]
    return d


def test_empty_results_returns_empty():
    assert _rrf_rank([], "anything") == []


def test_consensus_winner_ranks_first():
    """A candidate strong in BOTH vector and BM25 fuses above one strong in only one."""
    # 'both' is closest by distance AND contains the query term most.
    cands = [
        _cand("both", "alpha alpha alpha", distance=0.10),
        _cand("vec_only", "nothing relevant here", distance=0.12),
        _cand("bm25_only", "alpha alpha", distance=0.90),
    ]
    ranked = _rrf_rank(cands, "alpha")
    assert ranked[0]["id"] == "both"


def test_adds_bm25_score_to_each_result():
    cands = [
        _cand("a", "alpha beta", distance=0.2),
        _cand("b", "gamma delta", distance=0.3),
    ]
    ranked = _rrf_rank(cands, "alpha")
    assert all("bm25_score" in r for r in ranked)


def test_adds_rrf_score_to_each_result():
    cands = [
        _cand("a", "alpha", distance=0.2),
        _cand("b", "beta", distance=0.3),
    ]
    ranked = _rrf_rank(cands, "alpha")
    assert all("rrf_score" in r for r in ranked)
    # Sorted descending by rrf_score.
    scores = [r["rrf_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_distance_none_candidate_only_in_bm25_list():
    """A distance=None (BM25-only) candidate must still be rankable.

    It contributes to the BM25 ordering but is absent from the vector
    ordering. With a strong BM25 match it should still surface.
    """
    cands = [
        _cand("vec", "unrelated text", distance=0.15),
        _cand("bm25_only", "needle needle needle", distance=None),
    ]
    ranked = _rrf_rank(cands, "needle")
    ids = {r["id"] for r in ranked}
    assert ids == {"vec", "bm25_only"}
    # The BM25-only candidate is the only one matching the query term, so
    # it tops the BM25 list; the vector candidate tops the vector list.
    # Both are rank-1 in one list → tie, both present.
    assert ranked[0]["id"] in {"vec", "bm25_only"}


def test_bm25_backend_match_floored_to_near_max():
    """matched_via=bm25_postgres candidates get a floored bm25_score (>=0.9).

    Mirrors the tokenizer-disagreement guard in _hybrid_rank: a backend
    BM25 hit shouldn't be demoted by the weaker local tokenizer recompute.
    """
    cands = [
        _cand("pg", "ts_rank_cd", distance=None, matched_via="bm25_postgres"),
        _cand("plain", "something else entirely", distance=0.2),
    ]
    _rrf_rank(cands, "ts_rank_cd")
    pg = next(c for c in cands if c["id"] == "pg")
    assert pg["bm25_score"] >= 0.9


def test_chunk_precise_identity_keeps_distinct_chunks_separate():
    """Two chunks of the same file are distinct candidates, not fused as one."""
    cands = [
        _cand("c0", "alpha", distance=0.2, source_file="/x/doc.md", chunk_index=0),
        _cand("c1", "alpha", distance=0.3, source_file="/x/doc.md", chunk_index=1),
    ]
    ranked = _rrf_rank(cands, "alpha")
    assert len(ranked) == 2
    assert {r["id"] for r in ranked} == {"c0", "c1"}


def test_candidate_identity_prefers_source_chunk_tuple():
    r = {
        "_source_file_full": "/a/b.md",
        "_chunk_index": 3,
        "id": "xyz",
        "source_file": "b.md",
    }
    assert _candidate_identity(r) == ("/a/b.md", 3)


def test_candidate_identity_falls_back_to_id_then_source():
    assert _candidate_identity({"id": "the-id", "source_file": "f.md"}) == "the-id"
    assert _candidate_identity({"source_file": "f.md"}) == "f.md"
    # No identifying metadata at all → falls back to object identity (never
    # collides, never raises).
    bare = {"text": "x"}
    assert _candidate_identity(bare) == id(bare)


def test_fusion_mode_registry_has_both_modes():
    assert set(_FUSION_RANKERS) == {"convex", "rrf"}


def test_validate_fusion_mode_accepts_known():
    _validate_fusion_mode("convex")
    _validate_fusion_mode("rrf")


def test_validate_fusion_mode_rejects_unknown():
    with pytest.raises(ValueError, match="fusion_mode must be one of"):
        _validate_fusion_mode("bogus")


def test_custom_k_changes_nothing_structurally():
    """A different k still returns all candidates, sorted descending."""
    cands = [
        _cand("a", "alpha", distance=0.2),
        _cand("b", "beta", distance=0.3),
        _cand("c", "gamma", distance=0.4),
    ]
    ranked = _rrf_rank(cands, "alpha", k=10)
    assert {r["id"] for r in ranked} == {"a", "b", "c"}
    scores = [r["rrf_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
