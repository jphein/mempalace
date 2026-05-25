#!/usr/bin/env python3
"""Backfill driver for KG triple extraction across the existing palace.

Wraps ``mempalace-kg-extract --backfill`` with operator ergonomics:

- single python process that runs the worker with ``--workers 24``
- SIGTERM handler that releases in-flight queue rows so a restart re-claims them
- one-line progress log every 60s (drawers_completed, rate, errors, ETA)
- resumable — the queue table itself is the cursor, no external bookkeeping

Concurrency calibration
-----------------------

JP asked for 24 in-flight requests against the extractor. Morpheus's
worker uses ``asyncio + semaphore(N)`` to fan out, so a single python
process with ``--workers 24`` keeps 24 concurrent HTTP requests against
the llama-server endpoint. The llama-server runs with ``--parallel 8``
(see ``scratch/kg-extract/llama-server-extractor.service``), so excess
requests queue at the server — but throughput is server-bound either
way, so concurrency above 8 just keeps the pipe full.

If a future operator wants true CPU parallelism for JSON parsing /
DB writes, the queue claim uses ``UPDATE ... SKIP LOCKED``, so N copies
of this script can run side-by-side trivially: each worker process will
claim a disjoint batch from the queue. Just launch more processes.

Invocation
----------

    # default — 24 in-flight, batch of 100
    python scripts/backfill_kg_triples.py

    # custom workers / batch
    python scripts/backfill_kg_triples.py --workers 16 --batch-size 50

    # forward arbitrary flags to mempalace-kg-extract
    python scripts/backfill_kg_triples.py --workers 8 -- --model phi-4-mini
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional


logger = logging.getLogger("mempalace.backfill_kg_triples")


PROGRESS_SQL_PENDING = """
SELECT COUNT(*) FROM mempalace_kg_extraction_queue
 WHERE completed_at IS NULL AND error IS NULL
"""
PROGRESS_SQL_IN_FLIGHT = """
SELECT COUNT(*) FROM mempalace_kg_extraction_queue
 WHERE started_at IS NOT NULL AND completed_at IS NULL AND error IS NULL
"""
PROGRESS_SQL_COMPLETED = """
SELECT COUNT(*) FROM mempalace_kg_extraction_queue
 WHERE completed_at IS NOT NULL
"""
PROGRESS_SQL_ERRORS = """
SELECT COUNT(*) FROM mempalace_kg_extraction_queue WHERE error IS NOT NULL
"""

# SIGTERM hand-off: release rows the worker had claimed but hadn't
# finished. Setting started_at = NULL puts them back in the pending pool
# for the next run (or a parallel worker still running). We scope the
# reset to rows whose started_at is older than 1s to avoid racing with
# a worker that just claimed in the window between SIGTERM delivery
# and process exit.
RELEASE_IN_FLIGHT_SQL = """
UPDATE mempalace_kg_extraction_queue
   SET started_at = NULL
 WHERE started_at IS NOT NULL
   AND completed_at IS NULL
   AND error IS NULL
   AND started_at < NOW() - INTERVAL '1 second'
"""


def _format_progress(
    *,
    completed: int,
    pending: int,
    in_flight: int,
    errors: int,
    rate_per_min: float,
    elapsed_sec: float,
) -> str:
    """Render the one-line progress format.

    Pulled out of the loop so tests can pin the exact format string —
    operators script against ``grep`` so the shape is part of the
    contract.
    """
    if rate_per_min > 0:
        eta_min = pending / rate_per_min
        if eta_min < 60:
            eta = f"{eta_min:.0f}m"
        elif eta_min < 24 * 60:
            eta = f"{eta_min / 60:.1f}h"
        else:
            eta = f"{eta_min / (24 * 60):.1f}d"
    else:
        eta = "unknown"
    return (
        f"drawers_completed={completed} "
        f"in_flight={in_flight} "
        f"pending={pending} "
        f"rate={rate_per_min:.1f}/min "
        f"errors={errors} "
        f"eta={eta} "
        f"elapsed={elapsed_sec:.0f}s"
    )


def _read_counters(dsn: str) -> Optional[dict]:
    """Query the queue table for progress counters. Returns None on
    transient DB failure — caller logs and keeps going."""
    try:
        from mempalace.backends.postgres import _load_psycopg2

        psycopg2, _ = _load_psycopg2()
        with psycopg2.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(PROGRESS_SQL_PENDING)
                pending = cur.fetchone()[0]
                cur.execute(PROGRESS_SQL_IN_FLIGHT)
                in_flight = cur.fetchone()[0]
                cur.execute(PROGRESS_SQL_COMPLETED)
                completed = cur.fetchone()[0]
                cur.execute(PROGRESS_SQL_ERRORS)
                errors = cur.fetchone()[0]
        return {
            "pending": pending,
            "in_flight": in_flight,
            "completed": completed,
            "errors": errors,
        }
    except Exception as exc:
        logger.warning("progress query failed: %s", exc)
        return None


def _release_in_flight(dsn: str) -> int:
    """SIGTERM handler hook: re-queue rows the worker had claimed.

    Returns row count released. On error returns 0 — we never want
    SIGTERM cleanup to crash the shutdown path.
    """
    try:
        from mempalace.backends.postgres import _load_psycopg2

        psycopg2, _ = _load_psycopg2()
        with psycopg2.connect(dsn, connect_timeout=5) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(RELEASE_IN_FLIGHT_SQL)
                return cur.rowcount or 0
    except Exception as exc:
        logger.warning("release-in-flight failed: %s", exc)
        return 0


class _ProgressLogger(threading.Thread):
    """Background thread that emits one-line progress every interval."""

    def __init__(self, dsn: str, interval: float = 60.0):
        super().__init__(daemon=True, name="backfill-progress")
        self.dsn = dsn
        self.interval = interval
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._last_completed: Optional[int] = None
        self._last_t: Optional[float] = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            counters = _read_counters(self.dsn)
            if counters is None:
                continue
            now = time.monotonic()
            completed = counters["completed"]
            if self._last_completed is not None and self._last_t is not None:
                delta_done = completed - self._last_completed
                delta_t = now - self._last_t
                rate = (delta_done / delta_t) * 60.0 if delta_t > 0 else 0.0
            else:
                rate = 0.0
            line = _format_progress(
                completed=completed,
                pending=counters["pending"],
                in_flight=counters["in_flight"],
                errors=counters["errors"],
                rate_per_min=rate,
                elapsed_sec=now - self._t0,
            )
            logger.info(line)
            self._last_completed = completed
            self._last_t = now


def run(
    *,
    dsn: str,
    workers: int = 24,
    batch_size: int = 100,
    poll_interval: int = 30,
    progress_interval: float = 60.0,
    extra_args: Optional[list[str]] = None,
) -> int:
    """Drive the backfill. Returns the worker process's exit code."""
    cmd = [
        sys.executable,
        "-m",
        "mempalace.kg_triple_worker",
        "--backfill",
        "--dsn",
        dsn,
        "--workers",
        str(workers),
        "--batch-size",
        str(batch_size),
        "--poll-interval",
        str(poll_interval),
    ]
    if extra_args:
        cmd.extend(extra_args)
    logger.info("launching worker: %s", " ".join(cmd[:6]) + " ...")

    progress = _ProgressLogger(dsn, interval=progress_interval)
    progress.start()

    proc = subprocess.Popen(cmd)

    def _handle_signal(signum, _frame):  # noqa: ANN001
        logger.warning("received signal %s — forwarding to worker", signum)
        try:
            proc.terminate()
        except Exception:
            pass
        released = _release_in_flight(dsn)
        logger.warning("released %d in-flight rows back to pending", released)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        rc = proc.wait()
    finally:
        progress.stop()
        progress.join(timeout=2.0)

    counters = _read_counters(dsn)
    if counters is not None:
        logger.info(
            "final: completed=%d pending=%d in_flight=%d errors=%d",
            counters["completed"],
            counters["pending"],
            counters["in_flight"],
            counters["errors"],
        )
    return rc


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Backfill KG triple extraction across the existing palace.",
        epilog="Extra args after `--` are forwarded to mempalace-kg-extract.",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("MEMPALACE_POSTGRES_DSN"),
        help="Postgres DSN (default: $MEMPALACE_POSTGRES_DSN)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=24,
        help="Concurrent in-flight extraction requests (default: 24)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Drawers claimed per queue dequeue (default: 100)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Worker poll interval in seconds when queue is empty (default: 30)",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=60.0,
        help="Progress log interval in seconds (default: 60)",
    )
    return parser.parse_known_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args, extras = _parse_args(argv)
    if not args.dsn:
        print(
            "error: --dsn required (or set MEMPALACE_POSTGRES_DSN)",
            file=sys.stderr,
        )
        return 2
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run(
        dsn=args.dsn,
        workers=args.workers,
        batch_size=args.batch_size,
        poll_interval=args.poll_interval,
        progress_interval=args.progress_interval,
        extra_args=extras,
    )


if __name__ == "__main__":
    sys.exit(main())
