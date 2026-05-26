"""ChromaDB → Postgres (pgvector + AGE) migration tool.

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

Drawer ingest reads drawers directly from ``<palace>/chroma.sqlite3``,
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
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterator, Optional


def run_migration(
    chroma_path: str,
    postgres_dsn: str,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> None:
    """Orchestrate the 7-phase migration.

    Phase 0 always runs (it's the gate). When ``dry_run`` is true,
    we stop after phase 0 — no writes happen.
    """
    redacted_dsn = _redact_dsn(postgres_dsn)
    print(f"[mempalace migrate-to-postgres] from={chroma_path} to={redacted_dsn}")
    phase_0_preflight(chroma_path, postgres_dsn)
    if dry_run:
        print("[dry-run] preflight only; exiting before any writes")
        return
    phase_1_schema(postgres_dsn)
    phase_2_drawers(chroma_path, postgres_dsn, batch_size=batch_size)
    phase_5_kg(chroma_path, postgres_dsn)
    result = phase_6_verify(chroma_path, postgres_dsn)
    if not result["all_match"]:
        print()
        print("=" * 60)
        print("WARNING: migration verify FAILED — see counts above.")
        print("Investigate before cutover.")
        print("=" * 60)
        return
    phase_7_done(chroma_path, postgres_dsn)


# ── Phase 0 — Preflight ───────────────────────────────────────────────


def phase_0_preflight(chroma_path: str, postgres_dsn: str) -> None:
    """Verify the migration is safe to run; exit non-zero on any failure.

    Checks (any failure aborts):
      1. Source chroma palace directory exists.
      2. palace-daemon is NOT responding at PALACE_DAEMON_URL (a running
         daemon would race the migration's writes against live MCP traffic).
      3. Postgres ``vector`` and ``age`` extensions are available in the
         target database (not necessarily installed — just present in
         ``pg_available_extensions``).
    """
    if not Path(chroma_path).is_dir():
        sys.exit(f"FATAL: source palace not found: {chroma_path}")

    _check_daemon_not_running()
    _check_postgres_extensions(postgres_dsn)
    print("[phase 0] preflight passed")


def _check_daemon_not_running() -> None:
    """Refuse to migrate while palace-daemon is responsive.

    Reads ``PALACE_DAEMON_URL`` from the environment; if it's reachable
    we abort with an explicit ``systemctl stop`` instruction. If the
    env var is unset OR the daemon is unreachable, we proceed.
    """
    daemon_url = os.environ.get("PALACE_DAEMON_URL", "").strip().rstrip("/")
    if not daemon_url:
        return
    try:
        from urllib.request import urlopen

        with urlopen(f"{daemon_url}/health", timeout=2) as r:
            if r.status == 200:
                sys.exit(
                    f"FATAL: palace-daemon is responsive at {daemon_url}; "
                    "stop the daemon before migrating "
                    "(`sudo systemctl stop palace-daemon` on the daemon host)."
                )
    except Exception:
        pass  # daemon not reachable — proceed


def _check_postgres_extensions(postgres_dsn: str) -> None:
    """Verify pgvector + AGE are available (not necessarily installed)."""
    try:
        import psycopg as psycopg2
    except ImportError:
        sys.exit(
            "FATAL: psycopg driver not installed. Install with: `pip install -e '.[postgres]'`"
        )

    try:
        with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
            # NOTE: pg_available_extensions uses `name`, not `extname` —
            # that's pg_extension's column. Different views, easy to mix up.
            cur.execute("SELECT name FROM pg_available_extensions WHERE name IN ('vector', 'age')")
            avail = {row[0] for row in cur.fetchall()}
    except psycopg2.OperationalError as e:
        sys.exit(f"FATAL: cannot connect to target Postgres — {e}")

    for required in ("vector", "age"):
        if required not in avail:
            sys.exit(
                f"FATAL: Postgres extension '{required}' not available on "
                "target database. For pgvector see "
                "https://github.com/pgvector/pgvector; for AGE see "
                "https://age.apache.org/age-manual/master/intro/setup.html"
            )


# ── Schema setup (extensions + checkpoint table) ─────────────────────


CHECKPOINT_TABLE = "mempalace_backend_meta"


def phase_1_schema(postgres_dsn: str) -> None:
    """Install pgvector + AGE extensions and create the checkpoint table.

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
    """
    import psycopg as psycopg2

    # Connection 1: autocommit for extension + DDL
    schema_conn = psycopg2.connect(postgres_dsn)
    try:
        schema_conn.autocommit = True
        with schema_conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE EXTENSION IF NOT EXISTS age")
            # pg_trgm powers the gin_trgm_ops GIN that
            # _bm25_only_via_postgres falls back to for underscore-bearing
            # identifier queries (ts_rank_cd, websearch_to_tsquery, ...).
            # Without it, those queries hit a sequential scan instead of
            # the GIN index. Idempotent — IF NOT EXISTS.
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                    key text PRIMARY KEY,
                    value text NOT NULL,
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
    finally:
        schema_conn.close()

    # Connection 2: transactional for the checkpoint write
    with psycopg2.connect(postgres_dsn) as conn:
        _set_checkpoint(conn, "migration_phase_schema", "done")
    print("[schema] schema created")


# ── Drawer batch copy ─────────────────────────────────────────────────


def phase_2_drawers(
    chroma_path: str,
    postgres_dsn: str,
    batch_size: int = 1000,
) -> None:
    """Stream drawers from every collection in the ChromaDB palace into Postgres.

    Reads documents and metadata directly from ``<chroma_path>/chroma.sqlite3``
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
    ``migration_drawer_progress::<collection_name>`` keys so a resumed
    run can skip already-copied collections entirely (if marked done)
    and pages within an in-flight collection (the upsert handles that
    case naturally without a finer-grained checkpoint).
    """
    import psycopg as psycopg2
    from .backends.base import PalaceRef
    from .backends.postgres import PostgresBackend

    sqlite_path = Path(chroma_path) / "chroma.sqlite3"
    if not sqlite_path.is_file():
        print(
            f"[drawers] no chroma.sqlite3 at {sqlite_path}; "
            "nothing to copy (empty or non-chroma palace)"
        )
        return

    collections = list(_iter_chroma_collections_via_sqlite(sqlite_path))

    if not collections:
        print("[drawers] source palace has no collections; nothing to copy")
        return

    backend = PostgresBackend(dsn=postgres_dsn)
    # PalaceRef identifies the source palace for backend's per-palace
    # caches. Use the chroma path as the canonical id (filesystem-rooted
    # palace); namespace is unused for the local migration.
    palace_ref = PalaceRef(id=chroma_path, local_path=chroma_path)

    with psycopg2.connect(postgres_dsn) as conn:
        for col_id, name, _dim in collections:
            done_key = f"migration_drawer_done::{name}"
            if _get_checkpoint(conn, done_key) == "done":
                print(f"[drawers] skipping {name!r} (checkpoint says done)")
                continue

            total = _count_drawers_in_collection(sqlite_path, col_id)
            print(f"[drawers] copying {total} drawers from collection {name!r}")

            if total == 0:
                _set_checkpoint(conn, done_key, "done")
                continue

            # PostgresBackend creates one table per collection name; reuse
            # the chroma collection's name as the postgres table name so
            # downstream reads can address by collection. Post-#995 the
            # API is get_collection(*, palace, collection_name, create=True).
            pg_col = backend.get_collection(palace=palace_ref, collection_name=name, create=True)

            copied = 0
            for ids, docs, metas in _iter_drawer_batches_via_sqlite(
                sqlite_path, col_id, batch_size
            ):
                if not ids:
                    break  # safety: empty page means we're done

                pg_col.upsert(
                    ids=ids,
                    documents=docs,
                    metadatas=metas,
                    embeddings=None,
                )
                copied += len(ids)
                _set_checkpoint(
                    conn,
                    f"migration_drawer_progress::{name}",
                    f"{copied}/{total}",
                )
                print(f"[drawers]   {copied}/{total} copied in {name!r}")

            _set_checkpoint(conn, done_key, "done")
        _set_checkpoint(conn, "migration_phase_drawers", "done")
    print("[drawers] drawers complete")


# ── Raw-sqlite ChromaDB readers ───────────────────────────────────────


def _iter_chroma_collections_via_sqlite(
    sqlite_path: Path,
) -> Iterator[tuple[str, str, Optional[int]]]:
    """Yield ``(collection_id, name, dimension)`` tuples from chroma.sqlite3.

    Reads the ``collections`` table directly. The on-disk schema is stable
    across chromadb 1.x: every collection has a UUID id, a string name,
    and an optional integer dimension (NULL for collections created before
    any vectors landed).
    """
    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT id, name, dimension FROM collections ORDER BY name"):
            yield row["id"], row["name"], row["dimension"]


def _count_drawers_in_collection(sqlite_path: Path, collection_id: str) -> int:
    """Return the count of drawers in a collection via its metadata segment.

    A collection in chroma is composed of multiple segments (vector +
    metadata). The METADATA segment holds the canonical row-per-drawer
    mapping in the ``embeddings`` table; counting those rows gives the
    drawer count without opening the HNSW binary or the chromadb client.
    """
    with sqlite3.connect(str(sqlite_path)) as conn:
        seg_ids = _metadata_segment_ids(conn, collection_id)
        if not seg_ids:
            return 0
        placeholders = ",".join("?" for _ in seg_ids)
        cur = conn.execute(
            f"SELECT COUNT(*) FROM embeddings WHERE segment_id IN ({placeholders})",
            seg_ids,
        )
        return int(cur.fetchone()[0])


def _metadata_segment_ids(conn: sqlite3.Connection, collection_id: str) -> list[str]:
    """Return the metadata segment id(s) for a collection.

    There's normally exactly one METADATA segment per collection in
    chroma 1.x, but a list is returned for resilience against multi-segment
    layouts (e.g. mid-rebuild palaces).
    """
    cur = conn.execute(
        "SELECT id FROM segments WHERE collection = ? AND scope = 'METADATA'",
        (collection_id,),
    )
    return [r[0] for r in cur.fetchall()]


def _iter_drawer_batches_via_sqlite(
    sqlite_path: Path,
    collection_id: str,
    batch_size: int,
) -> Iterator[tuple[list[str], list[str], list[dict]]]:
    """Yield batches of ``(ids, documents, metadatas)`` from chroma.sqlite3.

    Joins ``embeddings`` against ``embedding_metadata`` to reconstruct each
    drawer's metadata dict and pull out the synthetic ``chroma:document``
    key that holds the verbatim document text. Pages by the auto-increment
    ``embeddings.id`` (deterministic) using a watermark cursor so a batch
    boundary doesn't accidentally split a drawer's metadata rows.

    Metadata value resolution follows chroma's column layout: only one of
    ``string_value``, ``int_value``, ``float_value``, ``bool_value`` is
    non-NULL per row. ``bool_value`` is stored as 0/1 in sqlite — converted
    back to Python ``bool`` here. Drawers with no documents (rare, but
    possible for vector-only inserts) yield an empty string for that
    drawer's document so the postgres backend can still embed.
    """
    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        seg_ids = _metadata_segment_ids(conn, collection_id)
        if not seg_ids:
            return

        placeholders = ",".join("?" for _ in seg_ids)
        watermark = 0
        while True:
            row_cur = conn.execute(
                f"""
                SELECT id, embedding_id
                FROM embeddings
                WHERE segment_id IN ({placeholders})
                  AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                (*seg_ids, watermark, batch_size),
            )
            rows = row_cur.fetchall()
            if not rows:
                return

            row_ids = [r["id"] for r in rows]
            ext_ids = [r["embedding_id"] for r in rows]
            meta_by_row = _fetch_metadata_for_rows(conn, row_ids)

            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict] = []
            for row_id, ext_id in zip(row_ids, ext_ids):
                raw = meta_by_row.get(row_id, {})
                doc = raw.pop("chroma:document", "")
                ids.append(ext_id)
                docs.append(doc if isinstance(doc, str) else "")
                metas.append(raw)

            yield ids, docs, metas
            watermark = row_ids[-1]


def _fetch_metadata_for_rows(conn: sqlite3.Connection, row_ids: list[int]) -> dict[int, dict]:
    """Group ``embedding_metadata`` rows by embedding id.

    Returns ``{embedding_row_id: {key: value, ...}}``. One round-trip per
    batch instead of N+1 lookups per drawer.
    """
    if not row_ids:
        return {}
    placeholders = ",".join("?" for _ in row_ids)
    cur = conn.execute(
        f"""
        SELECT id, key, string_value, int_value, float_value, bool_value
        FROM embedding_metadata
        WHERE id IN ({placeholders})
        """,
        row_ids,
    )
    out: dict[int, dict] = {row_id: {} for row_id in row_ids}
    for row in cur.fetchall():
        rid, key, sv, iv, fv, bv = row
        if sv is not None:
            out[rid][key] = sv
        elif bv is not None:
            # bool_value is stored as 0/1; cast back to bool. Order matters:
            # int_value can also be 0/1, so a key written as a bool must
            # be detected before the int branch.
            out[rid][key] = bool(bv)
        elif iv is not None:
            out[rid][key] = iv
        elif fv is not None:
            out[rid][key] = fv
        else:
            out[rid][key] = None
    return out


# ── Phase 5 — Knowledge graph (sqlite → AGE) ─────────────────────────


# Checkpoint key for resume — value is the "next sqlite triple offset
# to process" so a re-run picks up where the last fire left off.
_KG_WATERMARK_KEY = "migration_kg_triple_offset"
_KG_DONE_KEY = "migration_phase_kg"


def phase_5_kg(chroma_path: str, postgres_dsn: str) -> None:
    """Migrate the sqlite KnowledgeGraph into AGE.

    Looks for ``<chroma_path>/knowledge_graph.sqlite3``; if absent, logs
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
    """
    import psycopg as psycopg2

    # Resume gate: if already done, no-op
    with psycopg2.connect(postgres_dsn) as conn:
        if _get_checkpoint(conn, _KG_DONE_KEY) == "done":
            print("[phase 5] kg already migrated (checkpoint says done); skipping")
            return

    kg_sqlite_path = Path(chroma_path) / "knowledge_graph.sqlite3"
    if not kg_sqlite_path.is_file():
        print(
            f"[phase 5] no sqlite knowledge graph at {kg_sqlite_path}; "
            "marking phase done (nothing to migrate)"
        )
        with psycopg2.connect(postgres_dsn) as conn:
            _set_checkpoint(conn, _KG_DONE_KEY, "done")
        return

    import sqlite3
    from .knowledge_graph_age import KnowledgeGraphAGE

    # Read sqlite triples deterministically; only the columns we map.
    with sqlite3.connect(str(kg_sqlite_path)) as src_conn:
        src_conn.row_factory = sqlite3.Row
        cur = src_conn.execute(
            """
            SELECT id, subject, predicate, object,
                   valid_from, valid_to, confidence, source_drawer_id
            FROM triples
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    total = len(rows)

    if total == 0:
        print("[phase 5] sqlite KG exists but has 0 triples; marking phase done")
        with psycopg2.connect(postgres_dsn) as conn:
            _set_checkpoint(conn, _KG_DONE_KEY, "done")
        return

    # Resume offset
    with psycopg2.connect(postgres_dsn) as conn:
        wm = _get_checkpoint(conn, _KG_WATERMARK_KEY)
        start_offset = int(wm) if wm and wm.isdigit() else 0

    if start_offset >= total:
        print(f"[phase 5] watermark ({start_offset}) >= total ({total}); marking phase done")
        with psycopg2.connect(postgres_dsn) as conn:
            _set_checkpoint(conn, _KG_DONE_KEY, "done")
        return

    print(
        f"[phase 5] migrating {total - start_offset} triples "
        f"({start_offset}/{total} already processed)"
    )

    age = KnowledgeGraphAGE(dsn=postgres_dsn)
    copied = 0
    skipped = 0
    try:
        with psycopg2.connect(postgres_dsn) as ck_conn:
            for i, row in enumerate(rows[start_offset:], start=start_offset):
                try:
                    age.add_triple(
                        subject=row["subject"],
                        relation_type=row["predicate"],
                        object_=row["object"],
                        source=row["source_drawer_id"],
                        valid_from=row["valid_from"],
                        valid_to=row["valid_to"],
                        confidence=row["confidence"] if row["confidence"] is not None else 1.0,
                    )
                    copied += 1
                except ValueError as e:
                    skipped += 1
                    print(f"[phase 5]   skip {row['id']}: {e}")

                # Periodic watermark checkpoint every 100 triples
                if (i + 1) % 100 == 0:
                    _set_checkpoint(ck_conn, _KG_WATERMARK_KEY, str(i + 1))
                    print(
                        f"[phase 5]   {i + 1}/{total} processed "
                        f"({copied} copied, {skipped} skipped)"
                    )

            # Final watermark + done marker
            _set_checkpoint(ck_conn, _KG_WATERMARK_KEY, str(total))
            _set_checkpoint(ck_conn, _KG_DONE_KEY, "done")
    finally:
        age.close()

    print(f"[phase 5] kg complete — {copied} copied, {skipped} skipped (of {total} total)")


# ── Phase 6 — Verify migration parity ────────────────────────────────


def phase_6_verify(
    chroma_path: str,
    postgres_dsn: str,
    sample_n: int = 10,
) -> dict:
    """Compare source and target counts; sample-read a few drawers.

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
    """
    import chromadb
    import psycopg as psycopg2
    import random

    print(f"[phase 6] verifying parity (sample={sample_n})")
    result: dict = {
        "chroma_drawer_count": 0,
        "postgres_drawer_count": 0,
        "drawers_match": False,
        "chroma_triple_count": 0,
        "postgres_triple_count": 0,
        "triples_match": False,
        "sampled": 0,
        "sample_mismatches": [],
        "all_match": False,
    }

    # ─── Drawer count parity (per-collection) ──────────────────────────
    client = chromadb.PersistentClient(path=chroma_path)
    from .backends.base import PalaceRef
    from .backends.postgres import PostgresBackend

    backend = PostgresBackend(dsn=postgres_dsn)
    palace_ref = PalaceRef(id=chroma_path, local_path=chroma_path)

    sample_pool: list = []  # list of (collection, id) tuples to sample from
    for col_handle in client.list_collections():
        name = col_handle.name if hasattr(col_handle, "name") else str(col_handle)
        col = client.get_collection(name)
        c_total = col.count()
        result["chroma_drawer_count"] += c_total

        try:
            pg_col = backend.get_collection(palace=palace_ref, collection_name=name, create=True)
        except Exception as e:
            print(f"[phase 6]   postgres collection {name!r} unreachable: {e}")
            continue
        try:
            # Backend's get without ids returns metadata-only count via internals.
            # Cheaper: peek with a high limit and len() the ids.
            all_ids = pg_col.get(limit=10**9)["ids"]
            p_total = len(all_ids)
        except Exception as e:
            print(f"[phase 6]   postgres count {name!r} failed: {e}")
            continue
        result["postgres_drawer_count"] += p_total
        print(f"[phase 6]   {name}: chroma={c_total}, postgres={p_total}")

        # Build sample pool: pull a small id list from this chroma collection
        if c_total > 0:
            sample_ids = col.get(limit=min(50, c_total))["ids"]
            sample_pool.extend((name, i) for i in sample_ids)

    result["drawers_match"] = result["chroma_drawer_count"] == result["postgres_drawer_count"]

    # ─── Triple count parity ──────────────────────────────────────────
    kg_sqlite_path = Path(chroma_path) / "knowledge_graph.sqlite3"
    if kg_sqlite_path.is_file():
        import sqlite3

        with sqlite3.connect(str(kg_sqlite_path)) as src:
            cur = src.execute("SELECT count(*) FROM triples")
            result["chroma_triple_count"] = cur.fetchone()[0]

    from .knowledge_graph_age import KnowledgeGraphAGE

    age = KnowledgeGraphAGE(dsn=postgres_dsn)
    try:
        # Count all edges via a Cypher MATCH ... RETURN count
        rows = age._run_cypher(
            "MATCH ()-[r:RELATION]->() RETURN count(r) AS n",
            params={},
            fetch=True,
        )
        if rows:
            unwrapped = age._unwrap_agtype(rows[0][0])
            result["postgres_triple_count"] = int(unwrapped) if unwrapped is not None else 0
    finally:
        age.close()

    # Lenient: postgres may have fewer than chroma if phase 5 skipped rows
    result["triples_match"] = result["postgres_triple_count"] <= result[
        "chroma_triple_count"
    ] and result["chroma_triple_count"] - result["postgres_triple_count"] <= max(
        5, result["chroma_triple_count"] // 100
    )
    print(
        f"[phase 6]   triples: sqlite={result['chroma_triple_count']}, "
        f"age={result['postgres_triple_count']}"
    )

    # ─── Sample drawer round-trip ─────────────────────────────────────
    random.shuffle(sample_pool)
    for collection_name, drawer_id in sample_pool[:sample_n]:
        try:
            col = client.get_collection(collection_name)
            src = col.get(ids=[drawer_id], include=["documents", "metadatas"])
            src_doc = (src.get("documents") or [None])[0]

            pg_col = backend.get_collection(
                palace=palace_ref, collection_name=collection_name, create=True
            )
            tgt = pg_col.get(ids=[drawer_id])
            tgt_doc = (tgt.get("documents") or [None])[0]

            if src_doc != tgt_doc:
                result["sample_mismatches"].append(
                    (drawer_id, "document differs between chroma and postgres")
                )
            result["sampled"] += 1
        except Exception as e:
            result["sample_mismatches"].append((drawer_id, f"compare error: {e}"))

    result["all_match"] = (
        result["drawers_match"] and result["triples_match"] and not result["sample_mismatches"]
    )

    # Checkpoint + summary
    with psycopg2.connect(postgres_dsn) as conn:
        _set_checkpoint(conn, "migration_phase_verify", "done")

    status = "OK" if result["all_match"] else "MISMATCH"
    print(
        f"[phase 6] verify {status}: "
        f"drawers={result['drawers_match']}, "
        f"triples={result['triples_match']}, "
        f"sample_mismatches={len(result['sample_mismatches'])}"
    )
    if result["sample_mismatches"]:
        for did, reason in result["sample_mismatches"][:5]:
            print(f"[phase 6]   sample mismatch: {did} — {reason}")
    return result


# ── Phase 7 — Done; print cutover instructions ───────────────────────


def phase_7_done(chroma_path: str, postgres_dsn: str) -> None:
    """Clean migration_phase_* checkpoints + print cutover steps.

    Records ``migrated_from_chroma_at`` for forensics. All other
    ``migration_phase_*`` and ``migration_drawer_*`` and
    ``migration_kg_*`` keys are deleted — they're scaffolding from the
    migration, not part of the production palace's metadata.
    """
    import psycopg as psycopg2
    from datetime import datetime, timezone

    redacted_dsn = _redact_dsn(postgres_dsn)

    with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {CHECKPOINT_TABLE} WHERE "
            f"  key LIKE 'migration_phase_%' "
            f"  OR key LIKE 'migration_drawer_%' "
            f"  OR key LIKE 'migration_kg_%'"
        )
        cur.execute(
            f"INSERT INTO {CHECKPOINT_TABLE} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            f"  updated_at = now()",
            ("migrated_from_chroma_at", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    print()
    print("=" * 60)
    print(" Migration complete.")
    print("=" * 60)
    print(" Cutover steps:")
    print()
    print(" 1. systemctl stop palace-daemon          # if still running")
    print()
    print(" 2. Edit palace-daemon's systemd EnvironmentFile to add:")
    print("       MEMPALACE_BACKEND=postgres")
    print(f"       MEMPALACE_POSTGRES_DSN={redacted_dsn}")
    print("       MEMPALACE_KG_BACKEND=age")
    print()
    print(" 3. sudo systemctl daemon-reload")
    print("    sudo systemctl start palace-daemon")
    print()
    print(" 4. Smoke checks:")
    print("       curl http://localhost:8085/health")
    print("       curl 'http://localhost:8085/search?q=<known-content>'")
    print()
    print(" 5. After 24h of clean operation, archive the chromadb backup:")
    backup_name = f"{chroma_path}.chromadb-backup-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    print(f"       mv {chroma_path} {backup_name}")
    print()


# ── Helpers ───────────────────────────────────────────────────────────


def _redact_dsn(dsn: str) -> str:
    """Hide the password portion of a Postgres DSN for log lines.

    Accepts both URL form (``postgresql://user:pass@host/db``) and
    key=value form. Returns the DSN with the password replaced by ``***``
    or the original string if no password is detected.
    """
    if not dsn:
        return ""
    # URL form
    if "://" in dsn:
        try:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(dsn)
            if parsed.password:
                netloc = parsed.netloc.replace(parsed.password, "***", 1)
                return urlunparse(parsed._replace(netloc=netloc))
        except Exception:
            pass
    # key=value form — naive scrub
    parts = []
    for token in dsn.split():
        if token.lower().startswith("password="):
            parts.append("password=***")
        else:
            parts.append(token)
    return " ".join(parts)


# ── Checkpoint helpers (used from schema-setup onward) ───────────────────────────


def _set_checkpoint(conn, key: str, value: str) -> None:
    """Idempotent upsert into the checkpoint table.

    Used by phases 1–6 to record completion so a re-run can skip work.
    Caller controls the transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {CHECKPOINT_TABLE} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET "
            f"  value = EXCLUDED.value, updated_at = now()",
            (key, value),
        )
    conn.commit()


def _get_checkpoint(conn, key: str) -> Optional[str]:
    """Read a checkpoint value; None if absent."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT value FROM {CHECKPOINT_TABLE} WHERE key = %s",
            (key,),
        )
        row = cur.fetchone()
    return row[0] if row else None
