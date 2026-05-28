#!/usr/bin/env python3
"""eval_cross_encoder_rerank.py — A/B the cross-encoder rerank stage.

Issue #179. Measures whether the optional cross-encoder rerank stage
(``mempalace.cross_encoder_rerank``) improves retrieval quality vs the
baseline hybrid pipeline (vector + BM25 + AGE fusion).

Per ``feedback_test_retrieval_against_our_corpus`` — retrieval-stack
changes are A/B'd on our own corpus, never trusted from literature. This
script is the apparatus; the run itself is DEFERRED in production until
the KG backfill + #162 A/B finish, so we don't steal daemon/GPU capacity
mid-flight.

The scoring math is reused from ``scripts/eval_fusion_ab.py`` — same
``RankingMetrics``, ``compare_runs``, ``rank_of_target``. The only
difference is the toggle: ``MEMPALACE_RERANK_CROSS_ENCODER`` on vs off,
holding ``fusion_mode`` and ``candidate_strategy`` constant.

Probe-set format — JSON list of ``[query, expected_source_file, why]``
triples, identical to ``scripts/eval_fusion_ab.py`` and
``scripts/eval_multi_encoder_rrf.py`` so the existing probe sets
(``scripts/probes_v2_git_derived.json``) drop in unchanged.

Usage (DEFERRED — do not run against the live corpus mid-backfill)::

    python scripts/eval_cross_encoder_rerank.py \\
        --probes scripts/probes_v2_git_derived.json \\
        --palace-path "$MEMPALACE_PALACE_PATH" \\
        --candidate-strategy hybrid \\
        --fusion-mode rrf \\
        --n-results 10 \\
        --out docs/research/2026-05-28-cross-encoder-rerank-ab.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.eval_fusion_ab import (  # noqa: E402  — sys.path adjusted above
    compare_runs,
    rank_of_target,
)


def _run_one_pipeline(
    search_fn: Callable[..., dict],
    probes: Sequence[Sequence[str]],
    palace_path: str,
    fusion_mode: str,
    candidate_strategy: str,
    n_results: int,
    rerank_on: bool,
) -> tuple[list[Optional[int]], float]:
    """Run every probe under one rerank toggle.

    The rerank toggle is process-wide env state, so the caller flips it
    before invoking. Resetting the cache between runs is a no-op for the
    OFF arm (which never loads a model) and a small per-A/B cost for the
    ON arm. Returns ``(ranks, elapsed_secs)``.
    """
    prev = os.environ.get("MEMPALACE_RERANK_CROSS_ENCODER")
    os.environ["MEMPALACE_RERANK_CROSS_ENCODER"] = "1" if rerank_on else "0"
    try:
        ranks: list[Optional[int]] = []
        t0 = time.time()
        for query, expected, *_ in probes:
            result = search_fn(
                query,
                palace_path,
                n_results=n_results,
                candidate_strategy=candidate_strategy,
                fusion_mode=fusion_mode,
            )
            hits = result.get("results") or []
            source_files = [h.get("source_file", "") for h in hits]
            ranks.append(rank_of_target(source_files, expected))
        return ranks, time.time() - t0
    finally:
        if prev is None:
            os.environ.pop("MEMPALACE_RERANK_CROSS_ENCODER", None)
        else:
            os.environ["MEMPALACE_RERANK_CROSS_ENCODER"] = prev


def run_ab(
    probes: Sequence[Sequence[str]],
    palace_path: str,
    fusion_mode: str = "rrf",
    candidate_strategy: str = "hybrid",
    n_results: int = 10,
    search_fn: Optional[Callable[..., dict]] = None,
) -> dict[str, Any]:
    """Run rerank-off vs rerank-on and return a report dict."""
    if search_fn is None:
        from mempalace.searcher import search_memories

        search_fn = search_memories

    queries = [p[0] for p in probes]
    ranks_off, secs_off = _run_one_pipeline(
        search_fn, probes, palace_path, fusion_mode, candidate_strategy, n_results, rerank_on=False
    )
    ranks_on, secs_on = _run_one_pipeline(
        search_fn, probes, palace_path, fusion_mode, candidate_strategy, n_results, rerank_on=True
    )
    comparison = compare_runs(
        queries, ranks_off, ranks_on, label_a="rerank-off", label_b="rerank-on"
    )
    report = comparison.as_dict()
    report["fusion_mode"] = fusion_mode
    report["candidate_strategy"] = candidate_strategy
    report["n_results"] = n_results
    report["timing_secs"] = {
        "rerank_off": round(secs_off, 2),
        "rerank_on": round(secs_on, 2),
    }
    return report


def _load_probes(path: str) -> list[list[str]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"probe file {path} must contain a JSON list")
    return raw


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes", required=True, help="JSON probe set.")
    parser.add_argument(
        "--palace-path",
        default="",
        help="Palace path / collection. Defaults to env MEMPALACE_PALACE_PATH.",
    )
    parser.add_argument("--candidate-strategy", default="hybrid")
    parser.add_argument("--fusion-mode", default="rrf")
    parser.add_argument("--n-results", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="Cap probes (0 = all).")
    parser.add_argument("--out", default="", help="Write JSON report here.")
    parser.add_argument(
        "--i-know-the-corpus-is-stable",
        action="store_true",
        help=(
            "Required acknowledgement. This run hits daemon /search and "
            "steals capacity; defer until the KG backfill + the #162 A/B "
            "have completed. Pass only when they have."
        ),
    )
    args = parser.parse_args(argv)

    if not args.i_know_the_corpus_is_stable:
        print(
            "Refusing to run: this A/B hits the live corpus. Defer until "
            "the KG backfill + the #162 A/B complete. Re-run with "
            "--i-know-the-corpus-is-stable once they have.",
            file=sys.stderr,
        )
        return 2

    palace_path = args.palace_path or os.environ.get("MEMPALACE_PALACE_PATH", "")
    if not palace_path:
        print("No --palace-path and MEMPALACE_PALACE_PATH unset.", file=sys.stderr)
        return 2

    probes = _load_probes(args.probes)
    if args.limit > 0:
        probes = probes[: args.limit]

    report = run_ab(
        probes,
        palace_path,
        fusion_mode=args.fusion_mode,
        candidate_strategy=args.candidate_strategy,
        n_results=args.n_results,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
