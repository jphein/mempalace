# `mempalace.knowledge_graph`

Source: [`mempalace/knowledge_graph.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/knowledge_graph.py)

knowledge_graph.py — Temporal Entity-Relationship Graph for MemPalace
=====================================================================

Real knowledge graph with:
  - Entity nodes (people, projects, tools, concepts)
  - Typed relationship edges (daughter_of, does, loves, works_on, etc.)
  - Temporal validity (valid_from → valid_to — knows WHEN facts are true)
  - Closet references (links back to the verbatim memory)

Storage: SQLite (local, no dependencies, no subscriptions)
Query: entity-first traversal with time filtering

This is what competes with Zep's temporal knowledge graph.
Zep uses Neo4j in the cloud ($25/mo+). We use SQLite locally (free).

Usage:
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
    kg.add_triple("Max", "does", "swimming", valid_from="2025-01-01")
    kg.add_triple("Max", "loves", "chess", valid_from="2025-10-01")

    # Query: everything about Max
    kg.query_entity("Max")

    # Query: what was true about Max in January 2026?
    kg.query_entity("Max", as_of="2026-01-15")

    # Query: who is connected to Alice?
    kg.query_entity("Alice", direction="both")

    # Invalidate: Max's sports injury resolved
    kg.invalidate("Max", "has_issue", "sports_injury", ended="2026-02-15")

## Classes

### `class KnowledgeGraph`

#### `__init__`

```python
def __init__(self, db_path: str = None)
```

#### `close`

```python
def close(self)
```

Close the database connection.

#### `add_entity`

```python
def add_entity(self, name: str, entity_type: str = 'unknown', properties: dict = None)
```

Add or update an entity node.

#### `add_triple`

```python
def add_triple(self, subject: str, predicate: str, obj: str, valid_from: str = None, valid_to: str = None, confidence: float = 1.0, source_closet: str = None, source_file: str = None, source_drawer_id: str = None, adapter_name: str = None)
```

Add a relationship triple: subject → predicate → object.

``source_drawer_id`` and ``adapter_name`` are RFC 002 §5.5 provenance
fields populated by adapters that advertise ``supports_kg_triples``;
they default to ``None`` so every existing caller stays
source-compatible.

Examples:
    add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
    add_triple("Max", "does", "swimming", valid_from="2025-01-01")
    add_triple("Alice", "worried_about", "Max injury", valid_from="2026-01-01")

#### `invalidate`

```python
def invalidate(self, subject: str, predicate: str, obj: str, ended: str = None)
```

Mark a relationship as no longer valid (set valid_to date/time).

#### `query_entity`

```python
def query_entity(self, name: str, as_of: str = None, direction: str = 'both')
```

Get all relationships for an entity.

direction: "outgoing" (entity → ?), "incoming" (? → entity), "both"
as_of: ISO date or canonical UTC datetime — only return facts valid then

#### `query_relationship`

```python
def query_relationship(self, predicate: str, as_of: str = None)
```

Get all triples with a given relationship type.

#### `timeline`

```python
def timeline(self, entity_name: str = None)
```

Get all facts in chronological order, optionally filtered by entity.

#### `stats`

```python
def stats(self)
```

#### `seed_from_entity_facts`

```python
def seed_from_entity_facts(self, entity_facts: dict)
```

Seed the knowledge graph from fact_checker.py ENTITY_FACTS.
This bootstraps the graph with known ground truth.
