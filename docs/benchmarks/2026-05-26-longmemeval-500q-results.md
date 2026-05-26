# LongMemEval 500Q Results — Fork Reproduction

**Date:** 2026-05-26
**Dataset:** LongMemEval-S (500 questions, ~115K tokens per question, 44-53 distractor sessions each)
**Source:** `/home/jp/Projects/multipass-structural-memory-eval/sme/corpora/longmemeval/data/longmemeval_s_cleaned.json`
**Runner:** `benchmarks/longmemeval_bench.py`
**Fork commit:** `4f9bc28` (CI worktree branch: `ci/age-kg-tests-164`)
**Host:** katana (Linux 6.17, RTX 2080 Ti, 31 GiB RAM)
**Granularity:** session-level (one document per haystack session, default)
**Embedding:** ChromaDB default (all-MiniLM-L6-v2, 384-dim, runs on CPU)
**Retrieval pipeline:** in-memory ChromaDB EphemeralClient — fresh palace per question, throwaway. The canonical pgvector daemon at `familiar:8085` is NOT involved.

## Why this matters

SME issue [#19](https://github.com/techempower-org/multipass-structural-memory-eval/issues/19) asked for a 500-question reproduction of upstream's LongMemEval result on the fork, after the AGE search wedge fixes landed (PR #227 stopword filter, PR #228 + #229 `statement_timeout` guards). Upstream's published `R@5` for `raw` mode is 96.6% (`benchmarks/BENCHMARKS.md`). This run confirms the fork reproduces that number — and provides the first published numbers for the fork's `aaak` (AAAK-dialect compressed) and `rooms` (topic-room boosted) modes on the same 500Q corpus.

The headline metric is **session-level Recall@5** — does the labelled answer session appear in the top-5 retrieved candidates? This is retrieval recall, not end-to-end QA accuracy, and is not directly comparable to QA-accuracy benchmarks (see `benchmarks/BENCHMARKS.md` for the caveat).

## Results

### Session-level metrics

| Mode  | R@1   | R@3   | R@5   | R@10  | R@30  | R@50  | NDCG@5 | NDCG@10 |
|-------|-------|-------|-------|-------|-------|-------|--------|---------|
| raw   | 0.806 | 0.926 | **0.966** | 0.982 | 0.996 | 1.000 | 0.888  | 0.889   |
| aaak  | 0.602 | 0.780 | 0.842 | 0.920 | 0.980 | 1.000 | 0.720  | 0.736   |
| rooms | 0.686 | 0.840 | 0.894 | 0.956 | 0.996 | 1.000 | 0.789  | 0.803   |

**Headline:** `raw` mode reproduces upstream's published R@5 of 96.6% on the
nose. The fork's KG/search rewrites did not regress the embedding baseline.

### Turn-level metrics

In this configuration (session granularity, which is the default), turn-level
metrics are computed against the same single-document-per-session corpus as
the session-level metrics — so the two tables are numerically identical for
the 500Q runs reported here. The runner exposes a `--granularity turn` mode
that splits each session into per-user-turn documents; that's a separate
benchmark and out of scope for this reproduction.

### Per-question-type breakdown (session R@10)

| Question type                  | n   | raw   | aaak  | rooms |
|--------------------------------|-----|-------|-------|-------|
| single-session-user            | 70  | 0.971 | 0.786 | 0.914 |
| single-session-assistant       | 56  | 0.964 | 0.911 | 0.964 |
| single-session-preference      | 30  | 0.967 | 0.833 | 0.933 |
| knowledge-update               | 78  | 1.000 | 1.000 | 1.000 |
| temporal-reasoning             | 133 | 0.970 | 0.940 | 0.940 |
| multi-session                  | 133 | 1.000 | 0.947 | 0.970 |

The largest per-type gap is on single-session-user questions: raw 97.1% →
aaak 78.6% (a 18.5pp drop). AAAK's dialect compression preserves
entities and verbs but loses specific user-utterance phrasing, and
single-session-user questions reward retrieval that matches the original
words. Rooms recovers some of that gap (91.4%) by soft-boosting on
topic-keyword overlap, but the topic scheme is calibrated for personal
corpora rather than LongMemEval's broader-domain haystack — so it stays
behind raw on every question type.

### Runtime

| Mode  | Wall time | Per-question | Notes |
|-------|-----------|--------------|-------|
| raw   | 20.4 min  | 2.45 s       | 1224.1 s total |
| aaak  | 19.7 min  | 2.36 s       | 1182.0 s total; AAAK compression is per-document but fast (`dialect.compress`) |
| rooms | 20.0 min  | 2.40 s       | 1201.2 s total; topic-keyword scoring is cheap (string contains) plus a soft rerank pass |

Per-question time is dominated by ChromaDB's CPU embedder (`all-MiniLM-L6-v2`,
384-dim). The mode-specific overhead is small in all three cases — the AAAK
compression and rooms topic-detection pass each add roughly 0.1 s/q or less.

## Reproduction

The runner uses a throwaway in-memory ChromaDB collection per question — it does not touch any persistent palace state. Sequential mode runs to avoid GPU/embedder contention. Each run takes roughly 25-40 minutes on katana CPU (no GPU acceleration in this configuration; CPU embedder is the bottleneck for raw and rooms, while aaak adds a per-document AAAK compression pass).

```bash
python3 -m venv /tmp/echo-bench-venv
/tmp/echo-bench-venv/bin/pip install chromadb
/tmp/echo-bench-venv/bin/pip install /home/jp/Projects/memorypalace

DATA=/home/jp/Projects/multipass-structural-memory-eval/sme/corpora/longmemeval/data/longmemeval_s_cleaned.json

/tmp/echo-bench-venv/bin/python benchmarks/longmemeval_bench.py "$DATA" --mode raw
/tmp/echo-bench-venv/bin/python benchmarks/longmemeval_bench.py "$DATA" --mode aaak
/tmp/echo-bench-venv/bin/python benchmarks/longmemeval_bench.py "$DATA" --mode rooms
```

The mempalace package was installed non-editably in a disposable venv to avoid the worktree-`.pth`-trap (where `pip install -e .` from a worktree writes a `.pth` that breaks every shared venv when the worktree is removed). Raw per-question results JSONL files are kept in `bench-logs/` in the worktree but not committed (large, reproducible).

## How to interpret these numbers

- **R@5 is the headline.** Upstream's public number for `raw` mode is 96.6%. Anything within ~1pp of that on this fork is a successful reproduction.
- **R@10 ≥ R@5** (recall is monotonic in k). If R@5 is ~96% and R@10 is ~99%, that's the expected gap and matches the upstream profile.
- **AAAK vs raw.** AAAK is a lossy compression of session text — the question is whether the compressed representation retains enough signal for retrieval. Expect a modest R@5 drop (1-3pp). A bigger drop would mean the dialect is dropping too much.
- **Rooms vs raw.** The rooms mode soft-boosts results matching the same topic-keyword room as the query (20% distance discount). Expect rooms to be roughly on par with raw on this corpus — the topic-keyword scheme is calibrated for personal-knowledge corpora, and LongMemEval's haystack is broader-domain. A regression vs raw would be a flag that the topic-keyword scheme is mis-detecting question rooms.

## What's NOT in this run

- **No LLM rerank.** No Anthropic or Ollama key is involved; this is the pure-embedding retrieval pipeline.
- **No hybrid modes.** This run is the three "no-LLM" baselines only. Hybrid v3 / v4 + rerank are published in upstream `BENCHMARKS.md` at 99.4-100% R@5 and are tracked separately.
- **No alternative embedders.** ChromaDB default (`all-MiniLM-L6-v2`) only. The `--embed-model bge-large` / `nomic` / `mxbai` options exist in the runner but are out-of-scope for the reproduction; they're tracked in `docs/research/2026-05-15-multi-encoder-rrf.md`.
- **No daemon involvement.** Each question creates and tears down its own in-memory palace. The pgvector + AGE daemon at `familiar:8085` is not exercised by this benchmark.
