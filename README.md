<div align="center">

<img src="assets/mempalace_logo.png" alt="MemPalace" width="280">

# MemPalace (jphein fork)

**JP's production fork of [milla-jovovich/mempalace](https://github.com/milla-jovovich/mempalace)**

[![version-shield]][release-link]
[![python-shield]][python-link]
[![license-shield]][license-link]

</div>

---

## What This Is

A local AI memory system that stores every conversation, decision, and debugging session verbatim in ChromaDB. Semantic search finds anything instantly. No cloud, no API keys, no summarization — raw storage with structure.

This fork runs in production on my workstation with 134K+ drawers across 60+ rooms. Everything below is battle-tested.

**[Fork direction doc](docs/fork-direction.md)** — competitive analysis, what we learned, and what we're building next.

## What's Different From Upstream

22 fork changes submitted as [upstream PRs](#upstream-prs) — 5 merged, 9 open, rest closed. The highlights:

### Reliability
- **Stale HNSW detection** — MCP server detects external writes via mtime, auto-reconnects. Manual `mempalace_reconnect` tool for cache invalidation.
- **BLOB seq_id auto-repair** — Fixes the chromadb 0.6.x to 1.5.x migration crash (`Rust type u64 is not compatible with SQL type BLOB`) automatically before every client init.
- **Epsilon mtime comparison** — `abs() < 0.01` instead of `==` for float mtime dedup, preventing unnecessary re-mining.

### Performance
- **Batch ChromaDB writes** — One upsert per file instead of per chunk. Concurrent mining with ThreadPoolExecutor.
- **Graph cache** — `build_graph()` cached module-level with 60s TTL, invalidated on writes.
- **L1 importance pre-filter** — Tries `importance >= 3` first, falls back to full scan only if < 15 results.

### Features
- **Hybrid search fallback** — Keyword text-match via `where_document.$contains` when vector results are poor. Auto-extracts most distinctive token.
- **Tool output mining** — `normalize.py` captures tool_use/tool_result blocks from Claude Code JSONL with per-tool formatting (Bash head+tail, Read/Edit/Write path-only, Grep/Glob capped).
- **Hook system** — Stop hook saves directly via Python API with single-line `systemMessage` notification. Auto-mines JSONL transcripts. PreCompact hook for emergency save before context compression.
- **Diary wing routing** — `diary_write`/`diary_read` accept optional `wing` param. Stop hook derives project wing from transcript path.
- **New MCP tools** — `get_drawer`, `list_drawers`, `update_drawer`, `hook_settings`, `memories_filed_away`, `reconnect`. Palace export to markdown/JSON. *(Merged upstream via [#667](https://github.com/milla-jovovich/mempalace/pull/667))*
- **Entity detector** — 73 technical STOPWORDS added (Handler, Node, Service, etc.) to reduce false positives.

### Infrastructure
- **chromadb >= 1.5.4** — Upgraded from 0.6.x pin with auto-migration support.
- **CLI** — `mempalace --version`, repair nuke-rebuild, purge command.
- **Search** — `max_distance` parameter (cosine distance threshold), default 1.5 in MCP.

## Setup

```bash
git clone https://github.com/jphein/mempalace.git
cd mempalace
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

mempalace init ~/Projects --yes
mempalace mine ~/Projects/myproject
mempalace status
```

### MCP Server (Claude Code / Cursor / Gemini)

```bash
claude plugin marketplace add milla-jovovich/mempalace
claude plugin install --scope user mempalace
```

Restart Claude Code, then type `/skills` to verify "mempalace" appears.

### With Claude, ChatGPT, Cursor, Gemini (MCP-compatible tools)

```bash
# Connect MemPalace once
claude mcp add mempalace -- python -m mempalace.mcp_server
```

Now your AI has 22+ tools available through MCP. Ask it anything:

> *"What did we decide about auth last month?"*

Claude calls `mempalace_search` automatically, gets verbatim results, and answers you. You never type `mempalace search` again. The AI handles it.

MemPalace also works natively with **Gemini CLI** (which handles the server and save hooks automatically) — see the [Gemini CLI Integration Guide](examples/gemini_cli_setup.md).

### With local models (Llama, Mistral, or any offline LLM)

Local models generally don't speak MCP yet. Two approaches:

**1. Wake-up command** — load your world into the model's context:

```bash
mempalace wake-up > context.txt
# Paste context.txt into your local model's system prompt
```

This gives your local model ~170 tokens of critical facts (in AAAK if you prefer) before you ask a single question.

**2. CLI search** — query on demand, feed results into your prompt:

```bash
mempalace search "auth decisions" > results.txt
# Include results.txt in your prompt
```

Or use the Python API:

```python
from mempalace.searcher import search_memories
results = search_memories("auth decisions", palace_path="~/.mempalace/palace")
# Inject into your local model's context
```

Either way — your entire memory stack runs offline. ChromaDB on your machine, Llama on your machine, AAAK for compression, zero cloud calls.

---

## The Problem

Decisions happen in conversations now. Not in docs. Not in Jira. In conversations with Claude, ChatGPT, Copilot. The reasoning, the tradeoffs, the "we tried X and it failed because Y" — all trapped in chat windows that evaporate when the session ends.

**Six months of daily AI use = 19.5 million tokens.** That's every decision, every debugging session, every architecture debate. Gone.

| Approach | Tokens loaded | Annual cost |
|----------|--------------|-------------|
| Paste everything | 19.5M — doesn't fit any context window | Impossible |
| LLM summaries | ~650K | ~$507/yr |
| **MemPalace wake-up** | **~170 tokens** | **~$0.70/yr** |
| **MemPalace + 5 searches** | **~13,500 tokens** | **~$10/yr** |

MemPalace loads 170 tokens of critical facts on wake-up — your team, your projects, your preferences. Then searches only when needed. $10/year to remember everything vs $507/year for summaries that lose context.

---

## How It Works

### The Palace

The layout is fairly simple, though it took a long time to get there.

It starts with a **wing**. Every project, person, or topic you're filing gets its own wing in the palace.

Each wing has **rooms** connected to it, where information is divided into subjects that relate to that wing — so every room is a different element of what your project contains. Project ideas could be one room, employees could be another, financial statements another. There can be an endless number of rooms that split the wing into sections. The MemPalace install detects these for you automatically, and of course you can personalize it any way you feel is right.

Every room has a **closet** connected to it, and here's where things get interesting. We've developed an AI language called **AAAK**. Don't ask — it's a whole story of its own. Your agent learns the AAAK shorthand every time it wakes up. Because AAAK is essentially English, but a very truncated version, your agent understands how to use it in seconds. It comes as part of the install, built into the MemPalace code. In our next update, we'll add AAAK directly to the closets, which will be a real game changer — the amount of info in the closets will be much bigger, but it will take up far less space and far less reading time for your agent.

Inside those closets are **drawers**, and those drawers are where your original files live. In this first version, we haven't used AAAK as a closet tool, but even so, the summaries have shown **96.6% recall** in all the benchmarks we've done across multiple benchmarking platforms. Once the closets use AAAK, searches will be even faster while keeping every word exact. But even now, the closet approach has been a huge boon to how much info is stored in a small space — it's used to easily point your AI agent to the drawer where your original file lives. You never lose anything, and all this happens in seconds.

There are also **halls**, which connect rooms within a wing, and **tunnels**, which connect rooms from different wings to one another. So finding things becomes truly effortless — we've given the AI a clean and organized way to know where to start searching, without having to look through every keyword in huge folders.

You say what you're looking for and boom, it already knows which wing to go to. Just *that* in itself would have made a big difference. But this is beautiful, elegant, organic, and most importantly, efficient.

```
  +------------------------------------------------------------+
  ¦  WING: Person                                              ¦
  ¦                                                            ¦
  ¦    +----------+            +----------+                    ¦
  ¦    ¦  Room A  ¦  --hall--  ¦  Room B  ¦                    ¦
  ¦    +----------+            +----------+                    ¦
  ¦         ¦                                                  ¦
  ¦         v                                                  ¦
  ¦    +----------+      +----------+                          ¦
  ¦    ¦  Closet  ¦ ---> ¦  Drawer  ¦                          ¦
  ¦    +----------+      +----------+                          ¦
  +---------+--------------------------------------------------+
            ¦
          tunnel
            ¦
  +---------+--------------------------------------------------+
  ¦  WING: Project                                             ¦
  ¦         ¦                                                  ¦
  ¦    +----------+            +----------+                    ¦
  ¦    ¦  Room A  ¦  --hall--  ¦  Room C  ¦                    ¦
  ¦    +----------+            +----------+                    ¦
  ¦         ¦                                                  ¦
  ¦         v                                                  ¦
  ¦    +----------+      +----------+                          ¦
  ¦    ¦  Closet  ¦ ---> ¦  Drawer  ¦                          ¦
  ¦    +----------+      +----------+                          ¦
  +------------------------------------------------------------+
```

**Wings** — a person or project. As many as you need.
**Rooms** — specific topics within a wing. Auth, billing, deploy — endless rooms.
**Halls** — connections between related rooms *within* the same wing. If Room A (auth) and Room B (security) are related, a hall links them.
**Tunnels** — connections *between* wings. When Person A and a Project both have a room about "auth," a tunnel cross-references them automatically.
**Closets** — summaries that point to the original content. (In v3.0.0 these are plain-text summaries; AAAK-encoded closets are coming in a future update — see [Task #30](https://github.com/milla-jovovich/mempalace/issues/30).)
**Drawers** — the original verbatim files. The exact words, never summarized.

**Halls** are memory types — the same in every wing, acting as corridors:
- `hall_facts` — decisions made, choices locked in
- `hall_events` — sessions, milestones, debugging
- `hall_discoveries` — breakthroughs, new insights
- `hall_preferences` — habits, likes, opinions
- `hall_advice` — recommendations and solutions

**Rooms** are named ideas — `auth-migration`, `graphql-switch`, `ci-pipeline`. When the same room appears in different wings, it creates a **tunnel** — connecting the same topic across domains:

```
wing_kai       / hall_events / auth-migration  → "Kai debugged the OAuth token refresh"
wing_driftwood / hall_facts  / auth-migration  → "team decided to migrate auth to Clerk"
wing_priya     / hall_advice / auth-migration  → "Priya approved Clerk over Auth0"
```

Same room. Three wings. The tunnel connects them.

### Honest Take on Structure

The palace hierarchy (wings, rooms, halls, tunnels) provides navigable organization. However, in our 134K-drawer production deployment, **the retrieval wins come from ChromaDB's vector search + verbatim storage, not from the hierarchy itself.** Wing/room filtering is metadata narrowing on top of vector search — useful when you want it, but the same results are achievable with multi-label tags.

Our fork's [direction doc](docs/fork-direction.md) has the full analysis. The short version: verbatim storage is the real differentiator. Every competing system (Hindsight, Mem0, Cognee, Letta, engram) transforms content before storage. MemPalace keeps the original text.

### The Memory Stack

| Layer | What | Size | When |
|-------|------|------|------|
| **L0** | Identity — who is this AI? | ~50 tokens | Always loaded |
| **L1** | Critical facts — team, projects, preferences | ~120 tokens (AAAK) | Always loaded |
| **L2** | Room recall — recent sessions, current project | On demand | When topic comes up |
| **L3** | Deep search — semantic query across all closets | On demand | When explicitly asked |

Your AI wakes up with L0 + L1 (~170 tokens) and knows your world. Searches only fire when needed.

### AAAK Dialect (experimental)

AAAK is a lossy abbreviation system — entity codes, structural markers, and sentence truncation — designed to pack repeated entities and relationships into fewer tokens at scale. It is **readable by any LLM that reads text** (Claude, GPT, Gemini, Llama, Mistral) without a decoder, so a local model can use it without any cloud dependency.

**Honest status (April 2026):**

- **AAAK is lossy, not lossless.** It uses regex-based abbreviation, not reversible compression.
- **It does not save tokens at small scales.** Short text already tokenizes efficiently. AAAK overhead (codes, separators) costs more than it saves on a few sentences.
- **It can save tokens at scale** — in scenarios with many repeated entities (a team mentioned hundreds of times, the same project across thousands of sessions), the entity codes amortize.
- **AAAK currently regresses LongMemEval** vs raw verbatim retrieval (84.2% R@5 vs 96.6%). The 96.6% headline number is from **raw mode**, not AAAK mode.
- **The MemPalace storage default is raw verbatim text in ChromaDB** — that's where the benchmark wins come from. AAAK is a separate compression layer for context loading, not the storage format.

We're iterating on the dialect spec, adding a real tokenizer for stats, and exploring better break points for when to use it. Track progress in [Issue #43](https://github.com/milla-jovovich/mempalace/issues/43) and [#27](https://github.com/milla-jovovich/mempalace/issues/27).

### Contradiction Detection (experimental, not yet wired into KG)

A separate utility (`fact_checker.py`) can check assertions against entity facts. It's not currently called automatically by the knowledge graph operations — this is being fixed (track in [Issue #27](https://github.com/milla-jovovich/mempalace/issues/27)). When enabled it catches things like:

```
Input:  "Soren finished the auth migration"
Output: 🔴 AUTH-MIGRATION: attribution conflict — Maya was assigned, not Soren

Input:  "Kai has been here 2 years"
Output: 🟡 KAI: wrong_tenure — records show 3 years (started 2023-04)

Input:  "The sprint ends Friday"
Output: 🟡 SPRINT: stale_date — current sprint ends Thursday (updated 2 days ago)
```

Facts checked against the knowledge graph. Ages, dates, and tenures calculated dynamically — not hardcoded.

---

## Real-World Examples

### Solo developer across multiple projects

```bash
# Mine each project's conversations
mempalace mine ~/chats/orion/  --mode convos --wing orion
mempalace mine ~/chats/nova/   --mode convos --wing nova
mempalace mine ~/chats/helios/ --mode convos --wing helios

# Six months later: "why did I use Postgres here?"
mempalace search "database decision" --wing orion
# → "Chose Postgres over SQLite because Orion needs concurrent writes
#    and the dataset will exceed 10GB. Decided 2025-11-03."

# Cross-project search
mempalace search "rate limiting approach"
# → finds your approach in Orion AND Nova, shows the differences
```

### Team lead managing a product

```bash
# Mine Slack exports and AI conversations
mempalace mine ~/exports/slack/ --mode convos --wing driftwood
mempalace mine ~/.claude/projects/ --mode convos

# "What did Soren work on last sprint?"
mempalace search "Soren sprint" --wing driftwood
# → 14 closets: OAuth refactor, dark mode, component library migration

# "Who decided to use Clerk?"
mempalace search "Clerk decision" --wing driftwood
# → "Kai recommended Clerk over Auth0 — pricing + developer experience.
#    Team agreed 2026-01-15. Maya handling the migration."
```

### Before mining: split mega-files

Some transcript exports concatenate multiple sessions into one huge file:

```bash
mempalace split ~/chats/                      # split into per-session files
mempalace split ~/chats/ --dry-run            # preview first
mempalace split ~/chats/ --min-sessions 3     # only split files with 3+ sessions
```

---

## Knowledge Graph

Temporal entity-relationship triples — like Zep's Graphiti, but SQLite instead of Neo4j. Local and free.

```python
from mempalace.knowledge_graph import KnowledgeGraph

kg = KnowledgeGraph()
kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
kg.add_triple("Maya", "assigned_to", "auth-migration", valid_from="2026-01-15")
kg.add_triple("Maya", "completed", "auth-migration", valid_from="2026-02-01")

# What's Kai working on?
kg.query_entity("Kai")
# → [Kai → works_on → Orion (current), Kai → recommended → Clerk (2026-01)]

# What was true in January?
kg.query_entity("Maya", as_of="2026-01-20")
# → [Maya → assigned_to → auth-migration (active)]

# Timeline
kg.timeline("Orion")
# → chronological story of the project
```

Facts have validity windows. When something stops being true, invalidate it:

```python
kg.invalidate("Kai", "works_on", "Orion", ended="2026-03-01")
```

Now queries for Kai's current work won't return Orion. Historical queries still will.

| Feature | MemPalace | Zep (Graphiti) |
|---------|-----------|----------------|
| Storage | SQLite (local) | Neo4j (cloud) |
| Cost | Free | $25/mo+ |
| Temporal validity | Yes | Yes |
| Self-hosted | Always | Enterprise only |
| Privacy | Everything local | SOC 2, HIPAA |

---

## Specialist Agents

Create agents that focus on specific areas. Each agent gets its own wing and diary in the palace — not in your CLAUDE.md. Add 50 agents, your config stays the same size.

```
~/.mempalace/agents/
  ├── reviewer.json       # code quality, patterns, bugs
  ├── architect.json      # design decisions, tradeoffs
  └── ops.json            # deploys, incidents, infra
```

Your CLAUDE.md just needs one line:

```
You have MemPalace agents. Run mempalace_list_agents to see them.
```

The AI discovers its agents from the palace at runtime. Each agent:

- **Has a focus** — what it pays attention to
- **Keeps a diary** — written in AAAK, persists across sessions
- **Builds expertise** — reads its own history to stay sharp in its domain

```
# Agent writes to its diary after a code review
mempalace_diary_write("reviewer",
    "PR#42|auth.bypass.found|missing.middleware.check|pattern:3rd.time.this.quarter|★★★★")

# Agent reads back its history
mempalace_diary_read("reviewer", last_n=10)
# → last 10 findings, compressed in AAAK
```

Each agent is a specialist lens on your data. The reviewer remembers every bug pattern it's seen. The architect remembers every design decision. The ops agent remembers every incident. They don't share a scratchpad — they each maintain their own memory.

Letta charges $20–200/mo for agent-managed memory. MemPalace does it with a wing.

---

## MCP Server

```bash
# Via plugin (recommended)
claude plugin marketplace add milla-jovovich/mempalace
claude plugin install --scope user mempalace

# Or manual MCP
claude mcp add mempalace -- python -m mempalace.mcp_server
```

24 MCP tools available: search, diary, drawers, knowledge graph, palace graph traversal, taxonomy, export, hook settings, reconnect.

### Hooks

The plugin includes auto-save hooks with two save modes (`hook_silent_save` in config):

- **Silent mode** (default): Direct Python API save — plain text diary entry + transcript mining. Deterministic, no AI involved. Shows `"✦ N memories woven into the palace"` as a terminal notification.
- **Block mode** (legacy): Asks the AI to call MemPalace MCP tools. Non-deterministic.

| Hook | When It Fires | What Happens |
|------|--------------|-------------|
| **Stop hook** | Every 15 messages | Diary entry with theme extraction + transcript auto-mine |
| **PreCompact hook** | Before context compression | Emergency save of everything before context is lost |

Set `MEMPAL_DIR` to auto-mine a directory on each save trigger. Set `MEMPAL_PYTHON` to specify the Python interpreter (auto-detects repo venv if not set).

## Development

```bash
source venv/bin/activate
python -m pytest tests/ -x -q           # 740 tests expected
mempalace status                         # palace health
ruff check . && ruff format --check .    # lint + format
```

## Upstream PRs

All fork changes submitted as separate focused PRs to [milla-jovovich/mempalace](https://github.com/milla-jovovich/mempalace), targeting `develop`:

| PR | Type | Description |
|----|------|-------------|
| [#626](https://github.com/milla-jovovich/mempalace/pull/626) | fix | ~~Standalone bug fixes~~ — closed, split into #681-684 |
| [#629](https://github.com/milla-jovovich/mempalace/pull/629) | perf | Batch writes, concurrent mining |
| [#632](https://github.com/milla-jovovich/mempalace/pull/632) | feat | Repair, purge, --version |
| [#633](https://github.com/milla-jovovich/mempalace/pull/633) | feat | ~~Hook capture~~ — closed, resubmitted as #673 |
| [#635](https://github.com/milla-jovovich/mempalace/pull/635) | feat | New MCP tools, export — **merged** via [#667](https://github.com/milla-jovovich/mempalace/pull/667) |
| [#659](https://github.com/milla-jovovich/mempalace/pull/659) | fix | Diary wing parameter |
| [#660](https://github.com/milla-jovovich/mempalace/pull/660) | perf | L1 importance pre-filter |
| [#661](https://github.com/milla-jovovich/mempalace/pull/661) | perf | Graph cache with write-invalidation |
| [#662](https://github.com/milla-jovovich/mempalace/pull/662) | feat | Hybrid search fallback |
| [#663](https://github.com/milla-jovovich/mempalace/pull/663) | fix | ~~Stale HNSW mtime detection~~ — closed, upstream wrote [#757](https://github.com/milla-jovovich/mempalace/pull/757) |
| [#664](https://github.com/milla-jovovich/mempalace/pull/664) | fix | BLOB seq_id migration repair — **merged** |
| [#673](https://github.com/milla-jovovich/mempalace/pull/673) | feat | Deterministic hook saves — zero data loss via Python API (1 approval) |
| [#681](https://github.com/milla-jovovich/mempalace/pull/681) | fix | Unicode checkmark → ASCII (#535) |
| [#682](https://github.com/milla-jovovich/mempalace/pull/682) | fix | --yes flag for init (#534) — **merged** |
| [#683](https://github.com/milla-jovovich/mempalace/pull/683) | fix | Unicode sanitize_name (#637) — **merged** |
| [#684](https://github.com/milla-jovovich/mempalace/pull/684) | fix | VAR_KEYWORD kwargs check (#572) — **merged** |
| [#738](https://github.com/milla-jovovich/mempalace/pull/738) | docs | Update MCP tools reference for #667 additions |

## License

MIT — see [LICENSE](LICENSE).

<!-- Link Definitions -->
[version-shield]: https://img.shields.io/badge/version-3.2.0-4dc9f6?style=flat-square&labelColor=0a0e14
[release-link]: https://github.com/milla-jovovich/mempalace/releases
[python-shield]: https://img.shields.io/badge/python-3.9+-7dd8f8?style=flat-square&labelColor=0a0e14&logo=python&logoColor=7dd8f8
[python-link]: https://www.python.org/
[license-shield]: https://img.shields.io/badge/license-MIT-b0e8ff?style=flat-square&labelColor=0a0e14
[license-link]: https://github.com/jphein/mempalace/blob/main/LICENSE
