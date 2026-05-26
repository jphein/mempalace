# OpenCode + MemPalace: gap analysis of the three TomLucidor issues

- **Date:** 2026-05-26
- **Tracker:** [techempower-org/mempalace#38](https://github.com/techempower-org/mempalace/issues/38)
- **Upstream discussion:** [MemPalace/mempalace#1277](https://github.com/MemPalace/mempalace/discussions/1277)
- **Status:** Planning. No code changes proposed in this note.

## TL;DR

The three issues surfaced in discussion #1277 — anomalyco/opencode [#8554](https://github.com/anomalyco/opencode/issues/8554), [#11829](https://github.com/anomalyco/opencode/issues/11829), and code-yeongyu/oh-my-openagent [#1397](https://github.com/code-yeongyu/oh-my-openagent/issues/1397) — are not requests for MemPalace integration. They are requests for **Recursive Language Models** (the first two) and **automated learning capture into AGENTS.md** (the third). MemPalace already covers OpenCode session ingestion via `OpenCodeSourceAdapter` (cherry-picked from upstream PR [#1484](https://github.com/MemPalace/mempalace/pull/1484), documented in [`docs/integrations/opencode.md`](../integrations/opencode.md)). The three issues are **orthogonal to that work**, not unmet requests against it.

This note maps each external issue to the MemPalace surface that does (or does not) address it, so we can reply to discussion #1277 with a precise answer instead of a generic "we're working on it."

## Existing OpenCode integration surface (what we already ship)

Three integration directions, all live on this fork's `main`:

| Direction | Component | Source |
|---|---|---|
| Pull (retrospective ingest) | `OpenCodeSourceAdapter` reads `~/.local/share/opencode/opencode.db` SQLite, yields exchange-pair drawers on the RFC 002 contract | [`mempalace/sources/opencode.py`](../../mempalace/sources/opencode.py), cherry-picked from upstream PR [#1484](https://github.com/MemPalace/mempalace/pull/1484) |
| Push (live capture) | `examples/opencode/live-capture/` plugin subscribes to `session.idle` and POSTs transcripts to palace-daemon `/silent-save` | [`docs/integrations/opencode.md`](../integrations/opencode.md) §4 |
| Read (agent recall) | MCP stdio bridge via daemon wrapper exposes 30 MemPalace tools (`mempalace_search`, `mempalace_kg_query`, …) inside OpenCode sessions | [`docs/integrations/opencode.md`](../integrations/opencode.md) §3 |

The verbatim mission (`CLAUDE.md` §"Verbatim always") is honored: drawers store the user + assistant exchange text as it appears in OpenCode's SQLite `part.data`, with declared transformations enumerated in [`mempalace/sources/transforms.py`](../../mempalace/sources/transforms.py) per RFC 002 §1.4.

## Mapping the three issues to MemPalace surface

### anomalyco/opencode#8554 — `rlm_repl` built-in tool

**What it asks for.** A new OpenCode built-in tool that executes LLM-generated JavaScript with `sub_llm()`, `context.store()`, `context.load()`, `context.chunk()` in scope. The pattern is symbolic recursion: the model writes a loop that fires N sub-LLM calls, instead of emitting N tool calls.

**Relationship to MemPalace.** Orthogonal. RLM is about how the agent *manages context* during a single session — pointer-based access to large external content, symbolic chunking, recursive decomposition. MemPalace is about how content *persists across sessions* — verbatim, post-session, searchable later.

**Composition.** An RLM-enabled OpenCode would be a strict improvement for MemPalace recall. `context.search()` and `context.load()` in the proposed RLM runtime could call `mempalace_search` via the existing MCP bridge to populate the external context store from prior sessions. The MemPalace MCP tools are already shaped for this — they return verbatim drawer content with similarity scores, exactly the pointer-shape RLM needs.

**No MemPalace work required.** The existing OpenCode adapter and MCP bridge continue to work whether or not RLM lands in OpenCode. If RLM lands, MemPalace becomes one of the first useful pointer sources for it.

### anomalyco/opencode#11829 — Context as External Environment

**What it asks for.** The same RLM paradigm framed at the OpenCode session layer: replace compaction with a context store that the model queries programmatically (`context.search`, `context.filter`, `sub_llm_batch`). Builds on the sliding-window proposal in [opencode#4659](https://github.com/anomalyco/opencode/issues/4659).

**Relationship to MemPalace.** Orthogonal in the same way as #8554. The "external environment" the issue describes is a *per-session* context store. MemPalace is a *cross-session* verbatim store. They live at different lifetimes and the proposals do not collide.

**Composition.** Phase 4 of the #11829 plan (symbolic compression) is precisely the place where MemPalace's existing semantic search would let an RLM-enabled OpenCode delegate "where do I find this concept across all prior sessions?" to MemPalace instead of re-indexing per-session. Concretely:

```
# RLM symbolic context query
relevant = context.search("auth middleware decisions")

# Same query, against MemPalace via MCP
hits = mempalace_search(query="auth middleware decisions", limit=10)
```

The two return the same kind of structure (ranked verbatim snippets); the difference is scope (this session vs. all sessions across all projects). An OpenCode that implements RLM gains MemPalace recall for free through the existing MCP surface.

**No MemPalace work required.** Same as #8554.

### code-yeongyu/oh-my-openagent#1397 — Automated Learning Capture System

**What it asks for.** A system inside oh-my-openagent (omoa) that:

1. Detects "learnings" during a session via PostToolUse hook heuristics (error-recovery patterns, NEVER/ALWAYS keywords, procedure shapes).
2. Validates each candidate against prompt-injection and sensitive-data filters.
3. Writes structured entries to `.sisyphus/learnings/{session}.jsonl`.
4. Surfaces `/summarize-learnings` which opens a **PR against AGENTS.md / CLAUDE.md** with the extracted lessons.

**Relationship to MemPalace.** This is **not** the same problem MemPalace solves, but it overlaps in two ways worth being precise about:

| Dimension | omoa #1397 Learning Capture | MemPalace |
|---|---|---|
| Source content | Curated, classified, summarized learning entries | Verbatim exchanges, byte-preserving |
| Output target | `AGENTS.md` / `CLAUDE.md` files via PR review | Postgres drawers, queried via MCP / CLI |
| Persistence shape | Structured `LearningEntry` JSONL → reviewed markdown | Free-form drawer content + flat metadata |
| Lifecycle | Detect → validate → human PR review → merge | Auto-ingest → searchable forever |
| Mission | Maintain a living rule-set the agent reads at session start | Maintain a complete record of what was actually said |

**They are complementary, not substitutable.** A omoa user could enable both: capture learnings into AGENTS.md (omoa #1397's job), and archive the verbatim sessions that produced those learnings into MemPalace (this fork's `OpenCodeSourceAdapter` already does this — see below).

The piece of omoa #1397 that genuinely overlaps with us is the `maxo99` comment requesting **batch back-mining of historical sessions**. omoa sessions live in OpenCode's SQLite (omoa is a plugin layer on OpenCode — confirmed by `bunx oh-my-opencode doctor` and the `.opencode/opencode.json` plugin-array config). That means the existing `OpenCodeSourceAdapter` **already covers omoa session ingestion** today, without any new code. See the companion note [`2026-05-26-mempalace-source-omoa.md`](2026-05-26-mempalace-source-omoa.md) for the omoa-specific delta surface (`.omo/tasks/*.json`, project-tree `AGENTS.md` files, `.claude/skills/`).

**MemPalace work required.** Only the omoa-side adapter described in the companion note. Nothing for opencode-proper beyond what already ships.

## Open questions

These are deliberate non-decisions in this planning round. They get answered when (and if) implementation work is scheduled.

1. **Should we reply on discussion #1277?** Per [`feedback_upstream_comment_timing.md`](https://github.com/jphein/) (private memory): defer upstream-coordination comments until implementation is also complete. The opencode side has working code; the omoa side has a one-pager only. Recommend: wait until the omoa adapter has a draft PR before replying on #1277, so the comment lands with shippable code on both axes.
2. **Should we coordinate with the RLM PRs directly?** The two opencode RLM issues have working forks at [BowTiedSwan/opencode](https://github.com/BowTiedSwan/opencode) and [fgroo/rlm-opencode](https://github.com/fgroo/rlm-opencode). If RLM lands in OpenCode mainline, an RLM-aware demo in `examples/opencode/` that fetches MemPalace context as the external store would be a strong showcase. Not in scope for this planning round.
3. **Should `OpenCodeSourceAdapter` learn an `omo` mode?** Probably not. The session content is identical between bare OpenCode and omoa-on-OpenCode; only the surrounding project-tree artifacts differ. Splitting into a second adapter (as in the companion note) keeps the SQLite adapter focused and lets the project-tree artifacts go through a dedicated mining surface.

## Effort estimate

| Item | Effort | Notes |
|---|---|---|
| Reply on discussion #1277 with the mapping above | S (~30 min) | Wait for omoa draft per open question #1 |
| Cross-link this gap analysis from `docs/integrations/opencode.md` | XS (~10 min) | Optional |
| Watch RLM PRs and prepare a follow-up showcase | M (~1d) when RLM lands | Speculative; deferred |

## References

- This fork's existing OpenCode integration: [`docs/integrations/opencode.md`](../integrations/opencode.md)
- The adapter: [`mempalace/sources/opencode.py`](../../mempalace/sources/opencode.py)
- Upstream RFC 002: [`docs/rfcs/002-source-adapter-plugin-spec.md`](../rfcs/002-source-adapter-plugin-spec.md)
- Upstream PR cherry-picked into this fork: [MemPalace/mempalace#1484](https://github.com/MemPalace/mempalace/pull/1484)
- omoa session storage is OpenCode SQLite: [oh-my-openagent README](https://github.com/code-yeongyu/oh-my-openagent) (plugin layer over OpenCode)
- RLM source paper: [arXiv:2512.24601](https://arxiv.org/abs/2512.24601)
