"""Tests for mempalace.cross_encoder_rerank.

Stub the cross-encoder model so the unit tests run without
sentence-transformers installed. End-to-end A/B against the corpus
lives in scripts/eval_cross_encoder_rerank.py.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import pytest

from mempalace import cross_encoder_rerank as cer
from mempalace.config import MempalaceConfig


@contextmanager
def _env(**overrides: "str | None") -> Iterator[None]:
    """Set env vars for the duration of a block. None deletes."""
    saved: dict[str, "str | None"] = {}
    try:
        for key, value in overrides.items():
            saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, prev in saved.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


# ── is_enabled ───────────────────────────────────────────────────────────────


def test_is_enabled_default_off():
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=None):
        assert cer.is_enabled() is False
        assert cer.is_enabled({}) is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_is_enabled_truthy_env(val: str):
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=val):
        assert cer.is_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_is_enabled_falsy_env(val: str):
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=val):
        # Empty string falls through to file-config — but here the
        # file-config is also unset, so we get the default False.
        assert cer.is_enabled() is False


def test_is_enabled_from_file_config():
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=None):
        assert cer.is_enabled({"cross_encoder_rerank": True}) is True
        assert cer.is_enabled({"cross_encoder_rerank": False}) is False
        assert cer.is_enabled({"cross_encoder_rerank": "yes"}) is True


def test_env_wins_over_file_config():
    """Env var overrides file config (matches the rest of MempalaceConfig)."""
    with _env(MEMPALACE_RERANK_CROSS_ENCODER="1"):
        # Even when file says False, env=1 wins.
        assert cer.is_enabled({"cross_encoder_rerank": False}) is True
    with _env(MEMPALACE_RERANK_CROSS_ENCODER="0"):
        # Env=0 wins over file=True.
        assert cer.is_enabled({"cross_encoder_rerank": True}) is False


# ── model + top_n resolution ─────────────────────────────────────────────────


def test_default_model_is_minilm_l_6():
    """Issue #179 specifies ms-marco-MiniLM-L-6-v2 as the default — 22M params, CPU-friendly."""
    with _env(MEMPALACE_RERANK_CROSS_ENCODER_MODEL=None):
        assert cer.get_model_name() == "cross-encoder/ms-marco-MiniLM-L-6-v2"
        assert cer.DEFAULT_MODEL == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_model_override_via_env():
    with _env(MEMPALACE_RERANK_CROSS_ENCODER_MODEL="cross-encoder/ms-marco-MiniLM-L-12-v2"):
        assert cer.get_model_name() == "cross-encoder/ms-marco-MiniLM-L-12-v2"


def test_model_override_via_file_config():
    with _env(MEMPALACE_RERANK_CROSS_ENCODER_MODEL=None):
        assert (
            cer.get_model_name({"cross_encoder_model": "cross-encoder/ms-marco-MiniLM-L-12-v2"})
            == "cross-encoder/ms-marco-MiniLM-L-12-v2"
        )


def test_default_top_n_is_25():
    with _env(MEMPALACE_RERANK_TOP_N=None):
        assert cer.get_top_n() == 25
        assert cer.DEFAULT_TOP_N == 25


def test_top_n_override_via_env():
    with _env(MEMPALACE_RERANK_TOP_N="50"):
        assert cer.get_top_n() == 50


def test_top_n_override_via_file_config():
    with _env(MEMPALACE_RERANK_TOP_N=None):
        assert cer.get_top_n({"cross_encoder_top_n": 10}) == 10
        assert cer.get_top_n({"cross_encoder_top_n": "10"}) == 10


def test_top_n_invalid_falls_back_to_default():
    with _env(MEMPALACE_RERANK_TOP_N="not-a-number"):
        assert cer.get_top_n() == cer.DEFAULT_TOP_N
    with _env(MEMPALACE_RERANK_TOP_N="-1"):
        assert cer.get_top_n() == cer.DEFAULT_TOP_N
    with _env(MEMPALACE_RERANK_TOP_N="0"):
        assert cer.get_top_n() == cer.DEFAULT_TOP_N


# ── rerank() core behavior ───────────────────────────────────────────────────


def _make_hit(text: str, **extra) -> dict:
    """Construct a minimal hit dict matching what search_memories produces."""
    return {
        "text": text,
        "drawer_id": extra.get("drawer_id", text[:8]),
        "wing": "test",
        "room": "test",
        "similarity": 0.5,
        "distance": 1.0,
        "matched_via": "drawer",
        **extra,
    }


def test_rerank_empty_hits_passthrough():
    """Empty input passes straight through — no scorer call."""

    def scorer(_pairs):
        raise AssertionError("scorer should not be called on empty hits")

    assert cer.rerank("query", [], scorer=scorer) == []


def test_rerank_reorders_by_score():
    """Higher-scored documents float to the top."""
    hits = [
        _make_hit("apple banana"),
        _make_hit("relevant exact match for query"),
        _make_hit("totally unrelated text"),
    ]

    # Fake scorer: higher score for the doc containing "relevant".
    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        return [
            10.0 if "relevant" in doc else (-5.0 if "unrelated" in doc else 1.0) for _, doc in pairs
        ]

    reranked = cer.rerank("query", hits, scorer=scorer, top_n=10)

    assert [h["text"] for h in reranked] == [
        "relevant exact match for query",
        "apple banana",
        "totally unrelated text",
    ]


def test_rerank_attaches_cross_encoder_score():
    """Each rescored hit gains a ``cross_encoder_score`` key for observability."""
    hits = [_make_hit("foo"), _make_hit("bar")]

    def scorer(pairs):
        return [3.14, 2.71]

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=10)
    scores = {h["text"]: h["cross_encoder_score"] for h in reranked}
    assert scores == {"foo": 3.14, "bar": 2.71}


def test_rerank_does_not_mutate_input():
    """rerank returns a new list; input hits keep their original order + fields."""
    hits = [_make_hit("a"), _make_hit("b")]

    def scorer(_pairs):
        return [-1.0, 5.0]

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=10)
    # Original order preserved on the input.
    assert [h["text"] for h in hits] == ["a", "b"]
    assert "cross_encoder_score" not in hits[0]
    assert "cross_encoder_score" not in hits[1]
    # Reranked list has new top.
    assert reranked[0]["text"] == "b"


def test_rerank_respects_top_n_window():
    """Only the top-N hits are rescored; the tail keeps its fused position."""
    hits = [_make_hit(f"doc-{i}") for i in range(10)]

    def scorer(pairs):
        # Invert the order within the top-N: later docs in the head get higher score.
        return list(range(len(pairs)))

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=3)

    # Top 3 reordered (doc-2 was scored 2, doc-1 was scored 1, doc-0 was 0).
    assert [h["text"] for h in reranked[:3]] == ["doc-2", "doc-1", "doc-0"]
    # Tail (doc-3 through doc-9) keeps the original fused order.
    assert [h["text"] for h in reranked[3:]] == [f"doc-{i}" for i in range(3, 10)]


def test_rerank_top_n_zero_is_noop():
    """top_n <= 0 disables the rerank — hits pass through untouched."""
    hits = [_make_hit("a"), _make_hit("b")]

    def scorer(_pairs):
        raise AssertionError("scorer should not be called when top_n=0")

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=0)
    assert reranked == hits


def test_rerank_handles_hits_without_text():
    """Hits with empty text sink to the bottom of the reranked window — recall preserved."""
    hits = [
        _make_hit(""),
        _make_hit("real content"),
        _make_hit(""),
    ]

    def scorer(pairs):
        # Only "real content" gets scored — one pair.
        assert len(pairs) == 1
        return [7.0]

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=10)
    # Real content rose to the top, but no hit was dropped.
    assert reranked[0]["text"] == "real content"
    assert len(reranked) == 3
    assert {h["text"] for h in reranked[1:]} == {""}


def test_rerank_scorer_exception_returns_fused_order():
    """If the scorer raises, the rerank degrades gracefully (returns original order)."""
    hits = [_make_hit("a"), _make_hit("b")]

    def scorer(_pairs):
        raise RuntimeError("OOM")

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=10)
    # Returns the original hits list as-is; no rerank scores attached.
    assert reranked == hits


def test_rerank_stable_for_tie_scores():
    """Equal scores preserve the original fused ordering (stable sort by index)."""
    hits = [_make_hit("first"), _make_hit("second"), _make_hit("third")]

    def scorer(_pairs):
        return [1.0, 1.0, 1.0]

    reranked = cer.rerank("q", hits, scorer=scorer, top_n=10)
    assert [h["text"] for h in reranked] == ["first", "second", "third"]


# ── scorer caching ───────────────────────────────────────────────────────────


def test_get_scorer_is_cached(monkeypatch):
    """Loading a CrossEncoder is expensive; identical calls return the same callable."""
    cer.reset_model_cache()

    build_calls: list[str] = []

    def fake_build(model_name: str):
        build_calls.append(model_name)

        def score(_pairs):
            return [0.0] * len(_pairs)

        return score

    monkeypatch.setattr(cer, "_build_scorer", fake_build)

    s1 = cer.get_scorer("fake-model")
    s2 = cer.get_scorer("fake-model")
    assert s1 is s2
    assert build_calls == ["fake-model"]

    cer.reset_model_cache()


def test_lazy_import_no_sentence_transformers_at_module_load():
    """Importing the module must NOT import sentence_transformers — keeps the off-by-default cost zero."""
    import importlib
    import sys

    # If sentence_transformers happens to already be loaded for unrelated
    # reasons, this test still passes the spirit of the rule by re-importing
    # the rerank module and checking it doesn't trigger a new load.
    if "sentence_transformers" in sys.modules:
        pytest.skip("sentence_transformers already imported (test runner env)")

    importlib.reload(cer)
    assert "sentence_transformers" not in sys.modules


# ── MempalaceConfig integration ──────────────────────────────────────────────


def test_config_cross_encoder_rerank_default_off(tmp_path):
    cfg = MempalaceConfig(config_dir=tmp_path)
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=None):
        assert cfg.cross_encoder_rerank is False


def test_config_cross_encoder_rerank_env_on(tmp_path):
    cfg = MempalaceConfig(config_dir=tmp_path)
    with _env(MEMPALACE_RERANK_CROSS_ENCODER="1"):
        assert cfg.cross_encoder_rerank is True


def test_config_cross_encoder_rerank_via_file(tmp_path):
    import json

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"cross_encoder_rerank": True}))
    cfg = MempalaceConfig(config_dir=tmp_path)
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=None):
        assert cfg.cross_encoder_rerank is True


def test_config_model_and_top_n_defaults(tmp_path):
    cfg = MempalaceConfig(config_dir=tmp_path)
    with _env(MEMPALACE_RERANK_CROSS_ENCODER_MODEL=None, MEMPALACE_RERANK_TOP_N=None):
        assert cfg.cross_encoder_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
        assert cfg.cross_encoder_top_n == 25


# ── searcher integration ─────────────────────────────────────────────────────


def test_searcher_resolve_returns_none_when_disabled(tmp_path, monkeypatch):
    """When the flag is off, _cross_encoder_rerank_config returns None — no model load attempted."""
    from mempalace import searcher

    monkeypatch.setattr(
        "mempalace.config.MempalaceConfig",
        lambda *a, **k: MempalaceConfig(config_dir=tmp_path),
    )
    with _env(MEMPALACE_RERANK_CROSS_ENCODER=None):
        assert searcher._cross_encoder_rerank_config() is None


def test_searcher_resolve_returns_config_when_enabled(tmp_path, monkeypatch):
    """When enabled, the resolver returns the configured model + top_n."""
    from mempalace import searcher

    monkeypatch.setattr(
        "mempalace.config.MempalaceConfig",
        lambda *a, **k: MempalaceConfig(config_dir=tmp_path),
    )
    with _env(
        MEMPALACE_RERANK_CROSS_ENCODER="1",
        MEMPALACE_RERANK_CROSS_ENCODER_MODEL="custom-model",
        MEMPALACE_RERANK_TOP_N="13",
    ):
        cfg = searcher._cross_encoder_rerank_config()
    assert cfg == {"model": "custom-model", "top_n": 13}
