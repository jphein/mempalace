# `mempalace.cli`

Source: [`mempalace/cli.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/cli.py)

MemPalace — Give your AI a memory. No API key required.

Three ways to ingest:
  Projects:      mempalace mine ~/projects/my_app                  (code, docs, notes)
  Conversations: mempalace mine &lt;convo-dir> --mode convos          (Claude Code, Claude.ai, ChatGPT, Slack exports)
  Documents:     mempalace mine &lt;docs-dir> --mode extract          (PDF, DOCX, PPTX, XLSX, RTF, EPUB — requires mempalace[extract])

Same palace. Same search. Different ingest strategies.

Commands:
    mempalace init &lt;dir>                  Detect rooms from folder structure
    mempalace split &lt;dir>                 Split concatenated mega-files into per-session files
    mempalace mine &lt;dir>                  Mine project files (default)
    mempalace mine &lt;dir> --mode convos    Mine conversation exports
    mempalace mine &lt;dir> --mode extract   Mine binary office documents (PDF/DOCX/etc.)
    mempalace search "query"              Find anything, exact words
    mempalace mcp                         Show MCP setup command
    mempalace wake-up                     Show L0 + L1 wake-up context
    mempalace wake-up --wing my_app       Wake-up for a specific project
    mempalace status                      Show what's been filed
    mempalace mined                       List mined source files grouped by wing
    mempalace purge --source-file &lt;path>  Remove drawers mined from a specific file

Examples:
    mempalace init ~/projects/my_app
    mempalace mine ~/projects/my_app
    mempalace mine ~/.claude/projects/-Users-you-Projects-my_app --mode convos --wing my_app
    mempalace search "why did we switch to GraphQL"
    mempalace search "pricing discussion" --wing my_app --room costs

## Classes

### `class DaemonError(RuntimeError)`

Raised when a daemon HTTP call fails or returns a JSON-RPC error.

## Functions

### `cmd_init`

```python
def cmd_init(args)
```

### `cmd_mine`

```python
def cmd_mine(args)
```

### `cmd_sweep`

```python
def cmd_sweep(args)
```

Sweep a transcript file or directory.

The sweeper deduplicates against its own prior writes via
deterministic drawer IDs + a timestamp cursor. It does NOT currently
coordinate with the file-level miners (miner.py / convo_miner.py) —
those produce char-chunked drawers without compatible message
metadata, so running both miners may store overlapping content under
different IDs.

### `cmd_sync`

```python
def cmd_sync(args)
```

Prune drawers whose source files are gitignored, deleted, or moved (#1252).

### `cmd_daemon`

```python
def cmd_daemon(args)
```

### `cmd_search`

```python
def cmd_search(args)
```

### `cmd_list`

```python
def cmd_list(args)
```

Fast direct-to-daemon drawer browser (issue #191).

Pure metadata listing — wraps ``GET /list?wing=&room=&limit=&offset=``
on the palace daemon. Output formats: ``table`` (default), ``compact``,
``full``, ``json``. Daemon unreachable → stderr error + exit 1.

### `cmd_move`

```python
def cmd_move(args)
```

Fast direct-to-daemon single-drawer relocation (issue #191).

Wraps ``PATCH /memory/&#123;drawer_id}`` with a body carrying only the
supplied ``wing`` / ``room`` keys. At least one is required — an empty
PATCH is an ambiguous no-op the daemon would 400, so we refuse it
client-side with a clear message. No ``--content`` flag exists by
design: verbatim-always means the human CLI never edits drawer text.
Daemon unreachable / 404 / 401 / 403 → exit 1 (sibling parity with
cmd_list / cmd_graph / cmd_cypher / cmd_stats); inner-error envelope
(daemon reachable but the move failed) → exit 2.

### `cmd_bulk_move`

```python
def cmd_bulk_move(args)
```

Bulk drawer relocation by source wing/room (issue #191).

The multi-drawer complement to ``move``. Selects drawers via
``GET /list`` (offset-paginated) and PATCHes each match to the target
wing/room. Dry-run by default; ``--apply`` mutates (TTY prompt unless
``--yes``; refuses unattended without ``--yes``). Verbatim-always:
metadata only, no ``--content`` flag. Daemon unreachable / 404 / 401 /
403 during listing → exit 1; selection/target missing or any PATCH
failure → exit 2.

### `cmd_graph`

```python
def cmd_graph(args)
```

Fast direct-to-daemon KG + palace structural snapshot (issue #191).

Pure read — wraps ``GET /graph?limit=`` on the palace daemon, which
returns pre-aggregated wing/room/tunnel counts plus a KG slice
(top-N entities, sample RELATION/MENTIONS triples, global kg_stats).
Output formats: ``table`` (default summary), ``full`` (every wing,
every sampled triple, no truncation), ``json`` (pass-through shape).
Daemon unreachable → stderr error + exit 1; inner-error payload → exit 2.

### `cmd_cypher`

```python
def cmd_cypher(args)
```

Run a read-only Cypher query against the AGE knowledge graph (issue #191).

Wraps the daemon's ``POST /cypher``, which executes inside a
``READ ONLY`` postgres transaction (write verbs fail with HTTP 403,
SQLSTATE 25006). Output formats: ``table`` (aligned columns),
``json`` (pass-through), ``csv`` (pipe-friendly). The optional
``--limit`` is advisory — the daemon's own statement_timeout is the
real ceiling.

Daemon unreachable → stderr error + exit 1; 403 read-only write
attempt → friendly hint + exit 2; inner-error payload → exit 2.

### `cmd_wakeup`

```python
def cmd_wakeup(args)
```

Show L0 (identity) + L1 (essential story) — the wake-up context.

Daemon-routes when ``_daemon_strict()`` is on and the user didn't
pass ``--palace`` (#285). The daemon-native ``mempalace_wakeup``
tool (palace-daemon #96) returns ``&#123;text, tokens, wing}``; we
print in the same shape as the local-path fallback below.

### `cmd_split`

```python
def cmd_split(args)
```

Split concatenated transcript mega-files into per-session files.

### `cmd_export`

```python
def cmd_export(args)
```

### `cmd_migrate`

```python
def cmd_migrate(args)
```

Migrate palace from a different ChromaDB version.

### `cmd_migrate_to_postgres`

```python
def cmd_migrate_to_postgres(args)
```

Migrate a ChromaDB palace to Postgres (pgvector + AGE).

Different from `cmd_migrate` (which handles intra-ChromaDB version
upgrades). This one moves the entire substrate. See
`mempalace/migrate_to_postgres.py` for the 7-phase pipeline.

### `cmd_rooms`

```python
def cmd_rooms(args)
```

Manage the canonical room set (mempalace_canonical_rooms table).

hybrid-search-taxonomy follow-up. The FK constraint on mempalace_drawers.room
means this CLI is the supported way to add/rename/remove canonical
rooms without breaking the DB. ON UPDATE CASCADE on the FK makes
renames safe (all drawers auto-update); removes fail if any drawer
still in the target room.

Daemon-routes when ``_daemon_strict()`` is on (#285). palace-daemon
PR #96 added the four ``mempalace_rooms_&#123;list,add,rename,remove}``
tools that wrap the same SQL the local path runs. When daemon-strict
is off, falls back to direct postgres via ``MEMPALACE_POSTGRES_DSN``.

### `cmd_purge`

```python
def cmd_purge(args)
```

Delete drawers by wing and/or room.

Uses ``collection.delete(where=...)`` — chromadb's filter-delete path
doesn't go through ``updatePoint`` / ``repairConnectionsForUpdate``,
which is the upsert-only race from #521 that an earlier draft of this
command tried to side-step with a nuke-and-rebuild. The simpler path
works without losing drawers if the process is interrupted, without
re-embedding the survivors under a default model, and without
bypassing the backend abstraction.

``--room`` without ``--wing`` purges that room across ALL wings.
Not idempotent — running purge twice on the same criteria prints
"No drawers found" the second time.

### `cmd_prune`

```python
def cmd_prune(args)
```

Delete drawers older than ``--stale-days N`` (dry-run by default).

Age is the span between a drawer's ``filed_at`` timestamp and now. Unlike
``purge``'s metadata-equality filter, the staleness predicate is a string
timestamp that chromadb ``where=`` can't range-compare reliably, so we
fetch candidate metadata and decide age in Python (``mempalace.recency``),
then delete by explicit id list.

Safety: this is the only command that destroys data on a *time* predicate
rather than an explicit selection, so it is **dry-run by default**. Nothing
is deleted unless ``--confirm`` is passed. A drawer with no parseable
``filed_at`` is treated as ageless and is **never** pruned — we never
delete a drawer we can't date.

### `cmd_rename_wing`

```python
def cmd_rename_wing(args)
```

### `cmd_replay`

```python
def cmd_replay(args)
```

Drain ``~/.mempalace/pending/*.jsonl`` by re-issuing each request to the daemon.

Pending requests accumulate when the Stop / PreCompact hooks fire while
the daemon (or its backend) is unreachable — see the 2026-05-21
power-resilience design. Drain semantics:

* Each line is one ``&#123;"dir", "wing", "mode", "ts"}`` mine request.
* On 2xx daemon response the line is consumed; on failure the line
  stays in the file for the next attempt.
* Duplicate ``(dir, wing, mode)`` tuples are deduped before transmit
  so a long outage doesn't replay the same target N times.

### `cmd_migrate_wings`

```python
def cmd_migrate_wings(args)
```

Normalize legacy wing names (strip leading/trailing separators).

### `cmd_status`

```python
def cmd_status(args)
```

### `cmd_mined`

```python
def cmd_mined(args)
```

List mined source files grouped by wing.

Companion to ``status`` (which groups by wing × room) — answers "which
files have I mined into this wing?" so an operator can pick targets
for ``mempalace purge --source-file &lt;path>``.

Skips drawers without a ``source_file`` metadata key (typically
diary entries, kg drawers, manually-added entries).

Daemon-routes when ``_daemon_strict()`` is on and ``--palace`` was
not given (#285). palace-daemon's ``mempalace_mined`` tool (PR #96)
returns the same ``&#123;sources_by_wing, wing_filter, total_wings,
total_sources}`` shape the JSON path emits locally.

### `cmd_stats`

```python
def cmd_stats(args)
```

Fast direct-to-daemon palace analytics CLI (#191).

Wraps ``GET /stats`` on the palace daemon, which returns a unified
envelope of three blocks: ``kg`` (entities, triples, relationship
types), ``graph`` (rooms, tunnels, edges), ``status`` (drawer counts,
wings, rooms, protocol/AAAK text). Replaces the older multi-RPC
fan-out — one network hit instead of four. ``--section`` narrows the
table output to a single block; json mode always passes through the
whole envelope so jq pipelines see the daemon contract unchanged.
``--tags`` still triggers an extra ``mempalace_list_tags`` MCP call
because /stats doesn't include the tag breakdown (tag counts can be
100K+ entries and don't belong in the fast-path summary). Daemon
unreachable → exit 1 (matches sibling cmd_list/cmd_graph/cmd_cypher);
inner-error envelope → exit 2.

### `cmd_tags`

```python
def cmd_tags(args)
```

Fast direct-to-daemon tag inventory (slice of #191).

Calls the daemon's ``mempalace_list_tags`` MCP tool — already a
first-class read path on the daemon, just not previously exposed
as a CLI verb. Supports ``--wing`` / ``--room`` scoping and a
``--min-count`` floor; output formats match the sibling commands
(``--format=table`` default, ``--json``/``--format=json`` pass-through).

Daemon unreachable → exit 1; inner-error envelope → exit 2.

### `cmd_hallways`

```python
def cmd_hallways(args)
```

List within-wing entity hallways (the auto-built associative graph).

### `cmd_overlap`

```python
def cmd_overlap(args)
```

Cross-wing entity overlap via a single read-only Cypher hop (slice of #191).

Answers "what entities appear in both wing A and wing B?". Uses the
daemon's ``POST /cypher`` (read-only by SQLSTATE 25006); the two
wing names are sanitized + inlined as Cypher literals because the
endpoint accepts only ``&#123;cypher, graph}`` — no parameters.

Daemon unreachable / 401/404/403 → exit 1; 403 read-only write
attempt cannot fire here (only MATCH/RETURN); inner-error envelope
or sanitization failure → exit 2.

### `cmd_why`

```python
def cmd_why(args)
```

Explain a drawer — surface the signals that make it findable (slice of #191).

Composes three read-only daemon calls into one report:
  * ``mempalace_get_drawer`` for wing/room/tags + content
  * read-only Cypher for the drawer's :MENTIONS-Entity edges (top N)
  * ``mempalace_search`` on the drawer's own first paragraph for the
    nearest semantic neighbors (drawer itself filtered out)

Daemon unreachable → exit 1; missing drawer or inner-error envelope
→ exit 2. No ``searcher.py`` writes; pure orchestration over existing
read paths.

### `cmd_tunnels`

```python
def cmd_tunnels(args)
```

List cross-wing tunnels (slice of #191).

Wraps the daemon's ``mempalace_list_tunnels`` MCP tool. Default is
explicit-only (the agent-wired tunnels at ``~/.mempalace/tunnels.json``);
pass ``--passive`` to also include passive tunnels (rooms appearing
in 2+ wings, inferred from the palace graph — see issue #75).

Daemon unreachable → exit 1; inner-error envelope → exit 2.

### `cmd_palace_set_embedder`

```python
def cmd_palace_set_embedder(args)
```

Record (or force-override) a palace's embedder identity (RFC 001).

Resolves the ``unknown`` state for a legacy palace, or records a specific
model with ``--model``. It records identity on the palace only; it does not
change the configured model — when the two differ it prints how to align
``MEMPALACE_EMBEDDING_MODEL``. ``--force`` overwrites an existing,
differently-named identity.

### `cmd_repair_status`

```python
def cmd_repair_status(args)
```

Read-only HNSW capacity health check (#1222).

### `cmd_repair`

```python
def cmd_repair(args)
```

Repair palace state.

Default mode is full HNSW rebuild via extract + re-upsert
(``--mode rebuild`` / ``--mode legacy``, synonyms). Also handles
``--mode max-seq-id`` for un-poisoning ``max_seq_id`` rows
corrupted by the legacy 0.6.x → 1.5.x chromadb migration shim
(#1208 / #1288 family). The earlier ``reorganize`` mode was
retired alongside the recovery collection (PR #8 / row 32).

Closes Copilot finding on jphein/mempalace#8: docstring claimed
only "rebuild" while the function continued to dispatch
``max-seq-id`` based on ``args.mode``.

On a successful rebuild the palace SQLite file is VACUUMed and the
FTS5 index is rebuilt, so the next repair's integrity preflight reads
a consistent database (#1747).

### `cmd_hook`

```python
def cmd_hook(args)
```

Run hook logic: reads JSON from stdin, outputs JSON to stdout.

### `cmd_instructions`

```python
def cmd_instructions(args)
```

Output skill instructions to stdout.

### `cmd_mcp`

```python
def cmd_mcp(args)
```

Show how to wire MemPalace into MCP-capable hosts.

### `cmd_serve`

```python
def cmd_serve(args)
```

Run a secure remote HTTP MCP server for a team to share one palace (#1877).

A turnkey wrapper over ``mempalace-mcp --transport http``: it resolves a
bearer token (auto-generating a strong one for non-loopback binds), prints a
ready-to-paste client config, then execs the real server in the foreground so
Docker/systemd own the process lifecycle. The token is passed via the
environment, never argv, so it can't leak through ``ps``.

### `cmd_compress`

```python
def cmd_compress(args)
```

Compress drawers in a wing using AAAK Dialect.

### `main`

```python
def main()
```

CLI entry point for the ``mempalace`` console script.

Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
any subprocess this CLI spawns inherits a clean env. Host applications
that call ``main()`` programmatically should be aware that the parent
process loses ``PYTHONPATH`` as well. Library imports
(``import mempalace.searcher`` from a host app) do NOT trigger this
side effect; only the CLI/MCP entry points pop the env var.
