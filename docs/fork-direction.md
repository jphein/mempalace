# Fork Direction — What We Actually Need

*2026-04-13. Prompted by [codingwithcody.com critique](https://codingwithcody.com/2026/04/13/mempalace-digital-castles-on-sand/) and competitive landscape review.*

## Why we're staying on MemPalace

We searched for verbatim-first alternatives. There aren't any that meet our requirements.

| System | Verbatim? | Local? | MCP? | Notes |
|--------|-----------|--------|------|-------|
| **MemPalace** | Yes | Yes | Yes | What we have. 134K drawers. |
| **Hindsight** | No — LLM extracts facts | Yes (Docker) | Yes | Transforms source text. Original is lost. |
| **Mem0/OpenMemory** | No — extracts "memories" | Partial | Yes | Cloud-first, local mode is second-class. |
| **Cognee** | No — builds knowledge graph | Yes | No | No MCP. |
| **Letta** | No — tiered summarization | Yes | No | No MCP. |
| **engram** | Structured, not verbatim | Yes | Yes | Go binary, SQLite+FTS5. Stores title/type/what/why fields, not raw text. |
| **OpenMemory (CaviraOSS)** | No — temporal graph | Yes | Yes | SQL-native, but transforms on write. |
| **TagMem** | Unknown | Unknown | Unknown | Referenced in critique article. Can't find on GitHub. Possibly vaporware. |

**Verbatim storage is MemPalace's actual differentiator** — not the palace hierarchy, not AAAK, not the benchmark numbers. Every alternative transforms content before storage. For recovering exact commands, error messages, code snippets, and what someone actually said, you need the original text.

## What the critique got right

The [codingwithcody.com article](https://codingwithcody.com/2026/04/13/mempalace-digital-castles-on-sand/) is a competitive piece promoting TagMem, but the technical criticism of hierarchy-as-architecture is valid. Our own commit history is evidence:

**Hierarchy causes bugs we keep fixing:**
- Wing assignment bugs (#659) — memories going to wrong wing because classification is forced
- Entity detector false positives — 73 stopwords added to suppress bad room names
- room=None crashes — miner breaks when it can't classify into the hierarchy
- palace_graph.py tunnels — entire module exists to compensate for hierarchy splitting related things

**Hierarchy doesn't drive our retrieval:**
- We search with `mempalace_search "query"` — that's vector search
- Wing filtering is optional and rarely used
- Silent hook saves use plain text, no AAAK, no room routing
- The palace structure is a browsing layer we almost never browse

**The article's core claim checks out:** The retrieval wins come from ChromaDB + verbatim storage, not from the palace abstraction.

## What the critique got wrong

- Scaling fixes (pagination, HNSW detection, graph caching) are normal engineering, not evidence of architectural failure
- The TagMem alternative wasn't findable — can't evaluate something that doesn't appear to exist publicly
- The benchmark critique, while fair, doesn't affect us — we don't use MemPalace because of benchmarks

## Lessons from the competitive landscape

Beyond the critique article, we surveyed 8+ memory systems and read the [OSS Insight "Agent Memory Race"](https://ossinsight.io/blog/agent-memory-race-2026) analysis. Key takeaways that should inform our fork:

### 1. Multi-label tagging over hierarchy

The critique's strongest technical point. Memory is associative — a conversation about debugging ChromaDB in the MCP server touches `chromadb, hnsw, debugging, sqlite, python, mcp-server, testing`. Hierarchy forces one parent. Tags capture all of them. Every modern system (Hindsight's entity tags, Mem0's memory tags, CaviraOSS's SQL metadata) uses multi-label. We should too.

### 2. Entity resolution matters

Hindsight auto-merges "Jim" and "my coworker Jim" into one entity. We have no entity resolution — the KG creates duplicate entities for spelling variants. The entity detector's 73 stopwords are a symptom of the same problem: forced classification without fuzzy matching.

### 3. Temporal awareness is the next frontier

Zep/Graphiti tracks how entities and relationships change over time. CaviraOSS scores by recency/frequency/importance. We have none of this — all 134K drawers are equally weighted forever. Decay and recency weighting (TODO #4) address this.

### 4. Contradiction detection needs explicit facts, not hierarchy

The critique correctly notes that putting memories in rooms doesn't make contradictions easier to resolve. Contradictions are a fact-level problem: "API endpoint is X" vs "API endpoint is Y". Our KG could handle this (same subject + predicate with different objects) but only if it's populated (TODO #6) and has temporal validity.

### 5. The "entry as primitive" model is right

Store once, attach metadata, query from any angle. This is what ChromaDB already does under the hood — each drawer is a document with metadata. The palace hierarchy is a layer of forced classification on top of what's already a flat document store. We don't need to rip it out, but we should stop treating it as the primary organizing principle.

### 6. FTS5 hybrid search is better than vector-only

engram (Go + SQLite FTS5) proves that full-text search catches what embeddings miss — exact error codes, config keys, CLI flags. Our #662 hybrid fallback via `$contains` was a step in this direction; **upstream v3.3.0 shipped proper Okapi-BM25 hybrid search** (60% vector + 40% keyword) that supersedes our approach entirely. #662 is now closeable.

### 6b. Context management is the layer above retrieval

[context-engine](https://github.com/Emmimal/context-engine) (Python, [forked](https://github.com/jphein/context-engine)) demonstrates the pipeline that sits *between* retrieval and the LLM: re-ranking, memory decay, extractive compression, and token budget enforcement. MemPalace answers "what's relevant?" — context-engine answers "what fits in the prompt, in what order, and at what compression level?"

Key ideas applicable to our fork:
- **Exponential memory decay** — older conversation turns naturally lose priority unless high-importance. Maps directly to our TODO #4 (decay/recency weighting).
- **Token budget slots** — system prompt → history → documents, each with hard caps. Could inform how MemPalace results are packed into the L1 layer.
- **Extractive compression** — query-aware sentence extraction from long documents. Currently MemPalace returns full drawer text; compression would let us return more drawers within the same token budget.

### 6c. Codebase-to-context flattening

[context-builder](https://github.com/igorls/context-builder) (Rust, [forked](https://github.com/jphein/context-builder)) is a CLI that flattens an entire codebase into a single LLM-friendly markdown file with smart file ordering, Tree-sitter AST extraction, and token budgeting. Relevant because:
- The `--signatures` mode (function/class signatures only, no bodies) could feed into MemPalace mining — indexing a project's API surface as searchable metadata.
- Useful for feeding codebases to models without file access (ChatGPT, raw API calls).
- The relevance-based file ordering (configs → source → tests) is a pattern we could apply to how MemPalace search results are ordered.

### 7. Single-binary simplicity is underrated

engram ships as one Go binary + one SQLite file. No Python venv, no ChromaDB server, no Docker. Our setup requires `venv/`, `pip install -e .`, ChromaDB with its Rust compactor, and careful version pinning. Not a reason to switch (we need verbatim + vector search), but worth noting that operational simplicity is a real competitive advantage.

### 8. Benchmark claims need separation

The critique fairly notes that MemPalace's best scores blend local baseline with cloud-assisted reranking. We should describe our fork's value as "134K drawers of verbatim local history with semantic search" — not cite upstream's LongMemEval numbers as if they represent our setup.

## Fork improvements: what matters

Ordered by impact. Informed by the critique, competitive research, and our own experience.

### P0: Multi-label tags (new)

Add a `tags` metadata field alongside existing wing/room/hall.

**What:** During mining, extract 3-8 keyword tags from content. Store as comma-separated metadata. Search can filter with `where_document` or metadata `$contains`.

**Why:** A conversation about fixing ChromaDB's HNSW index currently gets `wing: mempalace, room: mcp-server`. With tags it also gets `chromadb, hnsw, debugging, sqlite, python, testing`. Queryable from any angle without tunnels.

**How:** ChromaDB metadata supports arbitrary string fields. Add `tags` field in `miner.py` and `convo_miner.py`. Extract via simple TF-IDF or longest-non-stopword heuristic (we already have `_extract_keyword` in searcher.py). MCP search gets optional `tags` filter param.

**Effort:** 1-2 days. No architecture change. Purely additive.

**Upstream potential:** High — this is the kind of improvement the whole project benefits from.

### P1: Make classification best-effort (new)

**What:** Wing and room assignment should be optional metadata, not a required gate. If classification fails, store the drawer anyway with empty wing/room. Never crash on room=None.

**Why:** Half our fork bugs are from forced classification failing. The miner should never drop content because it can't figure out where to file it.

**How:** Default wing to source directory name (already mostly works). Default room to empty string. Remove hard failures in entity detector. Treat wing/room as best-effort metadata enrichment.

**Effort:** Half day. Mostly removing error paths.

### P2: Decay / recency weighting (existing TODO #4)

Search results should favor recent, frequently-accessed memories over ancient never-touched ones. Every competitor has this — we don't. See TODO-fork-improvements.md for full design.

### P3: Feedback loops (existing TODO #5)

Manual `mempalace_rate_memory(drawer_id, useful: bool)` tool. Lets the system learn which memories matter. Hindsight calls this "reflect" — synthesizing across memories to identify what's useful. Our version is simpler but the signal is the same. See TODO-fork-improvements.md for full design.

### P4: KG auto-population + entity resolution (existing TODO #6-7)

The knowledge graph is built but empty. Hooks should extract structured facts on every save. Entity resolution prevents "chromadb" and "ChromaDB" from becoming separate entities. Prerequisite for contradiction detection. See TODO-fork-improvements.md for full design.

### P5: Temporal fact validity (new)

**What:** KG triples get `valid_from` and `valid_to` timestamps. When a fact changes, the old triple gets `valid_to` set and a new triple is created.

**Why:** "The API endpoint is /v1/search" and "The API endpoint is /v2/search" are both true — at different times. Without temporal validity, the KG accumulates contradictions silently. Zep/Graphiti's temporal graph model is the reference here.

**How:** Add columns to KG SQLite schema. On `kg_add`, check for existing triples with same subject+predicate — if found, close the old one and open the new one. Surface via `mempalace_kg_timeline(entity)`.

**Effort:** 1 day. Depends on P4.

### Unsolved: Auto-surfacing context Claude doesn't know to ask for

**The problem:** Claude frequently makes wrong assumptions about how things work. The correct information exists in MemPalace, but Claude doesn't know it's wrong, so it never searches. JP ends up saying "query mempalace" manually — often enough to be a real friction point.

**Compounding factor: stale docs.** Knowledge is scattered across 7+ layers (global CLAUDE.md, project CLAUDE.md, auto-memory, docs/, superpowers specs, code comments, MemPalace). The auto-loaded layers (CLAUDE.md, auto-memory) go stale and actively mislead Claude. Ironically, MemPalace is the only layer that *can't* go stale — it stores verbatim with timestamps — but it's the only layer that's never auto-loaded.

**What doesn't work:**
- SessionStart hook that pre-loads context — just reimplements Claude's built-in auto-memory
- Auto-memory bridge that syncs from MemPalace — same problem, plus Auto Dream consolidation is coming upstream
- PreCompact hook that re-reads MemPalace — also reimplements auto-memory
- CLAUDE.md instructions to "always query mempalace" — Claude doesn't reliably follow these

**What might work:**
- **engram-style file read interception** ([NickCirv/engram](https://github.com/NickCirv/engram), discussed in [#798](https://github.com/MemPalace/mempalace/discussions/798)) — intercepts file reads, injects MemPalace decisions alongside code structure. Claude doesn't decide to search; the context is just there when it touches the code. Only covers code-level assumptions, not workflow/config.
- **Stale docs audit** — the highest-value immediate fix. Most wrong assumptions come from stale auto-loaded docs, not missing MemPalace context. Clean the layers Claude trusts most. Recurring maintenance, not one-time.
- **Reduce the number of knowledge layers** — fewer places for things to go stale. Consolidate where possible. Single source of truth per fact.

**What this means for the fork:** This is a consumption problem, not a storage problem. MemPalace's write path (hooks, mining) is solid. The read path (explicit MCP search) works when triggered. The gap is *automatic* surfacing of relevant context at the moment it's needed. No memory system has solved this well — it's the "unsolved problem" from the [OSS Insight analysis](https://ossinsight.io/blog/agent-memory-race-2026).

### P6: Stale docs strategy (not a MemPalace feature — a workflow problem)

The most common source of wrong assumptions isn't missing MemPalace context. It's stale docs that Claude auto-loads and trusts.

**The layers, ranked by damage when stale:**

| Layer | Auto-loaded? | Staleness risk | Damage |
|-------|-------------|----------------|--------|
| Global CLAUDE.md | Every session | High — large file, many facts | Highest — Claude follows it blindly |
| Project CLAUDE.md | Every session | High | High |
| Auto-memory (14 files) | Every session | Medium — manually written | High — treated as ground truth |
| Superpowers specs | When skill fires | Low — rarely change | Medium |
| Code comments | When file is read | Medium | Medium — localized to that file |
| docs/ folder | Only when explicitly read | High | Low — not auto-loaded |
| MemPalace | Never auto-loaded | None — verbatim + timestamped | None — can't mislead if not loaded |

**Specific known issues:**
- CLAUDE.md LAN services table uses IPs instead of domain names
- Version references (was 3.1.0, now 3.2.0 — already fixed this session)
- PR status tables go stale as PRs merge/close
- Auto-memory entries reference states that have changed

**What we need:**
1. **Audit pass** — review every auto-loaded layer for stale facts. Fix or remove.
2. **Freshness discipline** — date-stamp facts that can change. "As of 2026-04-13: 9 open PRs" tells future Claude to verify. Undated facts get treated as permanent truths.
3. **Reduce layers** — if a fact lives in CLAUDE.md AND auto-memory AND a docs/ file, pick one home and delete the others. Every duplicate is a future staleness bug.
4. **Recurring maintenance** — this isn't a one-time fix. Every session that changes project state can leave stale docs behind. The `/housekeep` skill should include a stale docs check.
5. **Staleness verifier** — a custom skill or slash command (see P7 below) that detects stale facts automatically.

**This should happen before any MemPalace fork work.** Cleaning stale docs will prevent more wrong assumptions than any amount of auto-querying MemPalace.

**Existing tools that help (already installed or available):**

| Tool | What it does | Limitation |
|------|-------------|------------|
| `/revise-claude-md` (claude-md-management plugin) | Captures session learnings into CLAUDE.md. Shows diffs, requires approval. | Adds but doesn't remove stale content. Accumulates over time. |
| `/claude-md-improver` (same plugin) | Audits CLAUDE.md quality against templates. | Checks structure, not factual freshness. |
| `project-doc-organizer` (agent type) | Audits project structure, generates/updates docs, archives stale files, commits. | Broad — not focused on the specific staleness problem. |

**External tools worth evaluating:**

| Tool | What it does | Relevance |
|------|-------------|-----------|
| [Swimm](https://swimm.io) | Code-coupled docs. Links documentation to specific code snippets, auto-flags when linked code changes but doc doesn't. CI integration catches drift on every PR. | Closest thing to a real stale-doc solution. Detects exactly the problem we have. Commercial product. |
| [claude-code-skills](https://github.com/levnikolaevich/claude-code-skills) | Plugin suite with `docs-auditor` that checks hierarchy, single source of truth, and freshness. Hash-verified editing prevents stale-context corruption. | Worth installing and testing. Open source. |
| [claude-organizer](https://github.com/ramakay/claude-organizer) | AI-powered file organization via Claude Code hooks. Sorts temp scripts from permanent docs, protects README/LICENSE/configs. | Lighter weight — file organization, not content freshness. |
| [CodeSpring](https://codespring.app) | Planning-to-code bridge — ingests GitHub repos into visual knowledge base with PRDs, Kanban tasks, auto-syncs on commit. Claude Code plugin (`/codespring`) via MCP skills. | Overlaps heavily with CLAUDE.md + task system we already have. The MCP skill is interesting for team projects. SaaS — project data hosted externally. |
| [Supadev](https://supadev.so) | Doc generator that outputs AI-optimized specs (requirements, architecture, implementation plans). Copy-paste into any AI tool. $12/mo. | Structured prompt templates — replicable with our brainstorming skill and CLAUDE.md. Low unique value for our workflow. |
| [context-engine](https://github.com/Emmimal/context-engine) | Pure-Python pipeline: retrieval → re-ranking → memory decay → compression → token budget → LLM. ([forked](https://github.com/jphein/context-engine)) | **High relevance.** The layer above MemPalace — decides what retrieved memories the LLM actually sees. Decay, compression, and budget enforcement map to TODOs #4-5. See §6b. |
| [context-builder](https://github.com/igorls/context-builder) | Rust CLI that flattens codebases into LLM-friendly markdown. Tree-sitter AST signatures, token budgeting, smart file ordering. ([forked](https://github.com/jphein/context-builder)) | Medium relevance. AST signature extraction could feed MemPalace mining. Useful for non-Claude-Code LLM workflows. See §6c. |

### P7: Staleness verifier (new — custom skill or slash command)

**The gap nobody has filled:** No tool scans all auto-loaded layers (CLAUDE.md, auto-memory, docs/), compares factual claims against current code/git state, and flags contradictions. Tools exist for generating docs and auditing structure, but not for detecting when existing docs have gone stale.

**What:** A `/verify-docs` skill or slash command that:
1. Reads CLAUDE.md (global + project) and auto-memory files
2. Extracts verifiable factual claims — version numbers, file paths, PR statuses, URLs, branch names, package versions, endpoint URLs
3. Checks each against current state:
   - Version numbers → compare with `version.py`, `pyproject.toml`, `package.json`
   - File paths → check if files still exist at those paths
   - PR statuses → query GitHub API for current open/merged/closed state
   - URLs → check if they resolve (or at least if the referenced service/path still exists)
   - Branch names → `git branch --list`
   - Package versions → check installed vs documented
4. Reports stale facts with location (file:line) and current vs documented value
5. Optionally auto-fixes simple cases (version numbers, PR statuses) with approval

**How:** Mostly regex extraction + shell verification. No LLM needed for the detection — pattern-match version strings, file paths, URLs, PR references. The verification is deterministic: does this file exist? Is this PR still open? Does this version match?

**Effort:** 1-2 days for a basic version. The extraction heuristics get better over time.

**Where it lives:** `~/.claude/commands/verify-docs.md` (global slash command, usable in all projects). Could also be a hook that runs at session start and returns a warning if stale facts are found.

**Integration with `/housekeep`:** The existing `/housekeep` skill should call this as a subtask. Currently housekeep cleans scratch files, worktrees, git branches — add stale docs to the sweep.

### Deprioritized

- **AAAK anything** — we use plain text, AAAK is upstream's concern
- **Hierarchy improvements** — tunnels, closets, new room types. The hierarchy isn't the value.
- **Benchmark work** — our value is "134K drawers of verbatim local history with fast search", not someone else's LongMemEval score
- **Full architecture rewrite** — not worth the migration cost. Improve incrementally.
- **FTS5 parallel index** — right idea (engram proves it), but adding SQLite FTS alongside ChromaDB is a significant infrastructure change. Revisit after tags and decay are proven.

## The honest assessment

MemPalace is a verbatim local memory archive with a vector search layer. The palace hierarchy on top is mostly decorative for our use case. That's fine — the underlying storage and search are genuinely useful and there's no better alternative for verbatim + local + MCP.

The right strategy is:
1. **Fix the stale docs problem first** — audit and clean every auto-loaded layer; build `/verify-docs` to catch staleness automatically
2. **Stop investing in hierarchy** — don't fix palace structure bugs, work around them
3. **Add tags** — get multi-label retrieval without removing the hierarchy
4. **Improve search quality** — decay, feedback, hybrid search (already done in #662)
5. **Add temporal awareness** — the KG should track when facts change, not just that they exist
6. **Keep the hooks pipeline** — the silent save + auto-mine architecture is the real value of our fork
7. **Evaluate engram integration** — NickCirv's approach of injecting MemPalace context on file reads is the most promising path to auto-surfacing context
8. **Wait and watch** — if a verbatim-first system with MCP and tagging appears, evaluate then
