"""Unit tests for scripts/eval_fusion_ab.py — the convex-vs-RRF A/B harness.

Verifies the pure scoring math (rank_of_target, evaluate_ranking,
compare_runs) and the run orchestration via an injected stub search_fn.
Nothing here touches the live corpus, daemon, or GPUs — per issue #162 the
real comparison run is deferred until the KG backfill completes.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_fusion_ab.py"
_spec = importlib.util.spec_from_file_location("eval_fusion_ab", _SCRIPT)
ab = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve cls.__module__.
sys.modules["eval_fusion_ab"] = ab
_spec.loader.exec_module(ab)


# ── rank_of_target ─────────────────────────────────────────────────────────


def test_rank_of_target_basename_match():
    files = ["/long/path/other.md", "/x/target.md", "third.md"]
    assert ab.rank_of_target(files, "/different/dir/target.md") == 2


def test_rank_of_target_missing_returns_none():
    assert ab.rank_of_target(["a.md", "b.md"], "c.md") is None


def test_rank_of_target_first_match_wins():
    files = ["dup.md", "dup.md"]
    assert ab.rank_of_target(files, "dup.md") == 1


def test_rank_of_target_handles_empty_and_whitespace():
    assert ab.rank_of_target(["", "  ", "hit.md"], "hit.md") == 3
    assert ab.rank_of_target([], "hit.md") is None


# ── evaluate_ranking ───────────────────────────────────────────────────────


def test_evaluate_ranking_empty():
    m = ab.evaluate_ranking([])
    assert m.n_probes == 0
    assert m.mrr == 0.0
    assert m.recall_at_5 == 0.0


def test_evaluate_ranking_all_rank_one():
    m = ab.evaluate_ranking([1, 1, 1])
    assert m.mrr == 1.0
    assert m.recall_at_5 == 1.0
    assert m.recall_at_10 == 1.0
    assert m.found == 3


def test_evaluate_ranking_mixed_ranks():
    # ranks: 1, 2, miss, 6
    m = ab.evaluate_ranking([1, 2, None, 6])
    # MRR = (1 + 0.5 + 0 + 1/6) / 4
    assert math.isclose(m.mrr, (1 + 0.5 + 1 / 6) / 4)
    # R@5: ranks <=5 are 1 and 2 → 2/4
    assert m.recall_at_5 == 0.5
    # R@10: 1, 2, 6 → 3/4
    assert m.recall_at_10 == 0.75
    assert m.found == 3


def test_evaluate_ranking_boundary_at_five_and_ten():
    m = ab.evaluate_ranking([5, 10, 11])
    assert m.recall_at_5 == pytest.approx(1 / 3)  # only rank 5
    assert m.recall_at_10 == pytest.approx(2 / 3)  # ranks 5 and 10


def test_metrics_as_dict_rounds():
    m = ab.evaluate_ranking([3])
    d = m.as_dict()
    assert d["mrr"] == round(1 / 3, 4)
    assert set(d) == {"n_probes", "mrr", "recall_at_5", "recall_at_10", "found"}


# ── compare_runs ───────────────────────────────────────────────────────────


def test_compare_runs_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        ab.compare_runs(["q1", "q2"], [1], [1, 2])


def test_compare_runs_detects_improvement_and_regression():
    queries = ["q_improved", "q_regressed", "q_same"]
    ranks_a = [5, 1, 3]
    ranks_b = [1, 4, 3]  # B better on first, worse on second, tied on third
    cmp = ab.compare_runs(queries, ranks_a, ranks_b)
    improved_qs = {row["query"] for row in cmp.improved}
    regressed_qs = {row["query"] for row in cmp.regressed}
    assert improved_qs == {"q_improved"}
    assert regressed_qs == {"q_regressed"}


def test_compare_runs_miss_to_hit_is_improvement():
    cmp = ab.compare_runs(["q"], [None], [3])
    assert len(cmp.improved) == 1
    assert len(cmp.regressed) == 0


def test_compare_runs_hit_to_miss_is_regression():
    cmp = ab.compare_runs(["q"], [3], [None])
    assert len(cmp.regressed) == 1
    assert len(cmp.improved) == 0


def test_compare_runs_delta_sign_favors_b():
    # B strictly better everywhere → positive deltas. Use ranks that move
    # R@5 (A misses / ranks past 5, B inside 5).
    cmp = ab.compare_runs(["a", "b"], [8, None], [1, 2])
    d = cmp.as_dict()
    assert d["delta"]["mrr"] > 0
    assert d["delta"]["recall_at_5"] > 0
    assert d["label_a"] == "convex"
    assert d["label_b"] == "rrf"


# ── run_ab orchestration (injected stub, no corpus) ────────────────────────


def _make_stub(rank_map):
    """Build a search_fn stub.

    ``rank_map`` maps ``(query, fusion_mode)`` → list of source_file names in
    rank order. The stub returns those as result hits so the harness computes
    deterministic ranks without any retrieval.
    """

    def stub(query, palace_path, *, n_results, candidate_strategy, fusion_mode):
        files = rank_map[(query, fusion_mode)]
        return {"results": [{"source_file": f} for f in files]}

    return stub


def test_run_ab_end_to_end_with_stub():
    probes = [
        ["find the cat", "cat.md", "why1"],
        ["find the dog", "dog.md", "why2"],
    ]
    rank_map = {
        # convex: cat at rank 2, dog at rank 1
        ("find the cat", "convex"): ["other.md", "cat.md"],
        ("find the dog", "convex"): ["dog.md", "other.md"],
        # rrf: cat at rank 1 (improved), dog at rank 1 (same)
        ("find the cat", "rrf"): ["cat.md", "other.md"],
        ("find the dog", "rrf"): ["dog.md", "other.md"],
    }
    report = ab.run_ab(
        probes,
        palace_path="dummy",
        candidate_strategy="hybrid",
        n_results=5,
        search_fn=_make_stub(rank_map),
    )
    # RRF improved the cat probe (rank 2 → 1), dog unchanged.
    assert report["delta"]["mrr"] > 0
    assert {r["query"] for r in report["improved"]} == {"find the cat"}
    assert report["regressed"] == []
    assert report["candidate_strategy"] == "hybrid"
    assert report["n_results"] == 5
    assert "convex" in report["timing_secs"]
    assert "rrf" in report["timing_secs"]


def test_run_ab_passes_fusion_mode_through():
    """The stub asserts it receives both fusion modes, proving the harness
    drives each pipeline under the right mode."""
    seen_modes = set()

    def recording_stub(query, palace_path, *, n_results, candidate_strategy, fusion_mode):
        seen_modes.add(fusion_mode)
        return {"results": [{"source_file": "x.md"}]}

    ab.run_ab([["q", "x.md", "w"]], palace_path="d", search_fn=recording_stub)
    assert seen_modes == {"convex", "rrf"}


def test_load_probes_accepts_legacy_list_shape(tmp_path):
    p = tmp_path / "legacy.json"
    p.write_text('[["q1", "f1.md", "w1"], ["q2", "f2.md", "w2"]]', encoding="utf-8")
    out = ab._load_probes(str(p))
    assert out == [["q1", "f1.md", "w1"], ["q2", "f2.md", "w2"]]


def test_load_probes_accepts_v2_dict_shape(tmp_path):
    """The on-disk shape used by ``scripts/probes_v2_git_derived.json``."""
    p = tmp_path / "v2.json"
    p.write_text(
        '{"_meta": {"n_probes": 2}, '
        '"probes": ['
        '{"query": "q1", "expected": "f1.md", "why": "w1"}, '
        '{"query": "q2", "expected": "f2.md", "why": "w2"}'
        "]}",
        encoding="utf-8",
    )
    out = ab._load_probes(str(p))
    assert out == [["q1", "f1.md", "w1"], ["q2", "f2.md", "w2"]]


def test_load_probes_v2_dict_treats_why_as_optional(tmp_path):
    p = tmp_path / "v2_nowhy.json"
    p.write_text(
        '{"probes": [{"query": "q1", "expected": "f1.md"}]}',
        encoding="utf-8",
    )
    out = ab._load_probes(str(p))
    assert out == [["q1", "f1.md", ""]]


def test_load_probes_rejects_dict_without_probes_list(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"not": "a probe set"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must carry a 'probes' list"):
        ab._load_probes(str(p))


def test_load_probes_rejects_scalar(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON list of triples or a dict"):
        ab._load_probes(str(p))


def test_main_refuses_palace_path_without_ack(capsys, tmp_path):
    probes = tmp_path / "p.json"
    probes.write_text('[["q", "f.md", "w"]]', encoding="utf-8")
    rc = ab.main(["--probes", str(probes), "--palace-path", "/dummy"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "self-contained eval" in err or "i-know-the-backfill-is-done" in err


def test_main_rejects_mine_and_palace_together(capsys, tmp_path):
    probes = tmp_path / "p.json"
    probes.write_text('[["q", "f.md", "w"]]', encoding="utf-8")
    rc = ab.main(
        [
            "--probes",
            str(probes),
            "--mine-corpus",
            str(tmp_path),
            "--palace-path",
            "/dummy",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err
