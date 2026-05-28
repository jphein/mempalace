# Ecosystem

MemPalace-orbit projects, companion tools, evaluation frameworks, and active forks. From a 2026-04-21 sweep of upstream issue/comment/discussion history, updated with peer-build entries surfaced in May 2026. State moves; check repos directly for current status.

For the broader memory-system landscape comparison (25+ systems), see the [comparison tables in the README](../README.md#why-this-fork-exists).

## Convergence with peer systems

The strongest defense of the fork's architectural choices in 2026 isn't anything this fork wrote — it's that three other production-shaped systems built on the MemPalace substrate independently arrived at compatible designs. Different domains, different teams, different downstream consumers, same four agreements at the architectural level.

The four agreements: (1) verbatim storage as the base layer, (2) no LLM in the index path, (3) wings as scope routing rather than required classification, (4) the consumption problem is real and not solved by retrieval quality. The divergence — where intelligence above retrieval lives — is where the field's debate actually is, and it's an interesting place to be wrong in different directions.

### Peer builds on MemPalace

Built *on top of* or *alongside* MemPalace by community contributors who use the palace as substrate.

- **[Familiar](https://github.com/jphein/familiar.realm.watch)** (@jphein) — deterministic retrieval pipeline running llama.cpp + Phi-4 on Pascal GPUs against MemPalace. Rerank, temporal decay, temporal query expansion, extractive compression, grounding directives, all running unconditionally. Measured: **78.33% recall** on jp-realm-v0.1.
- **[CampaignGenerator](https://github.com/kostadis/CampaignGenerator)** (@kostadis, with kostadis/mempalace fork) — RPG session-prep over a 2 TB local PDF library + 5etools JSON. Rank-bucketed AAAK projections enable hierarchical pruning before drawer-level vector search runs. Measured: **19.82× cost reduction at 0% recall@10 loss** on a 281-entry benchmark fixture. Articulates the *why* of deterministic intermediate compression more clearly than the fork itself does.
- **[Kent](https://github.com/kenchambers/kent)** (@kenchambers) — typed async agent runtime with APO (Automatic Prompt Optimization via Microsoft Agent Lightning) training memory invocation policies. Recall games (recall@k / scope / closet fidelity / tunnel utility). Heartbeat agent for between-conversation memory maintenance. Channel-as-wing automatic routing. Measured: APO Round 01 drawer-aware queries average 0.323 embedding similarity vs 0.027 for unrelated (3/3 pairwise wins).
- **[adaptmem](https://github.com/nakata-app/adaptmem)** (@nakata-app) — sits at the encoder layer, not the consumption layer; orthogonal lift across retrieval modes; clean independent reproduction of MemPalace's published 0.966 R@5 number via monkey-patch encoder swap on `longmemeval_bench.py`.
- **[GraphPalace](https://github.com/web3guru888/GraphPalace)** (@web3guru888) — graph-layer build. Forked at [jphein/GraphPalace](https://github.com/jphein/GraphPalace).
- **[mempalace-viz](https://github.com/JoeDoesJits/mempalace-viz)** (@JoeDoesJits) — visualization layer (wings, rooms, tunnels, drawer counts). Forked at [jphein/mempalace-viz](https://github.com/techempower-org/mempalace-viz).
- **[AutomataArena](https://github.com/astrutt/AutomataArena)** (@astrutt) — multi-agent orchestration substrate. Forked at [jphein/AutomataArena](https://github.com/jphein/AutomataArena).

The three-way (Familiar / CampaignGenerator / Kent) comparison plus the adaptmem orthogonality finding live at [`docs/research/three-mempalace-consumers.md`](research/three-mempalace-consumers.md) and [`docs/research/adaptmem-orthogonal-layers.md`](research/adaptmem-orthogonal-layers.md).

## Companion tools

These compose with MemPalace without replacing it.

- **[palace-daemon](https://github.com/rboarescu/palace-daemon)** (@rboarescu) — FastAPI gateway + MCP-over-HTTP proxy. Three asyncio semaphores (read / write / mine). Pins correctness floor at MemPalace ≥3.3.2. **This fork migrated to palace-daemon on 2026-04-24** ([`c09582c`](https://github.com/techempower-org/mempalace/commit/c09582c) wired MCP + hooks; [`0e97b19`](https://github.com/techempower-org/mempalace/commit/0e97b19) added daemon-strict mode). All reads and writes from the plugin flow through the daemon; auto-migrate-on-startup of the checkpoint split landed as palace-daemon [`034023c`](https://github.com/techempower-org/palace-daemon/commit/034023c). JP's deployment runs at [`techempower-org/palace-daemon`](https://github.com/techempower-org/palace-daemon).
- **[engram](https://github.com/NickCirv/engram)** (@NickCirv) — File-read interception for AI coding assistants. Uses MemPalace as one of six context providers via `mcp-mempalace mempalace-search`; caches with 1h TTL. Upstream [discussion #798](https://github.com/MemPalace/mempalace/discussions/798).
- **[engram](https://github.com/harreh3iesh/engram)** (@harreh3iesh — different project, same name) — Hooks + tools for AI memory, first-class MemPalace backend. **Stuck detector** (`PreToolUse` hook counts Grep/Glob calls and nudges the AI when spinning) is a pattern worth borrowing. Upstream [discussion #748](https://github.com/MemPalace/mempalace/discussions/748).
- **[cdd-mempalace](https://github.com/fuzzymoomoo/cdd-mempalace)** (@fuzzymoomoo) — Bridge library mapping Context-Driven Development methodology onto wings/halls/rooms. Multiple active upstream PRs.

## Evaluation frameworks

- **[multipass-structural-memory-eval](https://github.com/M0nkeyFl0wer/multipass-structural-memory-eval)** (@M0nkeyFl0wer) — Nine-category diagnostic framework. **"Category 9: The Handshake"** tests integration under production model usage, not just offline retrieval — the gap the four-layer model's invocation finding lives in. Forked at [jphein/multipass-structural-memory-eval](https://github.com/jphein/multipass-structural-memory-eval). The mempalace-daemon adapter at `sme/adapters/mempalace_daemon.py` talks HTTP/MCP only — no parallel `PersistentClient`, daemon-strict-compatible. The Cat 9 A/B harness used for the 2026-04-25 → 2026-04-26 measurements lives here.

## Adjacent / competing memory systems

See the [expanded comparison table](../README.md#why-this-fork-exists) in the README for the full landscape survey (updated 2026-05-24). Systems with specific fork interactions or benchmark findings worth noting:

- **[agentmemory](https://github.com/rohitg00/agentmemory)** (@rohitg00) — BM25 + vector hybrid. **95.2% R@5** on LongMemEval-S with same MiniLM embedding model. Filed methodology review in upstream [#747](https://github.com/MemPalace/mempalace/discussions/747). Now 9.4K stars with 53 MCP tools.
- **[engram-2](https://github.com/199-biotechnologies/engram-2)** — Rust CLI, deterministic, SQLite + FTS5 only. **0.990 R@5** vs MemPalace's 0.984 with no reranking. Their published README describes a ~17-point gap between engram-2's own LoCoMo score (74.5%) and SOTA (91.7%), attributed to the answerer model; an earlier reading of this as "engram-2 claims 17% E2E QA for MemPalace" was [not substantiated](../docs/research/2026-05-24-memory-system-benchmarks.md#the-engram-2-17-e2e-qa-for-mempalace-claim) in their materials. Memory-layer-budgeting (identity / critical / topic / deep tiers with token accounting) is worth studying.
- **[Tiro (project-tiro)](https://github.com/esagduyu/project-tiro)** (@esagduyu) — Same data-spine architecture (FastAPI + ChromaDB + SQLite + sentence-transformers + MCP) but *curated* input domain (web pages, email newsletters as clean markdown). Architectural twin to MemPalace's auto-mine-everything: same stack, different input shape. Forked at [jphein/project-tiro](https://github.com/jphein/project-tiro).
- **[claude-mem](https://github.com/thedotmack/claude-mem)** — 89K+ stars, largest community in the space. Explicitly non-verbatim (AI compression). Its dominance makes it the default comparison point even though its architecture takes the opposite approach.

## Adjacent inference paradigms

Different layer than memory, but relevant to consumption-layer research.

- **[RLM (Recursive Language Models)](https://github.com/alexzhang13/rlm)** (@alexzhang13, MIT OASYS) — LM offloads context as a REPL variable and recursively decomposes. Targets near-infinite context length. Forked at [jphein/rlm](https://github.com/jphein/rlm); integration example at [`examples/mempalace_demo.py`](https://github.com/jphein/rlm/blob/main/examples/mempalace_demo.py). The four-layer section's 46.67% / 78.33% finding came from running this fork's SME adapter against the same RLM substrate against Familiar's deterministic pipeline.
- **[ASI-Evolve](https://github.com/GAIR-NLP/ASI-Evolve)** (@GAIR-NLP) — Closed-loop autonomous research agent (Researcher / Engineer / Analyzer). Two parallel memory systems: **Cognition Store** (upfront domain knowledge) and **Experiment Database** (every trial). Validated on neural architecture design (+0.97 over DeltaNet — ~3× recent human gains). [arXiv 2603.29640](https://arxiv.org/abs/2603.29640). Forked at [jphein/ASI-Evolve](https://github.com/jphein/ASI-Evolve). The Cognition Store is exactly the role MemPalace would play.

## Active forks beyond ours

| Fork | Contributor work |
|---|---|
| [techempower-org/mempalace](https://github.com/techempower-org/mempalace) | this fork (transferred from `jphein/mempalace` in May 2026) |
| [kostadis/mempalace](https://github.com/kostadis/mempalace) | hierarchical AAAK pruning branch |
| [fuzzymoomoo/cdd-mempalace](https://github.com/fuzzymoomoo/cdd-mempalace) | 10 comment refs; CDD integration layer |
| [potterdigital/mempalace](https://github.com/potterdigital/mempalace) | author of upstream [#1081](https://github.com/MemPalace/mempalace/pull/1081) |
| [vnguyen-lexipol/mempalace](https://github.com/vnguyen-lexipol/mempalace) | author of upstream [#851](https://github.com/MemPalace/mempalace/pull/851) |
