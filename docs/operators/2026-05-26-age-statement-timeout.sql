-- AGE Entity-name expression index for searcher._graph_expand_from_entities
--
-- Companion to fix(search): apply statement_timeout in same transaction
-- as cypher() (PR #228 follow-up, 2026-05-26).
--
-- Background
-- ----------
-- The hot path in mempalace/searcher.py:_graph_expand_from_entities issues
-- one Cypher per query-NER entity:
--
--     MATCH (a:Entity)-[r:RELATION]-()
--     WHERE a.name = '<entity>'
--     RETURN DISTINCT r.source AS source
--     LIMIT 10
--
-- The existing `idx_entity_name` is GIN over the whole agtype `properties`
-- column. AGE rewrites `a.name = 'X'` as an equality on
-- `agtype_access_operator(VARIADIC ARRAY[properties, '"name"'::agtype])`,
-- which the GIN index does NOT accelerate. With 475K+ Entity rows, the
-- planner falls back to a parallel seq scan, and the Cypher's nested-loop
-- over RELATION + Drawer + Entity blows up to multi-trillion-cost plans
-- that wedge the daemon under any modest fan-out.
--
-- This btree expression index matches AGE's filter shape exactly, so the
-- Entity-side lookup drops from O(n) seq scan to O(log n) index scan.
-- The downstream JOIN to _ag_label_vertex is still expensive (separate
-- AGE-planner work) but the 3s statement_timeout in the application code
-- now actually bounds the worst case (fixed in the accompanying PR).
--
-- Verification (familiar:5433, mempalace_2026_05_13, 2026-05-26)
-- --------------------------------------------------------------
-- Before this index:
--   EXPLAIN ... WHERE a.name = 'pgvector' →
--       Parallel Seq Scan on "Entity" a  (cost=0.00..7650.52 rows=992)
--
-- After this index:
--   EXPLAIN ... WHERE a.name = 'pgvector' →
--       Index Scan using entity_name_idx on "Entity" a  (cost=0.42..7709.95)
--       Index Cond: (agtype_access_operator(VARIADIC ARRAY[properties,
--                    '"name"'::agtype]) = '"pgvector"'::agtype)
--
-- Apply with
-- ----------
-- Apply by hand after PR review. CONCURRENTLY avoids a write lock during
-- live backfill. Safe to re-run (IF NOT EXISTS).

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

CREATE INDEX CONCURRENTLY IF NOT EXISTS entity_name_idx
    ON mempalace_kg."Entity"
    USING btree ((ag_catalog.agtype_access_operator(VARIADIC ARRAY[properties, '"name"'::ag_catalog.agtype])));
