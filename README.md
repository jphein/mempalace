# MemPalace (techempower-org fork)

**TechEmpower's production fork of [MemPalace/mempalace](https://github.com/MemPalace/mempalace)** (transferred from `jphein/mempalace` in May 2026)

> [!CAUTION]
> # 🚨 CRITICAL SECURITY WARNING: BEWARE OF SCAMS (upstream notice)
> **MemPalace has NO other official websites.**
>
> The **ONLY** official sources are:
> 1. The upstream **[GitHub repository](https://github.com/MemPalace/mempalace)** and this fork's **[GitHub repository](https://github.com/techempower-org/mempalace)**
> 2. The **[PyPI package](https://pypi.org/project/mempalace/)**
> 3. The docs at **[mempalaceofficial.com](https://mempalaceofficial.com)**
>
> **ANY other domain** (including `.tech`, `.net`, or other `.com` variants) is an **impostor** and may distribute **malware**. Do not download executables from untrusted sites. Details and timeline: [docs/HISTORY.md](docs/HISTORY.md).

> [!IMPORTANT]
> **🚨 Claude Code sessions expire in 30 days w/out auto-save hooks wired!** **[Read this →](https://github.com/MemPalace/mempalace/discussions/1388)**
>
> Need the shortest recovery/setup path? Use the
> [Claude Code retention setup checklist](https://mempalaceofficial.com/guide/claude-code-retention.html).

[![version-shield](https://img.shields.io/badge/version-3.3.5-4dc9f6?style=flat-square&labelColor=0a0e14)](https://github.com/techempower-org/mempalace/releases) [![upstream-shield](https://img.shields.io/badge/upstream-3.3.5-7dd8f8?style=flat-square&labelColor=0a0e14)](https://github.com/MemPalace/mempalace/releases)
[![python-shield](https://img.shields.io/badge/python-3.9+-7dd8f8?style=flat-square&labelColor=0a0e14&logo=python&logoColor=7dd8f8)](https://www.python.org/)
[![license-shield](https://img.shields.io/badge/license-MIT-b0e8ff?style=flat-square&labelColor=0a0e14)](LICENSE)

---

## What this is

A verbatim-first local AI memory system. This fork tracks `upstream/develop` through the 2026-05-23 sync (commit `eb77c8c`) and runs in production on a **335K+ drawer Postgres + pgvector + Apache AGE palace** behind [palace-daemon](https://github.com/techempower-org/palace-daemon). It carries ~490 fork-ahead commits that compose with — not replace — bensig's release direction; the v3.3.5 release (2026-05-10) includes our co-authored `_get_collection` retry-once via upstream #1377. 2993 tests pass on `main`.

The fork's architectural thinking — the four-layer memory model, the verbatim-vs-derivative thesis, design principles, and the two-memory-layer pairing with Auto Dream — lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The new things here are *what we've learned*, not just what we've fixed.

## Why this fork exists

We surveyed the memory-system landscape in April 2026 and found no verbatim-first local system with MCP. The landscape has since fragmented — MCP memory servers proliferated in May 2026 — but the verbatim-vs-derivative axis remains the clearest architectural dividing line. Updated survey as of 2026-05-24:

### Verbatim-first systems

| System | Local? | MCP? | First public | Notes |
|---|---|---|---|---|
| **MemPalace** ([upstream](https://github.com/MemPalace/mempalace)) / **[techempower-org fork](https://github.com/techempower-org/mempalace)** | Yes | Yes | 2026-04-06 (v3.0.0) | What we have. 335K+ drawers in production. Postgres + pgvector + AGE knowledge graph + BM25/vector/graph hybrid search. ~23K stars (upstream). |
| [Longhand](https://github.com/Wynelson94/longhand) | Yes | Yes, 17 tools | 2026-04-14 (v0.9.1) | Closest cousin. Claude Code-specific — indexes `~/.claude/projects/*.jsonl` verbatim. SQLite + ChromaDB. Deterministic file-state replay via stored diffs. |
| [Celiums](https://celiums.ai/) | Yes (SQLite, Docker, or DO) | Yes, 6 tools | 2026-04-08 | Full module text with PAD emotional vectors, importance scores, circadian metadata. 500K+ expert-module knowledge base alongside personal memory. |
| [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | Yes (SQLite) or Cloudflare Workers | Yes | 2024-12-26 | The long-standing verbatim option (v10.36.6). Turn-level storage; MiniLM local embeddings. REST API + MCP + OAuth + CLI + dashboard. |
| [iai-mcp](https://github.com/CodeAbra/iai-mcp) | Yes (LanceDB) | Yes | ~2026 | Three-layer: episodic (verbatim, write-once), semantic (consolidated summaries), procedural (stable preferences). Background sleep-cycle consolidation. |
| [ai-memory](https://github.com/alphaonedev/ai-memory-mcp) | Yes (SQLite FTS5) | Yes, 43 tools | ~2026 | Rust binary. Three tiers with configurable TTL. Autonomous curator daemon (auto-tag, contradiction detection, dedup). Ed25519 attestation. |
| [Open Brain (OB1)](https://github.com/NateBJones-Projects/OB1) | Yes (Postgres + pgvector, Docker) | Yes, 10 tools | ~2026 | Separates raw data from embedding indexes — rebuild indexes without touching source. HNSW sub-ms vector search. |

### Extraction-based / derivative systems

| System | Local? | MCP? | First public | Notes |
|---|---|---|---|---|
| [claude-mem](https://github.com/thedotmack/claude-mem) | Yes (SQLite + ChromaDB) | Yes | ~2025-10 | **89K+ stars** — largest community by far. AI-compressed summaries, not verbatim. "Endless Mode" for extended sessions. |
| [Mem0](https://github.com/mem0ai/mem0) / [OpenMemory](https://github.com/mem0ai/mem0/tree/main/openmemory) | Partial | Yes | 2023-06 | ~48K stars. New 2026 algorithm: single-pass hierarchical extraction + multi-signal retrieval (91.6% accuracy). Opt-in `infer=False` for verbatim hard constraints. Graph Memory locked behind Pro. |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | Partial (Neo4j/FalkorDB) | Yes (Graphiti MCP v1.0) | 2023 / 2024 | ~22.8K stars. Temporal knowledge graph with dual timelines. 63.8% LongMemEval. Cloud Pro $99/mo+. |
| [Letta](https://github.com/letta-ai/letta) (formerly MemGPT) | Yes | Partial (transitioning) | 2023-10 | ~22.8K stars. V1 architecture rework (Mar 2026) — heartbeats deprecated. New [Letta Code](https://github.com/letta-ai/letta-code) (memory-first coding agent). Three-tier: core/recall/archival. MCP shifting from server-side to client-side skills. |
| [Supermemory](https://github.com/supermemoryai/supermemory) | Cloud-first (Cloudflare Workers) | Yes | ~2024 | 22.7K stars. Fact extraction + graph. Dual-layer timestamps. Plugins for Claude Code, OpenCode, Hermes. |
| [Cognee](https://github.com/topoteretes/cognee) | Yes | Yes | 2023-08 | ~14.8K stars. "Memory control plane" via ECL pipeline. MCP with graph/RAG/code/cypher search modes. v1.1.0.dev1. |
| [Hindsight](https://github.com/vectorize-io/hindsight) | Yes (Docker) | Yes | 2026-01-05 | ~14K stars. v0.6.2. Three ops: retain/recall/reflect. Bank Template Hub, Constellation graph view. Fortune 500 production use. |
| [CaviraOSS OpenMemory](https://github.com/CaviraOSS/OpenMemory) | Yes | Yes | 2025-10-26 | 4.1K stars. TypeScript. Time-based filtering, connectors for GitHub/Notion/GDrive. Migration tools from Mem0/Zep/Supermemory. |

### Structured / hybrid approaches

| System | Local? | MCP? | First public | Notes |
|---|---|---|---|---|
| [agentmemory](https://github.com/rohitg00/agentmemory) | Yes (SQLite) | Yes, 53 tools | ~2026-04 | 9.4K stars. BM25 + vectors + KG via RRF. Confidence decay, auto-archival. 95.2% R@5 on LongMemEval-S. |
| [EngramX](https://github.com/NickCirv/engram) | Yes (SQLite) | Yes | 2026-04-11 | v4.0 "Skill Pack" (May 2026). Context spine intercepts file reads — ~89% token reduction. 8 IDEs. Bi-temporal mistake prevention. 3-layer cache (23us/op). |
| [EverOS / EverMind](https://github.com/EverMind-AI/EverOS) | Yes (Docker) | Yes | ~2025 | SOTA on LoCoMo (93.05%), LongMemEval-S (83.0%). Three-phase lifecycle: episodic → semantic → reconstructive. Multimodal. |
| [OMEGA](https://github.com/omega-memory/core) | Yes (SQLite + ONNX) | Yes, 25 tools | ~2026-03 | 95.4% LongMemEval. Zero external deps. AES-256-GCM encryption at rest. Open-core (Apache 2.0 core; Pro for multi-agent). |

### Academic / not-yet-shipped

| System | Notes |
|---|---|
| [True Memory](https://arxiv.org/abs/2605.04897) | arXiv 2026-05. Six-layer verbatim-first architecture. 93.0% LoCoMo, 87.8% LongMemEval, 76.6% BEAM-1M. Argues "extraction at ingestion is the wrong primitive" — independent validation of the verbatim thesis. No code release yet. |

### Notable shifts since April 2026

- **The verbatim thesis has academic validation.** True Memory (arXiv:2605.04897) independently argues that extraction at ingestion is the wrong primitive, scoring 93.0% on LoCoMo vs Mem0's 61.4%. New entrants iai-mcp and ai-memory both chose verbatim-first designs, suggesting the pattern has reached broader adoption.
- **claude-mem (89K+ stars) is the elephant in the room.** Explicitly non-verbatim (AI compression), but its community size makes it the default comparison point. The largest system taking the opposite architectural approach.
- **Letta V1 rework (Mar 2026) deprecates heartbeats and server-side MCP.** MCP support is shifting to client-side skills; the story is less clear-cut than before.
- **Mem0 shipped a significant algorithm upgrade** (single-pass hierarchical extraction + multi-signal retrieval → 91.6%) without going verbatim. Added opt-in `infer=False` for verbatim hard constraints — an escape hatch, not a core commitment.
- **MCP memory server space fragmented dramatically.** At least 6 new systems with MCP support since April: agentmemory, OMEGA, ai-memory, iai-mcp, Open Brain, EngramX v4.0. Most are local-first SQLite. Differentiators narrowing to verbatim-vs-extraction and consolidation strategy.

The April-2026 verbatim cluster (MemPalace, Celiums, Longhand, engram all within ~8 days) is no longer an isolated coincidence — it was the leading edge of a pattern now confirmed by academic work and a second wave of implementations. The differentiator: **verbatim storage is the foundation; everything else (tags, KG, decay, summaries, consolidated indices) is enrichment layered on top.**

## Quickstart

```bash
git clone https://github.com/techempower-org/mempalace.git
cd mempalace
uv sync --extra dev          # recommended; or: python -m venv venv && pip install -e ".[dev]"

uv run mempalace init ~/Projects --yes
uv run mempalace mine ~/Projects/myproject
uv run mempalace search "why did we switch to GraphQL"
```

For a daemon-fronted deployment (recommended once palace size reaches the multi-thousand-drawer range), see [palace-daemon](https://github.com/techempower-org/palace-daemon)'s setup. The fork's `scripts/deploy.sh` is a one-command Syncthing-aware redeploy: push fork main, restart palace-daemon, post-restart import-check that the new fork-ahead surface is loaded.

## What it looks like in production

A Stop hook fires every 15 messages in Claude Code, triggers verbatim transcript mining via the daemon's `/mine` endpoint (no LLM in the loop), and renders a terminal line so the user sees the ingest land:

```json
{"systemMessage": "✦ Transcript ingest triggered (wing=wing_realmwatch)"}
```

`search_memories` (via `mempalace_search` MCP tool) returns results with scope-authoritative context so callers can tell when the vector layer underdelivered:

```json
{
  "query": "kiyo xhci usb crash fix razer",
  "total_before_filter": 15,
  "available_in_scope": 160351,
  "warnings": [],
  "results": [
    {"drawer_id": "drawer_kiyo-xhci-fix_technical_a8b2c4...", "wing": "projects",
     "room": "technical", "similarity": 0.859, "matched_via": "drawer", ...},
    {"drawer_id": "drawer_kiyo-xhci-fix_technical_d5e7f9...", "wing": "kiyo-xhci-fix",
     "room": "technical", "similarity": 0.852, "matched_via": "drawer", ...}
  ]
}
```

When the HNSW index is genuinely degraded (rare, post-fix), the same call returns `warnings: ["vector search returned 0 of 5 requested; filled 5 from sqlite+BM25 keyword match"]` with hits tagged `"matched_via": "sqlite_bm25_fallback"` — data is never silently hidden.

## Current state

**Substrate (2026-05-15).** Postgres + pgvector + Apache AGE shipped on `main` and serving production traffic. PG16 + pgvector 0.8.2 + AGE 1.6.0 on `disks.jphe.in:5433`. One engine consolidates vector search, full-text search (tsvector BM25), graph traversal, and the temporal entity-relationship store — previously four separate systems (ChromaDB + SQLite + graph cache). 8/9 bench suites pass. Full operator narrative at [`docs/operators/pgvector-cutover-runbook.md`](docs/operators/pgvector-cutover-runbook.md).

**AGE integration (2026-05-22).** [PR #101](https://github.com/techempower-org/mempalace/pull/101) merged — six-phase AGE integration complete. Writethrough middleware on every drawer write extracts entities and creates `:MENTIONS` edges in the AGE graph. Backfill running against 335K+ existing drawers at ~5/s. The `mempalace_walk_palace` MCP tool enables Cypher traversal by wing, room, or entity. A [2026-05-17 spike](https://github.com/techempower-org/multipass-structural-memory-eval/blob/feat/rlm-adapter/docs/benchmarks/2026-05-17-age-write-through-spike.md) showed graph signal adds **+9pp R@5** over vector-only retrieval.

**Hybrid retrieval (2026-05-24).** `candidate_strategy="hybrid"` (vector ∪ tsvector BM25 ∪ AGE graph-expanded candidates, hybrid-reranked) is now the MCP default for all callers.

## What this fork ships

Three bands of work, all instances of the [architectural principles](docs/ARCHITECTURE.md#design-principles). Detail rows in the [fork change inventory](#fork-change-inventory) and [`FORK_CHANGELOG.md`](FORK_CHANGELOG.md).

- **Structural retrieval fixes.** Verbatim-only model: hooks no longer write 1KB checkpoint summaries; auto-mined transcript chunks land in `mempalace_drawers` and `mempalace_search` reaches them directly. One collection, one search path, no kind-filter / over-fetch hack.
- **Single-writer architecture.** [palace-daemon](https://github.com/techempower-org/palace-daemon) is the only process that opens the palace; clients connect over HTTP. ChromaDB HNSW concurrency hazards become structurally impossible.
- **Deterministic hook saves.** Silent saves bypass auto-memory conflicts — the LLM is no longer in the save path. Verbatim transcript ingest is the entire save path.

## Planned work

Organized around the [verbatim-vs-derivative axis](docs/ARCHITECTURE.md#1-verbatim-vs-derivative-is-the-canonical-axis). Each item evaluated against the architectural principles.

| ID | What | Status | Tracking |
|---|---|---|---|
| P0 | Multi-label tags (3-8 per drawer, TF-IDF extraction) | Open | Fork-side |
| P1 | Derive hierarchy from unambiguous signals (cwd, transcript path) | Open | Fork-side |
| P2 | Decay / recency weighting (Weibull) | Tracked upstream | [#1032](https://github.com/MemPalace/mempalace/pull/1032) |
| P3 | Feedback loops (rerank + rating MCP tool) | Rerank tracked upstream | [#1032](https://github.com/MemPalace/mempalace/pull/1032) |
| P4 | KG auto-population + entity resolution | **Shipped 2026-05-22** | [PR #101](https://github.com/techempower-org/mempalace/pull/101) |
| P5 | Temporal fact validity (SPOC context slot) | Open, depends on P4 | — |
| P6 | Input sanitization on writes | Low priority while local-only | — |
| P7 | Alternative storage modes | **Shipped** (pgvector+AGE) | [RFC 001 #743](https://github.com/MemPalace/mempalace/pull/743) |
| P8 | Corpus partitioning by purpose | On hold | [Design doc](docs/designs/multi-palace-separation.md) |

## Active investigations

- **Engram-2's "17% E2E QA" critique** — closing. Structural fix shipped (checkpoint corpus-shape correction); E2E LongMemEval run instrumented, results to publish at `notebook/data/cat9-postmigrate-e2e/REPORT.md`.
- **Cat 9 / The Handshake** — generalizable measurement of the retrieval→consumption gap. 46.67% / 78.33% on RLM-vs-Familiar. Scaling across the verbatim-first cohort via [`jphein/multipass-structural-memory-eval`](https://github.com/jphein/multipass-structural-memory-eval).
- **Multi-palace separation** — curated "authority" vs auto-mined memory ([upstream #1018](https://github.com/MemPalace/mempalace/discussions/1018)). P8 may absorb. [Design doc](docs/designs/multi-palace-separation.md).

## Composition with upstream

A meaningful shift in 2026-04 and 2026-05: this fork increasingly *composes with* upstream rather than carrying parallel implementations.

- **Cherry-picks (in-flight upstream PRs we use early):** [#665](https://github.com/MemPalace/mempalace/pull/665) PostgreSQL backend (commit `5e90c72`, the substrate work above), [#1085](https://github.com/MemPalace/mempalace/pull/1085) batched inserts (`6be6fff` — CLOSED 2026-05-16, superseded by merged [#1185](https://github.com/MemPalace/mempalace/pull/1185); safe to drop on next sync), [#1087 rewrite](https://github.com/MemPalace/mempalace/pull/1087) `cmd_purge` via `delete(where=)` (`366a9ad`), [#1094](https://github.com/MemPalace/mempalace/pull/1094) None-metadata coercion (`43d728d`).
- **Co-authored merges:** [#1377](https://github.com/MemPalace/mempalace/pull/1377) (surgical `_get_collection` retry-once, shipped in v3.3.5 — originated from this fork via #1286 which igorls closed and re-extracted with `Co-authored-by` credit).
- **Coordinated reviews:** [#1199](https://github.com/MemPalace/mempalace/pull/1199) (rmdes' unbounded-ingest fix), [#1219](https://github.com/MemPalace/mempalace/pull/1219) (pepo72's drawer_id), [RFC 001 #743](https://github.com/MemPalace/mempalace/pull/743) (storage backend spec).
- **Closed in favor of upstream:** [#1171](https://github.com/MemPalace/mempalace/pull/1171) cross-process write lock (closed 2026-04-25 — Felipe's [#976](https://github.com/MemPalace/mempalace/pull/976) plus daemon-strict architecture obsoleted ours).

The fork ships structural moves first, validates them on the canonical palace, then either contributes upstream as PRs or aligns with upstream's parallel implementation. The composition is the point.

## Ecosystem

Four peer builds converged on the same architectural agreements as this fork (verbatim base layer, no LLM in index path, wings as scope routing, consumption problem unsolved by retrieval): [Familiar](https://github.com/jphein/familiar.realm.watch) (78.33% recall), [CampaignGenerator](https://github.com/kostadis/CampaignGenerator) (19.82x cost reduction), [Kent](https://github.com/kenchambers/kent) (APO training), [adaptmem](https://github.com/nakata-app/adaptmem) (orthogonal encoder lift). The [multipass-structural-memory-eval](https://github.com/M0nkeyFl0wer/multipass-structural-memory-eval) framework provides the Cat 9 / Handshake diagnostic.

Full inventory of companion tools, evaluation frameworks, competing systems, peer builds, and active forks in [`docs/ECOSYSTEM.md`](docs/ECOSYSTEM.md).

## Open upstream PRs

Open from this fork as of 2026-05-24. Run `gh pr list --repo MemPalace/mempalace --author jphein --state open` for the live list. Recently merged: [#1142](https://github.com/MemPalace/mempalace/pull/1142) (RELEASING.md, 2026-05-22), [#1494](https://github.com/MemPalace/mempalace/pull/1494) (recovery runbook, 2026-05-22), [#1487](https://github.com/MemPalace/mempalace/pull/1487) (rebuild_index progress, 2026-05-13), [#1024](https://github.com/MemPalace/mempalace/pull/1024) (configurable chunking, 2026-05-15), [#1459](https://github.com/MemPalace/mempalace/pull/1459) (empty-metadata sentinel, 2026-05-13) and [#1474](https://github.com/MemPalace/mempalace/pull/1474) (convo_miner bulk pre-fetch, 2026-05-13).

| PR | Status | Description |
|---|---|---|
| [#660](https://github.com/MemPalace/mempalace/pull/660) | CI green, awaiting review | L1 importance pre-filter |
| [#1005](https://github.com/MemPalace/mempalace/pull/1005) | CI green, Dialectician-acked | Warnings + sqlite BM25 top-up — never silently return fewer results than scope contains |
| [#1086](https://github.com/MemPalace/mempalace/pull/1086) | CI green, awaiting review | `mempalace export` CLI wrapper |
| [#1087](https://github.com/MemPalace/mempalace/pull/1087) | CI green, **rewritten 2026-04-26** per @igorls's review | `mempalace purge --wing/--room` via `delete(where=)` (no nuke-and-rebuild) |
| [#1094](https://github.com/MemPalace/mempalace/pull/1094) | CI green, awaiting review | Coerce `None` metadatas to `{}` at `ChromaCollection` boundary |
| [#1378](https://github.com/MemPalace/mempalace/pull/1378) | CI green | Hoist `CLOSET_RANK_BOOSTS` to module level + record VecRecall ablation finding |
| [#1382](https://github.com/MemPalace/mempalace/pull/1382) | CI green | Benchmarks UTF-8 encoding + ASCII print chrome on Windows |
| [#1484](https://github.com/MemPalace/mempalace/pull/1484) | CI pending | OpenCode source adapter on RFC 002 contract — co-authored with @JakobSachs |
| [#1508](https://github.com/MemPalace/mempalace/pull/1508) | CI pending | `symbol_header_prefix` kwarg in `chunk_text` |

## What's next

- **Publish Cat 9 end-to-end results** on the post-migration palace, with adapter parity numbers across the verbatim-first cohort.
- **Publish the multipass-structural-memory-eval harness** with adapters for MemPalace, Longhand, Celiums, mcp-memory-service.
- **Land P0 (multi-label tags) and P2 (decay/recency)** — P2 tracked upstream via [#1032](https://github.com/MemPalace/mempalace/pull/1032); P0 fork-side.
- **Agent-shaped CLI surface** — `mempalace search ... --json` for non-MCP integration. Prior art: Grafana's [GCX CLI](https://www.infoq.com/news/2026/04/grafana-loki-ai-agents/).
- **First-class support across AI coding agents** — Claude Code, OpenCode, Cursor, Aider, Gemini CLI, Codex CLI, Warp. Path: upstream's [RFC 002 source-adapter spec](https://github.com/MemPalace/mempalace/pull/990). Three cells: **read** (MCP, already agent-agnostic), **mine** (per-agent via RFC 002), **hook/event** (per-host or mining-on-cron fallback).

## Setup / Development

```bash
# Setup
git clone https://github.com/techempower-org/mempalace.git
cd mempalace
uv sync --extra dev                       # recommended; or pip install -e ".[dev]"

# Develop
uv run pytest tests/ -q                   # ~2993 tests (benchmarks deselected)
uv run mempalace status                   # palace health
uv run ruff check . && uv run ruff format --check .

# Doc maintenance (canonical YAML + renderer, see CLAUDE.md)
./scripts/render-docs.py                  # regenerate FORK_CHANGELOG from docs/fork-changes.yaml
./scripts/check-docs.sh                   # lint test count, fork hashes, render parity, upstream PR states

# Deploy fork main → palace-daemon on disks
./scripts/deploy.sh                       # one command: push, sync, restart, health, import-check
```

## Fork change inventory

The full enumeration of fork-ahead changes. The canonical source is [`docs/fork-changes.yaml`](docs/fork-changes.yaml); [`FORK_CHANGELOG.md`](FORK_CHANGELOG.md) is regenerated from it and contains the complete open/pending table. Run `./scripts/check-docs.sh` to verify everything resolves to live state.

### Recently merged into upstream

- **2026-05-22:** [#1142](https://github.com/MemPalace/mempalace/pull/1142) (`docs/RELEASING.md`), [#1494](https://github.com/MemPalace/mempalace/pull/1494) (recovery runbook for chromadb dimensionality=None corruption)
- **2026-05-15:** [#1024](https://github.com/MemPalace/mempalace/pull/1024) — Configurable `chunk_size` / `chunk_overlap` / `min_chunk_size` exposed via `MempalaceConfig`
- **2026-05-13:** [#1487](https://github.com/MemPalace/mempalace/pull/1487) (`rebuild_index` progress callback), [#1459](https://github.com/MemPalace/mempalace/pull/1459) (empty-metadata sentinel), [#1474](https://github.com/MemPalace/mempalace/pull/1474) (convo_miner bulk pre-fetch)
- **2026-05-06 (in v3.3.5):** [#1377](https://github.com/MemPalace/mempalace/pull/1377) — `_get_collection` retry-once + log-on-failure (co-authored from this fork via the closed #1286)
- **2026-05-01 (post-v3.3.4):** [#1262](https://github.com/MemPalace/mempalace/pull/1262), [#1289](https://github.com/MemPalace/mempalace/pull/1289), [#1303](https://github.com/MemPalace/mempalace/pull/1303)
- **2026-04-26:** [#1173](https://github.com/MemPalace/mempalace/pull/1173), [#1177](https://github.com/MemPalace/mempalace/pull/1177), [#1198](https://github.com/MemPalace/mempalace/pull/1198), [#1201](https://github.com/MemPalace/mempalace/pull/1201)
- **2026-04-23:** [#659](https://github.com/MemPalace/mempalace/pull/659) — diary `wing` parameter
- **2026-04-22:** [#661](https://github.com/MemPalace/mempalace/pull/661), [#673](https://github.com/MemPalace/mempalace/pull/673), [#1021](https://github.com/MemPalace/mempalace/pull/1021)
- **2026-04-21 (in v3.3.2):** [#1000](https://github.com/MemPalace/mempalace/pull/1000), [#1023](https://github.com/MemPalace/mempalace/pull/1023), [#681](https://github.com/MemPalace/mempalace/pull/681)
- **2026-04-18:** [#999](https://github.com/MemPalace/mempalace/pull/999) — None-metadata guards across 8 read paths
- **In v3.3.0:** [#664](https://github.com/MemPalace/mempalace/pull/664), [#682](https://github.com/MemPalace/mempalace/pull/682), [#683](https://github.com/MemPalace/mempalace/pull/683), [#684](https://github.com/MemPalace/mempalace/pull/684), [#635](https://github.com/MemPalace/mempalace/pull/635) (via #667)

### Closed (superseded or withdrawn)

- [#1085](https://github.com/MemPalace/mempalace/pull/1085) (cherry-pick — closed by @midweste 2026-05-16, superseded by merged upstream [#1185](https://github.com/MemPalace/mempalace/pull/1185))
- [#1286](https://github.com/MemPalace/mempalace/pull/1286) (drifted; @igorls closed and re-extracted the surgical fix as #1377 with co-author credit)
- [#1171](https://github.com/MemPalace/mempalace/pull/1171) (cross-process write lock — superseded by #976 + daemon-strict)
- [#1146](https://github.com/MemPalace/mempalace/pull/1146), [#1115](https://github.com/MemPalace/mempalace/pull/1115), [#629](https://github.com/MemPalace/mempalace/pull/629), [#632](https://github.com/MemPalace/mempalace/pull/632), [#662](https://github.com/MemPalace/mempalace/pull/662), [#663](https://github.com/MemPalace/mempalace/pull/663), [#738](https://github.com/MemPalace/mempalace/pull/738), [#1036](https://github.com/MemPalace/mempalace/pull/1036) — all superseded

## Sources

See [`docs/BIBLIOGRAPHY.md`](docs/BIBLIOGRAPHY.md) for the complete documentation index and external references.

## License

MIT — see [LICENSE](LICENSE).
