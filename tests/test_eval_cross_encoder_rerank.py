"""Unit tests for scripts/eval_cross_encoder_rerank.py — the #179 A/B harness.

Verifies the run orchestration via an injected stub search_fn. Nothing
here touches the live corpus, daemon, GPUs, or the cross-encoder model
— per issue #179 the real comparison run is deferred until the KG
backfill + #162 A/B finish.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_cross_encoder_rerank.py"
_spec = importlib.util.spec_from_file_location("eval_cross_encoder_rerank", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve cls.__module__.
sys.modules["eval_cross_encoder_rerank"] = mod
_spec.loader.exec_module(mod)


def _make_stub(rank_map_off: dict, rank_map_on: dict):
    """Build a search_fn stub that branches on the rerank env toggle."""

    def stub(query, palace_path, *, n_results, candidate_strategy, fusion_mode):
        on = os.environ.get("MEMPALACE_RERANK_CROSS_ENCODER", "0").strip() == "1"
        rank_map = rank_map_on if on else rank_map_off
        rank = rank_map.get(query)
        if rank is None:
            return {"results": []}
        results = [{"source_file": f"distractor-{i}.md"} for i in range(rank - 1)]
        results.append({"source_file": "target.md"})
        return {"results": results}

    return stub


def test_run_ab_end_to_end_with_stub():
    """Rerank-on can improve, regress, or hold steady per probe; report sums correctly."""
    probes = [
        ["q-improved", "target.md", "rerank pushes target up"],
        ["q-regressed", "target.md", "rerank buries target"],
        ["q-steady", "target.md", "no change"],
    ]
    ranks_off = {"q-improved": 5, "q-regressed": 2, "q-steady": 3}
    ranks_on = {"q-improved": 1, "q-regressed": 7, "q-steady": 3}
    stub = _make_stub(ranks_off, ranks_on)

    report = mod.run_ab(
        probes,
        palace_path="dummy",
        fusion_mode="rrf",
        candidate_strategy="hybrid",
        n_results=10,
        search_fn=stub,
    )

    assert report["label_a"] == "rerank-off"
    assert report["label_b"] == "rerank-on"
    assert {entry["query"] for entry in report["improved"]} == {"q-improved"}
    assert {entry["query"] for entry in report["regressed"]} == {"q-regressed"}
    assert report["fusion_mode"] == "rrf"
    assert report["candidate_strategy"] == "hybrid"


def test_run_ab_restores_env_after_each_arm():
    """The orchestration toggles MEMPALACE_RERANK_CROSS_ENCODER; restore on exit."""
    prev = os.environ.get("MEMPALACE_RERANK_CROSS_ENCODER")
    try:
        os.environ["MEMPALACE_RERANK_CROSS_ENCODER"] = "preset"

        def stub(*_a, **_k):
            return {"results": [{"source_file": "target.md"}]}

        mod.run_ab(
            [["q", "target.md", "w"]],
            palace_path="dummy",
            search_fn=stub,
        )
        # Env restored to caller's value after the A/B finishes.
        assert os.environ.get("MEMPALACE_RERANK_CROSS_ENCODER") == "preset"
    finally:
        if prev is None:
            os.environ.pop("MEMPALACE_RERANK_CROSS_ENCODER", None)
        else:
            os.environ["MEMPALACE_RERANK_CROSS_ENCODER"] = prev


def test_main_refuses_to_run_without_ack(capsys, tmp_path):
    """Default invocation refuses to hit the live corpus."""
    probes_file = tmp_path / "probes.json"
    probes_file.write_text("[]")
    rc = mod.main(["--probes", str(probes_file), "--palace-path", "dummy"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Refusing to run" in err
