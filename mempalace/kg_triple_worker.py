"""Async worker that drains ``mempalace_kg_extraction_queue``.

The worker pulls drawer_ids from the postgres queue, calls the LLM
extractor on each drawer's text, and writes the resulting triples into
the AGE knowledge graph via a per-coroutine connection from an
``AsyncConnectionPool``.

Concurrency model:
  - One ``httpx.AsyncClient`` shared across the loop.
  - ``asyncio.Semaphore(max_concurrency)`` caps in-flight LLM calls so
    we never overrun ``llama-server --parallel N``.
  - Postgres I/O uses psycopg3 ``AsyncConnectionPool``. Each coroutine
    acquires its own connection for queue ops and AGE triple writes, so
    there is no global write lock. Pool size matches the LLM concurrency
    cap so the worker can overlap N drawer extractions with N writes.

Queue claim uses ``FOR UPDATE SKIP LOCKED`` so multiple worker
processes (or threads) can drain the queue without colliding.

Migration note: this module previously serialized triple writes through
a single psycopg2 connection wrapped in an ``asyncio.Lock``. After
``llm_refine`` ReDoS fix #206 raised the LLM throughput ~10×, that lock
became the binding constraint. The pool-per-coroutine design replaces
both the lock and the per-call ``asyncio.to_thread`` hop.

The CLI entry point is exposed as ``mempalace-kg-extract`` via
pyproject.toml; see ``cli_main`` at the bottom of this module.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .kg_llm_extractor import extract_triples
from .knowledge_graph_age import _cypher_literal, _inline_cypher_params

logger = logging.getLogger("mempalace.kg_triple_worker")


DEFAULT_ENDPOINT = "http://familiar.jphe.in:11436"
DEFAULT_MODEL = "phi-4-mini"
DEFAULT_BATCH_SIZE = 20
DEFAULT_POLL_INTERVAL = 30
DEFAULT_CONCURRENCY = 8
DEFAULT_TRIPLE_CONFIDENCE = 0.7

AGE_GRAPH_NAME = "mempalace_kg"
# Same dollar-quote tag KnowledgeGraphAGE uses for its synchronous writes.
# Kept in sync so adversarial-value checks in ``_cypher_literal`` apply
# uniformly across both paths.
_AGE_DQ_TAG = "mp_age_q"
_AGE_DQ_OPEN = f"${_AGE_DQ_TAG}$"
_AGE_DQ_CLOSE = f"${_AGE_DQ_TAG}$"


# ── Postgres helpers ──────────────────────────────────────────────────


def _load_psycopg2():
    """Return the psycopg driver. Name retained for monkeypatch compat.

    Driver is now psycopg3 (``import psycopg``); the public surface used
    here (``connect``, ``%s`` placeholders, ``errors``) is the same as
    psycopg2 from this module's point of view.
    """
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "kg_triple_worker requires the psycopg driver. "
            'Install with: pip install "mempalace[kg-extract]"'
        ) from exc
    return psycopg


@dataclass
class _ClaimedDrawer:
    drawer_id: str
    wing: Optional[str]
    room: Optional[str]


_CLAIM_SQL = """
    UPDATE mempalace_kg_extraction_queue q
    SET started_at = NOW(), worker_id = %s
    FROM (
        SELECT drawer_id
        FROM mempalace_kg_extraction_queue
        WHERE started_at IS NULL AND completed_at IS NULL
        ORDER BY queued_at
        LIMIT %s
        FOR UPDATE SKIP LOCKED
    ) sub
    WHERE q.drawer_id = sub.drawer_id
    RETURNING q.drawer_id, q.wing, q.room
"""


async def _claim_batch_async(conn, worker_id: str, batch_size: int) -> list[_ClaimedDrawer]:
    """Atomically claim up to ``batch_size`` queued drawers via async conn.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers split the queue
    without blocking each other or claiming the same row twice.
    """
    async with conn.cursor() as cur:
        await cur.execute(_CLAIM_SQL, (worker_id, batch_size))
        rows = await cur.fetchall()
    await conn.commit()
    return [_ClaimedDrawer(drawer_id=r[0], wing=r[1], room=r[2]) for r in rows]


def _claim_batch(conn, worker_id: str, batch_size: int) -> list[_ClaimedDrawer]:
    """Synchronous claim path. Kept for the test surface and any caller
    that holds a sync connection (e.g. ``get_status`` below)."""
    with conn.cursor() as cur:
        cur.execute(_CLAIM_SQL, (worker_id, batch_size))
        rows = cur.fetchall()
    conn.commit()
    return [_ClaimedDrawer(drawer_id=r[0], wing=r[1], room=r[2]) for r in rows]


async def _fetch_drawer_text_async(conn, drawer_id: str) -> Optional[str]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT document FROM mempalace_drawers WHERE id = %s LIMIT 1",
            (drawer_id,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


def _fetch_drawer_text(conn, drawer_id: str) -> Optional[str]:
    """Return the ``document`` column for ``drawer_id``, or None if absent."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT document FROM mempalace_drawers WHERE id = %s LIMIT 1",
            (drawer_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


async def _mark_completed_async(conn, drawer_id: str, triple_count: int) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE mempalace_kg_extraction_queue
            SET completed_at = NOW(),
                triples_extracted = %s,
                error = NULL
            WHERE drawer_id = %s
            """,
            (triple_count, drawer_id),
        )
    await conn.commit()


def _mark_completed(conn, drawer_id: str, triple_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mempalace_kg_extraction_queue
            SET completed_at = NOW(),
                triples_extracted = %s,
                error = NULL
            WHERE drawer_id = %s
            """,
            (triple_count, drawer_id),
        )
    conn.commit()


async def _mark_error_async(conn, drawer_id: str, message: str) -> None:
    short = (message or "")[:500]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE mempalace_kg_extraction_queue
            SET error = %s, started_at = NULL, queued_at = NOW()
            WHERE drawer_id = %s
              AND COALESCE(triples_extracted, 0) = 0
            """,
            (short, drawer_id),
        )
    await conn.commit()


def _mark_error(conn, drawer_id: str, message: str) -> None:
    """Record an error and unset started_at so the row becomes claimable again.

    Keep ``worker_id`` populated so callers can spot a row that keeps
    getting picked up by the same worker (and abandon it manually if a
    poison-pill drawer is jamming the queue).
    """
    short = (message or "")[:500]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mempalace_kg_extraction_queue
            SET error = %s, started_at = NULL, queued_at = NOW()
            WHERE drawer_id = %s
              AND COALESCE(triples_extracted, 0) = 0
            """,
            (short, drawer_id),
        )
    conn.commit()


def _seed_backfill_sql(limit: Optional[int]) -> tuple[str, tuple]:
    sql = """
        INSERT INTO mempalace_kg_extraction_queue (drawer_id, wing, room)
        SELECT id, wing, room
        FROM mempalace_drawers
        WHERE id NOT IN (
            SELECT drawer_id FROM mempalace_kg_extraction_queue
            WHERE completed_at IS NOT NULL
        )
    """
    params: tuple = ()
    if limit is not None:
        sql = sql + " LIMIT %s"
        params = (limit,)
    sql = sql + " ON CONFLICT (drawer_id) DO NOTHING"
    return sql, params


async def _seed_backfill_async(conn, limit: Optional[int] = None) -> int:
    sql, params = _seed_backfill_sql(limit)
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        seeded = cur.rowcount
    await conn.commit()
    return seeded


def _seed_backfill(conn, limit: Optional[int] = None) -> int:
    """Insert every drawer not yet in the queue.

    Re-enqueue is idempotent via ``ON CONFLICT DO NOTHING`` against the
    drawer_id primary key, so a backfill that runs twice doesn't double
    the queue depth.
    """
    sql, params = _seed_backfill_sql(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        seeded = cur.rowcount
    conn.commit()
    return seeded


_STATUS_SQL = """
    SELECT
        COUNT(*) FILTER (WHERE completed_at IS NULL AND started_at IS NULL) AS queue_depth,
        COUNT(*) FILTER (WHERE started_at IS NOT NULL AND completed_at IS NULL) AS in_progress,
        COUNT(*) FILTER (
            WHERE completed_at IS NOT NULL
              AND completed_at >= NOW() - INTERVAL '24 hours'
        ) AS completed_today,
        COUNT(*) FILTER (WHERE error IS NOT NULL) AS errors_total,
        COALESCE(
            SUM(triples_extracted) FILTER (
                WHERE completed_at >= NOW() - INTERVAL '5 minutes'
            ),
            0
        ) AS triples_5m,
        COUNT(*) FILTER (
            WHERE completed_at >= NOW() - INTERVAL '5 minutes'
        ) AS drawers_5m
    FROM mempalace_kg_extraction_queue
"""


def _status_row_to_dict(row: tuple) -> dict:
    queue_depth, in_progress, completed_today, errors_total, triples_5m, drawers_5m = row
    drawers_per_min = (drawers_5m or 0) / 5.0
    return {
        "queue_depth": int(queue_depth or 0),
        "in_progress": int(in_progress or 0),
        "completed_today": int(completed_today or 0),
        "errors_total": int(errors_total or 0),
        "triples_extracted_5m": int(triples_5m or 0),
        "drawers_per_min_5m": round(drawers_per_min, 2),
    }


def _status_snapshot(conn) -> dict:
    """Return queue depth, in-progress, completed-today, and error counts."""
    with conn.cursor() as cur:
        cur.execute(_STATUS_SQL)
        row = cur.fetchone()
    return _status_row_to_dict(row)


# ── Connection pool wrapper ───────────────────────────────────────────


class _SyncConnPool:
    """Async psycopg3 connection pool with an asynccontextmanager façade.

    Wraps ``psycopg_pool.AsyncConnectionPool``. The class name is kept
    from the psycopg2 era so test factories (``pool_factory=lambda dsn,
    mn, mx: ...``) keep their existing call shape. Internally each
    coroutine gets its own connection — there is no longer a global
    write lock.

    Pool sizing convention: ``min_size`` is steady-state idle capacity;
    ``max_size`` should match the LLM concurrency cap so the worker can
    keep N writes overlapping with N LLM calls.

    AGE per-connection setup (``LOAD 'age'`` + ``search_path``) runs on
    every fresh connection via the pool's ``configure`` callback. The
    overhead is paid once per connection lifetime, not per write.
    """

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        _load_psycopg2()
        from psycopg_pool import AsyncConnectionPool

        async def _configure(conn) -> None:
            # AGE requires LOAD + search_path on every new connection
            # before any cypher() call resolves. Run them in autocommit
            # so the SET sticks across transactions on this connection.
            await conn.set_autocommit(True)
            async with conn.cursor() as cur:
                await cur.execute("LOAD 'age'")
                await cur.execute('SET search_path = ag_catalog, "$user", public')
            await conn.set_autocommit(False)

        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            configure=_configure,
            open=False,
        )
        self._opened = False

    async def open(self) -> None:
        if not self._opened:
            await self._pool.open()
            await self._pool.wait()
            self._opened = True

    async def aclose(self) -> None:
        try:
            await self._pool.close()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Synchronous close shim for test fakes / sync teardown paths."""
        try:
            asyncio.get_event_loop().run_until_complete(self.aclose())
        except RuntimeError:
            # No running loop available (we're being torn down already).
            pass

    @asynccontextmanager
    async def conn(self):
        await self.open()
        async with self._pool.connection() as conn:
            yield conn


# ── KG write façade ──────────────────────────────────────────────────


def _add_triple_cypher(
    subject: str,
    predicate: str,
    object_: str,
    *,
    source: Optional[str],
    valid_from: Optional[str],
    confidence: float,
) -> str:
    """Render the inlined Cypher source for a single ``add_triple`` write.

    Sanitization here is structural — values pass through ``_cypher_literal``
    which rejects any string carrying the AGE outer dollar-quote tag.
    Upstream callers (``extract_triples``) already strip nothing extra,
    so a hostile LLM output that happens to embed ``$mp_age_q$`` will
    fail loudly here rather than escape the SQL boundary.
    """
    cypher = """
        MERGE (s:Entity {name: $subj})
        MERGE (o:Entity {name: $obj})
        CREATE (s)-[r:RELATION {
            relation_type: $rt,
            source: $src,
            valid_from: $vf,
            valid_to: $vt,
            confidence: $conf
        }]->(o)
    """
    params = {
        "subj": subject,
        "obj": object_,
        "rt": predicate,
        "src": source,
        "vf": valid_from,
        "vt": None,
        "conf": confidence,
    }
    return _inline_cypher_params(cypher, params)


@dataclass
class _KGHandle:
    """Async triple writer backed by the worker's connection pool.

    Each ``add_triple`` call grabs its own connection from the pool, so
    there is no shared cursor and therefore no need for an
    ``asyncio.Lock``. Up to ``pool.max_size`` writes are in flight
    concurrently — the binding constraint moves back to LLM throughput
    or postgres CPU, never the worker.
    """

    pool: Any

    async def add_triple(
        self,
        subject: str,
        predicate: str,
        object_: str,
        *,
        source: str,
        valid_from: Optional[str] = None,
        confidence: float = DEFAULT_TRIPLE_CONFIDENCE,
    ) -> None:
        # Defense in depth: reject any value carrying the AGE outer
        # dollar-quote tag before the inlining step. ``_cypher_literal``
        # raises ValueError on hit; we let it bubble up so callers see
        # the offending triple.
        _cypher_literal(subject)
        _cypher_literal(object_)
        _cypher_literal(predicate)
        if source is not None:
            _cypher_literal(source)
        if valid_from is not None:
            _cypher_literal(valid_from)

        cypher_inlined = _add_triple_cypher(
            subject,
            predicate,
            object_,
            source=source,
            valid_from=valid_from,
            confidence=confidence,
        )
        sql = (
            f"SELECT * FROM cypher(%s, {_AGE_DQ_OPEN}{cypher_inlined}{_AGE_DQ_CLOSE}) "
            f"AS (ok agtype)"
        )
        async with self.pool.conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (AGE_GRAPH_NAME,))
            await conn.commit()


def _open_age_kg(dsn: str):
    """Open a KnowledgeGraphAGE — separated for tests to monkey-patch.

    Retained for callers that still want the synchronous KG handle (the
    worker no longer uses it on the write hot path).
    """
    from .knowledge_graph_age import KnowledgeGraphAGE

    return KnowledgeGraphAGE(dsn)


# ── Worker loop ──────────────────────────────────────────────────────


@dataclass
class WorkerStats:
    """Lightweight in-process counters that wrap the queue table snapshot."""

    started_at: float = field(default_factory=time.time)
    drawers_processed: int = 0
    triples_written: int = 0
    errors: int = 0

    def snapshot(self) -> dict:
        elapsed = max(time.time() - self.started_at, 1e-6)
        return {
            "uptime_seconds": round(elapsed, 1),
            "drawers_processed": self.drawers_processed,
            "triples_written": self.triples_written,
            "errors": self.errors,
            "drawers_per_min_inprocess": round((self.drawers_processed / elapsed) * 60.0, 2),
        }


async def _process_one(
    pool: Any,
    http_client: Any,
    endpoint: str,
    model: str,
    kg: _KGHandle,
    drawer: _ClaimedDrawer,
    sem: asyncio.Semaphore,
    stats: WorkerStats,
) -> None:
    async with sem:
        try:
            async with pool.conn() as conn:
                text = await _fetch_drawer_text_async(conn, drawer.drawer_id)
            if not text:
                async with pool.conn() as conn:
                    await _mark_completed_async(conn, drawer.drawer_id, 0)
                stats.drawers_processed += 1
                return

            triples = await extract_triples(http_client, endpoint, model, text)

            for t in triples:
                try:
                    await kg.add_triple(
                        t.subject,
                        t.predicate,
                        t.object,
                        source=f"drawer:{drawer.drawer_id}",
                        valid_from=t.valid_from,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "add_triple failed for drawer=%s triple=(%s,%s,%s): %s",
                        drawer.drawer_id,
                        t.subject,
                        t.predicate,
                        t.object,
                        e,
                    )

            async with pool.conn() as conn:
                await _mark_completed_async(conn, drawer.drawer_id, len(triples))
            stats.drawers_processed += 1
            stats.triples_written += len(triples)
        except Exception as e:  # noqa: BLE001
            logger.exception("worker failed on drawer=%s", drawer.drawer_id)
            stats.errors += 1
            try:
                async with pool.conn() as conn:
                    await _mark_error_async(conn, drawer.drawer_id, str(e))
            except Exception:  # noqa: BLE001
                logger.exception("failed to mark error for drawer=%s", drawer.drawer_id)


async def _open_http_client(timeout: float = 60.0):
    import httpx

    return httpx.AsyncClient(timeout=timeout)


async def run_worker(
    dsn: str,
    llm_endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    max_concurrency: int = DEFAULT_CONCURRENCY,
    worker_id: Optional[str] = None,
    backfill: bool = False,
    backfill_limit: Optional[int] = None,
    once: bool = False,
    pool_factory: Optional[Callable[[str, int, int], Any]] = None,
    kg_factory: Optional[Callable[[Any], _KGHandle]] = None,
    http_client_factory: Optional[Callable[[], Awaitable[Any]]] = None,
    stats: Optional[WorkerStats] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> WorkerStats:
    """Drain the extraction queue until cancelled (or ``once=True``).

    Args:
        dsn: Postgres DSN for both the queue and ``mempalace_drawers``.
        llm_endpoint: Base URL for the OpenAI-compatible inference server.
        model: Model alias.
        batch_size: How many rows to claim per poll cycle.
        poll_interval: Seconds to sleep when the queue is empty.
        max_concurrency: Cap on in-flight LLM calls (matches
            ``llama-server --parallel N``). The pool's max_size is set to
            ``max_concurrency + 2`` so every in-flight LLM call also has
            a write connection available, plus a small slack pool for the
            queue/claim ops.
        worker_id: Identifier written to ``worker_id`` on each claim.
            Defaults to ``hostname:pid:short-uuid``.
        backfill: If true, bulk-enqueue every uncompleted drawer before
            entering the normal claim loop. Lets one execution path
            cover both steady-state and one-shot backfill.
        backfill_limit: Cap the number of rows seeded in backfill mode.
        once: If true, run a single claim batch and return; useful for
            tests and CLI ``--once``.
        pool_factory / kg_factory / http_client_factory: Test seams so
            unit tests can inject in-memory stand-ins. ``kg_factory``
            takes the pool object (not the DSN) so the fake KG can share
            the fake pool's state if it wants to; production passes
            ``_open_kg_handle`` which just wraps the pool.
        stats: Pre-existing WorkerStats to mutate; one is created if None.
        stop_event: Async event that causes the loop to exit cleanly
            when set. Useful for tests and signal handlers.

    Returns the final ``WorkerStats``.
    """
    if worker_id is None:
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    if stats is None:
        stats = WorkerStats()
    if stop_event is None:
        stop_event = asyncio.Event()

    pool_factory = pool_factory or (lambda d, mn, mx: _SyncConnPool(d, min_size=mn, max_size=mx))
    kg_factory = kg_factory or _open_kg_handle
    http_client_factory = http_client_factory or _open_http_client

    pool = pool_factory(dsn, max(4, max_concurrency // 2), max_concurrency + 2)
    open_method = getattr(pool, "open", None)
    if callable(open_method):
        result = open_method()
        if asyncio.iscoroutine(result):
            await result
    kg = kg_factory(pool)

    http_client = await http_client_factory()

    sem = asyncio.Semaphore(max_concurrency)

    try:
        if backfill:
            async with pool.conn() as conn:
                seeded = await _seed_backfill_async(conn, backfill_limit)
            logger.info("backfill seeded %d rows into the queue", seeded)

        while not stop_event.is_set():
            async with pool.conn() as conn:
                claimed = await _claim_batch_async(conn, worker_id, batch_size)

            if not claimed:
                if once:
                    break
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    pass
                continue

            tasks = [
                asyncio.create_task(
                    _process_one(
                        pool,
                        http_client,
                        llm_endpoint,
                        model,
                        kg,
                        drawer,
                        sem,
                        stats,
                    )
                )
                for drawer in claimed
            ]
            await asyncio.gather(*tasks, return_exceptions=False)

            if once:
                break
    finally:
        try:
            close_method = getattr(http_client, "aclose", None)
            if close_method:
                await close_method()
        except Exception:  # noqa: BLE001
            pass
        try:
            aclose = getattr(pool, "aclose", None)
            if aclose:
                result = aclose()
                if asyncio.iscoroutine(result):
                    await result
            else:
                pool.close()
        except Exception:  # noqa: BLE001
            pass

    return stats


def _open_kg_handle(pool: Any) -> _KGHandle:
    """Default kg_factory — wrap the pool in the async ``_KGHandle``.

    Production callers just use the pool. Tests can pass their own
    factory to inject a fake that records writes in memory.
    """
    return _KGHandle(pool=pool)


# ── Standalone status helper ─────────────────────────────────────────


def get_status(dsn: str) -> dict:
    """One-shot status query used by the CLI's ``--status`` flag.

    Kept synchronous: callers run it from non-async contexts (CLI flag,
    monitoring scripts) where spinning up an async pool would be
    overkill. Uses a single short-lived psycopg3 connection.
    """
    psycopg2 = _load_psycopg2()
    conn = psycopg2.connect(dsn)
    try:
        return _status_snapshot(conn)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mempalace-kg-extract",
        description=(
            "Drain mempalace_kg_extraction_queue by calling an "
            "OpenAI-compatible LLM and writing typed triples to AGE."
        ),
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("MEMPALACE_POSTGRES_DSN"),
        help="Postgres DSN (env: MEMPALACE_POSTGRES_DSN).",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("MEMPALACE_KG_LLM_ENDPOINT", DEFAULT_ENDPOINT),
        help="OpenAI-compatible base URL (env: MEMPALACE_KG_LLM_ENDPOINT).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MEMPALACE_KG_LLM_MODEL", DEFAULT_MODEL),
        help="Model alias (env: MEMPALACE_KG_LLM_MODEL).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Rows claimed per poll cycle (default %(default)s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max in-flight LLM calls — match llama-server --parallel N (default %(default)s).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help="Seconds to sleep when the queue is empty (default %(default)s).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Seed the queue from mempalace_drawers before starting the loop.",
    )
    parser.add_argument(
        "--backfill-limit",
        type=int,
        default=None,
        help="Cap on rows seeded by --backfill (default: no cap).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print queue status as JSON and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single claim batch then exit (CI / manual smoke test).",
    )
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Override the worker identifier written to the queue.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default INFO).",
    )
    return parser


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.dsn:
        print(
            "error: --dsn is required (or set MEMPALACE_POSTGRES_DSN)",
            file=sys.stderr,
        )
        return 2

    if args.status:
        import json

        print(json.dumps(get_status(args.dsn), indent=2))
        return 0

    try:
        asyncio.run(
            run_worker(
                dsn=args.dsn,
                llm_endpoint=args.endpoint,
                model=args.model,
                batch_size=args.batch_size,
                poll_interval=args.poll_interval,
                max_concurrency=args.workers,
                worker_id=args.worker_id,
                backfill=args.backfill,
                backfill_limit=args.backfill_limit,
                once=args.once,
            )
        )
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli_main())
