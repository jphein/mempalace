# AI Memory System Benchmark Survey

**Date:** 2026-05-24
**Scope:** All systems listed in the MemPalace README comparison tables (lines 38-86)
**Methodology:** Published READMEs, arXiv papers, blog posts, benchmark repositories

---

## 1. Benchmark Overview

### LongMemEval (Wu et al., ICLR 2025)

- **What it measures:** Long-term conversational memory across 500 human-curated questions in 6 categories: single-session user recall, single-session assistant recall, single-session preference recall, knowledge updates, temporal reasoning, and multi-session reasoning.
- **Dataset:** LongMemEval-S (~115K tokens per question, ~48-62 distractor sessions per question). LongMemEval-M (~1.5M tokens) exists but is rarely used due to context window limits.
- **Canonical metric:** End-to-end QA accuracy (retrieve + generate answer + GPT-4o judge, >97% human agreement). Some systems report R@5/R@10 retrieval recall instead -- these are **not directly comparable** to QA accuracy.
- **What "good" means:** Full-context GPT-4o baseline scores 60.2%. Oracle (relevant sessions only) GPT-4o scores 87.0%. Anything above 85% is competitive; above 90% is current frontier.
- **Question count:** 500
- **Source:** [arxiv.org/abs/2410.10813](https://arxiv.org/abs/2410.10813), [github.com/xiaowu0162/longmemeval](https://github.com/xiaowu0162/longmemeval)

### LoCoMo (Maharana et al., ACL 2024)

- **What it measures:** Long-term conversational memory across multi-session dialogues. 4 question categories: single-hop, multi-hop, open-domain, temporal. Some implementations add an adversarial category (often skipped).
- **Dataset:** 10 multi-session conversations, ~1,540 questions (varies by evaluation protocol -- some use 300-question subsets, some 1,540). Context is modest by 2026 standards (~300K tokens total).
- **Canonical metric:** End-to-end QA accuracy via LLM-as-judge.
- **What "good" means:** Full-context baselines score ~52-66% depending on model. Above 85% is competitive; above 90% is frontier.
- **Question count:** ~1,540 (full) or ~300 (subset)
- **Source:** [snap-research.github.io/locomo](https://snap-research.github.io/locomo/)

### BEAM (Agent Memory Benchmark)

- **What it measures:** Memory retrieval at production scale. Tests across token buckets from 100K to 10M tokens. 10 memory ability types including preference following, contradiction resolution, temporal reasoning.
- **Dataset:** BEAM-1M: 700 questions across 35 conversations at 1M token scale. BEAM-10M: 200 questions at 10M scale.
- **Canonical metric:** End-to-end QA accuracy (pass rate).
- **What "good" means:** Performance degrades significantly with scale. 70%+ at 1M is competitive. At 10M, even 50% is notable. Most systems have no published BEAM results.
- **Source:** [github.com/mem0ai/memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) (hosts the evaluation framework)

### Other Benchmarks Encountered

| Benchmark | Description | Used by |
|---|---|---|
| **DMR** (Deep Memory Retrieval) | MemGPT's original eval metric | Zep (94.8%), MemGPT (93.4%) |
| **ConvoMem** | Personalization and preference learning | Supermemory, MemPalace upstream |
| **PersonaMem** | Persona-based memory evaluation | Hindsight (86.6% at 32K) |
| **LifeBenchEN** | English life-event memory | Hindsight (71.5%) |
| **MemoryStress** | Longitudinal stress test (1000 sessions, 10 months) | OMEGA (38.3%) |
| **EverMemBench** | Multi-party dialogue failures | EverMind (internal) |
| **Context-Bench** | Agentic context engineering (Letta's benchmark) | Letta leaderboard (model eval, not memory system eval) |
| **DevBench** | Developer workflow queries | mcp-memory-service (91.1% R@5) |
| **jp-realm-v0.1** | 30-question personal knowledge corpus | MemPalace fork/familiar (78.33% recall) |

---

## 2. Comparison Table

### Legend

- **QA** = End-to-end QA accuracy (retrieve + answer + judge)
- **R@K** = Retrieval recall at K (does the correct source appear in top-K results?)
- **self** = Self-reported by the system's own team
- **indie** = Independently reproduced or peer-validated
- **paper** = Published in a peer-reviewed or arXiv paper
- **--** = No published result found

### LongMemEval Results

| System | Score | Metric | Answer Model | Verification | Source |
|---|---|---|---|---|---|
| **OMEGA** | **95.4%** | QA | GPT-4.1 | self | [omegamax.co/benchmarks](https://omegamax.co/benchmarks), [DEV Community blog](https://dev.to/singularityjason/how-i-built-a-memory-system-that-scores-954-on-longmemeval-1-on-the-leaderboard-2md3) |
| Mastra (not in README) | 94.87% | QA | GPT-5-mini | self | [mastra.ai/research/observational-memory](https://mastra.ai/research/observational-memory) |
| Mem0 (platform v3) | 94.4-94.8% | QA | undisclosed "production stack" | self | [mem0.ai/research](https://mem0.ai/research), [memory-benchmarks repo](https://github.com/mem0ai/memory-benchmarks) |
| Hindsight | 91.4% | QA | Gemini 3 Pro | indie (Virginia Tech, Washington Post) | [arXiv:2512.12818](https://arxiv.org/abs/2512.12818), [hindsight-benchmarks repo](https://github.com/vectorize-io/hindsight-benchmarks) |
| agentmemory | 95.2% | **R@5** | n/a (retrieval only) | self | [github.com/rohitg00/agentmemory](https://github.com/rohitg00/agentmemory) |
| engram-2 | 0.990 (99.0%) | **R@5** | n/a (retrieval only) | self | [github.com/199-biotechnologies/engram-2](https://github.com/199-biotechnologies/engram-2) |
| MemPalace (upstream raw) | 96.6% | **R@5** | n/a (retrieval only) | indie (confirmed by agentmemory, M2 Ultra repro) | [BENCHMARKS.md](https://github.com/MemPalace/mempalace/blob/develop/benchmarks/BENCHMARKS.md), [Issue #29](https://github.com/MemPalace/mempalace/issues/29) |
| MemPalace (upstream hybrid v4 held-out) | 98.4% | **R@5** | n/a (retrieval only) | self | [BENCHMARKS.md](https://github.com/MemPalace/mempalace/blob/develop/benchmarks/BENCHMARKS.md) |
| ai-memory | 97.8% | **R@5** | n/a (retrieval only) | self | [github.com/alphaonedev/ai-memory-mcp](https://github.com/alphaonedev/ai-memory-mcp) |
| mcp-memory-service | 80.4% (turn) / 86.0% (session) | **R@5** | n/a (retrieval only) | self | [github.com/doobidoo/mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) |
| Supermemory | 81.6% (GPT-4o) / 85.2% (Gemini-3) | QA | GPT-4o / Gemini-3 | indie (Hindsight benchmark repo) | [supermemory README](https://github.com/supermemoryai/supermemory), [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks) |
| EverOS/EverMind | 83.0% | QA (unspecified metric) | undisclosed | self | [github.com/EverMind-AI/EverOS](https://github.com/EverMind-AI/EverOS) |
| Zep/Graphiti | 71.2% (GPT-4o) | QA | GPT-4o | self | [blog.getzep.com](https://blog.getzep.com/state-of-the-art-agent-memory/), [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) |
| True Memory (Pro) | 87.8% | QA | gpt-4.1-mini | self (paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| True Memory (Base) | 85.5% | QA | gpt-4.1-mini | self (paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| ENGRAM (academic paper) | 71.4% | QA | GPT-4o-mini | paper | [arXiv:2511.12960](https://arxiv.org/abs/2511.12960) |
| Celiums | 62.3% | QA | Opus (best of 5 models) | self | [celiums.ai/blog](https://celiums.ai/blog/longmemeval-benchmark-honest-results/) |
| Longhand | -- | -- | -- | -- | No benchmarks published |
| iai-mcp | -- | -- | -- | -- | LongMemEval-S run exists but score not disclosed |
| Open Brain (OB1) | -- | -- | -- | -- | No benchmarks published |
| claude-mem | -- | -- | -- | -- | No benchmarks published |
| Letta | -- | -- | -- | -- | No memory benchmarks (Context-Bench is model eval) |
| Cognee | -- | -- | -- | -- | No benchmarks published |
| CaviraOSS OpenMemory | -- | -- | -- | -- | No benchmarks published |
| EngramX (NickCirv) | -- | -- | -- | -- | Token reduction only; no memory retrieval benchmarks |

### LoCoMo Results

| System | Score | Metric | Answer Model | Verification | Source |
|---|---|---|---|---|---|
| EverOS/EverMind (EverCore) | 93.05% | QA | undisclosed | self | [EverOS README](https://github.com/EverMind-AI/EverOS) |
| True Memory (Pro) | 93.0% | QA | gpt-4.1-mini | self (paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| True Memory (Base) | 92.0% | QA | gpt-4.1-mini | self (paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| Mem0 (platform v3) | 92.5% | QA | undisclosed | self | [mem0.ai/research](https://mem0.ai/research) |
| MemMachine (not in README) | 91.7% | QA | gpt-4.1-mini | paper | [arXiv:2604.04853](https://arxiv.org/abs/2604.04853) |
| Hindsight | 89.61% (Gemini-3) / 85.67% (OSS-120B) | QA | Gemini-3 / OSS-120B | indie (Virginia Tech) | [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks) |
| MemPalace (upstream, top-10) | 88.9% | **R@10** | n/a (retrieval only) | self | [BENCHMARKS.md](https://github.com/MemPalace/mempalace/blob/develop/benchmarks/BENCHMARKS.md) |
| Memobase | 75.78% | QA | undisclosed | indie (Hindsight benchmarks) | [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks) |
| Zep | 75.14% | QA | undisclosed | indie (Hindsight benchmarks) | [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks) |
| ENGRAM (academic paper) | 77.55% | QA (judge) | GPT-4o-mini | paper | [arXiv:2511.12960](https://arxiv.org/abs/2511.12960) |
| engram-2 | 74.5% | QA (strict judge) | GPT-5.4 | self | [github.com/199-biotechnologies/engram-2](https://github.com/199-biotechnologies/engram-2) |
| Mem0 (OSS, older alg) | 66.88-68.5% | QA | undisclosed | indie (Hindsight benchmarks) | [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks) |
| Supermemory | 65.4% | QA | undisclosed | indie (True Memory paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| Mem0 (per True Memory paper) | 61.4% | QA | undisclosed | indie (True Memory paper) | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| mcp-memory-service | 49.7% | **R@5** | n/a (retrieval only) | self | [mcp-memory-service README](https://github.com/doobidoo/mcp-memory-service) |

### BEAM Results

| System | BEAM-1M | BEAM-10M | Metric | Answer Model | Source |
|---|---|---|---|---|---|
| True Memory (Pro) | 76.6% | 65.0% (prelim) | QA | gpt-4.1-mini | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| True Memory (Base) | 74.9% | -- | QA | gpt-4.1-mini | [arXiv:2605.04897](https://arxiv.org/abs/2605.04897) |
| Hindsight | 73.9% | 64.1% | QA | Gemini-3 | [benchmarks.hindsight.vectorize.io](https://benchmarks.hindsight.vectorize.io/) |
| Mem0 (platform v3) | 70.1% | 50.5% | QA | undisclosed | [mem0.ai/research](https://mem0.ai/research) |
| Mem0 (README, older) | 64.1% | 48.6% | QA | undisclosed | [mem0 README](https://github.com/mem0ai/mem0) |

### Systems With No Published Benchmark Results

| System | What they report instead |
|---|---|
| Longhand | No quantitative benchmarks |
| Open Brain (OB1) | Cost optimization metrics only (73% invocation reduction) |
| claude-mem | ~10x token savings claim; evals/swebench directory exists but no scores published |
| Letta | Context-Bench (model evaluation, not memory system eval) |
| Cognee | arXiv paper linked but no benchmark numbers in README |
| CaviraOSS OpenMemory | Feature comparison only |
| EngramX (NickCirv) | Token reduction benchmarks (89% savings); no retrieval/QA benchmarks |
| iai-mcp | Verbatim recall >= 99% at 10K items; latency < 100ms; LongMemEval-S score withheld |

---

## 3. Methodology Concerns

### The Retrieval Recall vs. QA Accuracy Gap

This is the single largest comparability problem in the landscape. Several systems (MemPalace upstream, agentmemory, engram-2, ai-memory, mcp-memory-service) report **R@5 retrieval recall** while others (Hindsight, Mem0, OMEGA, True Memory, EverOS, Zep, Supermemory) report **end-to-end QA accuracy**.

These metrics are fundamentally different:
- R@5 asks: "Is the correct source document in the top 5 retrieval results?"
- QA accuracy asks: "Did the full pipeline (retrieve + synthesize answer + judge) produce a correct answer?"

A system can have 100% R@5 and 40% QA accuracy (Celiums demonstrated this: 100% retrieval, 62.3% QA). Conversely, a system with imperfect retrieval can sometimes still answer correctly from partial context. The gap is driven by the difficulty of temporal reasoning, multi-session aggregation, and knowledge update synthesis.

True Memory's Section 6.2 retrieval bottleneck diagnostic quantifies this directly: 330 of 357 wrong answers were fixed when given the full correct context, confirming that retrieval is the dominant failure mode on these benchmarks. Their 56-configuration ablation (53/56 configs above 90% LoCoMo, only 3.2pp total spread) further demonstrates that **architecture matters more than component selection** — the choice of embedder or reranker model barely moves the needle compared to the retrieval design itself.

**Impact:** MemPalace upstream's 96.6% R@5 and agentmemory's 95.2% R@5 are **not comparable** to OMEGA's 95.4% QA or Hindsight's 91.4% QA. The R@5 numbers would need to be paired with an answer model and judge to produce comparable QA accuracy figures. Celiums's blog demonstrated that even with perfect retrieval, QA accuracy with Opus topped out at 62.3% on this specific benchmark.

### The MemPalace Upstream Benchmark Controversy

The MemPalace upstream 96.6% R@5 has been independently confirmed as reproducible (agentmemory team, M2 Ultra reproduction). However, multiple analyses have raised issues:

1. **Not MemPalace-specific:** The 96.6% uses `collection.add()` + `collection.query()` on ChromaDB with default all-MiniLM-L6-v2 embeddings. No palace features (wings, rooms, halls, AAAK) are involved. It is effectively a ChromaDB baseline.
2. **Enabling palace features hurts:** Rooms mode drops to 89.4% (-7.2pp). AAAK compression drops to 84.2% (-12.4pp).
3. **Trivial retrieval task:** With ~48-62 sessions per question and top-5 retrieval, ChromaDB returns ~10% of the corpus per query. BM25 alone scores 93.8% R@5 on the same setup.
4. **100% LoCoMo is structurally guaranteed:** Using top_k=50 against conversations with at most 32 sessions means the retrieval step is bypassed entirely. The honest LoCoMo number is 88.9% R@10 at top-10.

Sources: [Vectorize analysis](https://vectorize.io/articles/mempalace-benchmarks), [MemPalace Issue #29](https://github.com/MemPalace/mempalace/issues/29), [MemPalace Issue #214](https://github.com/milla-jovovich/mempalace/issues/214)

### Self-Reported vs. Independently Verified

| Verification Level | Systems |
|---|---|
| **Peer-reviewed paper** | True Memory (arXiv:2605.04897), ENGRAM (arXiv:2511.12960, ICLR-adjacent), Hindsight (arXiv:2512.12818) |
| **Independently reproduced** | Hindsight (Virginia Tech + Washington Post), MemPalace upstream 96.6% R@5 (agentmemory, M2 Ultra repro) |
| **Open eval framework** | Mem0 (memory-benchmarks repo), Hindsight (hindsight-benchmarks repo), EverMind (EverMemOS_Eval_Results on HuggingFace) |
| **Self-reported only** | OMEGA, agentmemory, engram-2, ai-memory, mcp-memory-service, EverOS, Supermemory, Celiums, iai-mcp |

### Dataset Version and Split Differences

- LongMemEval uses the "-S" split (~115K tokens) in nearly all published results. The "-M" split (~1.5M tokens) is almost never used. Cross-system comparisons within LongMemEval-S are generally valid.
- LoCoMo: Question count varies. The full dataset has ~1,540 questions, but some evaluations (engram-2) use 200-question subsets with confidence intervals. The adversarial category is often skipped by some systems (Hindsight explicitly skips it). **This makes LoCoMo cross-comparisons unreliable** without knowing the exact question subset and categories included.
- BEAM: Token bucket (100K, 500K, 1M, 10M) must match for comparison. Most published results are at 1M; only Mem0 and Hindsight publish 10M results.

### Answer Model Variation

The answer model matters enormously for QA accuracy:

| System | Answer Model | LongMemEval Score |
|---|---|---|
| OMEGA | GPT-4.1 | 95.4% |
| Mastra | GPT-5-mini | 94.87% |
| Hindsight | Gemini 3 Pro | 91.4% |
| Hindsight | OSS-120B | 89.0% |
| Supermemory | GPT-4o | 81.6% |
| Zep | GPT-4o | 71.2% |
| ENGRAM (paper) | GPT-4o-mini | 71.4% |
| Celiums | Opus (best) | 62.3% |

The difference between GPT-4.1 and GPT-4o-mini is ~24pp on the same benchmark. **No apples-to-apples comparison exists unless the answer model is held constant.** The Hindsight benchmark repo is the closest to systematic: it tests multiple backbone sizes against the same memory system. Mem0's memory-benchmarks repo also supports model swapping.

### Judge Model Variation

- Most systems use GPT-4o as judge (per LongMemEval's original protocol).
- engram-2 uses GPT-5.4 as a "strict judge" that "rejects partial list answers, requires resolved dates" -- their 74.5% LoCoMo is not comparable to systems using the standard judge.
- EverMind uses GPT-OSS-120b as judge at temperature 0.0.

### Mem0 Score Discrepancies

Mem0 scores vary significantly across sources:
- **Mem0 README (April 2026):** 91.6% LoCoMo, 94.8% LongMemEval (labeled as "new algorithm")
- **Mem0 research page:** 92.5% LoCoMo, 94.4% LongMemEval
- **Mem0 memory-benchmarks repo:** 92.5% LoCoMo (top 200), 94.4% LongMemEval (top 200)
- **True Memory paper (May 2026):** Mem0 at 61.4% LoCoMo
- **Hindsight benchmark repo:** Mem0 at 66.88% LoCoMo, Mem0-Graph at 68.44%

The discrepancy between 92.5% and 61.4-66.88% likely reflects the difference between Mem0 platform v3 (cloud, new algorithm, May 2026) vs. Mem0 OSS (older algorithm). The True Memory and Hindsight papers likely tested the open-source version, while Mem0's own numbers are from their commercial platform. **This is a crucial distinction that is almost never made explicit in comparison tables.**

### The engram-2 "17% E2E QA for MemPalace" Claim

The README's reference to engram-2 claiming "17% E2E QA for MemPalace" was not found substantiated in engram-2's published materials. The engram-2 README discusses a ~17-point gap between engram-2's own LoCoMo score (74.5%) and SOTA (91.7%), attributing it to the answerer model rather than retrieval quality. The repo title claims "R@5 = 0.99 on LongMemEval S (beats MemPalace)" but provides no specific MemPalace numbers for comparison. No "17% E2E QA" figure for MemPalace appears in their benchmark tables.

---

## 4. What Multipass SME Can Test Today and What It Would Need

### Current Capabilities

The multipass-structural-memory-eval (SME) framework currently has:

**Adapters (7):**
- `flat` -- baseline flat retrieval
- `mempalace` -- direct ChromaDB access (for upstream without daemon)
- `mempalace-daemon` -- HTTP to palace-daemon (production setup)
- `familiar` -- familiar.realm.watch retrieval pipeline (reranking, temporal decay, compression)
- `rlm` -- RLM orchestrator (LLM decides when to search)
- `ladybugdb` -- LadybugDB adapter
- `full-context` -- (implied by condition D documentation)

**Implemented Categories:**
- Cat 1 (The Lookup) -- factual retrieval via substring matching on `expected_sources`
- Cat 2c (The Stairway) -- multi-hop retrieval
- Cat 4 (The Threshold) -- ingestion integrity with B-Cubed scoring
- Cat 5 (The Missing Room) -- gap detection via topology
- Cat 8 (The Blueprint) -- ontology coherence
- Cat 9 (The Handshake) -- harness integration (scaffolding stage)

**Corpora:**
- `jp_realm_v0_1` -- 30 personal knowledge questions (live readings: familiar 78.33%, daemon raw, RLM variants)
- `standard_v0_1` -- seeded test corpus with vault
- `good-dog-corpus` -- seeded corpus for Cat 4 testing (alias/entity resolution)
- `longmemeval` -- LongMemEval-S 500-question loader (from HuggingFace dataset)

**Cross-validation infrastructure:**
- LongMemEval loader maps questions to SME categories
- B-Cubed scorer for entity resolution quality
- Claims library (`structural_claims.yaml`) for testable architectural claims
- A/B/C condition methodology for isolating structural contributions

**Existing baselines:**
- 11 baseline JSON files in `baselines/` comparing daemon, familiar, and RLM adapters

### What SME Can Already Measure That Would Fill Gaps

1. **R@5 retrieval recall on LongMemEval-S via any adapter.** The LongMemEval loader is already wired. Running `sme-eval retrieve --adapter mempalace-daemon --questions sme/corpora/longmemeval/` would produce an R@5 number directly comparable to MemPalace upstream's 96.6%, agentmemory's 95.2%, engram-2's 99.0%, ai-memory's 97.8%, and mcp-memory-service's 80.4%.

2. **Structural categories (Cat 2c, 4, 5, 8) that no other benchmark tests.** These are unique to SME and measure things published benchmarks ignore: cross-domain discovery, ingestion integrity, gap detection, ontology coherence. No competing system has published scores on these dimensions.

3. **A/B/C ablation** isolating what palace structure (wings, rooms, KG, BM25, graph hybrid) contributes vs. flat vector retrieval. This directly addresses the MemPalace benchmark controversy -- SME can quantify whether palace features help or hurt, under controlled conditions.

4. **Familiar vs. daemon delta** measuring what the reranking/pipeline layer adds on top of raw retrieval.

### What SME Would Need to Add

1. **E2E QA scoring pipeline.** This is the critical gap. SME currently scores retrieval via substring matching (`context_string` contains `expected_sources`). To produce numbers comparable to OMEGA (95.4%), Hindsight (91.4%), Mem0 (94.4%), and True Memory (87.8%), SME needs:
   - An answer generation step (feed retrieved context + question to an LLM)
   - A judge step (GPT-4o judge per LongMemEval protocol, or equivalent)
   - This is partially designed in `docs/cross_validation_2026.md` but not yet implemented

2. **Adapter for competing systems.** To make cross-system comparisons on identical infrastructure:
   - Hindsight adapter (MCP or REST API)
   - Mem0 adapter (Python SDK)
   - OMEGA adapter (Python SDK, pip install omega-memory)
   - agentmemory adapter (Python SDK)
   - This would be the first independent multi-system benchmark on identical corpus/model/judge

3. **LoCoMo corpus loader.** Currently only LongMemEval is wired. Adding LoCoMo would enable comparison against EverOS (93.05%), True Memory (93.0%), Hindsight (89.61%), Mem0 (92.5%), and engram-2 (74.5%).

4. **BEAM corpus loader.** The production-scale benchmark. Only 4 systems have published BEAM results. Adding this would differentiate SME from every other eval framework.

5. **Cat 9 (The Handshake) full implementation.** This is SME's unique contribution -- measuring whether the model actually reaches memory when running in production (invocation rate, call-through success). The RLM adapter readings (46.67% recall due to tool non-invocation) are early Cat 9 data. Full implementation would be the first benchmark of harness integration quality.

6. **Standardized model configuration.** To enable fair comparisons, SME should define and publish a reference answer model + judge model configuration. GPT-4o for judge (per LongMemEval convention) and a fixed answer model (e.g., GPT-4o or GPT-4.1) would make all numbers directly comparable.

---

## 5. Recommended Next Steps

### Short-term (measurable value with existing infrastructure)

1. **Run LongMemEval-S R@5 through the daemon adapter.** This produces the first R@5 number for the techempower-org fork's production palace (335K+ drawers, postgres + pgvector + BM25/vector/graph hybrid). Directly comparable to upstream's 96.6% ChromaDB baseline. The question: does the fork's hybrid search stack match or beat ChromaDB-only on the standard benchmark?

2. **Run LongMemEval-S R@5 through the familiar adapter.** Measures what the reranking pipeline adds. The delta between daemon and familiar on the same 500 questions is a clean measurement of pipeline value.

3. **Publish both numbers.** The fork currently cites 0.984 R@5 (held-out hybrid from upstream) and 78.33% recall on jp-realm. Neither is directly comparable to competing systems' LongMemEval numbers. Having a clean daemon R@5 on the standard 500-question corpus would be the first number outsiders can compare to.

### Medium-term (would differentiate SME)

4. **Implement E2E QA scoring.** The cross-validation design in `docs/cross_validation_2026.md` is 80% specified. Completing this makes SME the first framework that can produce both R@K retrieval numbers AND E2E QA accuracy on the same runs, showing the gap between the two metrics.

5. **Add adapters for 2-3 competing systems.** Priority order by information value:
   - OMEGA (highest claimed LongMemEval QA, local-only, pip-installable -- easiest to benchmark fairly)
   - Hindsight (published their benchmark data, MCP-compatible)
   - Mem0 OSS (widely cited, open eval framework, Python SDK)

6. **Run structural categories (Cat 4, 5, 8) on competing systems.** No other benchmark tests ingestion integrity, gap detection, or ontology coherence. This is SME's unique contribution and would be the first published structural quality comparison across memory systems.

### Long-term (research-grade contribution)

7. **Build a unified leaderboard** with mandatory disclosure of: metric type (R@K vs. QA), answer model, judge model, dataset split, question count, and whether adversarial/category-5 questions were included. The landscape desperately needs this -- every existing comparison table mixes metrics, models, and dataset versions.

8. **Cross-validate Cat 9 (The Handshake) across systems.** The RLM adapter data (46.67% recall despite available tool) demonstrates that invocation rate is a real failure mode no benchmark captures. Extending this to other MCP-based memory systems would quantify the "works in theory, fails in practice" gap.

---

## Appendix A: Per-System Detail Sheets

### MemPalace (techempower-org fork)

- **LongMemEval R@5:** 0.984 (held-out hybrid v4, 450 questions) -- inherited from upstream
- **jp-realm-v0.1:** 78.33% recall (familiar v0.3.9, deterministic, 30 questions)
- **No E2E QA scores published** on standard benchmarks
- **Source:** upstream BENCHMARKS.md, SME baselines

### MemPalace (upstream)

- **LongMemEval R@5:** 96.6% raw (ChromaDB baseline), 98.4% hybrid v4 held-out, 100% hybrid v4 + rerank (contaminated)
- **LoCoMo R@10:** 88.9% (top-10, no rerank), 100% (top-50, structurally guaranteed)
- **ConvoMem:** 92.9% (300-item sample)
- **All retrieval recall, no E2E QA**
- **Source:** [BENCHMARKS.md](https://github.com/MemPalace/mempalace/blob/develop/benchmarks/BENCHMARKS.md)

### Mem0

- **LoCoMo:** 92.5% (platform v3) / 61.4-68.5% (OSS, older alg)
- **LongMemEval:** 94.4-94.8% (platform v3) / 67.8% (old alg)
- **BEAM-1M:** 64.1-70.1% / BEAM-10M: 48.6-50.5%
- **Multiple score versions in circulation** -- platform vs. OSS vs. old-vs-new algorithm
- **Source:** [README](https://github.com/mem0ai/mem0), [mem0.ai/research](https://mem0.ai/research), [memory-benchmarks](https://github.com/mem0ai/memory-benchmarks)

### Hindsight

- **LongMemEval:** 91.4% (Gemini 3 Pro), 89.0% (OSS-120B), 83.6% (OSS-20B)
- **LoCoMo:** 89.61% (Gemini-3), 85.67% (OSS-120B), 83.18% (OSS-20B)
- **BEAM:** 75% (100K), 73.9% (1M), 71.1% (500K), 64.1% (10M)
- **PersonaMem:** 86.6% (32K) / **LifeBenchEN:** 71.5%
- **Independently reproduced** by Virginia Tech + Washington Post
- **Source:** [arXiv:2512.12818](https://arxiv.org/abs/2512.12818), [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks)

### OMEGA

- **LongMemEval:** 95.4% QA (GPT-4.1, 466/500)
- **MemoryStress:** 38.3% (1000 sessions, 10 months, adversarial)
- **Self-reported only**, solo developer, zero funding
- **Source:** [omegamax.co/benchmarks](https://omegamax.co/benchmarks)

### True Memory

- **LoCoMo:** 93.0% Pro / 92.0% Base (3-run mean) / **LongMemEval:** 87.8% Pro / 85.5% Base / **BEAM-1M:** 76.6% Pro / 74.9% Base / **BEAM-10M:** 65.0% Pro (preliminary)
- **Answer model:** gpt-4.1-mini
- **Pro tier adds HyDE query expansion** (~1.0pp gain) and larger cross-encoder reranker
- **56-configuration ablation:** 53/56 configs above 90% LoCoMo, only 3.2pp total spread — proves component choice is secondary to architecture
- **Retrieval bottleneck diagnostic (Section 6.2):** 330/357 wrong answers fixed when given full correct context — quantifies the R@K → QA gap
- **Six-layer verbatim-first architecture, no code release**
- **Source:** [arXiv:2605.04897](https://arxiv.org/abs/2605.04897)

### EverOS/EverMind

- **LoCoMo:** 93.05% (EverCore) / 92.73% (HyperMem)
- **LongMemEval:** 83.0%
- **Eval data published** on HuggingFace (EverMemOS_Eval_Results)
- **Source:** [EverOS README](https://github.com/EverMind-AI/EverOS), [arXiv:2601.02163](https://arxiv.org/abs/2601.02163)

### Zep/Graphiti

- **DMR:** 94.8% (GPT-4-Turbo)
- **LongMemEval:** 71.2% (GPT-4o) / 63.8% (GPT-4o-mini)
- **LoCoMo:** 75.14% (Hindsight benchmark repo)
- **Source:** [blog.getzep.com](https://blog.getzep.com/state-of-the-art-agent-memory/), [arXiv:2501.13956](https://arxiv.org/abs/2501.13956)

### Supermemory

- **LongMemEval:** 81.6% (GPT-4o) / 85.2% (Gemini-3) / 84.6% (GPT-5)
- **LoCoMo:** 65.4% (per True Memory paper)
- **ConvoMem:** Claims #1, no score published
- **Source:** [supermemory README](https://github.com/supermemoryai/supermemory), [hindsight-benchmarks](https://github.com/vectorize-io/hindsight-benchmarks)

### agentmemory

- **LongMemEval-S R@5:** 95.2% / R@10: 98.6% / MRR: 88.2%
- **Embedding:** all-MiniLM-L6-v2 (local, free)
- **Retrieval only, no E2E QA**
- **Source:** [agentmemory README](https://github.com/rohitg00/agentmemory)

### engram-2

- **LongMemEval-S R@5:** 0.990 (99.0%) / R@10: 0.998 / MRR: 0.946
- **Embedding:** Gemini Embed 2 + FTS5 + RRF
- **LoCoMo-QA:** 74.5% (strict judge, GPT-5.4 answerer, 200q subset)
- **Source:** [github.com/199-biotechnologies/engram-2](https://github.com/199-biotechnologies/engram-2)

### ai-memory

- **LongMemEval-S R@5:** 97.8% (smart tier with LLM query expansion)
- **R@10:** 99.0% / R@20: 99.8%
- **All tiers use local models** (FTS5, MiniLM-L6-v2, Gemma 4 E2B via Ollama)
- **Source:** [github.com/alphaonedev/ai-memory-mcp](https://github.com/alphaonedev/ai-memory-mcp)

### mcp-memory-service

- **LongMemEval R@5:** 80.4% (turn-level) / 86.0% (session-level)
- **R@10:** 90.4% / NDCG@10: 82.2% / MRR: 89.1%
- **LoCoMo R@5:** 49.7% / MRR: 0.414
- **DevBench R@5:** 91.1%
- **Embedding:** all-MiniLM-L6-v2
- **Source:** [github.com/doobidoo/mcp-memory-service](https://github.com/doobidoo/mcp-memory-service)

### Celiums

- **LongMemEval QA:** 62.3% (best of 5 models via DigitalOcean Gradient)
- **Retrieval:** 100% (engine always found the right session)
- **Key insight:** Gap is entirely in synthesis, not retrieval
- **Source:** [celiums.ai/blog](https://celiums.ai/blog/longmemeval-benchmark-honest-results/)

### ENGRAM (academic paper, distinct from EngramX/NickCirv)

- **LoCoMo:** 77.55% (GPT-4o-mini backbone)
- **LongMemEval-S:** 71.4% (GPT-4o-mini)
- **~99% token reduction** vs. full context
- **Source:** [arXiv:2511.12960](https://arxiv.org/abs/2511.12960)

---

## Appendix B: Landscape Summary (Sorted by Best Published LongMemEval QA Accuracy)

| Rank | System | LongMemEval QA | LoCoMo QA | BEAM-1M | Metric Type | Notes |
|---|---|---|---|---|---|---|
| 1 | OMEGA | 95.4% | -- | -- | QA | Self-reported; GPT-4.1 |
| 2 | Mem0 (platform v3) | 94.4% | 92.5% | 70.1% | QA | Self-reported; cloud-only v3 alg |
| 3 | Mastra | 94.87% | -- | -- | QA | Self-reported; GPT-5-mini |
| 4 | Hindsight | 91.4% | 89.61% | 73.9% | QA | Indie verified; Gemini 3 Pro |
| 5 | True Memory (Pro) | 87.8% | 93.0% | 76.6% | QA | arXiv paper; gpt-4.1-mini; 65.0% BEAM-10M |
| 5b | True Memory (Base) | 85.5% | 92.0% | 74.9% | QA | No HyDE; smaller reranker |
| 6 | Supermemory | 81.6-85.2% | 65.4% | -- | QA | Model-dependent; GPT-4o to Gemini-3 |
| 7 | EverOS/EverMind | 83.0% | 93.05% | -- | QA | Self-reported |
| 8 | ENGRAM (paper) | 71.4% | 77.55% | -- | QA | Paper; GPT-4o-mini |
| 9 | Zep/Graphiti | 71.2% | 75.14% | -- | QA | Blog + paper; GPT-4o |
| 10 | Celiums | 62.3% | -- | -- | QA | Self-reported; honest methodology |
| -- | MemPalace (upstream) | (96.6-98.4% R@5) | (88.9% R@10) | -- | R@K only | Not QA; ChromaDB baseline |
| -- | engram-2 | (99.0% R@5) | 74.5% QA | -- | Mixed | R@5 retrieval + LoCoMo QA |
| -- | agentmemory | (95.2% R@5) | -- | -- | R@K only | Not QA |
| -- | ai-memory | (97.8% R@5) | -- | -- | R@K only | Not QA |
| -- | mcp-memory-service | (80.4-86.0% R@5) | (49.7% R@5) | -- | R@K only | Not QA |
