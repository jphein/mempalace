"""Async worker that drains ``mempalace_kg_extraction_queue``.

The worker pulls drawer_ids from the postgres queue, calls the LLM
extractor on each drawer's text, and writes the resulting triples into
the AGE knowledge graph via ``KnowledgeGraphAGE.add_triple``.

Concurrency model:
  - One ``httpx.AsyncClient`` shared across the loop.
  - ``asyncio.Semaphore(max_concurrency)`` caps in-flight LLM calls so
    we never overrun ``llama-server --parallel N``.
  - Postgres I/O uses psycopg2 (the same driver everything else in the
    package uses) wrapped in ``asyncio.to_thread`` so claim/update/AGE
    writes don't block the event loop.

Queue claim uses ``FOR UPDATE SKIP LOCKED`` so multiple worker
processes (or threads) can drain the queue without colliding.

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
from typing import Any, Callable, Optional

from .kg_llm_extractor import extract_triples

logger = logging.getLogger("mempalace.kg_triple_worker")


DEFAULT_ENDPOINT = "http://familiar.jphe.in:11436"
DEFAULT_MODEL = "phi-4-mini"
DEFAULT_BATCH_SIZE = 20
DEFAULT_POLL_INTERVAL = 30
DEFAULT_CONCURRENCY = 8
DEFAULT_TRIPLE_CONFIDENCE = 0.7


# ── Postgres helpers ──────────────────────────────────────────────────


def _load_psycopg2():
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "kg_triple_worker requires psycopg2. "
            'Install with: pip install "mempalace[postgres]"'
        ) from exc
    return psycopg2


@dataclass
class _ClaimedDrawer:
    drawer_id: str
    wing: Optional[str]
    room: Optional[str]


def _claim_batch(conn, worker_id: str, batch_size: int) -> list[_ClaimedDrawer]:
    """Atomically claim up to ``batch_size`` queued drawers.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers split the queue
    without blocking each other or claiming the same row twice.
    """
    sql = """
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
    with conn.cursor() as cur:
        cur.execute(sql, (worker_id, batch_size))
        rows = cur.fetchall()
    conn.commit()
    return [_ClaimedDrawer(drawer_id=r[0], wing=r[1], room=r[2]) for r in rows]


def _fetch_drawer_text(conn, drawer_id: str) -> Optional[str]:
    """Return the ``document`` column for ``drawer_id``, or None if absent."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT document FROM mempalace_drawers WHERE id = %s LIMIT 1",
            (drawer_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


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
            SET error = %s, started_at = NULL
            WHERE drawer_id = %s
              AND COALESCE(triples_extracted, 0) = 0
            """,
            (short, drawer_id),
        )
    conn.commit()


def _seed_backfill(conn, limit: Optional[int] = None) -> int:
    """Insert every drawer not yet in the queue.

    Re-enqueue is idempotent via ``ON CONFLICT DO NOTHING`` against the
    drawer_id primary key, so a backfill that runs twice doesn't double
    the queue depth.
    """
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
    with conn.cursor() as cur:
        cur.execute(sql, params)
        seeded = cur.rowcount
    conn.commit()
    return seeded


def _status_snapshot(conn) -> dict:
    """Return queue depth, in-progress, completed-today, and error counts."""
    with conn.cursor() as cur:
        cur.execute(
            """
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
        )
        row = cur.fetchone()
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


# ── Connection pool wrapper ───────────────────────────────────────────


class _SyncConnPool:
    """Minimal sync psycopg2 pool wrapped for ``asyncio.to_thread`` callers.

    Real connection pooling (psycopg2.pool.ThreadedConnectionPool) plus
    a thin acquire/release contract. Each connection is autocommit=False
    so we control transaction boundaries — _claim_batch commits its
    UPDATE so the claim is durable even if the LLM call later crashes.
    """

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        _load_psycopg2()
        from psycopg2 import pool as _pool

        self._pool = _pool.ThreadedConnectionPool(min_size, max_size, dsn)

    def acquire(self):
        return self._pool.getconn()

    def release(self, conn) -> None:
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        self._pool.putconn(conn)

    def close(self) -> None:
        try:
            self._pool.closeall()
        except Exception:  # noqa: BLE001
            pass

    @asynccontextmanager
    async def conn(self):
        conn = await asyncio.to_thread(self.acquire)
        try:
            yield conn
        finally:
            await asyncio.to_thread(self.release, conn)


# ── KG write façade ──────────────────────────────────────────────────


@dataclass
class _KGHandle:
    """Wrap a synchronous ``KnowledgeGraphAGE`` for off-thread writes.

    The AGE KG uses psycopg2 internally and is not asyncio-aware. All
    triple writes go through ``asyncio.to_thread`` so the event loop
    keeps polling while postgres works.

    The lock serializes writes on the underlying single connection —
    AGE Cypher writes through psycopg2 aren't safe to multiplex on a
    shared cursor.
    """

    kg: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        def _do():
            self.kg.add_triple(
                subject=subject,
                relation_type=predicate,
                object_=object_,
                source=source,
                valid_from=valid_from,
                confidence=confidence,
            )

        async with self.lock:
            await asyncio.to_thread(_do)


def _open_age_kg(dsn: str):
    """Open a KnowledgeGraphAGE — separated for tests to monkey-patch."""
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
            "drawers_per_min_inprocess": round(
                (self.drawers_processed / elapsed) * 60.0, 2
            ),
        }


async def _process_one(
    pool: _SyncConnPool,
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
                text = await asyncio.to_thread(_fetch_drawer_text, conn, drawer.drawer_id)
            if not text:
                async with pool.conn() as conn:
                    await asyncio.to_thread(
                        _mark_completed, conn, drawer.drawer_id, 0
                    )
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
                await asyncio.to_thread(
                    _mark_completed, conn, drawer.drawer_id, len(triples)
                )
            stats.drawers_processed += 1
            stats.triples_written += len(triples)
        except Exception as e:  # noqa: BLE001
            logger.exception("worker failed on drawer=%s", drawer.drawer_id)
            stats.errors += 1
            try:
                async with pool.conn() as conn:
                    await asyncio.to_thread(_mark_error, conn, drawer.drawer_id, str(e))
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
    pool_factory: Optional[Callable[[str, int, int], _SyncConnPool]] = None,
    kg_factory: Optional[Callable[[str], Any]] = None,
    http_client_factory: Optional[Callable[[], Any]] = None,
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
            ``llama-server --parallel N``).
        worker_id: Identifier written to ``worker_id`` on each claim.
            Defaults to ``hostname:pid:short-uuid``.
        backfill: If true, bulk-enqueue every uncompleted drawer before
            entering the normal claim loop. Lets one execution path
            cover both steady-state and one-shot backfill.
        backfill_limit: Cap the number of rows seeded in backfill mode.
        once: If true, run a single claim batch and return; useful for
            tests and CLI ``--once``.
        pool_factory / kg_factory / http_client_factory: Test seams so
            unit tests can inject in-memory stand-ins.
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

    pool_factory = pool_factory or (
        lambda d, mn, mx: _SyncConnPool(d, min_size=mn, max_size=mx)
    )
    kg_factory = kg_factory or _open_age_kg
    http_client_factory = http_client_factory or _open_http_client

    pool = pool_factory(dsn, 2, max_concurrency + 2)
    raw_kg = await asyncio.to_thread(kg_factory, dsn)
    kg = _KGHandle(kg=raw_kg)

    http_client = await http_client_factory()

    sem = asyncio.Semaphore(max_concurrency)

    try:
        if backfill:
            async with pool.conn() as conn:
                seeded = await asyncio.to_thread(_seed_backfill, conn, backfill_limit)
            logger.info("backfill seeded %d rows into the queue", seeded)

        while not stop_event.is_set():
            async with pool.conn() as conn:
                claimed = await asyncio.to_thread(
                    _claim_batch, conn, worker_id, batch_size
                )

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
            close_kg = getattr(raw_kg, "close", None)
            if close_kg:
                await asyncio.to_thread(close_kg)
        except Exception:  # noqa: BLE001
            pass
        try:
            pool.close()
        except Exception:  # noqa: BLE001
            pass

    return stats


# ── Standalone status helper ─────────────────────────────────────────


def get_status(dsn: str) -> dict:
    """One-shot status query used by the CLI's ``--status`` flag."""
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
