# `mempalace.kg_writethrough`

Source: [`mempalace/kg_writethrough.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/kg_writethrough.py)

KG write-through hooks for PostgresCollection drawer writes.

Inline KG enrichment: every drawer write extracts entities and adds
them to the AGE knowledge graph so retrieval can fuse vector + graph
signals without an offline backfill pass.

Hook contract (matches PostgresCollection.set_kg_writethrough):

    hook(drawer_id: str, document: str, metadata: dict) -> None

Hooks are called from inside ``_insert_rows`` *after* the drawer row
commits. They run synchronously on the writer's connection thread —
keep them fast or they slow down the write path. Exceptions are caught
upstream so a misbehaving extractor can't break ingest.

This module ships two hook factories:

- ``make_age_writethrough(kg, extractor)`` — extracts entities from each
  drawer and adds (drawer_filename → MENTIONS → entity_name) triples to
  the AGE KG via ``KnowledgeGraphAGE.add_triple``.
- ``make_null_writethrough()`` — no-op for tests / disabling.

The extractor is pluggable: pass any callable matching
``(text: str) -> list[Entity]`` where Entity has at least a ``.name``
attribute. The default is a regex-based extractor importable from the
SME repo (see sme/extractors/regex.py); production deployments can swap
in spaCy or LLM-backed extractors without touching the write-through
plumbing.

## Functions

### `make_age_writethrough`

```python
def make_age_writethrough(kg: Any, extractor: Extractor, *, relation_type: str = 'mentions', confidence: float = 0.5, max_entities_per_drawer: int = 100)
```

Build a write-through hook that populates AGE from drawer writes.

For each drawer, the hook:

1. Runs ``extractor(document)`` to get a list of entities.
2. For each entity (capped at ``max_entities_per_drawer``), calls
   ``kg.add_triple(subject=drawer_id, relation_type=relation_type,
   object_=entity.name, confidence=confidence)``.

The triples land as ``(drawer_id) -[mentions]-> (entity_name)`` in
AGE. ``drawer_id`` is typically the source filename (matches the
``expected_sources`` shape used in retrieval benchmarks), making the
graph queryable as "which drawers mention X" via:

    MATCH (d:Entity)-[r:RELATION]->(e:Entity)
    WHERE e.name = $entity AND r.relation_type = 'mentions'
    RETURN d.name

Capping at ``max_entities_per_drawer`` bounds the per-write cost;
each add_triple is ~2-5ms on AGE (MERGE + CREATE round-trip), so a
drawer with 100 entities adds ~250-500ms to its write. Tunable based
on extractor verbosity vs latency budget.

Args:
    kg: A ``KnowledgeGraphAGE`` (or any compatible KG with the same
        ``add_triple`` signature).
    extractor: Callable returning entities from text.
    relation_type: The Cypher edge label (default "mentions" matches
        the read-side fusion convention).
    confidence: Per-extraction confidence — 0.5 default reflects
        that regex extraction is high-recall but lower precision
        than e.g. LLM extraction.
    max_entities_per_drawer: Cap on entities per drawer write.

Returns:
    A hook callable suitable for ``PostgresCollection.set_kg_writethrough``.

### `make_null_writethrough`

```python
def make_null_writethrough()
```

A no-op hook. Useful for disabling KG writes in tests or rollouts
without removing the ``set_kg_writethrough`` call from the writer
setup path.

### `make_writethrough_from_env`

```python
def make_writethrough_from_env(kg: Optional[Any] = None)
```

Build a hook based on environment variables.

Env vars:
  MEMPALACE_KG_WRITETHROUGH=0|1     — master switch (default off)
  MEMPALACE_KG_EXTRACTOR=regex|spacy|llm|null  — choose extractor (default regex)

Returns ``None`` if write-through is disabled. Returns a hook
otherwise. ``kg`` is required when the master switch is on.

Regex extractor needs an SME-repo import — kept optional so the
mempalace package doesn't hard-require SME. If unavailable, falls
back to a built-in tiny regex extractor (lower recall than the SME
one but no cross-package dependency).
