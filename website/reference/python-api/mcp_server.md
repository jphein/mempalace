# `mempalace.mcp_server`

Source: [`mempalace/mcp_server.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/mcp_server.py)

MemPalace MCP Server — read/write palace access for Claude Code
================================================================
Install: claude mcp add mempalace -- mempalace-mcp [--palace /path/to/palace]

Tools (read):
  mempalace_status          — total drawers, wing/room breakdown
  mempalace_list_wings      — all wings with drawer counts
  mempalace_list_rooms      — rooms within a wing
  mempalace_get_taxonomy    — full wing → room → count tree
  mempalace_search          — semantic search, optional wing/room filter
  mempalace_check_duplicate — check if content already exists before filing

Tools (write):
  mempalace_add_drawer      — file verbatim content into a wing/room
  mempalace_delete_drawer   — remove a drawer by ID

Tools (maintenance):
  mempalace_reconnect       — force cache invalidation and reconnect after external writes

## Functions

### `tool_status`

```python
def tool_status()
```

### `tool_list_wings`

```python
def tool_list_wings()
```

### `tool_list_rooms`

```python
def tool_list_rooms(wing: str = None)
```

### `tool_get_taxonomy`

```python
def tool_get_taxonomy()
```

### `tool_search`

```python
def tool_search(query: str, limit: int = 15, wing: str = None, room: str = None, tags: list = None, max_distance: float = 1.5, min_similarity: float = None, context: str = None, candidate_strategy: str = 'hybrid', fusion_mode: str = 'convex', include_trace: bool = False)
```

### `tool_check_duplicate`

```python
def tool_check_duplicate(content: str, threshold: float = 0.9)
```

### `tool_get_aaak_spec`

```python
def tool_get_aaak_spec()
```

Return the AAAK dialect specification.

### `tool_traverse_graph`

```python
def tool_traverse_graph(start_room: str, max_hops: int = 2)
```

Walk the palace graph from a room. Find connected ideas across wings.

### `tool_walk_palace`

```python
def tool_walk_palace(start_wing: str = None, start_room: str = None, start_entity: str = None, depth: int = 2, limit: int = 50)
```

Agent-facing palace walk via AGE Cypher traversal.

Phase 6 of the AGE-integration goal. Exposes the "agent walks into the
palace" metaphor as a single tool: pass a starting node (wing OR room
OR entity) and a depth, get back the navigable subgraph it touches.

Three traversal modes by starting node:

- **start_wing="memorypalace"**: enumerate rooms in this wing
  (depth=1), plus drawers in those rooms (depth=2), plus mentioned
  entities (depth=3). The "walking into a wing" pattern.
- **start_room="problems"**: enumerate drawers in this room across
  all wings (depth=1), plus their mentioned entities (depth=2). The
  "walking into a specific room" pattern.
- **start_entity="pgvector"**: enumerate drawers mentioning this
  entity (depth=1), plus the rooms+wings containing them (depth=2).
  The "find where in the palace X is discussed" pattern (inverse
  walk — entity → drawer → room → wing).

Exactly one of (start_wing, start_room, start_entity) must be given.

Returns a structured walk result with:
  - ``start``: the input anchor
  - ``walk``: list of &#123;wing, room, drawer, entity} rows, one per
    leaf reached
  - ``stats``: &#123;wings_touched, rooms_touched, drawers_touched,
    entities_touched}

Requires MEMPALACE_BACKEND=postgres and the AGE graph populated via
kg_writethrough or backfill_age.

### `tool_find_tunnels`

```python
def tool_find_tunnels(wing_a: str = None, wing_b: str = None)
```

Find rooms that bridge two wings — the hallways connecting domains.

### `tool_graph_stats`

```python
def tool_graph_stats()
```

Palace graph overview: nodes, tunnels, edges, connectivity.

### `tool_create_tunnel`

```python
def tool_create_tunnel(source_wing: str, source_room: str, target_wing: str, target_room: str, label: str = '', source_drawer_id: str = None, target_drawer_id: str = None)
```

Create an explicit cross-wing tunnel between two palace locations.

Use when you notice content in one project relates to another project.
Example: an API design discussion in project_api connects to the
database schema in project_database.

### `tool_list_tunnels`

```python
def tool_list_tunnels(wing: str = None, include_passive: bool = False)
```

List cross-wing tunnels, optionally filtered by wing.

Default returns only explicit (agent-created) tunnels stored at
``~/.mempalace/tunnels.json``. Pass ``include_passive=True`` to also
include passive tunnels (rooms appearing in 2+ wings, computed from
``graph_stats``). Each result is tagged with ``kind: 'explicit'|'passive'``.
See techempower-org/mempalace#75 for the asymmetry that motivated the
merged-result form.

### `tool_delete_tunnel`

```python
def tool_delete_tunnel(tunnel_id: str)
```

Delete an explicit tunnel by its ID.

### `tool_follow_tunnels`

```python
def tool_follow_tunnels(wing: str, room: str)
```

Follow explicit tunnels from a room to see connected drawers in other wings.

### `tool_add_drawer`

```python
def tool_add_drawer(wing: str, room: str, content: str, source_file: str = None, added_by: str = 'mcp', tags: list = None)
```

File verbatim content into a wing/room. Checks for duplicates first.

Content above ``chunk_size`` is split into bounded per-chunk drawers
via a single batched upsert. Each chunk carries ``parent_drawer_id``
linkage and ``chunk_index`` metadata so search can rejoin them. The
returned ``drawer_id`` is the LOGICAL group handle on the chunked
path; physical drawer ids are in ``chunk_ids`` (#1539). To delete
or fetch the underlying drawers, iterate ``chunk_ids`` or query by
``parent_drawer_id`` — ``tool_get_drawer(drawer_id)`` and
``tool_delete_drawer(drawer_id)`` report "not found" on the chunked
path because no row is stored under the logical group id.

``tags`` is an optional list of cross-cutting labels (multi-label
additive layer over the strict wing/room hierarchy). See
``mempalace.tags`` for normalisation rules.

### `tool_delete_drawer`

```python
def tool_delete_drawer(drawer_id: str)
```

Delete a single drawer by ID.

### `tool_sync`

```python
def tool_sync(project_dir: str = None, wing: str = None, apply: bool = False)
```

Prune drawers whose source files are gitignored, missing, or moved (#1252).

### `tool_get_drawer`

```python
def tool_get_drawer(drawer_id: str)
```

Fetch a single drawer by ID. Returns full content and metadata.

### `tool_list_drawers`

```python
def tool_list_drawers(wing: str = None, room: str = None, tags: list = None, limit: int = 20, offset: int = 0)
```

List drawers with pagination. Optional wing/room/tag filter.

### `tool_update_drawer`

```python
def tool_update_drawer(drawer_id: str, content: str = None, wing: str = None, room: str = None, tags: list = None)
```

Update an existing drawer's content and/or metadata.

``tags`` semantics:
    * ``None`` — leave the existing tag list untouched.
    * ``[]``   — clear all tags.
    * non-empty list — replace the existing tag list with the
      normalised input.

### `tool_rate_memory`

```python
def tool_rate_memory(drawer_id: str, useful: bool)
```

Record feedback on whether a search result was helpful (#159).

Stores the rating as drawer *metadata* — the verbatim content is never
touched. Each call increments one of two counters (``rating_useful`` /
``rating_not_useful``); the net of the two becomes a bounded, capped
ranking signal in ``search_memories`` that can reorder neighbours but
never excludes a drawer (recall is preserved).

### `tool_rename_wing`

```python
def tool_rename_wing(from_wing: str, to_wing: str, batch_size: int = 500)
```

Rename all drawers in one wing to another, server-side.

Iterates through the source wing in batches and updates each
drawer's metadata. Much faster than individual update_drawer calls
over HTTP since it operates directly on the collection.

### `tool_list_tags`

```python
def tool_list_tags(wing: str = None, room: str = None, min_count: int = 1)
```

Return every unique tag in the palace with the number of drawers carrying it.

Results are sorted by count (descending). ``wing`` and ``room`` scope
the count to a subset of the palace. ``min_count`` drops tags below
the threshold from the result; default 1 keeps any tag with at least
one drawer.

### `tool_kg_query`

```python
def tool_kg_query(entity: str, as_of: str = None, direction: str = 'both')
```

Query the knowledge graph for an entity's relationships.

### `tool_kg_add`

```python
def tool_kg_add(subject: str, predicate: str, object: str, valid_from: str = None, valid_to: str = None, source_closet: str = None, source_file: str = None, source_drawer_id: str = None, context: str = None)
```

Add a relationship to the knowledge graph.

All temporal and provenance fields are optional. ``valid_to`` lets callers
backfill historical facts with a known end date/time in a single call
instead of a separate ``kg_invalidate`` call.

Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
the form ``YYYY-MM-DDTHH:MM:SSZ``.

``context`` is the SPOC fourth-axis (techempower-org/mempalace#161):
a free-form anchor naming where the fact was witnessed (e.g.
``drawer:abc123``, ``conversation:2026-05-28``). The AGE backend
stores it on the RELATION edge and surfaces it through every read
path; the SQLite backend silently accepts and ignores it (storage
schema doesn't yet have a column for it) so callers don't need to
branch on backend.

### `tool_kg_invalidate`

```python
def tool_kg_invalidate(subject: str, predicate: str, object: str, ended: str = None)
```

Mark a fact as no longer true.

Returns the actual ``ended`` date/time that was stored. When the caller
omits ``ended``, the underlying graph stamps ``date.today()`` and the
response reflects that resolved value.

Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
the form ``YYYY-MM-DDTHH:MM:SSZ``.

### `tool_kg_timeline`

```python
def tool_kg_timeline(entity: str = None, as_of: str = None)
```

Get chronological timeline of facts, optionally for one entity.

``as_of`` (techempower-org/mempalace#161) filters to facts whose
temporal interval contains the given date/datetime; NULL ends are
treated as open intervals (same semantics as ``mempalace_kg_query``).
Omit ``as_of`` to see the full timeline including expired facts.

### `tool_kg_stats`

```python
def tool_kg_stats()
```

Knowledge graph overview: entities, triples, relationship types.

Returns a structured error envelope on transient postgres failures
(the connection dropped between `_call_kg` opening the handle and
`kg.stats()` finishing its query — typically caused by a postgres
OOM-kill or restart under load). The caller sees
``&#123;"error": "backend_unavailable", "retryable": True, ...}`` and can
surface "try again in a moment" instead of an opaque -32000
internal error. See techempower-org/mempalace#299.

Non-transient errors (cypher syntax, value-validation, schema
mismatch) still propagate — those need a real fix, not a retry.

### `tool_diary_write`

```python
def tool_diary_write(agent_name: str, entry: str, topic: str = 'general', wing: str = '', session_id: str = '')
```

Write a diary entry for this agent. Entries are timestamped and
accumulate over time in a diary room.

This is the agent's personal journal — observations, thoughts,
what it worked on, what it noticed, what it thinks matters.

All entries land in the main ``mempalace_drawers`` collection — the
earlier dedicated checkpoint collection has been retired (verbatim
transcripts already cover the recovery use case).

Note: ``agent_name`` is normalized to lowercase before storage so
that diary reads are case-insensitive (see #1243). "Claude",
"claude", and "CLAUDE" all resolve to the same agent.

### `tool_diary_read`

```python
def tool_diary_read(agent_name: str, last_n: int = 10, wing: str = '')
```

Read an agent's recent diary entries. Returns the last N entries
in chronological order — the agent's personal journal.

When ``wing`` is provided, reads only from that wing. When ``wing``
is empty or omitted, returns entries from every wing this agent has
written to. Diary writes from hooks land in project-derived wings
(``wing_&lt;project>``), so requiring a specific wing on read would
silo those entries from agent-initiated reads.

Note: ``agent_name`` is normalized to lowercase before filtering so
that reads are case-insensitive (see #1243). Entries written under
pre-fix mixed-case agent names will not match the lowercase filter;
use ``mempalace repair`` to migrate legacy data if needed.

### `tool_hook_settings`

```python
def tool_hook_settings(silent_save: bool = None, desktop_toast: bool = None)
```

Get or set hook behavior settings.

- silent_save: True = stop hook saves directly (no MCP clutter),
  False = legacy blocking MCP calls. Default: True.
- desktop_toast: True = show notify-send desktop toast on save,
  False = terminal-only notification. Default: False.

Call with no arguments to see current settings.

### `tool_memories_filed_away`

```python
def tool_memories_filed_away()
```

Acknowledge the latest silent checkpoint. Returns a short summary.

### `tool_reconnect`

```python
def tool_reconnect()
```

Force the MCP server to drop cached ChromaDB + KnowledgeGraph state.

Use after external scripts or CLI commands modify the palace database
or replace ``knowledge_graph.sqlite3`` directly, which can leave the
in-memory HNSW index stale or pin a closed-on-disk SQLite connection.

### `handle_request`

```python
def handle_request(request)
```

### `main`

```python
def main()
```

MCP server entry point for the ``mempalace-mcp`` console script.

Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
any subprocess this server spawns inherits a clean env. Host
applications that call ``main()`` programmatically should be aware
that the parent process loses ``PYTHONPATH`` as well. Library imports
(``import mempalace.searcher`` from a host app) do NOT trigger this
side effect; only the CLI/MCP entry points pop the env var.
