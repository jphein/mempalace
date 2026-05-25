# Auto-Query Integration Spec

- **Status:** Draft
- **Tracking issue:** [#123](https://github.com/techempower-org/mempalace/issues/123)
- **Related:**
  - [`docs/research/three-patterns-for-agent-memory.md`](../research/three-patterns-for-agent-memory.md) — Layer 3 (surfacing) is the dominant bottleneck; invocation, not retrieval, is what fails
  - [`docs/integrations/opencode.md`](../integrations/opencode.md) — existing harness wiring (read via MCP, push via live-capture, pull via adapter)
  - [`docs/rfcs/002-source-adapter-plugin-spec.md`](../rfcs/002-source-adapter-plugin-spec.md) — read-side adapter contract (the source side of what we surface)
  - SME categories 1-9 in [techempower-org/multipass-structural-memory-eval](https://github.com/techempower-org/multipass-structural-memory-eval) — Cat 9a is the unfilled measurement slot this spec unblocks
- **Spec version:** `0.1`

## Summary

A specification for a context classifier that auto-invokes MemPalace queries during a Claude Code or OpenCode session, so the harness reaches the palace on the turns that need it without the user typing `mempalace_search ...`. Defines the signals that trigger an auto-query, the mapping from intent to MCP tool, how results are injected into the assistant's working context, the performance budget, and how the integration is measured against SME Cat 9a.

The spec is deliberately scoped to a *thin classifier with conservative defaults*: it never fires more than once per turn, it always falls back to "do nothing" on uncertainty, and every decision is logged for tuning. The architectural bet is that a 90%-recall classifier is more useful in production than a 99%-recall one that interrupts the conversation.

## Motivation

Two data points frame the problem:

1. **SME's first live readings on `jp-realm-v0.1`** (30 questions): `rlm + Qwen 2.5 7B` (2/30 tool calls) and `rlm + Llama 3.3 70B` (8/30 tool calls) both score 46.67% recall — identical despite a 4× difference in invocation rate. The deterministic pipeline (`familiar` v0.3.9) on the same corpus scores 78.33%. The LLM-orchestrated runs ceiling at *willingness to call the tool*, not at retrieval quality.
2. **OpenCode + Claude Code current state**: the MCP server exposes 30 tools (`mempalace_search`, `mempalace_kg_query`, `mempalace_diary_read`, `mempalace_list_wings`, `mempalace_get_taxonomy`, `mempalace_traverse`, `mempalace_status`, `mempalace_kg_timeline`, etc.). Per `docs/integrations/opencode.md` §"A note on automatic context injection", today's recall path is *agents explicitly invoking the MCP tools*. There is no per-turn classifier deciding when memory would help; the agent decides, often does not, and the user has to prompt "check mempalace for X" to make it happen.

This is the SME Cat 9a gap (invocation rate by a live model) on the same fork that owns the corpus. We have the substrate (300K+ verbatim drawers in pgvector behind palace-daemon), we have the MCP tool surface, and we have the empirical evidence that pushing retrieval quality further has diminishing returns past good invocation. Building the auto-query layer is the prerequisite for measuring Cat 9a and the highest-leverage place to spend an engineering week.

## Non-goals

- **Replacing explicit invocation.** Users who type `mempalace_search ...` still get exactly that call. Auto-query is additive — a parallel cheap channel — not a gate.
- **Mandatory always-on retrieval.** §"Practical caveats" in the three-patterns research note flags forced-invocation as a separate ablation worth running, but it is not what this spec proposes. The classifier may decide a turn does not need memory.
- **Cross-session memory consolidation.** Auto Dream (Claude Code `/dream`, Managed Agents Dreams API) handles consolidation/derivative-summary content. MemPalace stays verbatim. See `memory/project_auto_dream.md` — the Dreams API design ratifies this axis.
- **Modifying drawer contents.** Auto-query is read-only. Writes continue to go through the existing live-capture / stop-hook / adapter paths.
- **A new MCP tool.** We compose existing tools (`mempalace_search`, `mempalace_kg_query`, `mempalace_diary_read`, `mempalace_get_taxonomy`, `mempalace_list_wings`, `mempalace_traverse`). The classifier lives in the harness shim, not in the MCP server.

## Goals

1. **Auto-query fires on entity mentions** with known palace wings (people, projects, services already represented in the corpus).
2. **Auto-query fires on temporal references** that imply prior session history ("last time we…", "when did we…", "the bug we hit yesterday").
3. **Auto-query fires on task resumption** — a new session in a known project directory should warm the assistant's context with what was happening last time.
4. **All auto-queries stay under 500ms end-to-end** (classifier + MCP roundtrip + injection), matching the existing hook budget.
5. **SME Cat 9a is measurable** against the integration — the harness must emit a structured invocation log so an evaluator can compute invocation-rate per question category.
6. **No false-positive queries** on turns that do not need memory — measured as the rate at which auto-injected context goes unused (no token from the injection appears in the assistant's reply, and no user follow-up references it).
7. **Conservative by default.** The classifier ships disabled until a per-harness allowlist signs off, has a `dry-run` mode that logs decisions without firing queries, and exposes a single config flag (`auto_query.enabled`) to kill it.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Harness (Claude Code / OpenCode)                                 │
│                                                                  │
│   ┌─ turn arrives ──────────────────────────────────────────┐    │
│   │ user message + recent conversation tail                 │    │
│   └─────────────────────────────────────────────────────────┘    │
│                          │                                       │
│                          ▼                                       │
│   ┌──────── Context Classifier (this spec) ─────────────────┐    │
│   │  (1) Signal extraction  (2) Intent score  (3) Tool pick │    │
│   │     entities                if score >= τ:                │  │
│   │     temporal cues             pick MCP tool + args        │  │
│   │     task resumption           emit invocation log         │  │
│   │     explicit hints                                        │  │
│   └─────────────────────────────────────────────────────────┘    │
│                          │                                       │
│                          ▼ (only when score >= τ)                │
│   ┌──────── MCP call (existing tool surface) ───────────────┐    │
│   │  mempalace_search / kg_query / diary_read / traverse    │    │
│   └─────────────────────────────────────────────────────────┘    │
│                          │                                       │
│                          ▼                                       │
│   ┌──────── Result injection (system message / context) ────┐    │
│   │  attach verbatim drawer snippets + provenance footer    │    │
│   └─────────────────────────────────────────────────────────┘    │
│                          │                                       │
│                          ▼                                       │
│   ┌──────── Assistant turn proceeds ────────────────────────┐    │
│   │  may also call MCP tools explicitly; that path unchanged│    │
│   └─────────────────────────────────────────────────────────┘    │
│                          │                                       │
│                          ▼                                       │
│   ┌──────── Feedback collector ─────────────────────────────┐    │
│   │  implicit: did assistant reply cite/use the injection?  │    │
│   │  explicit: user "good recall" / "irrelevant" markers    │    │
│   │  writes to ~/.mempalace/auto_query/decisions.jsonl       │   │
│   └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

The classifier is a **harness-side shim**, not an MCP tool. It composes existing MCP tools through their existing surface. This keeps the spec orthogonal to RFC 001 (storage backends) and RFC 002 (source adapters) — they are *what is stored*; this spec is *when to read it back*.

## 1. Context classifier

### 1.1 Signal extraction

Run on every assistant turn, before the LLM call. Inputs:

| Input | Source | Cost |
|---|---|---|
| Latest user message | turn payload | free |
| Last 5 turns (truncated to 4 KB total) | turn payload | free |
| Project directory | `cwd` + harness env (`OPENCODE_SESSION_DIR`, Claude Code `transcript_path` → `wing_<project>`) | free |
| Known palace wings | `mempalace_list_wings` cached for 60 s | one MCP call per minute |
| Session entity set | accumulated during session, see §1.3 | in-memory dict |

The classifier extracts four signal classes:

#### 1.1.1 Entity signals

Run the user message through `mempalace.entity_detector.extract_candidates(text)`. For each candidate:

- **Known-wing match:** candidate normalized to a wing slug (`wing_<sanitized>`) exists in the cached `list_wings` result. Score `+3`.
- **Known-people-map match:** candidate appears in `~/.mempalace/people_map.json` (canonical or alias). Score `+3`.
- **Known-KG-entity match:** candidate appears in the KG `entity` table (cached top-1000 by triple count, refreshed every 5 min). Score `+2`.
- **Capitalized noun phrase, no match:** Score `+0` but track — these are candidates for new-wing creation, not for auto-query.

`extract_candidates` already exists in `mempalace/entity_detector.py:144` and is the same function the offline miner uses for entity disambiguation, so the surface is shared.

#### 1.1.2 Temporal signals

Regex sweep on the user message for explicit prior-session references:

```
\b(last (time|week|session|night|run)|earlier (today|this week)|yesterday|that time we|when (did|we) )\b
```

- Hit: score `+2`.
- Hit on a phrase that also contains an entity match: bump to `+3` (compounding evidence).

Why a regex and not the LLM: this fires on every turn, the budget is 500 ms, and the false-positive class ("the last commit", "the time complexity") is small enough to filter with a stopword list.

#### 1.1.3 Task-resumption signals

A turn counts as "task resumption" iff *all* of:

- This is the first user message in the session (`turn_index == 1`).
- The harness reports a `cwd` mapping to a known palace wing (via `_wing_from_transcript_path` in `mempalace/hooks_cli.py:895`).
- The wing has at least one drawer filed in the last 7 days (cheap query — `mempalace_list_drawers(wing=W, limit=1)`).

Score `+4` (high — task resumption is the cleanest auto-query case and least intrusive).

#### 1.1.4 Explicit hint signals

A user message containing any of `(remind me|do we have|did we ever|what did we|history of|prior|earlier)` paired with a `?` scores `+5` — at that point the user has effectively asked for memory but has not phrased it as a tool call.

### 1.2 Intent score and threshold

Sum the per-signal scores. The classifier emits an auto-query iff:

| Mode | Threshold τ | Notes |
|---|---|---|
| `off` | ∞ | classifier runs but never fires (default until 1.0) |
| `dry-run` | 4 | classifier fires logically and logs the decision; no MCP call |
| `conservative` | 6 | high precision; ships first |
| `balanced` | 4 | default after `conservative` proves out |
| `aggressive` | 2 | research mode for SME Cat 9a ceiling-testing |

Config flag at `~/.mempalace/config.json`:

```jsonc
{
  "auto_query": {
    "enabled": true,
    "mode": "conservative",
    "max_per_turn": 1,
    "max_per_minute": 6
  }
}
```

`max_per_turn: 1` is non-negotiable in v1 — one auto-query, then quiet. `max_per_minute` is a fuse against runaway behavior in tight conversational loops.

### 1.3 Session entity set

The classifier maintains a per-session `Set[str]` of entities already extracted. An entity that has been auto-queried once in a session does *not* re-trigger in the same session — re-injecting the same drawer for the same entity is the dominant false-positive shape we expect. The set is reset on `session_start` and not persisted.

## 2. Query formulation — intent → MCP tool

Once the threshold is crossed, the classifier picks exactly one MCP call:

| Detected shape | Tool | Args |
|---|---|---|
| Entity match only, no temporal cue | `mempalace_search` | `query=<entity_phrase>`, `wing=<matched_wing>` (if confident), `limit=3` |
| Entity match + temporal cue | `mempalace_kg_query` | `entity=<canonical_name>`, `direction="both"` |
| Temporal cue only ("last time we…") | `mempalace_search` | `query=<full_user_message_truncated_to_200_chars>`, `wing=<current_project_wing>`, `limit=3` |
| Task resumption (turn 1, known wing) | `mempalace_diary_read` | `agent_name=<harness_default>`, `wing=<current_project_wing>`, `last_n=3` |
| Explicit hint ("remind me…") | `mempalace_search` | `query=<sanitized_user_message>`, `limit=5` |
| Cross-wing question ("how does X connect to Y") | `mempalace_traverse` | `start_room=<best_match_room>`, `max_hops=2` |

**Sanitization:** `query` strings go through `mempalace.query_sanitizer.sanitize_query()` (same path as explicit tool calls) before the MCP roundtrip. This mitigates the system-prompt-contamination class (Issue #333).

**Wing scoping:** when the project directory maps to a known wing with ≥ 1 drawer, scope the query to that wing. Cross-wing recall is reserved for the `traverse` shape and for explicit-hint queries with no clear wing.

**No new tools.** Every row in the table above is an existing MCP tool. The classifier picks; the MCP server's existing code handles the call. This is intentional — RFC 002 plus this spec means the read path is fully composed of contracts that already exist.

## 3. Result injection

After the MCP call returns (or times out at 400 ms, see §4), the classifier wraps the result in a system message and prepends it to the assistant's context for the current turn only.

### 3.1 Format

```
[mempalace:auto-query]
trigger: entity=alice (matched wing_alice) | score=6 | tool=mempalace_search
results (3):
  1. wing_alice/people  drawer 0abc1234  (2026-04-18)
     > Alice mentioned the new oncall rotation starts on May 1...
  2. wing_alice/decisions  drawer 0def5678  (2026-03-22)
     > Decided to move Alice off the firefighter rotation because...
  3. wing_alice/meetings  drawer 0fed9012  (2026-02-10)
     > 1:1 with Alice — discussed promo packet, agreed to target Q3...
provenance: mempalace_search(query="alice", wing="wing_alice", limit=3) | latency=287ms
hint to assistant: cite drawer_id if you use this; ignore the block if irrelevant.
[/mempalace:auto-query]
```

Three properties of this format matter:

1. **Verbatim snippets only.** No summarization. The block is direct copy from drawer content (first 200 chars per drawer with `...` continuation). The CLAUDE.md verbatim rule applies to the read path too.
2. **Provenance is explicit.** The exact tool call is shown so a debugger or user can reproduce it.
3. **Tagged with sentinel tokens** (`[mempalace:auto-query]` / `[/mempalace:auto-query]`) so the assistant can be instructed to ignore the block if irrelevant and the feedback collector (§5) can detect citation reliably.

### 3.2 Injection point per harness

| Harness | Mechanism |
|---|---|
| Claude Code | Stop-hook adjacent: a new `PreUserTurn` or `BeforeAssistant` hook would be cleanest. As of 2026-05 only `Stop`, `SessionStart`, and `PreCompact` are documented stable. Until a `PreUserTurn` hook exists, inject via the harness shim layer that already wraps MCP calls — see `examples/opencode/live-capture/` for the pattern; the Claude Code equivalent is the same shape applied to the user-turn boundary. |
| OpenCode | OpenCode 1.15.7's `experimental.chat.system.transform` would be ideal but does not fire (per `docs/integrations/opencode.md`). Until the OpenCode team ships a real `chat.message.beforeAssistant` hook, inject via a small plugin that posts the auto-query block as a system message before the assistant turn — the same plugin shape `examples/opencode/live-capture/` already uses for capture, in the opposite direction. |

This part is *intentionally underspecified*: both harnesses have ongoing plugin-API work, and the right injection point will exist within a release or two. v1 uses whatever hook is closest, accepting that the timing is "before the next assistant turn" rather than "in the system prompt".

### 3.3 Disruption budget

The injected block is capped at **1500 tokens** (≈ 6000 chars). Drawers exceeding that are truncated per-result first, then the result count is reduced from 5 → 3 → 1. If even one full drawer cannot fit, the classifier emits just the provenance line and a "skipped — too large" note; the assistant still knows a query happened and can decide to fetch explicitly.

## 4. Performance budget

| Stage | Budget | Notes |
|---|---|---|
| Signal extraction | 50 ms | regex sweep, dict lookups, `extract_candidates` already <30 ms on 4 KB |
| `list_wings` cache lookup | 5 ms | in-memory dict, refresh in background |
| KG entity-set cache lookup | 5 ms | same |
| MCP call (network) | 400 ms | hard timeout; cancel and skip on miss |
| Injection serialization | 40 ms | string formatting, no LLM |
| **Total** | **500 ms** | matches existing hook budget per CLAUDE.md "Performance budgets" |

**Cancellation semantics:** if any stage exceeds budget, the classifier emits an "auto-query skipped (deadline)" line into the feedback log and proceeds with no injection. The assistant turn must not be delayed by classifier failure modes. The 400 ms MCP-call budget is the dominant slice; in measurements on `palace-daemon` at `familiar.jphe.in:8085`, `mempalace_search` with `limit=3` returns in 80-200 ms p50 / 300-400 ms p99, so the budget is realistic but tight.

**Cold-start carve-out:** on the very first auto-query in a session, the classifier may consume up to 1500 ms (palace-daemon connection establishment + first-query overhead). Subsequent queries in the same session use the 500 ms budget.

## 5. SME Cat 9a measurement

The integration must emit an invocation-rate log that SME's adapter can read. Cat 9a (live invocation rate) compares against Cat 9b (harness call-through, already measurable from explicit `mempalace_search` traces in OpenCode/Claude Code transcripts).

### 5.1 Decision log shape

Every classifier decision — fired or skipped — is appended to `~/.mempalace/auto_query/decisions.jsonl`:

```jsonl
{"ts":"2026-05-22T14:03:12Z","session_id":"abc","turn":7,"signals":{"entity":[{"name":"alice","wing":"wing_alice","score":3}],"temporal":[],"resumption":false,"explicit":false},"score":3,"threshold":6,"mode":"conservative","decision":"skip","reason":"below-threshold"}
{"ts":"2026-05-22T14:03:51Z","session_id":"abc","turn":8,"signals":{"entity":[{"name":"alice","wing":"wing_alice","score":3}],"temporal":[{"phrase":"last time"}],"resumption":false,"explicit":false},"score":5,"threshold":6,"mode":"conservative","decision":"skip","reason":"below-threshold"}
{"ts":"2026-05-22T14:04:28Z","session_id":"abc","turn":9,"signals":{"entity":[{"name":"alice","wing":"wing_alice","score":3}],"temporal":[{"phrase":"last time"}],"resumption":false,"explicit":true},"score":10,"threshold":6,"mode":"conservative","decision":"fire","tool":"mempalace_search","args":{"query":"alice last time","wing":"wing_alice","limit":3},"latency_ms":287,"result_drawers":3,"injection_tokens":842}
```

### 5.2 SME adapter contract

SME runs against `jp-realm-v0.1` (30 questions). The adapter reads `decisions.jsonl` for the SME run's session IDs and computes:

| Metric | Definition |
|---|---|
| **Cat 9a — invocation rate** | `fired / (fired + skip-below-threshold)` over the SME question set |
| **Cat 9a-precision** | `fired ∩ result-used / fired` — auto-queries whose drawers were cited (§5.3) |
| **Cat 9a-recall** | `fired ∩ result-used / (questions-with-relevant-drawers)` — needs an oracle from SME's existing relevance labels |
| **Mode comparison** | the same `decisions.jsonl` rerun with `mode={conservative,balanced,aggressive}` to plot precision/recall tradeoffs |

This is the measurement Cat 9a needs that today does not exist: a *live model* with deterministic logging of whether the harness reached the palace, sliced by question shape.

### 5.3 Citation detection

The feedback collector (§6) decides whether an injected result was "used". Heuristic:

- The assistant's reply contains the `drawer_id` literal of an injected drawer (the format §3.1 nudges it to cite). Strong signal.
- The assistant's reply contains a substring ≥ 20 chars from an injected drawer's content body. Medium signal.
- Neither: the injection is marked `result-used: false`.

Citation detection feeds Cat 9a-precision.

## 6. Feedback loop

Three feedback channels, listed from most to least implicit:

1. **Implicit-cite** (§5.3): the assistant's reply references the injection.
2. **Implicit-no-cite**: the assistant's reply does not reference the injection. This is the dominant false-positive signal.
3. **Explicit user verdict**: the user's *next* message contains one of `(good recall|nice find|that's it|irrelevant|wrong drawer|not what i meant)`. Captured by the same regex sweep that already runs on user messages in §1.1.

Each verdict appends to the same `decisions.jsonl` line by ID (`session_id`, `turn`). After 100 fires, the classifier emits a per-mode summary to `~/.mempalace/auto_query/summary.json`:

```json
{
  "mode": "conservative",
  "fires": 127,
  "implicit_cite": 89,
  "explicit_positive": 12,
  "explicit_negative": 4,
  "skipped_deadline": 3,
  "avg_latency_ms": 264,
  "p99_latency_ms": 472
}
```

This is the operator-facing surface for tuning τ and per-mode threshold tables. It is intentionally *not* an automatic tuner — JP's CLAUDE.md "no over-engineering" principle applies; ship the log, watch it for a week, adjust thresholds by hand, then decide whether automation pays for itself.

## 7. Implementation plan

Five PRs, smallest first, per JP's [small-focused-PRs feedback](https://github.com/jphein/mempalace/issues?q=small+focused). Each PR is independently mergeable; the classifier is dead code until PR 5 wires it.

1. **PR 1 — Decision log primitives.** `mempalace/auto_query/decisions.py` + tests. No classifier, no integration; just the append-only JSONL writer with a rotation/size cap. Wire to a CLI debug command (`mempalace auto-query log dump --last 50`). Lands first because everything else writes to this log.
2. **PR 2 — Signal extractor.** `mempalace/auto_query/signals.py` + tests against fixtures. Pure function: `extract_signals(text, session_state, project_wing) -> SignalSet`. No MCP calls, no I/O. Unit tests cover the four signal classes from §1.1.
3. **PR 3 — Tool picker.** `mempalace/auto_query/router.py` + tests. Pure function: `pick_tool(signals, score, threshold) -> Optional[MCPCall]`. Uses the table from §2. Tests cover every row + boundary conditions.
4. **PR 4 — Result formatter + injection block.** `mempalace/auto_query/formatter.py` + tests. Pure function: `format_injection(tool_call, mcp_result) -> str`. Verbatim snippets only, sentinel-tagged, truncated to the disruption budget. Tests assert the no-summarization invariant.
5. **PR 5 — Harness wiring + config flag.** A thin shim (Python; spawned by harness hooks) that calls 2 → 3 → MCP → 4 → injects, plus the config flag (§1.2) and the SME adapter shape (§5.2). Lands disabled (`auto_query.enabled: false`) until SME validates the conservative threshold in dry-run mode for at least 100 fires.

Each PR target is `main` on `techempower-org/mempalace` (per [Upstream PRs target develop](../../README.md#fork-change-queue) — that rule applies to *upstream* PRs only; fork-internal PRs target our `main`).

Upstream coordination: once the implementation matches the spec, file the upstream PR series on `MemPalace/mempalace` rebased onto `develop`. Per [upstream-comment-timing feedback](https://github.com/jphein/mempalace), defer the upstream-coordination comment until the code works in this fork — "we have working code" lands; "we have a spec" promises.

## 8. Open questions

1. **Forced-invocation ablation.** The three-patterns research note flags this as an open question: if a system prompt mandates a MemPalace check on every turn, does it close the 46.67% → 78.33% gap? This spec's `aggressive` mode is the closest available proxy. Worth running the ablation before declaring a winning threshold.
2. **Cross-harness convergence.** Claude Code and OpenCode have different turn boundaries and different hook APIs. v1 ships harness-specific shims. v2 might converge on a daemon-side classifier that both harnesses call via `palace-daemon`, removing the duplicated logic.
3. **Push-side surfacing.** The research note (§"Open questions") flags engram-style file-read interception as the most underinvested Layer 3 direction. This spec is pull-side (query on user turn). A push-side counterpart (auto-inject on file read) is a separate spec; the decision-log shape (§5.1) is designed to also carry push-side events when that lands.
4. **KG vs vector for entity recall.** The current §2 table picks `kg_query` for entity+temporal and `search` for entity-only. The right cut might be the inverse (KG for known relationships, search for content recall). v1 picks the split by intuition; the decision log will show which is actually more useful.
5. **Per-turn caching.** Same-entity, same-turn re-injection is prevented (§1.3). But the same entity across consecutive turns may legitimately want fresh injection if the user asked a follow-up. v1 conservatively suppresses; the decision log will show how often "suppressed but should have fired again" happens, and we adjust in v2.

## Appendix A — MCP tool surface inventory

For reference, the existing MCP tools the classifier composes (from `mempalace/mcp_server.py`):

| Tool | Purpose | Used by classifier? |
|---|---|---|
| `mempalace_status` | palace health | no |
| `mempalace_list_wings` | wing inventory | yes (cached) |
| `mempalace_list_rooms` | room inventory | no |
| `mempalace_get_taxonomy` | full wing/room map | no |
| `mempalace_search` | semantic + hybrid search | yes |
| `mempalace_check_duplicate` | dedupe before write | no |
| `mempalace_kg_query` | entity relationships | yes |
| `mempalace_kg_add` / `kg_invalidate` / `kg_timeline` / `kg_stats` | KG management | no |
| `mempalace_diary_write` / `diary_read` | per-agent journals | yes (read) |
| `mempalace_add_drawer` / `delete_drawer` / `get_drawer` / `list_drawers` / `update_drawer` | drawer CRUD | yes (`list_drawers` for resumption check) |
| `mempalace_traverse` / `find_tunnels` / `create_tunnel` / `list_tunnels` / `delete_tunnel` / `follow_tunnels` | cross-wing graph | yes (`traverse`) |
| `mempalace_graph_stats` | graph overview | no |
| `mempalace_walk_palace` | AGE Cypher traversal | no (research-mode candidate) |
| `mempalace_get_aaak_spec` | AAAK dialect spec | no |
| `mempalace_sync` | gitignored drawer pruning | no |
| `mempalace_hook_settings` | hook config | no |
| `mempalace_memories_filed_away` | checkpoint status | no |
| `mempalace_reconnect` | HNSW reset | no |

The classifier uses 5 of 30 tools. The rest are management/write surfaces that auto-query has no business touching.

## Appendix B — Worked example

A user opens Claude Code in `~/Projects/familiar.realm.watch` (mapped to `wing_familiar`) and types:

> Last time we were debugging the cross-encoder reranker — what did we conclude about the latency-vs-recall tradeoff?

Signal extraction:

- Entity signals: `cross-encoder reranker` (no wing match, no people map match, no KG entity). Score `+0`.
- Temporal signals: `Last time we` matches the regex. Score `+2`.
- Task-resumption: turn 1, `cwd` maps to `wing_familiar`, wing has drawers in last 7 days. Score `+4`.
- Explicit hint: `?` present but no `do we have / did we ever / what did we / remind me` phrase. Score `+0`.

Total: `+6`. Mode `conservative`, τ `= 6`. **Fires.**

Tool picker: task-resumption signal dominates → `mempalace_diary_read(agent_name="claude-code", wing="wing_familiar", last_n=3)`.

MCP roundtrip: 234 ms. Returns 3 diary entries.

Injection block: 612 tokens, sentinel-tagged, prepended to the assistant turn.

Assistant replies citing `drawer_id 7fa3` (matches an injected drawer). Citation detection marks the fire `result-used: true`. Decision log line includes `latency_ms: 234`, `result_drawers: 3`, `injection_tokens: 612`, and later (after the assistant reply) `result_used: true`.

This is the load-bearing case: a known project, a temporal cue, and a task-resumption signal compounding to put the conversation back where it left off without the user having to ask.
