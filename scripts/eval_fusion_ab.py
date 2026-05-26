#!/usr/bin/env python3
"""eval_fusion_ab.py — A/B the RRF fusion mode against the convex-blend rerank.

Issue #162. The current production rerank (``fusion_mode="convex"``) blends
normalized vector similarity and BM25 on a shared scale. RRF
(``fusion_mode="rrf"``) instead fuses the two *rank orderings* — scale
agnostic. #82 found raw-vector RRF lift does NOT survive the hybrid pipeline;
this harness measures the question that prior work left open: pure RRF over
the vector + BM25 orderings vs the convex blend, on OUR corpus.

Per ``feedback_test_retrieval_against_our_corpus`` — retrieval-stack changes
are A/B'd on our own corpus, never trusted from literature. This script is
the apparatus; the run itself is DEFERRED until the live KG backfill
completes (running it hits daemon /search and steals GPU/daemon capacity).

The scoring math (``evaluate_ranking``, ``compare_runs``, ``rank_of_target``)
is pure and unit-tested in ``tests/test_eval_fusion_ab.py`` so the harness
logic is verified without touching the live corpus.

Probe-set format — JSON list of ``[query, expected_source_file, why]``
triples, identical to ``scripts/eval_multi_encoder_rrf.py`` so the existing
probe sets (``scripts/probes_v2_git_derived.json``) drop in unchanged.

Usage (DEFERRED — do not run against the live corpus mid-backfill)::

    python scripts/eval_fusion_ab.py \\
        --probes scripts/probes_v2_git_derived.json \\
        --palace-path "$MEMPALACE_PALACE_PATH" \\
        --candidate-strategy hybrid \\
        --n-results 10 \\
        --out /tmp/fusion_ab.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ── pure scoring math (unit-tested, no I/O) ───────────────────────────────


def rank_of_target(ranked_source_files: Sequence[str], target: str) -> Optional[int]:
    """1-indexed rank of the first hit whose basename matches ``target``.

    Matches on basename (``Path(...).name``) so a probe's expected path and
    the hit's ``source_file`` compare equal regardless of directory prefix —
    same identity rule as the multi-encoder harness. Returns ``None`` when no
    hit matches.
    """
    target_name = Path(target).name
    for i, sf in enumerate(ranked_source_files, start=1):
        if Path((sf or "").strip()).name == target_name:
            return i
    return None


@dataclass
class RankingMetrics:
    """Aggregate retrieval metrics over a probe set."""

    n_probes: int
    mrr: float
    recall_at_5: float
    recall_at_10: float
    found: int  # probes whose expected doc appeared anywhere in results

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_probes": self.n_probes,
            "mrr": round(self.mrr, 4),
            "recall_at_5": round(self.recall_at_5, 4),
            "recall_at_10": round(self.recall_at_10, 4),
            "found": self.found,
        }


def evaluate_ranking(ranks: Sequence[Optional[int]]) -> RankingMetrics:
    """Compute MRR / Recall@5 / Recall@10 from per-probe ranks.

    ``ranks[i]`` is the 1-indexed rank of probe ``i``'s expected doc, or
    ``None`` if it never appeared. Recall@k is the fraction of probes whose
    expected doc ranked at position ``<= k``. MRR averages ``1/rank`` (0 for
    misses).
    """
    n = len(ranks)
    if n == 0:
        return RankingMetrics(0, 0.0, 0.0, 0.0, 0)
    rr_sum = 0.0
    r5 = 0
    r10 = 0
    found = 0
    for rank in ranks:
        if rank is None:
            continue
        found += 1
        rr_sum += 1.0 / rank
        if rank <= 5:
            r5 += 1
        if rank <= 10:
            r10 += 1
    return RankingMetrics(
        n_probes=n,
        mrr=rr_sum / n,
        recall_at_5=r5 / n,
        recall_at_10=r10 / n,
        found=found,
    )


@dataclass
class ABComparison:
    """Side-by-side A vs B metrics plus per-probe rank deltas."""

    label_a: str
    label_b: str
    metrics_a: RankingMetrics
    metrics_b: RankingMetrics
    improved: list[dict[str, Any]] = field(default_factory=list)
    regressed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label_a": self.label_a,
            "label_b": self.label_b,
            "metrics_a": self.metrics_a.as_dict(),
            "metrics_b": self.metrics_b.as_dict(),
            "delta": {
                "mrr": round(self.metrics_b.mrr - self.metrics_a.mrr, 4),
                "recall_at_5": round(self.metrics_b.recall_at_5 - self.metrics_a.recall_at_5, 4),
                "recall_at_10": round(self.metrics_b.recall_at_10 - self.metrics_a.recall_at_10, 4),
            },
            "improved": self.improved,
            "regressed": self.regressed,
        }


def _rank_sort_key(rank: Optional[int]) -> int:
    """Sort key treating a miss (None) as worse than any finite rank."""
    return rank if rank is not None else 1_000_000


def compare_runs(
    queries: Sequence[str],
    ranks_a: Sequence[Optional[int]],
    ranks_b: Sequence[Optional[int]],
    label_a: str = "convex",
    label_b: str = "rrf",
) -> ABComparison:
    """Compare two per-probe rank vectors (A vs B) over the same queries.

    Positive deltas in the result favor B. ``improved`` lists probes where B
    ranked the expected doc strictly better than A (lower rank, miss→hit);
    ``regressed`` lists the reverse. A miss is treated as rank +inf for the
    better/worse comparison.
    """
    if not (len(queries) == len(ranks_a) == len(ranks_b)):
        raise ValueError(
            f"length mismatch: queries={len(queries)} ranks_a={len(ranks_a)} ranks_b={len(ranks_b)}"
        )
    improved: list[dict[str, Any]] = []
    regressed: list[dict[str, Any]] = []
    for q, ra, rb in zip(queries, ranks_a, ranks_b):
        ka, kb = _rank_sort_key(ra), _rank_sort_key(rb)
        if kb < ka:
            improved.append({"query": q, "rank_a": ra, "rank_b": rb})
        elif kb > ka:
            regressed.append({"query": q, "rank_a": ra, "rank_b": rb})
    return ABComparison(
        label_a=label_a,
        label_b=label_b,
        metrics_a=evaluate_ranking(ranks_a),
        metrics_b=evaluate_ranking(ranks_b),
        improved=improved,
        regressed=regressed,
    )


# ── live run (DEFERRED — hits the corpus; gated behind main) ──────────────


def _run_one_pipeline(
    search_fn: Callable[..., dict],
    probes: Sequence[Sequence[str]],
    palace_path: str,
    fusion_mode: str,
    candidate_strategy: str,
    n_results: int,
) -> tuple[list[Optional[int]], float]:
    """Run every probe through ``search_fn`` under one fusion mode.

    ``search_fn`` is injected (defaults to the real ``search_memories`` in
    ``main``) so unit tests can drive this with a stub and never touch the
    live corpus. Returns ``(ranks, elapsed_secs)``.
    """
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


def run_ab(
    probes: Sequence[Sequence[str]],
    palace_path: str,
    candidate_strategy: str = "hybrid",
    n_results: int = 10,
    search_fn: Optional[Callable[..., dict]] = None,
) -> dict[str, Any]:
    """Run the convex vs rrf A/B over ``probes`` and return a report dict.

    DEFERRED in production until the backfill completes. ``search_fn`` is
    injectable so tests exercise the orchestration on a stub.
    """
    if search_fn is None:
        from mempalace.searcher import search_memories

        search_fn = search_memories

    queries = [p[0] for p in probes]
    ranks_convex, secs_convex = _run_one_pipeline(
        search_fn, probes, palace_path, "convex", candidate_strategy, n_results
    )
    ranks_rrf, secs_rrf = _run_one_pipeline(
        search_fn, probes, palace_path, "rrf", candidate_strategy, n_results
    )
    comparison = compare_runs(queries, ranks_convex, ranks_rrf)
    report = comparison.as_dict()
    report["candidate_strategy"] = candidate_strategy
    report["n_results"] = n_results
    report["timing_secs"] = {
        "convex": round(secs_convex, 2),
        "rrf": round(secs_rrf, 2),
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
    parser.add_argument("--n-results", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="Cap probes (0 = all).")
    parser.add_argument("--out", default="", help="Write JSON report here.")
    parser.add_argument(
        "--i-know-the-backfill-is-done",
        action="store_true",
        help=(
            "Required acknowledgement. This run hits daemon /search and "
            "steals GPU/daemon capacity; #162 defers it until the KG "
            "backfill completes. Pass only when it actually has."
        ),
    )
    args = parser.parse_args(argv)

    if not args.i_know_the_backfill_is_done:
        print(
            "Refusing to run: this A/B hits the live corpus. Per issue #162 "
            "the comparison is deferred until the KG backfill completes. "
            "Re-run with --i-know-the-backfill-is-done once it has.",
            file=sys.stderr,
        )
        return 2

    import os

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
        candidate_strategy=args.candidate_strategy,
        n_results=args.n_results,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
