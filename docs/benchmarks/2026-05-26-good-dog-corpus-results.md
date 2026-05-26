# good-dog-corpus Benchmark — Fork Reproduction

**Date:** 2026-05-26
**Dataset:** good-dog-corpus v0.1 (24 questions, 23 vault notes, 6 SME categories)
**Source:** `/home/jp/Projects/multipass-structural-memory-eval/sme/corpora/good-dog-corpus/`
**Runner:** `benchmarks/good_dog_bench.py`
**Fork commit:** `0bd46eb` (worktree branch: `bench/good-dog-corpus`)
**Host:** katana (Linux 6.17, CPU embedder)
**Embedding:** ChromaDB default (`all-MiniLM-L6-v2`, 384-dim)
**Retrieval pipeline:** in-memory ChromaDB `EphemeralClient` — one persistent
collection per mode, queried 24× (paralleling the LongMemEval pattern at a
much smaller corpus size). The canonical pgvector daemon at `familiar:8085`
is **not** involved.

## Why this matters

LongMemEval measures retrieval against a long-conversation haystack (~115K
tokens per question, 44-53 distractor sessions). It exercises one slice of
memory behaviour — single/multi-session recall, preference, knowledge-update,
temporal-reasoning — but every question collapses to a single labelled
answer session. It does not test:

- **contradiction surfacing** (Cat 3) — the system flagging both sides of a
  contradiction, not silently picking one;
- **alias resolution** (Cat 4) — recognizing that "Alsatian" and "German
  Shepherd Dog" name the same breed;
- **temporal supersession** (Cat 6) — the academic-research-vs-clinical-
  consensus lag (Mech 1999 → AVSAB 2008), the Hill's vitamin D recall
  4-step lifecycle (announcement → expansion → expansion → FDA warning
  letter).

`good-dog-corpus` is multipass-structural-memory-eval's seeded test corpus
for exactly these categories. It is small enough (23 notes) that we can
ingest the whole vault into a single ChromaDB collection in one shot — no
per-question palace rebuild needed — yet it probes behaviour that
LongMemEval and ChromaDB-baseline R@5 numbers don't.

The headline metric here is **fraction of `expected_sources` substrings
matched** in the concatenated top-K retrieved documents — the SME spec
convention. This is retrieval recall, not end-to-end QA accuracy, and
sits in the same R@K bucket as the LongMemEval 500Q reproduction (see
`docs/benchmarks/2026-05-26-longmemeval-500q-results.md`).

## Results

### Overall recall (fraction of expected_sources matched across all 24 questions)

| Mode  | R@1   | R@3   | R@5   | R@10  | Elapsed (s) |
|-------|-------|-------|-------|-------|-------------|
| raw   | 0.865 | 0.973 | **1.000** | 1.000 | 5.8 |
| aaak  | 0.553 | 0.707 | 0.875 | 0.965 | 6.6 |
| rooms | 0.809 | 0.952 | **1.000** | 1.000 | 6.7 |

**Headline:** `raw` and `rooms` modes both reach 100% R@5 — every question's
full `expected_sources` set is present in the top-5 retrieved notes.

R@5 is saturated by the corpus size (the vault is 23 notes, so top-5 covers
22% of the corpus). The more informative number on a corpus this small is
**R@1** — does the single top-ranked note contain all the expected
substrings? On R@1, `raw` is 86.5%, `rooms` is 80.9%, `aaak` drops to 55.3%.

### Per-SME-category breakdown (Recall@5)

| Category | n | raw | aaak | rooms |
|----------|---|-----|------|-------|
| Cat 1 (factual retrieval / The Lookup) | 4 | 1.000 | 1.000 | 1.000 |
| Cat 2c (multi-hop / The Stairway) | 3 | 1.000 | 1.000 | 1.000 |
| Cat 3 (contradiction / The Dissonance) | 6 | 1.000 | 0.833 | 1.000 |
| Cat 4 (alias resolution / The Threshold 4a) | 3 | 1.000 | **0.467** | 1.000 |
| Cat 6 (temporal supersession / The Archive) | 6 | 1.000 | 0.933 | 1.000 |
| Cat 7 (token efficiency / The Abacus) | 2 | 1.000 | 1.000 | 1.000 |

### Per-SME-category breakdown (Recall@1)

| Category | n | raw | aaak | rooms |
|----------|---|-----|------|-------|
| Cat 1 | 4 | 1.000 | 0.833 | 0.917 |
| Cat 2c | 3 | 0.833 | 0.367 | 0.833 |
| Cat 3 | 6 | 0.875 | 0.375 | 0.708 |
| Cat 4 | 3 | 0.689 | 0.400 | 0.689 |
| Cat 6 | 6 | 0.878 | 0.567 | 0.878 |
| Cat 7 | 2 | 0.833 | 1.000 | 0.833 |

## Interpretation

### raw vs. rooms

`rooms` mode soft-boosts (20% distance discount) any retrieved doc whose
metadata `room` matches the room detected for the question. For
good-dog-corpus we use the vault subdirectory as the room (e.g.
`nutrition_safety`, `behavioral_research`) — this is a stronger signal than
LongMemEval's runtime topic-keyword detection because the rooms are
hand-authored by the corpus.

Despite that, `rooms` does not beat `raw` here: both hit 100% R@5 and `raw`
is slightly better at R@1 (86.5% vs 80.9%). Two factors:

1. The corpus is small enough that any reasonable embedder ranks the right
   note in the top 5 — there is no headroom for room-boosting to claw back.
2. On Cat 3 questions specifically, the question often spans rooms (a
   contradiction-surfacing question asks about BOTH framings of grain-free
   DCM — one note in `veterinary_research/`, the other in
   `community_journalism/`). Soft-boosting the question-side detected room
   slightly demotes the cross-room evidence and hurts Cat 3 R@1 (87.5% →
   70.8%).

The takeaway is the same as the LongMemEval result: on a small,
well-embedded corpus, the embedding baseline is hard to beat without an
LLM rerank, and topic-room boosting is a wash at best.

### AAAK's structural weak spot: alias resolution

AAAK's Cat 4 (alias resolution) R@5 of **0.467** is the lowest cell in the
table. Of the three Cat 4 questions:

- **q12** ("What does the abbreviation APBT refer to?") missed `APBT`,
  `American Pit Bull Terrier`, and `UKC` entirely from the top-5 — 0/3
  expected substrings matched.
- **q13** ("What does the term 'BEG diet' refer to in the canine-DCM
  literature?") missed `boutique`, `exotic`, `Tufts` — 2/5 matched.
- **q11** (Alsatian / German Shepherd Dog) matched in the top-5.

This is mechanically the same failure mode the LongMemEval result surfaced
(single-session-user dropped from 97.1% raw to 78.6% AAAK): AAAK's dialect
compression preserves entity codes but loses the exact surface form of an
abbreviation or acronym. A question that asks "what does APBT refer to?"
gets answered by a note that contains the literal string `APBT` — and the
AAAK-compressed representation of that note no longer contains the raw
acronym. Embedding similarity alone does not bridge it.

A natural next step is to keep raw text alongside AAAK in the index (the
upstream hybrid v4 approach), or to extend the dialect to preserve
explicit `alias_of` markers for capitalized short forms. Out of scope
for this benchmark.

### Comparison to LongMemEval (500Q)

| Metric (R@5)            | LongMemEval-S | good-dog-corpus |
|-------------------------|---------------|-----------------|
| raw                     | 0.966         | **1.000**       |
| aaak                    | 0.842         | 0.875           |
| rooms                   | 0.894         | **1.000**       |
| Corpus size (docs/q)    | 44-53 distractors | 23 (shared) |
| Question count          | 500           | 24              |
| Per-q time (s)          | 2.45          | 0.24            |

R@5 is higher across all modes on good-dog-corpus, which is expected — the
distractor pool is roughly 2× smaller per question and the vault is
hand-authored to make each question's answer-note unambiguous. The
**relative** ranking of modes (`raw ≈ rooms > aaak`) matches LongMemEval
exactly. The **structural weak spot for AAAK** (alias / surface-form
questions) is also consistent across both benchmarks.

## What this run does NOT cover

- **No daemon involvement.** Each mode creates one in-memory ChromaDB
  collection and tears it down. The pgvector + AGE daemon at
  `familiar:8085` is not exercised. A separate
  `--adapter mempalace-daemon` SME run via
  `sme/corpora/good-dog-corpus/` would measure the canonical palace's
  hybrid (BM25 + vector + KG) pipeline against the same questions; that is
  tracked separately as part of the multipass SME readings.
- **No LLM rerank.** No Anthropic or Ollama key is involved.
- **No hybrid modes.** Only the three "no-LLM" baselines from the upstream
  runner are scored here — same convention as the LongMemEval reproduction
  doc.
- **No alternative embedders.** ChromaDB default (`all-MiniLM-L6-v2`) only.
- **No end-to-end QA accuracy.** Substring matching only — same caveat as
  the LongMemEval reproduction. The SME team's
  `scripts/cross_validate_longmemeval.py` (upstream multipass) is the
  intended path for adding LLM-judge QA scoring; out of scope here.

## Reproduction

The shared `/home/jp/Projects/memorypalace/.venv` works because the
benchmark imports `mempalace.dialect` and `chromadb` only — no editable
install is needed. From the worktree root:

```bash
CORPUS=/home/jp/Projects/multipass-structural-memory-eval/sme/corpora/good-dog-corpus

PYTHONPATH=. /home/jp/Projects/memorypalace/.venv/bin/python \
  benchmarks/good_dog_bench.py "$CORPUS" --mode raw \
  --json bench-logs/good-dog-raw.json --jsonl bench-logs/good-dog-raw.jsonl

PYTHONPATH=. /home/jp/Projects/memorypalace/.venv/bin/python \
  benchmarks/good_dog_bench.py "$CORPUS" --mode aaak \
  --json bench-logs/good-dog-aaak.json --jsonl bench-logs/good-dog-aaak.jsonl

PYTHONPATH=. /home/jp/Projects/memorypalace/.venv/bin/python \
  benchmarks/good_dog_bench.py "$CORPUS" --mode rooms \
  --json bench-logs/good-dog-rooms.json --jsonl bench-logs/good-dog-rooms.jsonl
```

Per-question results JSONL files are kept in `bench-logs/` but not
committed (reproducible).

## How to interpret these numbers

- **R@5 = 1.000 on raw is not surprising.** The vault is 23 notes; top-5
  covers 22% of the corpus. R@5 should be 100% on raw for any reasonable
  embedder. The interesting number on a corpus this small is R@1.
- **R@1 is the headline.** raw 86.5%, rooms 80.9%, aaak 55.3%. The AAAK
  gap at R@1 (-31pp vs raw) is the dominant signal.
- **Cat 4 R@5 for AAAK (0.467) is the structural finding.** It mirrors
  the LongMemEval single-session-user gap and points at the same root
  cause (lost surface forms / acronyms).
- This benchmark complements LongMemEval; it does not replace it. The
  question categories test things LongMemEval's haystack doesn't reach.
