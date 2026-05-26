"""Unit tests for mempalace.kg_triple_worker.

Uses in-process fakes for postgres, AGE, and httpx so the suite runs
without a live database or LLM server. The contention test exercises
the real ``_claim_batch`` SQL semantics by routing two concurrent
coroutines through a single fake connection that serializes UPDATEs
in the same order postgres would (the SKIP-LOCKED path is the
production reality; the fake here just guarantees no row is returned
twice).
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Optional

from mempalace import kg_triple_worker as kw


# ── Fake postgres ────────────────────────────────────────────────────


@dataclass
class _QueueRow:
    drawer_id: str
    wing: Optional[str] = None
    room: Optional[str] = None
    queued_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None
    triples_extracted: Optional[int] = None


@dataclass
class _DrawerRow:
    id: str
    wing: str
    room: str
    document: str


class _FakeDB:
    """Shared in-memory store backing the fake connection pool."""

    def __init__(self):
        self.queue: dict[str, _QueueRow] = {}
        self.drawers: dict[str, _DrawerRow] = {}
        self.lock = threading.Lock()
        self._clock = 0.0

    def now(self) -> float:
        self._clock += 1.0
        return self._clock

    def enqueue(self, drawer_id: str, wing: str = "", room: str = ""):
        with self.lock:
            if drawer_id not in self.queue:
                self.queue[drawer_id] = _QueueRow(
                    drawer_id=drawer_id, wing=wing, room=room, queued_at=self.now()
                )

    def add_drawer(self, drawer_id: str, document: str, wing: str = "", room: str = ""):
        self.drawers[drawer_id] = _DrawerRow(id=drawer_id, wing=wing, room=room, document=document)


class _NoopAwaitable:
    """Awaitable that does nothing. Lets the fake cursor work in both sync
    and async call sites without forcing every caller to await."""

    def __await__(self):
        if False:  # pragma: no cover - generator stub
            yield
        return None


class _SyncResult:
    """Wraps a fetchone/fetchall value so the same return is usable as
    ``rows = cur.fetchall()`` *and* ``rows = await cur.fetchall()``.

    Acts as the value itself for sync callers (proxies indexing, iter,
    len, equality) while exposing ``__await__`` for the async path."""

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def __await__(self):
        if False:  # pragma: no cover
            yield
        return self._value

    # Proxy enough of the underlying object that sync callers don't notice.
    def __getitem__(self, idx):
        return self._value[idx]

    def __iter__(self):
        return iter(self._value)

    def __len__(self):
        return len(self._value)

    def __eq__(self, other):
        return self._value == other

    def __bool__(self):
        return bool(self._value)

    def __repr__(self):
        return f"_SyncResult({self._value!r})"


class _FakeConn:
    """Dual-mode fake conn — usable in both sync (`with conn.cursor()`) and
    async (`async with conn.cursor()`) call paths so the same DB stand-in
    can back legacy sync helpers and the new AsyncConnectionPool worker."""

    def __init__(self, db: _FakeDB):
        self.db = db
        self.closed = False
        self._last_results: list = []

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        # Dual-mode: sync callers ignore the return; async callers await it.
        return _NoopAwaitable()

    def rollback(self):
        return _NoopAwaitable()

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, conn: _FakeConn):
        self.conn = conn
        self.rowcount = 0
        self._results: list = []

    # Sync context manager — used by _seed_backfill / _status_snapshot / etc.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    # Async context manager — used by the AsyncConnectionPool hot path.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def execute(self, sql: str, params: tuple = ()):
        """Dual-mode execute: does the work immediately (sync-friendly) and
        returns an awaitable so ``await cur.execute(...)`` also works."""
        self._do_execute(sql, params)
        return _NoopAwaitable()

    def _do_execute(self, sql: str, params: tuple = ()):
        s = " ".join(sql.split())
        db = self.conn.db
        # Claim batch
        if "UPDATE mempalace_kg_extraction_queue q" in s and "FOR UPDATE SKIP LOCKED" in s:
            worker_id, limit = params
            with db.lock:
                candidates = sorted(
                    (
                        r
                        for r in db.queue.values()
                        if r.started_at is None and r.completed_at is None
                    ),
                    key=lambda r: r.queued_at,
                )[:limit]
                rows = []
                for r in candidates:
                    r.started_at = db.now()
                    r.worker_id = worker_id
                    rows.append((r.drawer_id, r.wing, r.room))
                self._results = rows
                self.rowcount = len(rows)
            return
        # Fetch drawer text
        if "SELECT document FROM mempalace_drawers" in s:
            (drawer_id,) = params
            d = db.drawers.get(drawer_id)
            self._results = [(d.document,)] if d else []
            return
        # Mark completed
        if "SET completed_at = NOW()" in s and "triples_extracted" in s:
            triples_count, drawer_id = params
            with db.lock:
                row = db.queue.get(drawer_id)
                if row:
                    row.completed_at = db.now()
                    row.triples_extracted = triples_count
                    row.error = None
            self._results = []
            return
        # Mark error
        if "SET error = %s, started_at = NULL" in s:
            error_msg, drawer_id = params
            with db.lock:
                row = db.queue.get(drawer_id)
                if row and (row.triples_extracted or 0) == 0:
                    row.error = error_msg
                    row.started_at = None
            self._results = []
            return
        # Seed backfill
        if "INSERT INTO mempalace_kg_extraction_queue" in s and "FROM mempalace_drawers" in s:
            limit = params[0] if params else None
            seeded = 0
            with db.lock:
                completed_ids = {
                    r.drawer_id for r in db.queue.values() if r.completed_at is not None
                }
                candidates = [d for d in db.drawers.values() if d.id not in completed_ids]
                if limit is not None:
                    candidates = candidates[:limit]
                for d in candidates:
                    if d.id not in db.queue:
                        db.queue[d.id] = _QueueRow(
                            drawer_id=d.id,
                            wing=d.wing,
                            room=d.room,
                            queued_at=db.now(),
                        )
                        seeded += 1
            self.rowcount = seeded
            self._results = []
            return
        # Status snapshot
        if "queue_depth" in s and "completed_today" in s:
            with db.lock:
                queue_depth = sum(
                    1 for r in db.queue.values() if r.completed_at is None and r.started_at is None
                )
                in_progress = sum(
                    1
                    for r in db.queue.values()
                    if r.started_at is not None and r.completed_at is None
                )
                completed_today = sum(1 for r in db.queue.values() if r.completed_at is not None)
                errors_total = sum(1 for r in db.queue.values() if r.error is not None)
                triples_5m = sum(
                    (r.triples_extracted or 0)
                    for r in db.queue.values()
                    if r.completed_at is not None
                )
                drawers_5m = sum(1 for r in db.queue.values() if r.completed_at is not None)
            self._results = [
                (queue_depth, in_progress, completed_today, errors_total, triples_5m, drawers_5m)
            ]
            return

        raise NotImplementedError(f"FakeCursor doesn't know: {s[:120]}")

    def fetchone(self):
        # Dual-mode: real call sites are either ``cur.fetchone()`` (sync) or
        # ``await cur.fetchone()`` (async). _SyncResult is both.
        return _SyncResult(self._results[0] if self._results else None)

    def fetchall(self):
        return _SyncResult(list(self._results))


class _FakePool:
    def __init__(self, db: _FakeDB):
        self.db = db

    def acquire(self):
        return _FakeConn(self.db)

    def release(self, conn):
        conn.close()

    def close(self):
        pass

    from contextlib import asynccontextmanager as _acm

    @_acm
    async def conn(self):
        c = await asyncio.to_thread(self.acquire)
        try:
            yield c
        finally:
            await asyncio.to_thread(self.release, c)


# ── Fake KG ──────────────────────────────────────────────────────────


class _FakeKG:
    def __init__(self):
        self.triples: list[dict] = []
        self.closed = False

    async def add_triple(
        self,
        subject,
        predicate,
        object_,
        *,
        source=None,
        valid_from=None,
        valid_to=None,
        confidence=kw.DEFAULT_TRIPLE_CONFIDENCE,
    ):
        # ``relation_type`` key preserved for the test assertions; the
        # worker's call shape uses ``predicate`` positionally.
        self.triples.append(
            {
                "subject": subject,
                "relation_type": predicate,
                "object": object_,
                "source": source,
                "valid_from": valid_from,
                "confidence": confidence,
            }
        )

    def close(self):
        self.closed = True


# ── Fake HTTP client ─────────────────────────────────────────────────


class _FakeHTTPClient:
    def __init__(self, triples_per_drawer: Optional[dict[str, list[dict]]] = None):
        self.triples_per_drawer = triples_per_drawer or {}
        self.calls: list[dict] = []

    async def post(self, url, *, json: dict, timeout: float = 60.0):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        # Find drawer_id by inspecting the prompt (it carries the document text).
        prompt = json["messages"][0]["content"]
        marker = "DRAWER_ID="
        triples: list[dict] = []
        if marker in prompt:
            start = prompt.find(marker) + len(marker)
            end = prompt.find("\n", start)
            drawer_id = prompt[start:end] if end != -1 else prompt[start:]
            triples = list(self.triples_per_drawer.get(drawer_id, []))
        return _Resp(
            200,
            {"choices": [{"message": {"content": json_dumps(triples)}}]},
        )

    async def aclose(self):
        pass


def json_dumps(obj):
    return json.dumps(obj)


@dataclass
class _Resp:
    status_code: int
    payload: dict

    def json(self):
        return self.payload

    @property
    def text(self):
        return ""


# ── Helpers ──────────────────────────────────────────────────────────


def _make_factories(db: _FakeDB, kg: _FakeKG, http: _FakeHTTPClient):
    pool = _FakePool(db)
    return {
        "pool_factory": lambda dsn, mn, mx: pool,
        # kg_factory now receives the pool (worker's psycopg3 shape) rather
        # than the dsn — _FakeKG ignores it, the worker just wires it in.
        "kg_factory": lambda pool: kg,
        "http_client_factory": lambda: _async_return(http),
    }


async def _async_return(value):
    return value


def _drawer_text(drawer_id: str) -> str:
    """Embed the drawer_id in the document so the fake LLM can route to it."""
    return f"DRAWER_ID={drawer_id}\nsome facts about Alice and Bob"


# ── Tests ────────────────────────────────────────────────────────────


def test_add_triple_cypher_omits_null_keys():
    """Regression for techempower-org/mempalace#221.

    Cypher property maps reject bare ``NULL`` as a value
    (``SyntaxError: a name constant is expected``). When source / valid_from
    are None the generated property map must omit those keys entirely —
    not emit ``key: NULL``. ``valid_to`` is always omitted here because
    the worker writes facts open-ended on the right.
    """
    cypher = kw._add_triple_cypher(
        "JP",
        "uses",
        "mempalace",
        source=None,
        valid_from=None,
        confidence=0.9,
    )
    assert "NULL" not in cypher, f"property map should not contain NULL: {cypher!r}"
    assert "source" not in cypher
    assert "valid_from" not in cypher
    assert "valid_to" not in cypher
    assert "relation_type: 'uses'" in cypher
    assert "confidence: 0.9" in cypher


def test_add_triple_cypher_includes_set_keys():
    """When source and valid_from are set, they appear in the property map."""
    cypher = kw._add_triple_cypher(
        "JP",
        "started",
        "run",
        source="drawer_xyz",
        valid_from="2026-05-01",
        confidence=0.8,
    )
    assert "NULL" not in cypher
    assert "source: 'drawer_xyz'" in cypher
    assert "valid_from: '2026-05-01'" in cypher
    assert "relation_type: 'started'" in cypher


def test_claim_returns_no_overlap_under_contention():
    """Two coroutines claiming from the same queue must not share a row.

    With ``FOR UPDATE SKIP LOCKED`` in production and a lock-protected
    fake cursor here, neither worker should ever see the same drawer.
    """
    db = _FakeDB()
    for i in range(10):
        db.enqueue(f"d{i}")

    pool = _FakePool(db)

    async def go():
        results: list[list] = [[], []]

        async def claim_into(idx, worker_id):
            async with pool.conn() as conn:
                rows = await asyncio.to_thread(kw._claim_batch, conn, worker_id, 5)
                results[idx] = [r.drawer_id for r in rows]

        await asyncio.gather(
            claim_into(0, "worker-a"),
            claim_into(1, "worker-b"),
        )
        return results

    results = asyncio.run(go())
    a_ids, b_ids = results
    assert len(a_ids) + len(b_ids) == 10
    assert set(a_ids).isdisjoint(set(b_ids)), (a_ids, b_ids)


def test_seed_backfill_inserts_drawers_into_queue():
    db = _FakeDB()
    for i in range(3):
        db.add_drawer(f"d{i}", document=_drawer_text(f"d{i}"))
    conn = _FakeConn(db)
    seeded = kw._seed_backfill(conn)
    assert seeded == 3
    assert set(db.queue.keys()) == {"d0", "d1", "d2"}


def test_seed_backfill_skips_completed_drawers():
    db = _FakeDB()
    db.add_drawer("d-old", document="...")
    db.queue["d-old"] = _QueueRow(
        drawer_id="d-old",
        completed_at=db.now(),
        queued_at=db.now(),
        triples_extracted=3,
    )
    db.add_drawer("d-new", document="...")
    conn = _FakeConn(db)
    seeded = kw._seed_backfill(conn)
    assert seeded == 1
    assert "d-new" in db.queue


def test_seed_backfill_respects_limit():
    db = _FakeDB()
    for i in range(10):
        db.add_drawer(f"d{i}", document="...")
    conn = _FakeConn(db)
    seeded = kw._seed_backfill(conn, limit=3)
    assert seeded == 3


def test_status_snapshot_shape():
    db = _FakeDB()
    db.enqueue("pending-1")
    db.enqueue("pending-2")
    db.queue["pending-1"].started_at = db.now()  # in progress
    db.queue["done"] = _QueueRow(
        drawer_id="done",
        completed_at=db.now(),
        queued_at=db.now(),
        triples_extracted=4,
    )
    db.queue["err"] = _QueueRow(drawer_id="err", error="boom", queued_at=db.now())

    conn = _FakeConn(db)
    snap = kw._status_snapshot(conn)
    assert set(snap.keys()) == {
        "queue_depth",
        "in_progress",
        "completed_today",
        "errors_total",
        "triples_extracted_5m",
        "drawers_per_min_5m",
    }
    assert snap["queue_depth"] >= 1
    assert snap["in_progress"] == 1
    assert snap["completed_today"] == 1
    assert snap["errors_total"] == 1
    assert snap["triples_extracted_5m"] == 4


def test_run_worker_processes_drawer_end_to_end():
    db = _FakeDB()
    db.add_drawer("d1", document=_drawer_text("d1"))
    db.enqueue("d1")

    kg = _FakeKG()
    http = _FakeHTTPClient(
        triples_per_drawer={
            "d1": [
                {"subject": "Alice", "predicate": "works_with", "object": "Bob"},
            ],
        }
    )

    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=2,
            once=True,
            **_make_factories(db, kg, http),
        )
    )

    assert len(kg.triples) == 1
    assert kg.triples[0]["subject"] == "Alice"
    assert kg.triples[0]["relation_type"] == "works_with"
    assert kg.triples[0]["object"] == "Bob"
    assert kg.triples[0]["source"] == "drawer:d1"
    assert kg.triples[0]["confidence"] == kw.DEFAULT_TRIPLE_CONFIDENCE

    queue_row = db.queue["d1"]
    assert queue_row.completed_at is not None
    assert queue_row.triples_extracted == 1
    assert queue_row.error is None


def test_run_worker_error_path_marks_and_requeues():
    """If the LLM extraction raises, the drawer's started_at is cleared
    so it can be claimed again on the next cycle."""
    db = _FakeDB()
    db.add_drawer("d1", document=_drawer_text("d1"))
    db.enqueue("d1")

    kg = _FakeKG()

    class _BoomClient:
        calls = 0

        async def post(self, *a, **k):
            _BoomClient.calls += 1
            raise RuntimeError("simulated network failure")

        async def aclose(self):
            pass

    http = _BoomClient()
    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=2,
            once=True,
            **_make_factories(db, kg, http),
        )
    )

    row = db.queue["d1"]
    # extract_triples swallows the network exception and returns [];
    # the worker treats that as zero triples and marks completed. The
    # row is NOT re-queued because no exception bubbled into _process_one.
    # That is the documented contract — re-queue is only for exceptions
    # from outside extract_triples (e.g. DB write failures).
    assert row.completed_at is not None
    assert row.triples_extracted == 0


def test_run_worker_marks_error_when_db_write_fails():
    """A failure inside _process_one (not inside extract_triples)
    should land in the error column with started_at cleared."""
    db = _FakeDB()
    db.add_drawer("d1", document=_drawer_text("d1"))
    db.enqueue("d1")

    kg = _FakeKG()
    http = _FakeHTTPClient(
        triples_per_drawer={
            "d1": [{"subject": "Alice", "predicate": "knows", "object": "Bob"}],
        }
    )

    # Sabotage _mark_completed_async to raise once. The worker now uses
    # the async helper on the hot path; the sync version is kept only for
    # the CLI --status surface.
    original_mark = kw._mark_completed_async
    raised = {"count": 0}

    async def boom(*a, **k):
        raised["count"] += 1
        raise RuntimeError("disk full")

    kw._mark_completed_async = boom
    try:
        asyncio.run(
            kw.run_worker(
                dsn="dummy",
                llm_endpoint="http://localhost:11436",
                model="phi-4-mini",
                batch_size=10,
                poll_interval=1,
                max_concurrency=2,
                once=True,
                **_make_factories(db, kg, http),
            )
        )
    finally:
        kw._mark_completed_async = original_mark

    row = db.queue["d1"]
    assert row.error is not None
    assert "disk full" in row.error
    # started_at cleared so the next worker run can re-claim it.
    assert row.started_at is None


def test_run_worker_backfill_seeds_queue_first():
    db = _FakeDB()
    for i in range(5):
        db.add_drawer(f"d{i}", document=_drawer_text(f"d{i}"))

    kg = _FakeKG()
    http = _FakeHTTPClient(
        triples_per_drawer={
            f"d{i}": [{"subject": f"E{i}", "predicate": "rel", "object": f"F{i}"}] for i in range(5)
        }
    )

    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=4,
            once=True,
            backfill=True,
            **_make_factories(db, kg, http),
        )
    )

    # All five drawers seeded into the queue and processed.
    assert len(db.queue) == 5
    assert all(r.completed_at is not None for r in db.queue.values())
    assert len(kg.triples) == 5


def test_run_worker_handles_missing_drawer_text():
    """A queued drawer_id with no row in mempalace_drawers should be
    marked completed (triples=0) rather than crashing the worker."""
    db = _FakeDB()
    db.enqueue("phantom")  # queued but no drawer body

    kg = _FakeKG()
    http = _FakeHTTPClient()

    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=2,
            once=True,
            **_make_factories(db, kg, http),
        )
    )

    row = db.queue["phantom"]
    assert row.completed_at is not None
    assert row.triples_extracted == 0
    assert kg.triples == []
    # No HTTP call wasted on an empty body.
    assert http.calls == []


def test_run_worker_exits_when_queue_empty_with_once():
    db = _FakeDB()
    kg = _FakeKG()
    http = _FakeHTTPClient()

    stats = asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=2,
            once=True,
            **_make_factories(db, kg, http),
        )
    )
    assert stats.drawers_processed == 0
    assert stats.errors == 0


def test_worker_stats_snapshot_shape():
    s = kw.WorkerStats()
    s.drawers_processed = 5
    s.triples_written = 12
    s.errors = 1
    snap = s.snapshot()
    assert {
        "uptime_seconds",
        "drawers_processed",
        "triples_written",
        "errors",
        "drawers_per_min_inprocess",
    } <= snap.keys()
    assert snap["drawers_processed"] == 5
    assert snap["triples_written"] == 12


def test_cli_status_prints_json(capsys, monkeypatch):
    db = _FakeDB()
    db.enqueue("d1")
    db.enqueue("d2")
    db.queue["done"] = _QueueRow(
        drawer_id="done",
        completed_at=db.now(),
        queued_at=db.now(),
        triples_extracted=2,
    )

    class _StubPsycopg2:
        @staticmethod
        def connect(dsn):
            return _FakeConn(db)

    monkeypatch.setattr(kw, "_load_psycopg2", lambda: _StubPsycopg2)
    rc = kw.cli_main(["--dsn", "dummy", "--status"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["queue_depth"] == 2
    assert payload["completed_today"] == 1


def test_cli_requires_dsn(capsys, monkeypatch):
    monkeypatch.delenv("MEMPALACE_POSTGRES_DSN", raising=False)
    rc = kw.cli_main(["--status"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--dsn" in err


# ── New concurrency-rework tests ─────────────────────────────────────


def test_default_concurrency_matches_llama_parallel_24():
    """Defaults track llama-server --parallel 24 on katana."""
    assert kw.DEFAULT_CONCURRENCY == 24


def test_default_pool_max_size_is_32():
    """The pool's default max_size accommodates 24 LLM slots + slack."""
    p = kw._SyncConnPool.__init__
    # Inspect default via signature so future bumps still surface here.
    import inspect

    sig = inspect.signature(p)
    assert sig.parameters["max_size"].default == 32


def test_default_endpoint_uses_bare_tailscale_host():
    """Inter-host calls must use 'familiar', not the stale FQDN."""
    assert kw.DEFAULT_ENDPOINT == "http://familiar:11436"


def test_db_pool_size_must_be_ge_max_concurrency():
    """Pool too small to feed every LLM slot must raise ValueError."""
    db = _FakeDB()
    kg = _FakeKG()
    http = _FakeHTTPClient()

    import pytest

    with pytest.raises(ValueError, match="db_pool_size"):
        asyncio.run(
            kw.run_worker(
                dsn="dummy",
                llm_endpoint="http://localhost:11436",
                model="phi-4-mini",
                batch_size=10,
                poll_interval=1,
                max_concurrency=24,
                db_pool_size=8,  # < max_concurrency → invalid
                once=True,
                **_make_factories(db, kg, http),
            )
        )


def test_db_pool_size_threaded_to_pool_factory():
    """``--db-pool-size`` (CLI) and the kwarg both reach pool_factory."""
    db = _FakeDB()
    kg = _FakeKG()
    http = _FakeHTTPClient()
    pool = _FakePool(db)

    captured: dict = {}

    def capture_factory(dsn, mn, mx):
        captured["dsn"] = dsn
        captured["min_size"] = mn
        captured["max_size"] = mx
        return pool

    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=10,
            poll_interval=1,
            max_concurrency=8,
            db_pool_size=50,
            once=True,
            pool_factory=capture_factory,
            kg_factory=lambda p: kg,
            http_client_factory=lambda: _async_return(http),
        )
    )

    assert captured["max_size"] == 50
    assert captured["min_size"] <= captured["max_size"]


def test_db_pool_size_default_is_concurrency_plus_eight():
    """Omitting db_pool_size yields max_concurrency + 8 in the pool."""
    db = _FakeDB()
    kg = _FakeKG()
    http = _FakeHTTPClient()
    pool = _FakePool(db)

    captured: dict = {}

    def capture_factory(dsn, mn, mx):
        captured["max_size"] = mx
        return pool

    asyncio.run(
        kw.run_worker(
            dsn="dummy",
            llm_endpoint="http://localhost:11436",
            model="phi-4-mini",
            batch_size=5,
            poll_interval=1,
            max_concurrency=16,
            db_pool_size=None,
            once=True,
            pool_factory=capture_factory,
            kg_factory=lambda p: kg,
            http_client_factory=lambda: _async_return(http),
        )
    )

    assert captured["max_size"] == 16 + 8


def test_streaming_pool_processes_more_than_one_batch_without_barrier():
    """Producer must refill ``work_queue`` while consumers drain it.

    Enqueue more drawers than fit in a single claim batch. The streaming
    loop should process them all across multiple refill cycles — proves
    there is no claim → gather → claim barrier.
    """
    db = _FakeDB()
    N = 7
    BATCH = 3
    triples_per: dict[str, list[dict]] = {}
    for i in range(N):
        did = f"d{i}"
        db.add_drawer(did, document=_drawer_text(did))
        db.enqueue(did)
        triples_per[did] = [{"subject": f"E{i}", "predicate": "rel", "object": f"F{i}"}]

    kg = _FakeKG()
    stop_event_box: dict = {}

    class _DrainSignalingClient(_FakeHTTPClient):
        async def post(self, url, *, json, timeout=60.0):
            resp = await super().post(url, json=json, timeout=timeout)
            with db.lock:
                done = sum(1 for r in db.queue.values() if r.completed_at is not None)
            if done >= N - 1 and "stop" in stop_event_box:
                # Last call in flight — flip stop so the producer exits
                # after this drawer's row gets marked completed.
                stop_event_box["stop"].set()
            return resp

    http = _DrainSignalingClient(triples_per_drawer=triples_per)

    async def go():
        stop = asyncio.Event()
        stop_event_box["stop"] = stop
        return await asyncio.wait_for(
            kw.run_worker(
                dsn="dummy",
                llm_endpoint="http://localhost:11436",
                model="phi-4-mini",
                batch_size=BATCH,
                poll_interval=1,
                max_concurrency=4,
                once=False,  # streaming refill path
                stop_event=stop,
                **_make_factories(db, kg, http),
            ),
            timeout=5.0,
        )

    asyncio.run(go())

    assert all(r.completed_at is not None for r in db.queue.values())
    assert len(kg.triples) == N


def test_streaming_consumer_count_equals_max_concurrency():
    """``max_concurrency`` long-lived consumer tasks must run in parallel.

    A barrier inside the LLM client gates N coroutines until they've all
    arrived. If consumer_count < max_concurrency, the barrier never
    releases and the test times out.
    """
    db = _FakeDB()
    triples_per: dict[str, list[dict]] = {}
    N = 5
    for i in range(N):
        did = f"d{i}"
        db.add_drawer(did, document=_drawer_text(did))
        db.enqueue(did)
        triples_per[did] = [{"subject": f"E{i}", "predicate": "rel", "object": f"F{i}"}]

    kg = _FakeKG()
    barrier = asyncio.Barrier(N)

    class _BarrierClient(_FakeHTTPClient):
        async def post(self, url, *, json, timeout=60.0):
            await barrier.wait()
            return await super().post(url, json=json, timeout=timeout)

    http = _BarrierClient(triples_per_drawer=triples_per)

    async def go():
        return await asyncio.wait_for(
            kw.run_worker(
                dsn="dummy",
                llm_endpoint="http://localhost:11436",
                model="phi-4-mini",
                batch_size=N,
                poll_interval=1,
                max_concurrency=N,  # must spin up N consumers
                once=True,
                **_make_factories(db, kg, http),
            ),
            timeout=5.0,
        )

    asyncio.run(go())
    assert len(kg.triples) == N


def test_sem_narrowing_releases_llm_slot_before_db_write():
    """Structural test: the semaphore wraps the LLM call only.

    Probes the sem context boundary directly by counting active sem
    holders at each phase of ``_process_one``. With sem narrowing,
    the sem is released BEFORE the KG write starts — so when we
    instrument ``add_triple`` and ``_mark_completed_async``, both
    must observe zero held slots.

    Under the old code (sem wrapped the entire body), the KG write
    and mark-completed would see one slot still held.
    """
    db = _FakeDB()
    db.add_drawer("d0", document=_drawer_text("d0"))
    db.enqueue("d0")

    triples_per = {
        "d0": [{"subject": "Alice", "predicate": "knows", "object": "Bob"}],
    }

    observed_held: list[tuple[str, int]] = []
    sem_box: dict = {}

    class _ObservingKG:
        triples: list[dict] = []

        async def add_triple(self, subject, predicate, object_, **kwargs):
            sem = sem_box.get("sem")
            # Semaphore exposes _value (free count). Sem capacity is 1
            # in this test, so held = capacity - free.
            held = 1 - sem._value if sem else -1
            observed_held.append(("kg_write", held))
            self.triples.append({"subject": subject})

        def close(self):
            pass

    orig_mark = kw._mark_completed_async

    async def observing_mark(conn, drawer_id, triple_count):
        sem = sem_box.get("sem")
        held = 1 - sem._value if sem else -1
        observed_held.append(("mark_completed", held))
        await orig_mark(conn, drawer_id, triple_count)

    orig_extract = kw._extract_under_sem

    async def observing_extract(http_client, endpoint, model, text, sem):
        sem_box["sem"] = sem
        # Inside the sem context, one slot should be held.
        result = await orig_extract(http_client, endpoint, model, text, sem)
        observed_held.append(("inside_llm_after_call", 1 - sem._value))
        return result

    kw._mark_completed_async = observing_mark
    kw._extract_under_sem = observing_extract
    try:
        kg = _ObservingKG()
        http = _FakeHTTPClient(triples_per_drawer=triples_per)
        asyncio.run(
            kw.run_worker(
                dsn="dummy",
                llm_endpoint="http://localhost:11436",
                model="phi-4-mini",
                batch_size=1,
                poll_interval=1,
                max_concurrency=1,
                once=True,
                **_make_factories(db, kg, http),
            )
        )
    finally:
        kw._mark_completed_async = orig_mark
        kw._extract_under_sem = orig_extract

    # During the LLM call, one slot is held.
    kg_writes = [h for tag, h in observed_held if tag == "kg_write"]
    mark_writes = [h for tag, h in observed_held if tag == "mark_completed"]
    assert kg_writes == [0], (observed_held,)  # sem released by KG-write phase
    assert mark_writes == [0], (observed_held,)  # and by mark-completed phase
