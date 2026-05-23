# Design: scope/collection filter on `mempalace_search`

**Issue:** [techempower-org/mempalace#76](https://github.com/techempower-org/mempalace/issues/76)
**Author:** Iris (system designer, team dream-celestial)
**Date:** 2026-05-22
**Status:** Proposal — not implemented
**Recommendation:** **Option 1 (status quo, document) with a deferred Option 2 trigger.**

---

## TL;DR

`kind=` was retired in [`7ba28dc`](https://github.com/techempower-org/mempalace/commit/7ba28dc) because the structural fix (checkpoint → recovery collection split) made the filter inert. The follow-on retirement in [`0b945e1`](https://github.com/techempower-org/mempalace/commit/0b945e1) (PR #8) went one step further and dropped the `mempalace_session_recovery` collection itself — both write and read paths. **In this fork, today, there is exactly one MCP-visible collection (`mempalace_drawers`).** The premise of issue #76 ("the palace has at least two physically separate stores") is true upstream and on the live disks-daemon palace (the 763 archived recovery entries are still on disk), but the *fork's MCP/CLI surface* now reaches one collection.

That changes the design call. The right answer is the one this fork already ratifies in the [README "what this fork has learned"](../../README.md#what-this-fork-has-learned) section:

> Each new sibling collection has to earn its own read tool before it gets writes.

In other words: **Option 1 today, Option 2 the day a second MCP-visible collection earns its read surface, Option 3/4 deferred indefinitely unless a concrete consumer makes the case.**

The rest of this doc lays out the four options, their costs, and the downstream impact so the recommendation is defensible against the alternatives.

---

## Architecture today

### Collection surface

| Collection name | Backend table / chroma name | Writers | Readers (MCP) | Status |
|---|---|---|---|---|
| `mempalace_drawers` | default `_config.collection_name` | `tool_add_drawer`, `tool_diary_write`, miners, hook ingest | `tool_search`, `tool_get_drawer`, `tool_list_drawers`, `tool_update_drawer`, KG/tunnel/closet readers | **Live** |
| `mempalace_closets` | side collection (chroma) / fixed table (postgres) | closet writer | indirect via ranking signal in `_hybrid_rank` | **Live, internal** |
| `mempalace_session_recovery` | retired | none (PR #8) | none (PR #8) | **Retired** — on-disk data preserved on the live palace |

The postgres backend (`mempalace/backends/postgres.py:1010`) already keys cached collections by `(dsn, palace_id, table_name)` — multi-collection-per-palace is a first-class capability of the storage layer. What's gone is the MCP-visible second collection.

### Search call path

```
MCP tool_search (mcp_server.py:1169)
  └─> search_memories (searcher.py:1525)
        ├─> get_collection(palace_path, collection_name=_config.collection_name)
        ├─> _hybrid_rank (vector top-N + BM25 union strategy)
        └─> [closet boost as a ranking signal, not a gate]
```

`tool_search` passes exactly one `collection_name` (`_config.collection_name`, defaults to `mempalace_drawers`). There is no plumbing for a list of collections. The `candidate_strategy` parameter (`vector` / `union` / `hybrid`) is *within* one collection — vector vs. BM25 candidate selection — not across collections.

### Where the four options actually diverge

The premise behind issue #76's framing — *"search reads the main collection, recovery/audit tools read recovery"* — was the architecture between 2026-04-25 (Phase A of the split) and 2026-05-05 (PR #8). For ~10 days the fork had two MCP-visible collections and the corresponding "should search span both?" question was live. The split was retired because **the recovery collection never earned a `mempalace_search_recovery` MCP read tool, so checkpoints became invisible to retrieval** — exactly the failure mode Option 3 is intended to avoid. That history is the central data point this design has to honour.

---

## The four options

### Option 1 — Status quo, document it

`mempalace_search` reads `mempalace_drawers` only. Document in:

- README "Architecture" section (already implicit; make it explicit)
- `tool_search` MCP description string in `mcp_server.py`
- `MempalaceDaemonAdapter` docstring (SME)
- A note in `docs/designs/scope-collection-filter.md` linking back here

The deprecated `kind=` parameter stays gone. SME's `--kind` flag is removed (PR #7 cleanup as flagged in the issue).

**API surface change:** none.

**Pseudocode (no code change needed):**
```python
# mempalace/mcp_server.py — no diff
def tool_search(query, limit=5, wing=None, room=None, tags=None, ...):
    ...
```

### Option 2 — `collections=` parameter (deferred until a second read surface exists)

Add a `collections: list[str] | None = None` parameter to `mempalace_search` and the daemon `/search` endpoint. Default = `["mempalace_drawers"]`. Accepted values are the names listed in a fixed enum that grows as new collections earn their MCP surface.

**API surface change:**
```python
# mempalace/mcp_server.py
def tool_search(
    query: str,
    limit: int = 5,
    wing: str = None,
    room: str = None,
    tags: list = None,
    max_distance: float = 1.5,
    collections: list[str] | None = None,  # new
    ...
):
    collections = collections or ["mempalace_drawers"]
    _validate_collections(collections)  # rejects unknown names
    ...
    # Per-collection query + ranked merge (see "Ranking concerns" below)
```

**Wire shape (HTTP):**
```
POST /search
{"query": "...", "limit": 5, "collections": ["mempalace_drawers", "mempalace_closets"]}
```

**Migration semantics:** every collection in `collections` must (a) be registered to the search surface in a known list, (b) carry comparable embeddings (same dimension + same embedder identity, or be explicitly tagged as BM25-only), (c) implement the same metadata-filter contract for `wing`/`room`/`tags`.

**What's nontrivial:** ranking across collections. See "Ranking concerns" below — this is Option 4's hard part in miniature.

### Option 3 — Per-collection sibling tools

`mempalace_search` stays drawers-only. Each new collection lands a sibling: `mempalace_search_closets`, `mempalace_search_recovery` (if ever resurrected), etc. Each tool can have its own ranking semantics, score scale, and metadata contract.

**API surface change:**
```python
def tool_search(...):                # unchanged, drawers only
def tool_search_closets(...):        # new, closets only
def tool_search_<future>(...):       # one per future collection
```

**Wire shape (HTTP):** parallel endpoints. Cross-collection results are assembled client-side.

**SME impact:** the adapter grows a `--collection {drawers,closets,recovery}` choice flag whose values are 1:1 with daemon endpoints (no fusion logic on the daemon side).

### Option 4 — Federated default

`mempalace_search` reads all registered collections by default and fuses rank lists (RRF, weighted-RRF, or a learned reranker). Single tool, broadest surface.

**API surface change:**
```python
def tool_search(query, limit=5, ..., collections: list[str] | None = None):
    # collections=None  ⇒  ALL registered collections
    # collections=[...] ⇒  explicit subset (Option 2 carve-out)
```

**Ranking concerns:** RRF (Reciprocal Rank Fusion, Cormack 2009) is the natural default — agnostic to absolute score scale, works for arbitrary collections, well-understood failure modes. Plumbing for this already exists in the fork via `multi_encoder.fused_query` (used today for cross-encoder fusion at `searcher.py:1638`); the same primitive applies to cross-collection fusion. The hard part is not the fusion algorithm but **calibration**: each collection's recall depends on its embedder, its metadata distribution, and its size, and these can change independently. A federated default that silently degrades the moment a new collection is registered is exactly the failure mode this fork has learned to fear (recovery collection → invisible to search → drop the whole thing).

---

## Trade-offs matrix

| Dimension | Opt 1 (status quo) | Opt 2 (`collections=`) | Opt 3 (sibling tools) | Opt 4 (federated) |
|---|---|---|---|---|
| **Implementation complexity** | None | Moderate — per-collection query + merge + enum validation | Low — copy `tool_search` per collection, no fusion | High — RRF or learned fusion, calibration pipeline |
| **Performance (single-collection query)** | Baseline | Baseline (default path unchanged) | Baseline | Slightly worse — fusion overhead even when 1 collection |
| **Performance (multi-collection query)** | N/A (caller does N queries) | N parallel backend queries + merge | N tool calls round-trip | N parallel backend queries + fusion |
| **UX — MCP tool count** | 1 search tool | 1 search tool | N search tools (grows per collection) | 1 search tool |
| **UX — discoverability** | Best for the single-collection world | Good — one tool, clear surface | Poor — caller must know which sibling | Best for true multi-collection world |
| **UX — failure mode if a collection is misconfigured** | N/A | Validation error or filtered out | Sibling tool is unreachable; explicit | Silent rank degradation (worst case) |
| **Backwards compat (fork)** | Perfect — nothing changes | Backward-compat (default = drawers) | Backward-compat | Backward-compat *if* default = drawers; semantic break if default = all |
| **Backwards compat (upstream RFC 001)** | Aligned — RFC 001 doesn't mandate this | Aligned — extends the per-call collection selection RFC 001 §1 already allows | Aligned — RFC 001 doesn't restrict tool surface | Adds a fusion concern RFC 001 doesn't currently address |
| **Documentability** | One sentence in README | API doc + enum maintained | One section per sibling | Long — fusion semantics, calibration, score interpretation |
| **Cost to add a new collection later** | Decide each time | Add to enum + ensure embedder/contract parity | Write a new tool | Calibration audit + acceptance test |
| **Cost if a wrong-shaped collection slips in** | Cannot — only one is reachable | Validation rejects on registration | Sibling tool exists but is orthogonal | Pollutes top-N silently |
| **Composability with upstream** | Trivial | Easy — single optional parameter | Easy — additive | Hardest — fusion is the kind of thing upstream may want to own |
| **Echoes a known failure mode** | No | No | **Yes** — recovery collection split (2026-04-25 → 2026-05-05) failed because the sibling tool was never written | No |

The "echoes a known failure mode" row is the load-bearing one. Option 3 is the architecture the fork *had* during the recovery-collection split, and it broke because writes happened before the read surface existed. Repeating it would require strong evidence that the next sibling collection comes with its read tool from day one.

---

## Impact on palace-daemon API

The daemon's `/search` endpoint currently mirrors `tool_search`'s signature (modulo HTTP encoding). Each option lands as:

| Option | `/search` change | New endpoints |
|---|---|---|
| 1 | None | None |
| 2 | Optional `collections` JSON field (default `["mempalace_drawers"]`) | None |
| 3 | None | `/search/closets`, `/search/<name>` per collection |
| 4 | Optional `collections` JSON field (default ALL or `["mempalace_drawers"]`) | None |

Option 2/4 both keep the daemon endpoint count flat — the work moves into validation + per-collection query orchestration inside the daemon's search handler.

Option 3 grows the endpoint count by 1 per future collection. Versioning + deprecation policy gets messier (an endpoint can be removed; a parameter value can only be filtered out).

The daemon should also remain free to **reject** an unknown collection name with a 400 rather than silently degrading — this is the lesson of `kind=`'s soft-acceptance prelude. Loud failures over silent ones.

---

## Impact on SME adapter (multipass-structural-memory-eval)

SME's [`MemPalaceDaemonAdapter`](https://github.com/M0nkeyFl0wer/multipass-structural-memory-eval/blob/main/sme/adapters/mempalace_daemon.py) still carries `DEFAULT_KIND = "content"` and a `--kind` CLI flag. Each option maps to:

| Option | SME changes |
|---|---|
| 1 | Delete `DEFAULT_KIND`, delete `--kind` flag, delete the xfail'd `test_kind_content_excludes_stop_hook_checkpoints`, ship as part of next SME release |
| 2 | Rename `--kind` → `--collections` (comma-separated, default `mempalace_drawers`); the xfail becomes a real test against a multi-collection palace fixture |
| 3 | Rename `--kind` → `--adapter` (choice between `mempalace-daemon` and `mempalace-daemon-closets` etc.); each is a separate adapter class wrapping the corresponding daemon endpoint |
| 4 | Rename `--kind` → `--collections` (same as Option 2); the federated default means no flag = all collections, mirroring daemon behaviour |

Option 1 is the lightest path for SME (a single PR that removes code) and unblocks PR #7's xfail cleanup with no follow-up.

---

## Impact on MCP tool surface

| Option | MCP tool count delta | Schema delta |
|---|---|---|
| 1 | 0 | 0 — description string updated to call out "drawers only" |
| 2 | 0 | `collections: list[str] \| null` added to `tool_search.inputSchema` |
| 3 | +1 per future collection | None per tool, but N more tool entries |
| 4 | 0 | `collections: list[str] \| null` added; default semantics documented |

Option 3's tool-count growth has a real cost: MCP clients page through tool lists, descriptions consume context tokens, and every additional surface is one more thing for `claude-code-switcher` / `claude` / `codex` / `gemini-cli` to discover. Option 2/4 keep the tool inventory flat.

---

## Ranking concerns (Options 2 and 4)

A cross-collection result list is only useful if the scores compose. Three constraints have to hold:

1. **Same embedder, same dimension.** Mixing 384-dim MiniLM with 1024-dim BGE in one ranked list produces nonsense distances. The fork's embedder-identity check (`EmbedderIdentityMismatchError`, `backends/base.py:65`) already exists; cross-collection search has to call it per collection and refuse the request if they diverge.
2. **Comparable distance distributions.** Two collections with the same embedder can still have very different distance distributions because their content distributions differ (long-form prose vs. short metadata records). RRF dodges this by ignoring absolute distance and ranking by position only — the safe default.
3. **Metadata-filter parity.** `wing`, `room`, `tags` must work the same way across collections. The postgres backend already enforces this via the trigram + jsonb index, but a future collection that didn't include the metadata columns would silently lose filter coverage.

For Option 2, the minimum viable ranker is: query each collection independently with `_hybrid_rank`, then RRF-fuse the top-N from each. The fork has `multi_encoder.fused_query` (used for cross-encoder fusion at `searcher.py:1638`) as a working precedent — the same RRF primitive applies.

For Option 4, the same algorithm is the default, but it has to be the *only* algorithm because the caller can no longer opt out. That makes calibration acceptance tests mandatory: a regression suite that asserts top-5 stays stable when a new collection is registered. Without it, the federated default is one PR away from the recovery-collection-style invisibility regression.

---

## Recommendation

**Option 1 today.** Document the single-collection reality, ship the SME `--kind` removal as part of PR #7 cleanup, close the issue once the docs land.

**Option 2 the day a second collection earns its MCP read surface.** When (not if) a future collection — KG-triple store (P4), Haiku-enriched topic docs, the curated-vs-mined palace split discussed in upstream #1018 — earns its own write contract AND its own read tool AND demonstrates value in cross-collection retrieval, add `collections: list[str] | None` to `tool_search`. The signal that we've crossed that threshold: someone runs a benchmark showing that a query that returns top-5 from collection A misses results that collection B would have surfaced, AND those B-results are demonstrably useful for the agent.

**Option 3 (sibling tools) deferred indefinitely.** The recovery-collection split is the case study. Sibling tools require N writes AND N reads to ship in lockstep; in practice the writes arrive first, the reads slip, and the collection becomes a write-only black hole. The architectural pattern is sound *only* if every new collection earns its read surface before any write lands. That's the rule from the [README "what this fork has learned" section](../../README.md#what-this-fork-has-learned), and it's stronger than the "decide at design time" framing of Option 3.

**Option 4 (federated) deferred until we have ≥3 collections.** RRF over 2 collections is hard to distinguish from "query both, concatenate, dedupe by source." Federation earns its complexity at the point where the caller cannot reasonably enumerate the collections themselves — which is when there are enough collections that an enum-driven Option 2 stops being ergonomic. Today there is one. Tomorrow there is likely two. Option 4 is the right answer for a world that doesn't exist yet; building it now would be a future-need overshoot.

### Reasoning

1. **The premise of #76 is no longer true in this fork.** PR #8 retired the second MCP-visible collection. Resurrecting a cross-collection filter today is a parameter looking for a use case. The cheapest correct move is to document and move on.
2. **The pattern from the README is durable.** "Each new sibling collection has to earn its own read tool before it gets writes" is exactly what makes Option 1 → Option 2 the right trajectory. The trigger for Option 2 is *the existence of a second collection with a demonstrated read need*, not a prospective desire to plan for one.
3. **The recovery-collection failure is recent and instructive.** It cost the fork three weeks of split-collection complexity that was retired the moment the cross-collection invisibility became measurable. The lesson — and the cost of relearning it — is fresh. The bias should be against re-introducing the same shape until a concrete consumer pays for it.
4. **Option 2's API delta is forward-compatible.** Adding `collections: list[str] | None = None` later is non-breaking. We lose nothing by waiting and gain the ability to design it against a real second collection rather than a hypothetical one.
5. **SME's cleanup gets cheapest path.** Option 1 unblocks the xfail removal in SME PR #7 with no further coordination needed.

### Trigger conditions to revisit (Option 1 → Option 2)

A second collection earning its MCP read surface should satisfy *all* of:

- Has a documented write contract distinct from `mempalace_drawers`' purpose.
- Has a working read tool (`tool_search_<name>` or equivalent) that returns the same `{ids, documents, metadatas, distances}` shape.
- Has a benchmark showing that a representative query class returns useful results from this collection that `tool_search` over `mempalace_drawers` does not.
- Has the same embedder identity (or is explicitly tagged BM25-only) and the same metadata-filter contract for `wing`/`room`/`tags`.

When all four hold, file the Option 2 implementation issue, port `multi_encoder.fused_query`'s RRF into a `_cross_collection_merge` primitive, add `collections: list[str] | None = None` to `tool_search`, gate behind a configuration flag for one release cycle, and update SME's `--kind` → `--collections` rename in lockstep.

---

## Related work

- Upstream [RFC 001](https://github.com/MemPalace/mempalace/pull/743) — backend spec; `get_collection(palace, collection_name=...)` already supports multi-collection-per-palace at the storage layer. The fork has flagged this as worth naming explicitly upstream in the README "what this fork has learned" section. This design doc is one piece of that conversation: the storage layer is ready; the read surface should follow consumer demand, not anticipate it.
- Fork commit [`7ba28dc`](https://github.com/techempower-org/mempalace/commit/7ba28dc) — `kind=` retirement that triggered #76.
- Fork PR [#8](https://github.com/MemPalace/mempalace/pull/8) ([`0b945e1`](https://github.com/techempower-org/mempalace/commit/0b945e1)) — recovery-collection retirement that changed the architectural premise of #76.
- Upstream discussion [#1018](https://github.com/MemPalace/mempalace/discussions/1018) — kostadis' multi-palace request; potentially overlaps with collection-partitioning if the use case materializes.
- Fork README §"Architecture: P8 — Corpus partitioning by purpose" — the durable lesson from the recovery-collection split.
- [`docs/superpowers/specs/2026-05-05-verbatim-only-design.md`](../superpowers/specs/2026-05-05-verbatim-only-design.md) — the verbatim-only design that drove PR #8.
