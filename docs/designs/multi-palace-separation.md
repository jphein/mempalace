# Multi-palace separation — design evaluation

**Status:** design, not implemented
**Issue:** [techempower-org/mempalace#45](https://github.com/techempower-org/mempalace/issues/45)
**Upstream origin:** [MemPalace/mempalace discussions#1018](https://github.com/MemPalace/mempalace/discussions/1018) (@kostadis)
**Related design:** [scope/collection filter on search (#76)](https://github.com/techempower-org/mempalace/issues/76)
**Related historical attempt:** P8 corpus partitioning — recovery-collection split, retired 2026-05-05 (see [README §P8](../../README.md#multi-palace-separation--curated-authority-vs-auto-mined-memory))

## TL;DR

Two architectures address the "curated vs auto-mined" problem:

1. **Collection partitioning** — one palace, sibling collections per purpose. Already
   half-built (P8 attempt). Smaller change. Failed once because the recovery side
   never got a search MCP surface; the lesson stands but the architecture doesn't.
2. **Multi-palace** — `palace_path` becomes a named map; processes hold multiple
   palaces simultaneously, each with its own collections, embedder, HNSW segments,
   and routing rules.

**Recommendation: do collection partitioning first, but design it to compose with
multi-palace, not preclude it.** The two are not alternatives. Multi-palace is
collection-partitioning at the *palace* layer; the configuration shapes are
isomorphic. If we design the partition surface to address the read-side parity
lesson from P8 (every named partition gets a search surface, no exceptions),
the same surface upgrades cleanly to multi-palace when a real use case demands it
(e.g. an air-gapped curated palace whose embedder cannot be co-resident with the
chat-mining one). Until then the collection-partition path absorbs @kostadis's
authority-vs-mined separation with strictly less moving infrastructure.

The rest of this doc evaluates that recommendation against the issue's six design
questions, gives concrete config/CLI/API shapes for each layer, and lays out a
migration path from the current single-palace world.

## Background — what's already there

### Single-palace today (May 2026)

`MempalaceConfig.palace_path` is a single string (env > config > default). The MCP
server, CLI, hooks, miner, searcher, and KG all resolve through it. One palace, one
collection (`mempalace_drawers`) — plus `mempalace_closets` for the closet index and
the KG SQLite (or AGE) hanging off the side.

Backend routing already supports multiple palaces *per process* — `BaseBackend.get_collection`
is keyed by `PalaceRef(id, local_path, namespace)`, and `PostgresBackend._collections`
caches per `(dsn, palace.id, table)` tuple. The plumbing is there. What's missing is
the configuration and routing layer above it: nothing in the codebase today says
"this hook writes to palace A, that one writes to palace B."

### The P8 lesson (collection partitioning, 2026-04-25 → 2026-05-05)

The checkpoint-collection split moved Stop-hook checkpoints from `mempalace_drawers`
into a sibling collection `mempalace_session_recovery`. Same backend, same client,
same flock — two named collections inside one palace. It shipped, ran in production
for ~10 days, and was retired.

The reason it failed is the one architectural lesson worth carrying forward:

> The partition was write-side without read-side parity. The recovery collection
> never got a semantic-search MCP surface. Checkpoints became invisible to
> `mempalace_search` and the only read tool on the recovery side was a structured
> `mempalace_session_recovery_read` keyed by session_id. The user-facing question
> "what did I work on last Tuesday?" was unanswerable because the answers were in
> a collection nothing searched.

The codified rule is in [README §P8](../../README.md): *"each new sibling collection
has to earn its own read tool before it gets writes."* This design respects that.

### The substrate (postgres + pgvector + AGE)

Backend selection per RFC 001 is keyed by `(backend_name, palace_id, collection_name)`.
The postgres backend's table-naming convention already lets two collections within
one palace coexist as two tables in one database; two palaces would be either two
databases, two schemas, or two table-name prefixes — all of which the backend
contract supports today. ChromaDB is the same: two `PersistentClient` instances on
two paths, or one client with two collections inside one path. The backend layer
is not the blocker.

The KG is a separate question. SQLite-KG lives at `<palace_path>/knowledge_graph.sqlite3`
so it's already per-palace. AGE-KG lives in postgres and is keyed by the DSN
(plus a graph name), so multi-palace on AGE means either separate graph names or
separate databases. Neither is hard, but the choice matters for cross-palace
queries (do you want to ask "what does my curated palace say about Alice given
what my chat palace recorded?"). Defer this — start with the position that the
KG follows the palace (per-palace KG, no cross-palace queries) and revisit if
the use case appears.

## The six design questions

The issue body lists six questions. The recommendation answers all six on the
collection-partition track first; the multi-palace shape follows in §4.

### Q1 — Multiple `palace_path` values vs one path with named aliases?

**For collection partitioning (recommended first move):** keep `palace_path` a single
string. Add a `palaces` *partition* map keyed by purpose:

```yaml
# ~/.mempalace/config.json (excerpt, in YAML for legibility)
palace_path: /home/jp/.mempalace/palace        # the on-disk root, same as today
collection_name: mempalace_drawers              # the legacy default
partitions:
  drawers:                                      # the existing chat-mined collection
    collection: mempalace_drawers
    embedder: bge-large-en-v1.5
    description: "Auto-mined Claude Code transcripts + project files"
  authority:                                    # the new curated collection
    collection: mempalace_authority
    embedder: bge-large-en-v1.5                 # same embedder, scores are comparable
    description: "Manually curated reference material"
  recovery:                                     # rehabilitated P8 partition (if revived)
    collection: mempalace_session_recovery
    embedder: bge-large-en-v1.5
    description: "Stop-hook checkpoints (audit only)"
    searchable: false                           # opt-out marker — see §2.3
```

A *partition* in this design is a named tuple of `(collection, embedder, description,
searchable, write-permissions)`. The `palace_path` stays singular; partition names
are how callers address parts of it.

This is strictly additive on top of the existing config — `partitions` absent means
"one partition called `drawers` mapping to `collection_name`," which is the current
single-collection world.

**For multi-palace (if/when it becomes necessary):** lift the same `partitions` shape
to the outer layer, naming *palaces* instead of collections. The shapes are
isomorphic; the difference is whether the named map keys palaces or collections.

```yaml
# Multi-palace future — only if collection partitioning falls short
palaces:
  chat:
    palace_path: /home/jp/.mempalace/palace
    partitions:
      drawers:
        collection: mempalace_drawers
        embedder: bge-large-en-v1.5
  authority:
    palace_path: /srv/curated/authority-palace
    partitions:
      drawers:
        collection: mempalace_drawers
        embedder: e5-large-v2                  # different model, OK because different palace
```

The recommended migration path: ship the partition layer first. When (if) a
real palace-level separation requirement lands — different embedder, different
host, different lifecycle, different retention policy — promote the inner
partition map to an outer palace map. The earlier the partition map exists,
the more cheaply the multi-palace shape composes onto it.

**Verdict:** one path + named partitions today; promote to a palace map only if
the partition surface proves insufficient.

### Q2 — Per-hook target

Hooks today flow:

- Stop-hook: `mempal-stop-hook.sh` → `palace-daemon/clients/hook.py` → `disks.jphe.in:8085/silent-save`
- Manual mine: `mempalace mine <dir>` → `miner.run_mine` → `get_collection(...)`

Each writer needs to declare its target partition. The minimal surface:

1. **Hook config** — `mempalace.yaml` (per-project) and the global config both grow a
   `hook_targets` table:

   ```yaml
   hook_targets:
     stop: drawers          # default chat-mining destination
     mine_cli: drawers      # `mempalace mine` writes here unless --partition overrides
     authority_import: authority   # future hook for the curated import flow
   ```

2. **CLI flag** — `mempalace mine /path --partition authority`. `mempalace search
   --partition authority "..."`. `mempalace status --partition authority`.

3. **MCP tool param** — every write tool grows an optional `partition` kwarg:

   ```python
   mempalace_add_drawer(wing="curated", room="references",
                        content="...", partition="authority")
   ```

   Default partition is `drawers` (back-compat). Read tools grow `partition` and
   `partitions` parameters per §3.

The hook config layer is the load-bearing decision. Without it, the Stop-hook
keeps dumping everything into the same partition regardless of CLI gymnastics —
which is the problem we're solving. With it, the user's `mempalace.yaml` says
"Stop-hook writes to `drawers`, my curated CLI writes to `authority`," and the
write router honors that. The shim layer (`palace-daemon/clients/hook.py`)
becomes responsible for passing the partition name through to the daemon, which
is already a documented fork pattern ([sh-shim-strategy.md](../fork-decisions/sh-shim-strategy.md)).

**Verdict:** per-writer target via `hook_targets` in config + `--partition` on
every write CLI + `partition` kwarg on every write MCP tool. Defaults preserve
back-compat.

### Q3 — Search surface

This is the question [#76](https://github.com/techempower-org/mempalace/issues/76)
is also asking. The two designs need to converge — the partition map proposed in Q1
is precisely the "scope" #76 wants. Whichever design lands first should establish
the parameter shape; the other follows.

**Proposal:** every read tool grows two parameters, mutually exclusive:

- `partition: str | None` — query exactly one named partition. Default: the
  configured `default_search_partition` (which itself defaults to `drawers`).
- `partitions: list[str] | None` — query several, results interleaved with RRF
  (the same rank fusion the multi-encoder branch already uses internally).

If neither is set, default behavior is "search the default partition only,"
which matches today's behavior bit-for-bit.

```python
# Single-partition query (default — matches today)
mempalace_search(query="why did we switch to GraphQL")

# Explicit named partition
mempalace_search(query="...", partition="authority")

# Federated across two partitions, RRF-fused
mempalace_search(query="...", partitions=["authority", "drawers"])

# All partitions marked searchable=true
mempalace_search(query="...", partitions="*")
```

The `searchable` flag on each partition (Q1) is the gate that keeps recovery /
audit / KG-derivative partitions out of the default federation. A partition with
`searchable: false` is reachable only when explicitly named (`partition=recovery`,
not `partitions=*`).

**Cross-partition ranking** is the genuine implementation hazard. The recommended
discipline: every partition in a federated query must share an embedder identity
(same model, same revision). The backend already raises `EmbedderIdentityMismatchError`
for this; surface it at the search layer too. RRF over partitions that share an
embedder is the lowest-risk fusion strategy because per-partition distance
distributions stay comparable.

Partitions with *different* embedders cannot federate — query them one at a time
and have the caller combine results client-side. This is the same constraint that
makes the multi-palace promotion costly: different embedders per palace means no
federated search across palaces, only sequential.

**Verdict:** `partition` + `partitions` on every read tool, RRF for federation,
hard fail on embedder identity mismatch. `searchable: false` opts a partition
out of the default federation.

### Q4 — Daemon routing

Today the daemon (`palace-daemon`) is one process, one palace
(`PALACE_DAEMON_URL → http://disks.jphe.in:8085`). It serves `/search`, `/silent-save`,
`/mcp`, etc., all rooted on a single `palace_path` baked into its config at startup.

**For collection partitioning:** the daemon stays one-process-one-palace. The
partition name rides on the request path or as a query parameter:

```
POST /silent-save?partition=drawers       # default
POST /silent-save?partition=authority     # new, opt-in target

POST /search                              # default partition
POST /search?partition=authority          # named partition
POST /search                              # body: {"partitions": ["authority", "drawers"]}
```

The daemon's MCP-over-HTTP proxy passes the partition through to the underlying
MCP tool, which already accepts it (Q2/Q3). No protocol break — clients that
don't pass `partition` see the default-partition behavior.

This composes with the existing daemon-strict semantics from
[techempower-org/mempalace#49](https://github.com/techempower-org/mempalace/issues/49):
when `daemon_strict` is on, writes that name an unknown partition fail loud,
not silent-fall-back.

**For multi-palace (if/when):** options are

1. **One daemon per palace.** Two FastAPI processes on two ports
   (`:8085` for chat, `:8086` for authority). Clients pick the URL. Simple,
   matches the existing one-process-one-palace mental model. Cost: more
   processes, more flock coordination, separate `mine_global_lock` per palace
   (which the substrate already supports per-`palace.id`).
2. **One daemon, multi-palace.** Daemon config grows a `palaces` map matching
   the client-side one. Request path or header names the palace:
   `POST /silent-save?palace=authority&partition=drawers`. Cost: a single
   daemon process becomes the failure domain for both palaces. Saves one
   process; complicates connection pool sizing.

Recommendation if/when this matters: **one daemon per palace.** The blast radius
argument outweighs the resource argument at the scale we operate (two palaces,
not twenty). Two daemons is also the configuration the fork already supports
implicitly — `PALACE_DAEMON_URL` is a single string, so two daemons just means
swapping the URL per partition target in `hook.py`. Same shim file, different
upstream.

**Verdict:** collection partitioning needs no daemon change beyond a query
parameter. Multi-palace promotion picks "one daemon per palace."

### Q5 — Shared embedding model vs separate

**Within a single palace:** every partition shares the embedder by default.
This is what makes federation feasible (Q3) and what `EmbedderIdentityMismatchError`
already enforces in the backend layer. Different embedder per partition is
*technically* legal — the partition config's `embedder` field accepts it — but
federation across partitions with different embedders is forbidden.

Use cases for different-embedder-per-partition within one palace are narrow:
the most plausible is a curated *small* partition where a smaller / faster model
is acceptable. Even then the gain is marginal because the bigger model still has
to be loaded for the other partitions. The recommendation is to *allow* the
config but document the federation cost.

**Across palaces (multi-palace promotion):** no shared-embedder constraint.
This is one of the strongest arguments for promoting partition-to-palace: when
a curated dataset needs a different embedding family (e.g. a code-tuned encoder
for a code-snippet authority palace), it lives in its own palace with its own
embedder, and cross-palace search is per-palace + client-side fusion.

**Verdict:** shared embedder within a palace (enforced for federation, allowed
otherwise). Separate embedder across palaces (which is the point of having
multiple palaces in the first place).

### Q6 — CLI surface

The CLI shape proposed in Q2/Q3:

```bash
# Write — partition flag, default is `drawers`
mempalace mine /path/to/project --partition authority

# Read — same partition flag
mempalace search "alice's wedding" --partition authority
mempalace search "alice's wedding" --partitions authority,drawers
mempalace search "alice's wedding"                       # default partition

# Status — list partitions
mempalace status                                          # show every partition
mempalace status --partition authority                    # narrow to one
mempalace status --json                                   # machine-readable

# Migration aids
mempalace partitions list                                 # enumerate configured
mempalace partitions create authority                     # initialize from config
mempalace partitions move --from drawers --to authority \
    --where 'wing == "curated"'                          # bulk re-route
```

The `mempalace partitions` subcommand mirrors the existing `mempalace init` and
`mempalace repair` shapes. Bulk move uses the same `where` filter syntax the
backend already accepts for `get` and `delete` (RFC 001).

For multi-palace promotion the CLI grows `--palace NAME` at the same level as
`--partition`:

```bash
mempalace --palace authority search "..."
mempalace --palace authority mine /path
mempalace palaces list
mempalace palaces add authority --path /srv/curated/...
```

The existing `--palace PATH` flag (which takes a filesystem path, not a name)
gets a fork-safe alias: `--palace-path PATH` for the literal-path form, freeing
`--palace NAME` for the named-palace form. This is a back-compat hazard worth
budgeting for — see §5 migration.

**Verdict:** `--partition` and `--partitions` on every read/write subcommand,
`mempalace partitions` subcommand family for management. Defer `--palace NAME`
until multi-palace promotion is on the table; when it lands, alias the legacy
`--palace PATH` to `--palace-path PATH`.

## Daemon routing diagrams

### Today

```
┌──────────────┐         ┌──────────────────────────────┐
│ Claude Code  │         │ palace-daemon (disks:8085)   │
│              │ POST    │                              │
│ Stop-hook    ├────────▶│  /silent-save                │
│ shell shim   │         │       │                      │
│              │         │       ▼                      │
│              │         │  MempalaceConfig.palace_path │
│              │         │       │                      │
│              │         │       ▼                      │
└──────────────┘         │  ChromaDB(palace_path)       │
                         │       │                      │
                         │       ▼                      │
                         │  mempalace_drawers           │
                         └──────────────────────────────┘
```

### Collection partitioning (recommended first step)

```
┌──────────────┐         ┌─────────────────────────────────────────┐
│ Claude Code  │         │ palace-daemon (disks:8085)              │
│              │ POST    │                                         │
│ Stop-hook    ├────────▶│  /silent-save?partition=drawers         │
│   ↑ writes   │         │       │                                 │
│   "drawers"  │         │       ▼                                 │
│              │         │  hook_targets[stop] = drawers           │
│ Curated CLI  │         │       │                                 │
│ mempalace    │ POST    │       ▼                                 │
│   add-drawer ├────────▶│  /silent-save?partition=authority       │
│ --partition  │         │  hook_targets[authority_import]         │
│ authority    │         │       │                                 │
│              │         │       ▼                                 │
│              │         │  ChromaDB(palace_path)                  │
└──────────────┘         │       │                                 │
                         │       ├─▶ mempalace_drawers (chat)      │
                         │       ├─▶ mempalace_authority (curated) │
                         │       └─▶ mempalace_session_recovery    │
                         │                                         │
                         │  /search?partition=...                  │
                         │     → fan-out across partitions[*]      │
                         │     → RRF fuse if multiple              │
                         └─────────────────────────────────────────┘
```

### Multi-palace (only if collection partitioning is insufficient)

```
┌──────────────┐         ┌────────────────────────────────┐
│ Claude Code  │   POST  │ palace-daemon-chat (:8085)     │
│ Stop-hook    ├────────▶│  palace_path: /home/jp/.../    │
│              │         │  partitions: { drawers }       │
│              │         └────────────────────────────────┘
│              │
│ Curated CLI  │   POST  ┌────────────────────────────────┐
│ mempalace    ├────────▶│ palace-daemon-authority (:8086)│
│  --palace    │         │  palace_path: /srv/curated/... │
│  authority   │         │  partitions: { drawers }       │
│              │         └────────────────────────────────┘
└──────────────┘
```

The Stop-hook shim grows a `PALACE_DAEMON_URL_BY_TARGET` env or config table:

```bash
# In palace-daemon/clients/hook.py
PALACE_DAEMON_URL_BY_TARGET = {
    "chat":      "http://disks.jphe.in:8085",
    "authority": "http://disks.jphe.in:8086",
}
```

This is a small enough change that the multi-palace promotion stays cheap *if*
the partition-first work has already standardized the writer-names-its-target
discipline.

## Migration path

The transition lands in two phases:

**Phase 1 — additive partition surface, single default partition.** Ship the
`partitions` config block, `--partition` CLI flag, `partition` MCP kwarg, and
the daemon's `?partition=...` query parameter. Defaults preserve current
behavior bit-for-bit: a `mempalace.yaml` without `partitions` resolves to
`partitions: { drawers: { collection: mempalace_drawers } }`, every read tool
defaults to `partition=drawers`, every write tool defaults to whichever
`hook_targets` entry applies (also `drawers`). Existing palaces don't need
migration; the partition layer is purely a configuration wrapper.

This phase **does not** ship the `authority` partition itself — only the
machinery for users to declare one. No data moves. Existing tests pass with
no changes.

**Phase 2 — first second partition (authority) + bulk-move CLI.** Ship
`mempalace partitions create authority` (bootstraps the collection) and
`mempalace partitions move --from --to --where` (the bulk re-route).
Document the recommended workflow in the README:

1. `mempalace partitions create authority`
2. Edit `hook_targets` to declare which writers go where
3. (Optional) `mempalace partitions move --from drawers --to authority --where 'topic == "reference"'`
   to seed the authority partition from existing curated content already in
   `drawers`
4. Verify with `mempalace status --partition authority` and `mempalace search
   --partition authority "test query"`

Multi-palace promotion is a separate phase, deferred until the partition surface
fails to address a concrete need. The pre-condition for promotion: at least one
of (different embedder, different host, different retention, different blast
domain) is required by a real workload.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Re-running the P8 mistake: write-side without read-side parity. | Federation default (`partitions=*` on the search MCP tool) means every partition is reachable from the canonical search surface from day one, unless explicitly marked `searchable: false`. The `mempalace status` CLI lists every partition, searchable or not, so silence-mode (a write-only partition no caller ever reads) is impossible by inspection. |
| Drift between `partition` (collection-partitioning shape) and #76's `scope` parameter. | Pick one name and ship both designs against it. Recommend `partition` because it composes upward to the eventual multi-palace shape (`palace.partition`). Coordinate with Iris on #76 before either lands. |
| `--palace PATH` (legacy) vs `--palace NAME` (multi-palace future) collision. | Defer multi-palace CLI until promotion is justified. When promoted, alias `--palace-path PATH` for the legacy literal-path form and free `--palace NAME` for the named form. Single release with both spellings, then deprecate the literal form. |
| Cross-embedder federation surfaces silently bad results. | Backend already raises `EmbedderIdentityMismatchError`. Promote it to the MCP search layer so a federated query across embedder-mismatched partitions errors at the request, not in the rankings. |
| Migration of existing palaces: `mempalace_drawers` semantics shift. | Phase 1 ships with no data movement and no default-behavior change. Phase 2's `partitions move` CLI is opt-in, transactional per backend (postgres: one txn; chroma: best-effort with a dry-run flag), and writes a per-palace migration receipt to `<palace_path>/MIGRATIONS.log`. |
| Daemon protocol break for stale Claude Code sessions. | The shim layer ([`palace-daemon/clients/hook.py`](../fork-decisions/sh-shim-strategy.md)) translates absent `partition=` to the configured default. Stale sessions keep working unchanged. |

## Open questions for review

1. **Partition name format.** Issue body uses `--palace=authority`. This doc
   uses `--partition=authority` to leave `--palace` for the eventual multi-palace
   promotion. Acceptable? If not, propose an alternate reservation strategy for
   the multi-palace flag.
2. **Default `partitions=*` in federation.** Should a no-argument `mempalace_search`
   query *all* searchable partitions, or only the configured default? The doc
   recommends the latter (back-compat); the former is more discoverable but
   surprises existing callers when a second partition arrives.
3. **Closet collection routing.** The `mempalace_closets` collection is the
   closet index for the main `mempalace_drawers`. When a second searchable
   partition exists, does it get its own closet? Recommend yes (the closet
   contract is per-corpus), but the implementation cost is real. Tracking
   separately from this design.
4. **Coordination with #76.** Iris is working on the scope/collection filter
   design concurrently. The federation shape proposed here (`partition` /
   `partitions` / RRF / embedder identity check) should be the same surface
   that lands for #76. Pre-coordinate on parameter naming before either
   PR opens.

## Decision

Pursue **collection partitioning first** with the partition surface designed
to compose upward to multi-palace if/when promotion is justified. Carry the
P8 lesson explicitly: every named partition gets a read surface from day one.
Coordinate with [#76](https://github.com/techempower-org/mempalace/issues/76)
on parameter naming before either design lands.

The full rollout is two phases (additive surface, then first-second-partition).
Multi-palace promotion is a separate, deferred decision, gated on a workload
that the partition layer demonstrably cannot serve.
