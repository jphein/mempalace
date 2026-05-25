# `mempalace.palace_graph_age`

Source: [`mempalace/palace_graph_age.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/palace_graph_age.py)

Palace structure (Wing → Room → Drawer) as native AGE graph nodes.

Today's ``palace_graph.build_graph`` aggregates wing/room/tunnel
structure from the drawer table via SQL on every call. This module
mirrors that hierarchy into AGE so:

1. Cypher MATCH walks the palace structure natively — no SQL aggregation
   per query.
2. The Entity / MENTIONS layer (from kg_writethrough.py) connects to the
   palace structure layer via shared Drawer nodes.
3. The "agent walks into the palace" metaphor becomes a Cypher pattern:

   MATCH (w:Wing &#123;name: $wing})-[:CONTAINS]->(r:Room)-[:CONTAINS]->
         (d:Drawer)-[:MENTIONS]->(e:Entity)
   RETURN w, r, d, e

Node labels:
    Wing       — top-level grouping (project / repo / domain)
    Room       — topic within a wing
    Drawer     — individual drawer (one per stored memory)
    Entity     — extracted name (person, project, identifier, ...)

Edge labels:
    CONTAINS   — Wing → Room, Room → Drawer (hierarchical)
    MENTIONS   — Drawer → Entity (from kg_writethrough)
    SHARED_VIA — Wing ↔ Wing where they share a Room (tunnels)

The populate functions are idempotent: re-running on the same source
data MERGEs by name/id rather than blindly creating duplicates. They
are restartable.

Read-side helpers (walk_wing, find_drawers_in_room, etc.) are bundled
here for convenience, but the canonical query interface is
KnowledgeGraphAGE._run_cypher with arbitrary Cypher.

## Functions

### `populate_from_postgres`

```python
def populate_from_postgres(kg: KnowledgeGraphAGE, *, dsn: str, table_name: str = 'mempalace_drawers', skip_drawers: bool = False, skip_tunnels: bool = False, batch_log_every: int = 500) -> dict
```

Populate palace structure into AGE from the drawer table.

Reads the drawer table once, builds Wing/Room/Drawer/SHARED_VIA in
AGE. Idempotent — re-runs MERGE on identifier (wing.name, room.name,
drawer.id) so existing nodes aren't duplicated.

Args:
    kg: A KnowledgeGraphAGE instance (already connected, graph
        initialized).
    dsn: Postgres DSN to read drawers from (typically the same
        DSN the KG uses, but kept explicit so cross-database
        populates remain possible).
    table_name: Drawer table to read.
    skip_drawers: If True, only build Wing/Room/SHARED_VIA edges and
        skip the per-drawer Drawer nodes + CONTAINS edges. Faster
        for "I just want the high-level palace map" use cases.
    skip_tunnels: If True, skip SHARED_VIA edges (room→wing
        adjacency). Useful for first-pass population on huge
        palaces where you want CONTAINS first.

Returns a counters dict: &#123;wings, rooms, drawers, contains_edges,
shared_via_edges}.

### `walk_wing`

```python
def walk_wing(kg: KnowledgeGraphAGE, wing_name: str, depth: int = 2, limit: int = 100) -> list
```

Return a structured walk of a wing's contents.

Default depth=2 expands Wing → Room → Drawer; depth=3 also pulls in
MENTIONS → Entity. Result is a list of dicts:
&#123;wing, room, drawer, entity?} — one row per leaf reached at the
requested depth.

The "agent walks the palace" primitive — this is what an MCP tool
or RLM-orchestrator would call to enumerate what's inside a wing.

### `list_wings`

```python
def list_wings(kg: KnowledgeGraphAGE, limit: int = 100) -> list[str]
```

Return all wing names in the palace.

### `list_rooms_in_wing`

```python
def list_rooms_in_wing(kg: KnowledgeGraphAGE, wing_name: str, limit: int = 100) -> list[str]
```

Return all rooms in the named wing.

### `list_drawers_in_room`

```python
def list_drawers_in_room(kg: KnowledgeGraphAGE, room_name: str, limit: int = 100) -> list[str]
```

Return all drawer ids in the named room (across any wing).

### `tunnels_from_wing`

```python
def tunnels_from_wing(kg: KnowledgeGraphAGE, wing_name: str) -> list[dict]
```

Return all other wings reachable from this one via SHARED_VIA.
