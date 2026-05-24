# True Memory vs. MemPalace: Architectural Comparison

**Paper:** "Storage Is Not Memory: A Retrieval-Centered Architecture for Agent Recall"
(Joshua Adler, Guy Zehavi, May 2026). [arXiv:2605.04897](https://arxiv.org/abs/2605.04897).

**Compared against:** MemPalace techempower-org fork (`main` as of 2026-05-24).
335K+ drawers on Postgres + pgvector + Apache AGE, hybrid retrieval (vector + BM25 + graph fusion).

---

## Six layers vs. four layers

True Memory defines a six-layer architecture; MemPalace uses a four-layer model ([`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)). The layers aren't the same — True Memory interleaves ingestion and retrieval into a single 10-stage pipeline, while MemPalace treats each layer as independently improvable. The mapping:

| True Memory layer | What it does | MemPalace equivalent |
|---|---|---|
| **L0: Speaker Engram** | Char-n-gram style vectors per speaker; speaker-aware retrieval weighting | No equivalent — single-user palace; wing/room scoping serves a similar disambiguation role |
| **L1: Verbatim + Lexical** | Store verbatim events; BM25 index (sqlite-vec FTS) | **Storage** — verbatim drawers, tsvector BM25 index |
| **L2: Dense Vector** | Embedding index (sqlite-vec); HyDE query expansion (Pro tier) | **Encoder** — pgvector embeddings; no query expansion |
| **L3: Salience Reweighter** | Surprise scoring (gzip-based novelty), recency decay, modality-aware boosting | Partially in **Retrieval** — Familiar does temporal decay + rerank; AGE graph provides structural signals; no novelty scoring |
| **L4: Consolidator** | Batch post-processing: summary rows, contradiction records, timeline rows | No equivalent in verbatim layer — deliberately. Auto Dream handles derivative consolidation; AGE KG has `valid_from`/`valid_to` for temporal facts |
| **L5: Predictive Coder** | Surprise scoring via compression-ratio novelty (gzip NCD) | No equivalent |

**Key difference:** True Memory's L3–L5 are query-time neural/statistical stages (cross-encoder reranking, surprise weighting). MemPalace keeps retrieval purely algorithmic — no model inference at query time. The cost/latency trade-off is explicit: True Memory uses a 22M–149M parameter cross-encoder reranker; MemPalace uses reciprocal rank fusion across three index types (vector, BM25, graph).

**What MemPalace's four-layer model names that True Memory doesn't:** the **Consumption** layer (layer 4) — what happens after retrieval. This is where the R@k → E2E QA gap lives. True Memory diagnoses this gap (Section 6.2: 330/357 wrong answers fixed by full context) but doesn't architect for it. MemPalace's ecosystem has three competing solutions: Familiar (deterministic pipeline), Kent (APO-trained invocation policy), and CampaignGenerator (hierarchical AAAK pruning).

---

## Benchmarks

| Benchmark | True Memory Pro | True Memory Base | MemPalace (fork) | Notes |
|---|---|---|---|---|
| **LongMemEval** (500Q) | 87.8% E2E QA | 85.5% E2E QA | 0.984 R@5 (retrieval) | Not directly comparable — different metrics |
| **LoCoMo** (1540Q) | 93.0% E2E QA | 92.0% E2E QA | not measured | |
| **BEAM-1M** (700Q) | 76.6% E2E QA | 74.9% E2E QA | not measured | |
| **jp-realm-v0.1** (30Q) | not measured | not measured | 78.33% recall (Familiar) / 46.67% (RLM) | Invocation discipline, not retrieval quality |

The numbers aren't comparable because they measure different layers. True Memory reports **end-to-end QA accuracy** (retrieval + answer generation via gpt-4.1-mini). MemPalace reports **retrieval recall** (R@5). The gap between these metrics is exactly what MemPalace calls the integration gap (Cat 9 / The Handshake): MemPalace's upstream 0.966 R@5 → 82.6% E2E QA on Issue #39 reproduction; Celiums shows 100% retrieval but only 62.3% QA with Opus.

**Cross-validation opportunity:** The multipass SME project now has a [LongMemEval loader](https://github.com/techempower-org/multipass-structural-memory-eval) (`sme/corpora/longmemeval/loader.py`, 500Q) and a cross-validation harness (`scripts/cross_validate_longmemeval.py`) that runs SME's substring scorer against LongMemEval's GPT-4o judge. Running MemPalace through the same LongMemEval questions with E2E QA scoring would produce the first apples-to-apples comparison. The good-dog-corpus (18Q across 6 categories: factual lookup, multi-hop, contradiction surfacing, alias resolution, temporal supersession, token efficiency) tests structural capabilities True Memory's benchmarks don't isolate.

**Scale concern:** True Memory's largest evaluation is BEAM-1M (35 conversations, ~1M tokens). Their preliminary BEAM-10M shows significant degradation (76.6% → 65.0%). MemPalace operates at 335K+ drawers from months of continuous use. The corpus-shape lessons (checkpoint domination, derivative-vs-verbatim separation) only surface at production scale.

---

## Where the two systems agree

**Verbatim storage is non-negotiable.** Both reject extraction-at-ingest. True Memory's core thesis — "content discarded before the query is known cannot be recovered at retrieval time" — is MemPalace's Principle 1 word-for-word. True Memory's 30pp gap between extraction-based systems (Mem0 at 61.4%, Supermemory at 65.4%) and retrieval-based systems (TM Base at 92.0%) is the strongest empirical validation yet of MemPalace's design bet.

**Hybrid retrieval with RRF fusion.** Both run BM25 + dense vector retrieval, fuse with reciprocal rank fusion (both use k=60 from Cormack et al. 2009). True Memory's Stage 8 maps directly to MemPalace's vector + BM25 union strategy. MemPalace adds AGE graph as a third fusion source; True Memory adds a separation-list source for multi-speaker corpora.

**Component choice is secondary to architecture.** True Memory's 56-configuration ablation shows only 3.2pp total spread across all embedder/reranker combinations, with 53 of 56 above 90%. MemPalace's thesis says the same thing differently: "corpus shape eats retrieval algorithm for breakfast." Both conclude that getting the architecture right matters far more than component selection.

**Local-first, zero mandatory external API.** True Memory runs on SQLite with sqlite-vec on a Raspberry Pi 4 ($12/month). MemPalace runs on Postgres + pgvector on a homelab server. Neither requires cloud services for core memory operations.

---

## Where they diverge

### LLM in the query path

True Memory uses a cross-encoder reranker at Stage 10 (22M–149M parameters) plus HyDE query expansion in the Pro tier. MemPalace does not use any model at query time — retrieval is purely algorithmic. True Memory's ablation shows the reranker contributes at most 1.3pp within the 256d subfamily and query expansion adds 1.0pp. Real gains, but with latency and compute costs MemPalace's design philosophy rejects.

### Ingestion gating vs. ingest everything

True Memory's Stage 1 Encoding Gate computes novelty (gzip-based), salience (rule-based for short messages, scorer for long), and prediction error to decide whether to admit an event. This is the most philosophically divergent feature. **Critically, True Memory disables this gate for all benchmarks** (tau = -infinity), so its value is unmeasured. MemPalace solved the same problem structurally (separating derivative writes from the verbatim corpus) rather than with a statistical filter.

### Consolidation

True Memory's L4 Consolidator produces summary rows, contradiction records, and timeline rows as batch post-processing. MemPalace deliberately does not consolidate the verbatim layer — consolidation is a separate concern (Auto Dream handles derivative indices; AGE KG handles temporal facts with `valid_from`/`valid_to`). The good-dog-corpus's Cat 3 (contradiction surfacing) and Cat 6 (temporal supersession) categories test exactly these capabilities.

### Storage substrate

SQLite (single file, portable) vs. Postgres + pgvector + AGE (server-based, concurrent access, graph traversal). At True Memory's evaluation scale (10–35 conversations), SQLite is clearly sufficient. At MemPalace's production scale (335K+ drawers, concurrent MCP access from multiple sessions), postgres enables capabilities SQLite cannot.

---

## What MemPalace could learn

**Surprise scoring (L5).** Gzip-based novelty (compression-ratio NCD) is cheap — no model inference, just `gzip(a+b) / gzip(a) + gzip(b)`. AUC 0.788 vs 0.484 for cosine-similarity inversion. The insight: "noise like 'ok' is semantically distant from factual memories while important updates are semantically close" — embedding distance inverts the novelty signal. MemPalace could use this as a write-time tag (not a gate) and boost novel drawers at retrieval time.

**Cross-encoder reranking.** The ablation shows a cheap reranker (ms-marco-MiniLM-L-6-v2, 22M parameters, runs on CPU) captures most of the value; upgrading to 149M barely moves the needle. Familiar already does reranking — this validates the approach and suggests the model choice is less important than having any reranker at all.

**Speaker profiles (L0).** Char-n-gram style vectors for per-speaker retrieval weighting. With 335K+ drawers from conversations with multiple AI agents, speaker-style disambiguation could improve retrieval for queries about specific interaction contexts.

**Modality-aware reranking.** Stage 10's modality factor (detail questions penalize summaries by 0.7x, synthesis questions boost them by 1.2x) is simple but principled. Directly applicable if MemPalace ever introduces derivative collections alongside verbatim.

---

## What True Memory is missing

**The integration gap.** True Memory's Section 6.2 retrieval-bottleneck diagnostic identifies exactly what MemPalace calls Cat 9 / The Handshake. But True Memory stops at diagnosis. MemPalace has gone further: the 46.67% vs 78.33% gap between RLM and Familiar proves the problem is invocation discipline, not retrieval quality. Familiar's always-on pipeline removes agent choice from the loop — a working solution True Memory doesn't have.

**Graph-based retrieval.** MemPalace's AGE graph fusion (the third leg of hybrid search) has no equivalent. The knowledge graph with temporal validity, entity relationships, and Cypher traversal enables patterns neither BM25 nor dense vectors can serve — "what changed about X between dates Y and Z" requires graph structure.

**Corpus hygiene as architecture.** MemPalace's hardest-won lesson — 0.4% of drawers (checkpoint saves) dominated 80%+ of vector top-N, fix was structural separation, not better filtering — has no parallel. True Memory's encoding gate attempts something similar but is disabled in all evaluations.

**Structural evaluation.** The multipass SME framework tests capabilities True Memory's benchmarks don't isolate: contradiction surfacing (Cat 3), alias resolution with B-Cubed scoring (Cat 4), temporal supersession (Cat 6), multi-hop traversal (Cat 2c). LoCoMo and LongMemEval are retrieval-and-QA benchmarks; they don't diagnose *where* a system fails structurally. The Karpathy condition D2 (LLM-compiled wiki as baseline) asks whether compression helps or hurts — a question True Memory's consolidation layer should care about.

---

## Bottom line

True Memory is the strongest empirical validation of MemPalace's core design bet: verbatim storage + retrieval-time intelligence beats extraction-at-ingest by ~30 percentage points. The 56-configuration ablation proving component choice is secondary to architecture directly supports the "corpus shape eats algorithm" thesis. The systems are convergent on fundamentals and diverge on execution: True Memory invests in a sophisticated multi-stage query pipeline with neural reranking and cognitive-science-inspired scoring; MemPalace invests in graph-augmented retrieval, production-scale corpus management, and closing the integration gap. Neither system mentions the other — which makes the convergence all the more notable.

**Actionable next steps:**
1. Run MemPalace through the LongMemEval cross-validation harness for apples-to-apples E2E QA comparison
2. Evaluate gzip-based novelty scoring as a write-time drawer tag (not gate)
3. Run the good-dog-corpus Cat 3/6 questions against True Memory's consolidation layer claims
4. Consider optional cross-encoder reranking in MemPalace's retrieval path (Familiar already has the hook)
