# Multi-palace separation — curated authority vs auto-mined memory (2026-05-26)

Research agent: Reverie (team kg-backfill-stabilize). This is an evaluation
document for [techempower-org/mempalace#202][i202]. It frames the
curated-authority-vs-auto-mined problem, weighs the separation options against
the fork's design principles, and lands a concrete recommendation with trade-offs.

It is a companion to — not a replacement for — the existing engineering design
at [`docs/designs/multi-palace-separation.md`](../designs/multi-palace-separation.md),
which works out the config/CLI/daemon shapes in detail. Where that doc asks *how
do we build it*, this one asks *should the separation exist at all, and at which
layer, given what MemPalace promises*. The two agree on the recommendation; this
doc supplies the principle-level justification the design doc assumes.

## TL;DR

1. **The need is real and user-originated.** [@kostadis (upstream #1018)][d1018]
   wants a hand-curated "canon" palace alongside the auto-mined chat palace, with
   no auto-hooks writing into the curated side. This is a legitimate workload that
   the single-collection default cannot serve cleanly today.
2. **Separation does not violate any design principle — but one *implementation*
   of it would.** Verbatim-always, entity-first, incremental-only, and local-first
   all survive separation intact. The only principle at risk is the unwritten one
   the fork learned the hard way: **write-side without read-side parity = invisible
   data** (the P8 recovery-collection retirement, see [#169][i169]).
3. **Recommend collection-partitioning (one palace, sibling collections by
   purpose), not multi-palace (multiple `palace_path` roots).** Partitioning
   absorbs the kostadis use case with strictly less moving infrastructure, composes
   *upward* to multi-palace if a harder requirement ever lands, and keeps a single
   verbatim store, a single embedder identity, and a single entity namespace —
   which is exactly what entity-first wants.
4. **Make read-surface parity a hard precondition, encoded in the schema.** Every
   named partition must declare `searchable`, must appear in `mempalace status`,
   and must be reachable from the canonical search surface from the day it accepts
   its first write. This is the codified P8 lesson and the deciding constraint
   between the options.
5. **Defer multi-palace until a concrete trigger.** A second `palace_path` is only
   justified when at least one of *{different embedder family, different host /
   air-gap, different retention policy, different blast domain}* is required by a
   real workload. None is required by the curated-canon use case.

## The problem

MemPalace's default setup is one palace, one collection (`mempalace_drawers`),
fed by auto-hooks that mine every Claude Code transcript. That is the right
default for the project's core promise — *completeness is the feature* — but it
collides with a second, equally legitimate use:

> "I use it to keep track of a massively complex canon ... the default setup
> doesn't allow me to have a chat mempalace and a separate mempalace that I can
> curate manually with no auto-hooks." — [@kostadis, upstream #1018][d1018]

The collision has two distinct edges:

- **Write isolation.** The curated store must not receive auto-mined transcript
  fragments. Hooks write to chat; the human writes to canon. A `purpose` that is
  only a query-time filter does not give this — the hook still has to *choose* a
  target at write time, so the target must be a first-class write address.
- **Retrieval weighting.** Once the two corpora coexist, a curated fact ("the
  canon says X") should be distinguishable from a raw transcript fragment that
  merely mentions X in passing. Separation is the precondition for ever letting
  retrieval prefer the authority store — though weighting itself is out of scope
  here (it belongs to the searcher, post-separation).

The open architectural question, restated from #202: does the split live at the
**collection** level (multiple pgvector/Chroma collections inside one palace) or
at the **metadata** level (a `purpose` tag filtered at query time)? And the
larger framing from #169/#46: is the right unit a *collection* or a whole
*palace*?

## Evaluation against the design principles

The fork's principles ([CLAUDE.md][claudemd]) are non-negotiable. Each option
must clear all of them. This is where the option space actually narrows.

### Verbatim-always

Separation is *orthogonal* to verbatim storage — neither option summarizes or
paraphrases. The curated store is still the user's exact words; it is simply a
different write address. **No principle tension.**

One subtlety: a curated "authority" corpus invites the user to *edit* canon over
time (correct a fact, supersede a document). Verbatim-always governs how *we*
store what the user gives us, not whether the user may revise their own canon.
Editing is the user's prerogative; we still store each revision verbatim. The
incremental-only principle (below) constrains how that revision lands.

### Entity-first

This principle is the strongest argument *for* collection-partitioning and
*against* multi-palace as the first move. Entity-first means "everything keyed by
real names, disambiguated by DOB/ID/context; people matter more than topics."

- **One palace, sibling collections:** entities live in one namespace. The KG
  (AGE graph / SQLite) sees both corpora. The question "what does my curated canon
  say about Alice, given what my chat history recorded?" is answerable *because*
  the entity `Alice` resolves to one node regardless of which collection a mention
  came from. Provenance (which collection) becomes an edge attribute, not a
  partition wall.
- **Multiple palaces:** each palace gets its own KG keyed by DSN/graph-name (per
  the design doc §"The substrate"). Cross-palace entity resolution is *not free* —
  `Alice` in the canon palace and `Alice` in the chat palace are different nodes
  until something reconciles them. That reconciliation is exactly the hard
  entity-disambiguation problem the project already cares about, now multiplied by
  the palace count.

Entity-first therefore *prefers a single entity namespace*, which collection
partitioning preserves and multi-palace fractures. **Decisive in favor of
partitioning.**

### Incremental-only

"Append-only ingest after initial build. Never destroy existing data to rebuild.
A crash mid-operation must leave the existing palace untouched."

- Adding a sibling collection is purely additive — the existing `mempalace_drawers`
  is untouched. A crash while creating `mempalace_authority` leaves the chat
  corpus intact. **Clean fit.**
- The hazard is the *seed migration* — bulk-moving already-curated content out of
  `drawers` into `authority` (design doc Phase 2, `partitions move --where`). A
  *move* is delete-then-write, which brushes against incremental-only. Mitigation:
  make the seed a *copy* (write to authority, leave drawers untouched, optionally
  tombstone later under user control), and make it transactional per backend with
  a dry-run and a migration receipt. This keeps the crash-safety guarantee:
  interrupt the copy and both corpora are still readable.
- Multi-palace has the same migration hazard plus a cross-process one (two daemons,
  two flocks), so it is strictly worse on this axis.

### Local-first, zero external API by default

Both options are pure-local. No embedder, extraction, or storage decision here
reaches off-machine. The *only* place this principle bites is the temptation to
use a different embedder for the curated corpus (design doc Q5): any embedder must
still be a localhost runtime (Ollama / llama.cpp / vLLM / etc.). Both options
honor this; multi-palace makes the different-embedder case *easier to express*,
which is its one genuine advantage (see below). **No tension, mild relevance.**

### Performance budgets (hooks <500ms, startup <100ms)

- **Partitioning:** the write path gains one config lookup (which partition?) and
  the read path gains optional fan-out + RRF when federating. Single-partition
  reads (the default) are bit-for-bit today's cost. Federation across N partitions
  is N searches + a fuse, paid only when explicitly requested. Within budget.
- **Multi-palace:** federated read means querying N daemons/processes, which is
  network round-trips, not in-process collection switches. Higher tail latency,
  and the one-daemon-per-palace recommendation multiplies the cold-start surface.
  Worse, though still acceptable at the two-palace scale.

### The unwritten principle: read-surface parity

Not in the principles list, but learned in production and now codified in
[#169][i169]: **a write-side partition without a read-side surface is invisible
data.** The P8 recovery-collection split (Apr 25 → May 5 2026) moved Stop-hook
checkpoints to a sibling collection that `mempalace_search` never queried; "what
did I work on last Tuesday?" became unanswerable. The split was retired.

This is the single most important constraint for #202, and it is *option-neutral*
— both partitioning and multi-palace can re-commit the mistake. The mitigation is
to encode parity in the schema, not in discipline:

- Every partition/palace declares `searchable: true|false`.
- A `searchable: true` partition is reachable from the canonical search surface
  from its first write (federated by default, or explicitly addressable).
- A `searchable: false` partition (audit, recovery, KG-derivative) is reachable
  only when explicitly named, and *still appears in `mempalace status`* so a
  write-only-never-read partition is impossible to create by accident — it shows
  up the moment you inspect.

## The option space

Five shapes, narrowing under the principles above.

### Option A — metadata `purpose` tag, query-time filter

One collection, one embedder. Every drawer carries `purpose: chat|authority`.
Reads filter on it.

- **Pro:** smallest change; no new collection; no migration.
- **Con (write isolation):** the auto-hook still writes into the shared collection
  and *tags* its rows. There is no physical wall — a bug that drops the tag dumps
  transcript noise into the curated namespace. The kostadis requirement is "no
  auto-hooks in my curated palace," and a shared collection cannot give a hard
  guarantee of that, only a soft filter.
- **Con (tuning):** #202 names this directly — metadata filtering "doesn't allow
  per-purpose index tuning." One HNSW index serves both corpora; you cannot give
  the small curated set a tighter index or the large chat set a faster-but-looser
  one.
- **Verdict:** insufficient. Good enough for *weighting* experiments, not for the
  *isolation* the use case demands.

### Option B — collection partitioning (sibling collections, one palace) ← recommended

One `palace_path`, named sibling collections (`mempalace_drawers`,
`mempalace_authority`), each a `(collection, embedder, description, searchable,
write-permissions)` tuple. This is the design doc's recommended first move.

- **Pro (isolation):** physical write wall — the hook writes to `drawers`, the
  curated CLI writes to `authority`. A misrouted write fails loud under
  `daemon_strict` rather than contaminating.
- **Pro (entity-first):** single entity namespace, single KG. Provenance is an
  attribute, not a wall (see Entity-first above).
- **Pro (tuning):** each collection can carry its own HNSW parameters; the curated
  set can be tuned independently of the chat set.
- **Pro (composability):** the partition config shape is *isomorphic* to the
  eventual multi-palace shape — promote the inner partition map to an outer palace
  map if a harder requirement lands. Building partitioning first does not foreclose
  multi-palace; it pre-pays for it.
- **Con:** federation requires a shared embedder identity (the backend already
  raises `EmbedderIdentityMismatchError`). Cross-embedder partitions cannot RRF —
  acceptable because the curated and chat corpora *should* share an embedder for
  comparable scores anyway.
- **Verdict:** clears every principle, serves the use case, smallest change that
  gives a hard isolation guarantee.

### Option C — multi-palace (multiple `palace_path` roots)

Two palaces, each with its own collections, embedder, HNSW segments, KG, and
routing.

- **Pro:** the *only* option that allows a genuinely different embedder family per
  corpus (e.g. a code-tuned encoder for a code-snippet canon) and a different
  host / air-gap / retention policy per corpus.
- **Con (entity-first):** fractures the entity namespace and the KG (see above).
  Cross-palace entity resolution is unsolved work.
- **Con (cost):** two daemons or one multi-palace daemon, more flock coordination,
  network round-trips for federation, doubled cold-start.
- **Verdict:** correct *eventually*, for a workload the curated-canon case does not
  present. Defer, but design Option B so this composes onto it.

### Option D — separate read tools per corpus, no federation

Give each corpus its own MCP read tool (`mempalace_search`,
`mempalace_search_authority`), no unified search.

- **Con:** this *is* the P8 trap re-dressed — it pushes the "which corpus do I
  search?" decision onto every caller and makes "search everything" impossible
  without N tool calls. Discoverability dies. **Rejected.**

### Option E — single unified store, retrieval-time re-ranking only

No separation at all; teach the searcher to recognize and up-weight curated rows.

- **Con:** does not give write isolation (the hook still writes canon-adjacent
  noise into the same place) and depends on a classifier that does not exist.
  Solves the weaker half (weighting) and ignores the stronger half (isolation).
  **Rejected as a standalone answer**, though it is the natural *follow-on* to
  Option B once the corpora are physically separated.

## Recommendation

**Adopt Option B — collection partitioning — as the implementation of #202, with
read-surface parity encoded in the partition schema.** Concretely:

1. **One palace, sibling collections by purpose.** Ship the additive `partitions`
   config block (design doc Q1, Phase 1) with `drawers` as the back-compat default.
   No data moves; absent config resolves to today's single-collection behavior
   bit-for-bit.
2. **Write address is first-class.** The Stop-hook writes to `drawers`; a curated
   import path writes to `authority`. Routing via `hook_targets` + a `--partition`
   CLI flag + a `partition` MCP kwarg (design doc Q2). Under `daemon_strict`, an
   unknown partition fails loud.
3. **Read-surface parity is a schema invariant, not a guideline.** Every partition
   declares `searchable`; searchable partitions are federated from the canonical
   search surface from their first write; *all* partitions appear in
   `mempalace status`. This is the deciding constraint and the codified P8 lesson
   ([#169][i169]).
4. **Shared embedder within the palace.** Keeps federation scores comparable and
   the entity namespace single. Different-embedder-per-corpus is the trigger that
   *promotes* to multi-palace — it is not a within-palace feature.
5. **Defer Option C (multi-palace).** Gate promotion on a concrete workload
   requiring a different embedder family, host/air-gap, retention, or blast domain.
   The partition surface is designed to lift to a palace map when that day comes.
6. **Retrieval weighting (Option E) is a post-separation follow-on**, owned by the
   searcher, tracked separately. Separation is its precondition; do not couple them.

### Why this and not the alternatives

| Axis | A (metadata tag) | **B (partition)** | C (multi-palace) |
|---|---|---|---|
| Write isolation | soft (filter) | **hard (wall)** | hard (wall) |
| Entity-first / single KG | yes | **yes** | no (fractured) |
| Per-purpose index tuning | no | **yes** | yes |
| Incremental-only fit | clean | **clean (copy-seed)** | clean + cross-proc hazard |
| Federated search cost | trivial | **in-proc fan-out + RRF** | network round-trips |
| Different embedder per corpus | no | no | yes |
| Infra delta | none | **one config block** | daemons + KG split |
| Composes upward | n/a | **to C** | terminal |

Option B is the only row that clears write isolation *and* entity-first *and*
per-purpose tuning at the lowest infra cost, while leaving the door open to C.

### Trade-offs accepted

- **No different-embedder-per-corpus until multi-palace.** Accepted: the curated
  and chat corpora should share an embedder anyway for comparable scores. The day
  a code-tuned canon needs its own encoder is the day Option C is justified.
- **Federation is in-process fan-out + RRF, bounded by shared embedder identity.**
  Accepted: enforced by the existing `EmbedderIdentityMismatchError`, surfaced at
  the search layer so a mismatched federated query errors at the request, not in
  the rankings.
- **Seed migration is a copy, not a move, to honor incremental-only.** Accepted:
  costs transient disk for duplicated curated rows until the user tombstones the
  originals under their own control.

## Coordination

- **[#169][i169] (corpus partitioning P8)** is the implementation issue this
  evaluation feeds. #202 is "should we and at which layer"; #169 is "build the
  partition surface with read parity." This doc's recommendation is the input to
  #169's Phase 1.
- **[#46][i46] (RFC 001 follow-up, naming the multi-collection pattern upstream)**
  is the upstream-coordination track. The recommendation here — multi-collection
  with read-surface parity as a precondition — is exactly the pattern #46 wants
  named in RFC 001. Per the fork's upstream-comment-timing discipline, the
  upstream Discussion for #46 should wait until the fork has working partition
  code, not just this doc.
- **Scope/collection filter ([#76][i76], closed)** already established the
  `partition` / `partitions` parameter shape this doc assumes for the read surface.

## Limits of this evaluation

- **Retrieval weighting is asserted, not measured.** The claim that separating
  corpora *enables* preferring curated facts is structurally true, but the actual
  weighting function (and whether it beats a flat unified ranking on the fork's own
  corpus) is unmeasured. Per the fork's "test retrieval against our corpus"
  discipline, that A/B belongs to the searcher work, after separation lands.
- **The kostadis canon-editing pattern is under-specified.** "Curate manually"
  implies revising canon over time; the supersede/version semantics for a curated
  drawer (vs append-only chat drawers) are sketched here, not designed. Worth a
  follow-up once the partition surface exists.
- **Cross-partition closet routing is open** (design doc Open Question 3): does the
  authority partition get its own closet index? Tracked there, not resolved here.

[i202]: https://github.com/techempower-org/mempalace/issues/202
[i169]: https://github.com/techempower-org/mempalace/issues/169
[i46]: https://github.com/techempower-org/mempalace/issues/46
[i76]: https://github.com/techempower-org/mempalace/issues/76
[d1018]: https://github.com/MemPalace/mempalace/discussions/1018
[claudemd]: ../../CLAUDE.md
