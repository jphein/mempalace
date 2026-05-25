# `mempalace.migrate_to_postgres`

Source: [`mempalace/migrate_to_postgres.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/migrate_to_postgres.py)

ChromaDB → Postgres (pgvector + AGE) migration tool.

A restartable, idempotent, checkpointed migration. Seven phases:

    0. preflight  — env probes, daemon not running, extensions available
    1. schema     — CREATE EXTENSION + bootstrap mempalace.* tables
    2. drawers    — batch-copy drawers from chroma to pgvector
    3. closets    — batch-copy closets collection
    4. indexes    — HNSW + supporting indexes on copied data
    5. kg         — SQLite knowledge_graph.sqlite3 → AGE graph
    6. verify     — counts match, sample queries match

Per-phase checkpoint in ``mempalace.backend_meta`` so a re-run resumes
from the last completed phase. Designed to be safe to invoke multiple
times against the same source/target pair.

Drawer ingest reads drawers directly from ``&lt;palace>/chroma.sqlite3``,
not through the ``chromadb`` Python client. ChromaDB 1.5.x SIGSEGVs on
palaces with stale HNSW state and pins ``hnswlib`` as a native
dependency that may be unbuildable on the migration host. Pure-sqlite
ingest sidesteps both problems: the on-disk format of ``collections``,
``segments``, ``embeddings``, and ``embedding_metadata`` is stable
across chromadb 1.x releases, while the HNSW binary layout is not.
Embeddings are recomputed by ``PostgresBackend`` from the original
document text using the same ``DefaultEmbeddingFunction`` chromadb
itself uses — deterministic per (model, text), so re-embedding
matches the source vectors. The trade-off is migration runtime
(re-embedding takes minutes for a six-figure drawer count); the
upside is that the migration runs even when chromadb is broken or
absent.

Tracks Phase 3 of `docs/superpowers/plans/2026-05-10-pgvector-age-migration-impl.md`.
3.1 (this commit) ships the CLI scaffold + phase 0; phases 1–6 land in
subsequent commits.

## Functions

### `run_migration`

```python
def run_migration(chroma_path: str, postgres_dsn: str, batch_size: int = 1000, dry_run: bool = False) -> None
```

Orchestrate the 7-phase migration.

Phase 0 always runs (it's the gate). When ``dry_run`` is true,
we stop after phase 0 — no writes happen.

### `phase_0_preflight`

```python
def phase_0_preflight(chroma_path: str, postgres_dsn: str) -> None
```

Verify the migration is safe to run; exit non-zero on any failure.

Checks (any failure aborts):
  1. Source chroma palace directory exists.
  2. palace-daemon is NOT responding at PALACE_DAEMON_URL (a running
     daemon would race the migration's writes against live MCP traffic).
  3. Postgres ``vector`` and ``age`` extensions are available in the
     target database (not necessarily installed — just present in
     ``pg_available_extensions``).

### `phase_1_schema`

```python
def phase_1_schema(postgres_dsn: str) -> None
```

Install pgvector + AGE extensions and create the checkpoint table.

Idempotent: ``CREATE EXTENSION IF NOT EXISTS`` and
``CREATE TABLE IF NOT EXISTS`` mean a re-run is a no-op. The drawer
and closet tables themselves are NOT created here — ``PostgresBackend``
bootstraps those lazily during the drawer-copy phase when the first write lands.
Keeping schema-creation responsibilities split (extensions+meta here,
data tables in the backend) avoids two sources of truth for the
drawer schema.

Records ``migration_phase_schema=done`` in the checkpoint table so a
re-run of the whole migration can skip this phase next time.

Uses two separate connections (autocommit for schema DDL, normal
transaction for the checkpoint write). Toggling autocommit on a
single connection mid-flight raises "set_session cannot be used
inside a transaction" — psycopg2 forbids the switch once a query
has run.

### `phase_2_drawers`

```python
def phase_2_drawers(chroma_path: str, postgres_dsn: str, batch_size: int = 1000) -> None
```

Stream drawers from every collection in the ChromaDB palace into Postgres.

Reads documents and metadata directly from ``&lt;chroma_path>/chroma.sqlite3``
via ``sqlite3``; never opens a ``chromadb.PersistentClient``. The
metadata segment is the source of truth for drawer identity in chroma's
on-disk layout: every drawer that's been written has a row in the
``embeddings`` table (keyed by segment id) and one or more rows in
``embedding_metadata`` (including the synthetic ``chroma:document``
key that holds the verbatim document text).

Embeddings are recomputed by ``PostgresBackend`` from the document text
rather than copied from chroma's HNSW binary, which has no stable
public format. The model is deterministic for fixed (text, model)
inputs, so re-embedding matches the source vectors.

Iterates each collection, pages ``batch_size`` rows at a time, and
writes each batch through ``PostgresBackend.upsert()``. Upsert is
idempotent (ON CONFLICT (id) DO UPDATE), so re-running the phase
against the same source is safe and converges to the same state.

Progress checkpoints are written per-collection and per-batch under
``migration_drawer_progress::&lt;collection_name>`` keys so a resumed
run can skip already-copied collections entirely (if marked done)
and pages within an in-flight collection (the upsert handles that
case naturally without a finer-grained checkpoint).

### `phase_5_kg`

```python
def phase_5_kg(chroma_path: str, postgres_dsn: str) -> None
```

Migrate the sqlite KnowledgeGraph into AGE.

Looks for ``&lt;chroma_path>/knowledge_graph.sqlite3``; if absent, logs
and marks the phase done (nothing to migrate is not an error).

For each sqlite triple, calls ``KnowledgeGraphAGE.add_triple()`` with
the 7-field mapping the AGE schema supports today:

    sqlite        →  AGE add_triple
    subject       →  subject
    predicate     →  relation_type
    object        →  object_
    valid_from    →  valid_from
    valid_to      →  valid_to
    confidence    →  confidence
    source_drawer_id → source

Dropped (lossy, documented): ``source_closet``, ``source_file``,
``adapter_name``, ``extracted_at``. Future enhancement: extend AGE
add_triple to accept arbitrary edge properties so the migration is
fully lossless.

Resume semantics: sorts sqlite triples by id (deterministic). Reads
the ``migration_kg_triple_offset`` checkpoint and starts there.
Writes the checkpoint after every 100 triples.

Idempotency caveat: AGE's add_triple uses CREATE (not MERGE) for the
edge, so re-running this phase against the same AGE graph WILL
create duplicate edges. The ``migration_phase_kg=done`` checkpoint
skips the whole phase on subsequent ``run_migration`` calls. If you
need to re-run after partial failure: delete the checkpoint AND
call ``KnowledgeGraphAGE.clear()`` first.

Bad data handling: ValueError from add_triple (rejected by
sanitize_kg_value / sanitize_iso_temporal / inverted-interval check)
is logged and skipped. The phase reports a skipped count at the
end. A high skip count is operator-actionable but not a phase
failure.

### `phase_6_verify`

```python
def phase_6_verify(chroma_path: str, postgres_dsn: str, sample_n: int = 10) -> dict
```

Compare source and target counts; sample-read a few drawers.

Returns a result dict with:
  ``chroma_drawer_count``       — sum across all chroma collections
  ``postgres_drawer_count``     — sum across same-named postgres tables
  ``drawers_match``             — bool
  ``chroma_triple_count``       — total rows in sqlite triples table
  ``postgres_triple_count``     — total edges in AGE graph
  ``triples_match``             — bool (allows postgres < chroma when
                                   phase 5 skipped bad rows)
  ``sampled``                   — number of drawers we round-tripped
  ``sample_mismatches``         — list of (id, reason) for any mismatch
  ``all_match``                 — overall ok bool (drawers + triples
                                   + zero sample mismatches)

Drawer count parity is strict (every drawer must round-trip). Triple
count parity is lenient — postgres ≤ chroma is acceptable because
phase 5 may legitimately skip rows with bad sanitization data, and
those are reported via stdout during the phase.

Sample-read pulls ``sample_n`` random drawer ids from chroma, fetches
each from the postgres backend, and compares document + metadata
(with a small allowance for chromadb's None-vs-empty-string drift on
optional metadata fields).

### `phase_7_done`

```python
def phase_7_done(chroma_path: str, postgres_dsn: str) -> None
```

Clean migration_phase_* checkpoints + print cutover steps.

Records ``migrated_from_chroma_at`` for forensics. All other
``migration_phase_*`` and ``migration_drawer_*`` and
``migration_kg_*`` keys are deleted — they're scaffolding from the
migration, not part of the production palace's metadata.
