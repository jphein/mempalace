# Corpus partitioning by purpose — implementation spec (2026-05-26)

**Status:** spec, not implemented
**Issue:** [techempower-org/mempalace#169](https://github.com/techempower-org/mempalace/issues/169) (P8)
**Author:** Morpheus (team kg-backfill-stabilize)
**Feeds from:** [#202 multi-palace separation evaluation](https://github.com/techempower-org/mempalace/issues/202) (Reverie) — which **recommends** collection-partitioning over multi-palace. This spec is the implementation design for that recommendation. The companion evaluation doc lands as `docs/research/2026-05-26-multi-palace-separation.md` with Reverie's #202 PR.
**Composes with:** [`docs/designs/multi-palace-separation.md`](../designs/multi-palace-separation.md) (#45, config/CLI/daemon shapes) and [`docs/designs/scope-collection-filter.md`](../designs/scope-collection-filter.md) (#76, the `partition`/`partitions` read-parameter shape).
**Upstream coordination:** [#46](https://github.com/techempower-org/mempalace/issues/46) (RFC 001 follow-up — naming the multi-collection-by-purpose pattern). Per the fork's upstream-comment-timing discipline, the #46 Discussion waits until partition code is working, not just specified.

---

## TL;DR

Build **collection partitioning** — one palace, sibling collections keyed by *write-purpose* — with **read-surface parity encoded as a schema invariant**, not as discipline. A partition is a named tuple `(collection, embedder, description, searchable, writers)`. The default config (no `partitions:` block) resolves bit-for-bit to today's single-collection world.

The deciding constraint, inherited from the retired P8 recovery-collection split and ratified by #202: **a write-side partition without a read-side surface is invisible data.** This spec makes that impossible to recreate by accident — every partition appears in `mempalace status`, and every `searchable: true` partition is reachable from the canonical search surface from its first write. 100% recall is the design requirement; partitioning must never silently drop results.

Phased rollout:

- **Phase 1 (additive surface):** ship the `partitions` config block, the write-routing layer (`writers` / `--partition` / `partition` MCP kwarg), the read layer (`partition` / `partitions` on every read tool, federated default across `searchable` partitions), and the `searchable` invariant. No data moves. No second partition ships. Existing tests pass unchanged.
- **Phase 2 (first second partition):** ship `mempalace partitions create authority` and a **copy-not-move** seed CLI. This is the phase that actually serves @kostadis's curated-canon use case.
- **Deferred:** multi-palace promotion (#45 §4), retrieval weighting of curated rows (Option E), per-partition closet routing.

---

## 1. Problem statement

### 1.1 Where this comes from

MemPalace's default is one palace, one collection (`mempalace_drawers`), fed by auto-hooks that mine every transcript. That is correct for the core promise — *completeness is the feature*. It collides with a second, legitimate workload, surfaced by [@kostadis (upstream #1018)][d1018]:

> "I use it to keep track of a massively complex canon ... the default setup doesn't allow me to have a chat mempalace and a separate mempalace that I can curate manually with no auto-hooks."

Reverie's evaluation (#202) weighed five separation shapes against the fork's design principles and **recommended Option B — collection partitioning**, deferring multi-palace (Option C) until a concrete trigger (different embedder family, host/air-gap, retention, or blast domain) appears. The decisive reasons:

- **Entity-first prefers a single entity namespace.** One palace = one KG; `Alice` resolves to one node regardless of which collection a mention came from. Provenance becomes an edge attribute, not a partition wall. Multi-palace fractures the namespace and reintroduces cross-palace entity resolution — the hard disambiguation problem, multiplied by palace count.
- **Incremental-only is clean for an additive collection** and only at risk for a *seed migration*, which this spec resolves by making the seed a copy, not a move.
- **Lowest infra delta** that still gives a *hard* write-isolation wall (vs. the soft query-time filter of a metadata-only `purpose` tag).

This spec turns that recommendation into a build plan.

### 1.2 The constraint that decides the design

The P8 recovery-collection split (2026-04-25 → 2026-05-05) moved Stop-hook checkpoints into a sibling collection `mempalace_session_recovery` that `mempalace_search` never queried. "What did I work on last Tuesday?" became unanswerable. The split was retired.

The codified rule ([README §P8](../../README.md)): *"each new sibling collection has to earn its own read tool before it gets writes."* This spec strengthens it from a guideline to a **schema invariant** (§4). It is the single most important requirement here and it is *option-neutral* — partitioning can re-commit the mistake just as easily as multi-palace could. The mitigation is structural, not behavioural.

### 1.3 Non-goals (explicitly out of scope)

- **Multi-palace** (a second `palace_path` root). Deferred per #202 §"Defer Option C" and #45 §4. This spec designs the partition surface so it *composes upward* to multi-palace, but does not build it.
- **Retrieval weighting** — preferring a curated "authority" row over a transcript fragment that merely mentions the same fact. Separation is the *precondition*; the weighting function belongs to the searcher and must be A/B'd against the fork's own corpus (per "test retrieval against our corpus" discipline) *after* separation lands.
- **Canon edit/supersede semantics** — the @kostadis "curate manually" pattern implies revising canon over time. Versioning of a curated drawer (vs. append-only chat drawers) is sketched in §7, not designed here.
- **Per-partition closet routing** — whether the `authority` partition gets its own closet index (#45 Open Question 3). Tracked there.

---

## 2. The model: collection per write-purpose

### 2.1 What a partition is

A **partition** is a named, addressable tuple within one palace:

| Field | Type | Meaning |
|---|---|---|
| `collection` | str | The backend collection/table name (e.g. `mempalace_drawers`, `mempalace_authority`). |
| `embedder` | str | Embedder identity. Must match across partitions that federate (§5). |
| `description` | str | Human-readable purpose, shown in `mempalace status` and the MCP tool description. |
| `searchable` | bool | Default `true`. If `true`, the partition is reachable from the canonical search surface from its first write. If `false`, it is reachable only when explicitly named — but it *still appears in `mempalace status`* (§4). |
| `writers` | list[str] | Which write sources may target this partition (e.g. `stop_hook`, `mine_cli`, `curated_import`). The hard isolation wall. |

The `palace_path` stays a single string. Partition names are how callers address parts of one palace. This is intentionally isomorphic to the eventual multi-palace `palaces:` map (#45 Q1) — the only difference is whether the named map keys collections or palaces. Building partitioning first pre-pays for multi-palace; it does not foreclose it.

### 2.2 Which purposes/partitions exist

Phase 1 ships the *machinery* with exactly one partition declared — `drawers`, the back-compat default. The partitions that the machinery is designed to host:

| Partition | Purpose | Writers | `searchable` | Ships in |
|---|---|---|---|---|
| `drawers` | Auto-mined Claude Code transcripts + project files. Today's default. | `stop_hook`, `precompact_hook`, `mine_cli`, `add_drawer`, `diary_write` | `true` | Phase 1 (back-compat) |
| `authority` | Hand-curated reference / canon. **No auto-hooks.** | `curated_import`, `add_drawer --partition authority` | `true` | Phase 2 |
| `recovery` | Rehabilitated P8 checkpoint store, *if revived*. Audit-only. | `stop_hook` (opt-in) | `false` | not scheduled; shape reserved |
| `kg_topics` | Future: KG-derivative / Haiku-enriched topic docs (#160, P4). | `kg_enrich` | TBD by its own read-need | deferred |

The taxonomy point: a partition's purpose is its **write contract**, not its content category. `drawers` and `authority` can both contain a fact about Alice; what differs is *who is allowed to write there*. This is what makes the isolation wall meaningful — the hook physically cannot write to `authority` because it is not in that partition's `writers` list.

### 2.3 How a drawer's purpose is determined at write time

Write-purpose is resolved at the **write address**, never inferred from content. Three layers, in precedence order (highest wins):

1. **Explicit call argument** — `mempalace mine /path --partition authority`, or the `partition="authority"` MCP kwarg, or `POST /silent-save?partition=authority`. The caller names the target.
2. **Writer→partition routing table** — config maps each *write source* to a default partition:

   ```yaml
   writers:
     stop_hook: drawers
     precompact_hook: drawers
     mine_cli: drawers
     curated_import: authority
   ```

   The Stop-hook never passes `--partition`, so its rows always land in `drawers` via this table. This is the load-bearing decision: without it, the hook keeps dumping everything into one partition regardless of CLI gymnastics — which is the problem we are solving.
3. **Global default** — `default_write_partition` (itself defaulting to `drawers`) if neither above applies.

**Validation is loud, not silent.** Under `daemon_strict` (already a fork concept, #49), a write naming an unknown partition — or a write source not in the target partition's `writers` list — **fails with a 400**, it does not silently fall back. A misrouted curated write must error, not contaminate. This is the soft-vs-hard distinction that rules out the metadata-tag-only Option A: a dropped tag in Option A silently dumps transcript noise into the curated namespace; a partition wall refuses it.

---

## 3. Read-surface parity (the hard part)

100% recall is the design requirement. Partitioning must not reduce it. The default read behavior must therefore *widen*, never narrow, the corpus a query sees — while remaining bit-for-bit identical when only one partition exists.

### 3.1 Default read behavior

Every read tool (`mempalace_search`, and by extension `tool_get_drawer`, `tool_list_drawers`) grows two **mutually exclusive** parameters (the shape #76 established):

- `partition: str | None` — query exactly one named partition.
- `partitions: list[str] | str | None` — query several, results interleaved by RRF. `"*"` means "all `searchable: true` partitions."

The default-behavior decision — the one #45 Open Question 2 flagged — resolves as follows, and it is the crux of recall:

> **A no-argument `mempalace_search` queries every `searchable: true` partition (federated default), RRF-fused.**

Rationale: the P8 failure was a *write-only* partition. The structural guarantee against repeating it is that the default read surface *includes* every searchable partition automatically. If the default were "the configured default partition only," then the day an `authority` partition is created, every existing caller silently stops seeing it — which is the recovery-collection invisibility regression wearing a different hat. We choose recall-by-default over surprise-avoidance, because recall *is* the product.

The back-compat guarantee holds anyway: **when only `drawers` exists** (Phase 1, every existing install), "all searchable partitions" *is* `[drawers]`, so the federated default is bit-for-bit today's single-collection query — no fan-out, no RRF, no fusion overhead. Federation cost is paid only once a second searchable partition actually exists.

### 3.2 Opt-in scoping

- `mempalace_search(query=..., partition="authority")` — exactly one named partition.
- `mempalace_search(query=..., partitions=["authority", "drawers"])` — an explicit subset, RRF-fused.
- `mempalace_search(query=...)` — all `searchable: true` partitions (the default above).
- A `searchable: false` partition (recovery / audit / KG-derivative) is reachable **only** by naming it explicitly (`partition="recovery"`); `"*"` and the default never include it. This is the deliberate carve-out for audit stores that should not pollute normal retrieval — but it is *opt-out*, declared in config, and visible in `mempalace status`, so it can never be created silently.

### 3.3 The federation mechanism

- Federation = query each partition independently with the existing `_hybrid_rank`, then **RRF-fuse** the per-partition rank lists. RRF (Cormack 2009) is rank-based, so it ignores absolute distance scale — the safe default when two collections have different distance distributions. The fork already has the primitive: `multi_encoder.fused_query` (used today for cross-encoder fusion at `searcher.py:1638`). Phase 1 extracts a `_cross_partition_merge` from it; no new fusion algorithm is invented.
- **Embedder-identity is a hard gate.** Every partition in a federated query must share an embedder identity (model + revision). The backend already raises `EmbedderIdentityMismatchError` (`backends/base.py:65`); this spec promotes that check to the **search layer** so a federated query spanning mismatched embedders errors *at the request*, not silently in the rankings. Partitions with different embedders cannot federate — they must be queried one at a time and combined client-side. (This is also the cost that makes multi-palace promotion expensive, and is why within-palace partitions share an embedder by default.)
- **Metadata-filter parity.** `wing`, `room`, `tags` must behave identically across partitions. The postgres backend enforces this via its trigram + jsonb indexes; a partition whose collection lacks those columns would silently lose filter coverage. Phase 1's `partitions create` therefore provisions the same metadata schema for every partition (§6), and a partition that cannot honor the filter contract is rejected at registration.

### 3.4 Recall acceptance test (mandatory)

Because the federated default can silently degrade the moment a new partition is registered, Phase 1 ships a **calibration/recall regression suite** as a release gate:

- A fixture palace with two searchable partitions, each holding a known answer set.
- Assert that a no-argument `mempalace_search` returns results from *both* partitions (the anti-P8 assertion).
- Assert top-K stability: adding an empty second partition does not perturb the single-partition ranking (back-compat).
- Assert that a `searchable: false` partition is **absent** from the default and from `"*"`, and **present** only when named.
- Assert embedder-mismatch federation raises at the request, not in the rankings.

Without this suite the federated default is one PR away from re-creating the recovery-collection regression. The suite *is* the structural enforcement of read-surface parity.

---

## 4. The read-surface-parity invariant (schema-level)

This is the codified P8 lesson, lifted from discipline into the schema:

1. **Every partition declares `searchable` explicitly** (default `true`). There is no implicit/unknown state.
2. **A `searchable: true` partition is reachable from the canonical search surface from its first write** — guaranteed by the federated default (§3.1), not by remembering to add a tool.
3. **Every partition — searchable or not — appears in `mempalace status`.** A write-only-never-read partition is therefore impossible to create by accident: it shows up the instant anyone inspects the palace. `mempalace status` lists each partition with its name, collection, embedder, description, `searchable` flag, declared writers, and drawer count.
4. **Registration is the gate.** `mempalace partitions create` refuses to register a partition that cannot honor the metadata-filter contract or whose embedder mismatches the federation set (unless it is explicitly `searchable: false` and thus never federated).

Invariant, stated once: *a partition that can be written can be found.* The only escape hatch is an explicit, config-declared, status-visible `searchable: false` — and even that is reachable by name.

---

## 5. Migration / incremental story

Partitioning must be append-only and crash-safe. Never destroy the existing palace to repartition.

### 5.1 Phase 1 — additive, no data moves

- A `mempalace.yaml` (or global config) without a `partitions:` block resolves to `partitions: { drawers: { collection: <collection_name>, searchable: true, writers: [<all current writers>] } }`. This is today's behavior, expressed in the new vocabulary.
- Existing palaces need **no migration**. The partition layer is a configuration wrapper over the backend's already-multi-collection-capable `get_collection(palace, collection_name=...)` (`backends/base.py:355`). No table is created, renamed, or dropped.
- A crash during Phase 1 rollout leaves `mempalace_drawers` untouched — there is nothing to corrupt because nothing is written or moved.

### 5.2 Phase 2 — first second partition, copy-not-move seed

- `mempalace partitions create authority` provisions a new empty collection (and its metadata schema, §6). Creating it leaves `drawers` untouched; a crash mid-create leaves the chat corpus fully readable.
- **Seeding the authority partition from existing curated content is a *copy*, not a move.** `mempalace partitions copy --from drawers --to authority --where 'topic == "reference"'`:
  - writes matching rows into `authority`, leaves them in `drawers`;
  - is transactional per backend (postgres: one txn; chroma: best-effort with a mandatory `--dry-run` preview first);
  - writes a per-palace receipt to `<palace_path>/MIGRATIONS.log`.
  - Interrupt it at any point and **both corpora are still readable** — that is the crash-safety guarantee. A *move* (delete-then-write) would brush against incremental-only; we forbid it. The user may later tombstone the originals under their own explicit control (a separate, opt-in step), accepting transient duplicate disk usage in exchange for never destroying data automatically.
- Verify with `mempalace status --partition authority` and `mempalace search --partition authority "test query"` before trusting the seed.

### 5.3 Stale clients

The daemon shim (`palace-daemon/clients/hook.py`) translates an absent `partition=` to the configured default. Stale Claude Code sessions that never learned about partitions keep working unchanged — no protocol break.

---

## 6. Schema / storage implications (postgres + pgvector, design level)

The storage layer is **already ready** — this is a config/routing problem, not a backend rewrite.

- **Collections are tables.** The postgres backend caches collections by `(dsn, palace_id, table_name)` (`backends/postgres.py`, RFC 001). Two partitions = two tables in one database. `get_collection(palace=..., collection_name="mempalace_authority", create=True)` already does the right thing; Phase 1 adds no new backend method.
- **Partition registry.** The set of partitions for a palace is *configuration*, not a new DB object — it lives in `~/.mempalace/config.json` (global) and `mempalace.yaml` (per-project), read at startup. `mempalace status` reflects config ∪ live collection inventory, so a configured-but-not-yet-created partition shows as "declared, 0 drawers" and a live-but-undeclared collection shows as "orphan" (a warning, since it violates the invariant of §4).
- **Metadata parity.** Every partition table carries the same metadata columns (`wing`, `room`, `tags`, jsonb payload) and the same trigram + jsonb indexes, so `wing`/`room`/`tags` filters behave identically. `partitions create` provisions these; it refuses to register a collection that lacks them.
- **Embedder identity is per-collection metadata.** Stored alongside each collection (the backend already tracks it for `EmbedderIdentityMismatchError`). Federation reads it per partition and gates on equality (§3.3).
- **The KG follows the palace, not the partition.** AGE graph (postgres) and SQLite-KG are per-palace, keyed by DSN/graph-name. Both `drawers` and `authority` feed the *same* KG — this is the entity-first win (#202): provenance (which partition a mention came from) becomes an edge/triple attribute, not a separate graph. No KG split in this spec.
- **Closet collection** (`mempalace_closets`) stays the index for `drawers` in Phase 1/2. Whether `authority` earns its own closet is #45 Open Question 3, deferred.

---

## 7. Open questions

1. **Default federation scope (resolved here, flag for review).** §3.1 chooses "no-argument search = all searchable partitions." #45 Q2 left this open and leaned the other way (default partition only, for surprise-avoidance). This spec overrides toward recall. **Confirm with JP** if surprise-avoidance should win instead — it is a product call about whether recall or stability is the higher value when a second partition appears.
2. **Canon edit/supersede semantics.** @kostadis "curate manually" implies revising canon. Append-only chat drawers vs. a revisable authority drawer: does an edit append a new verbatim revision and tombstone the prior, or mutate in place? Verbatim-always governs how *we* store what the user gives us; the user revising *their own* canon is their prerogative — but the supersede mechanics (version chain, which revision search returns) need design. Out of scope; track as a follow-up once the partition surface exists.
3. **Per-partition embedder.** The config *allows* a different embedder per partition, but federation forbids mixing them. Is a non-federating, separately-embedded partition (queried only by name) worth supporting in Phase 1, or deferred to multi-palace? Lean: allow the config field, document the federation cost, do not optimize for it.
4. **`writers` enforcement granularity.** Is the writer→partition wall enforced only at the daemon (network boundary) or also in-process for direct CLI/library use? Lean: enforce at both, since a direct `tool_add_drawer(partition="authority")` from a misconfigured hook should fail the same way a daemon request would.
5. **Upstream naming (#46).** This spec uses `partition`; #76 used `scope`/`collections`; upstream RFC 001 uses `collection`. The fork should pick one name and carry it upstream in #46 once code is working. Lean: `partition`, because it composes upward to the multi-palace `palace.partition` address.

---

## 8. Phased rollout

| Phase | Scope | Data movement | Back-compat | Gate |
|---|---|---|---|---|
| **1 — additive surface** | `partitions` config block; `writers` routing table; `--partition` CLI + `partition` MCP kwarg + `?partition=` daemon param; `partition`/`partitions` read params; federated default across searchable partitions; `searchable` invariant; `mempalace status` shows all partitions; recall regression suite (§3.4). Ships with **one** partition (`drawers`). | None | Bit-for-bit (one partition ⇒ today's behavior) | Recall regression suite green; existing tests pass unchanged |
| **2 — first second partition** | `mempalace partitions create authority`; `mempalace partitions copy --from --to --where` (copy-not-move, dry-run, receipt); README workflow doc; SME `--kind` → `--partition` rename in lockstep (#76). | Opt-in copy, never move | Additive; `authority` joins the federated default automatically (read parity) | Copy is transactional + crash-safe; both corpora readable mid-copy |
| **Deferred** | Multi-palace promotion (#45 §4, gated on different-embedder/host/retention/blast-domain); retrieval weighting of curated rows (Option E, searcher-owned, A/B on our corpus); per-partition closet (#45 OQ3); canon supersede semantics (§7.2). | — | — | A concrete workload the partition layer demonstrably cannot serve |

### Coordination

- **#202 (Reverie's eval)** is the upstream input to this spec; this spec is the implementation answer to its recommendation.
- **#45 / #76** supplied the config/CLI/daemon shapes and the `partition`/`partitions` read-parameter shape; this spec adopts both and resolves their open default-scope question toward recall (§7.1).
- **#46** is the upstream-naming track; defer the upstream Discussion comment until Phase 1 code is working (upstream-comment-timing discipline — deliver, don't promise).
- **SME** (`multipass-structural-memory-eval`) renames `--kind` → `--partition` in lockstep with Phase 2 (#76 §"SME adapter").

[d1018]: https://github.com/MemPalace/mempalace/discussions/1018
