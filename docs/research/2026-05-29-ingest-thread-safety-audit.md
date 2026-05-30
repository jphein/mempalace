# Ingest / embedding / KG-write-through thread-safety audit

**Date:** 2026-05-29
**Author:** Onyx (sme-dreamteam)
**Branch:** `audit/ingest-thread-safety`
**Scope:** the ingest, embedding, and KG-write-through concurrency surface of
the mempalace library, as exercised by the palace-daemon under concurrent
benches on `familiar.jphe.in`.

## Why this audit exists

This is the **data-safety gate** before unlocking N concurrent benches on
`familiar` (24-thread Ryzen 9 3900X). The palace-daemon offloads every request
via `loop.run_in_executor(None, _mp.handle_request, …)` — the default
`ThreadPoolExecutor` — so under concurrent benches multiple requests run in
**parallel threads sharing mempalace library state**. The daemon caps
concurrent writes with `_write_sem` (default `PALACE_MAX_WRITE_CONCURRENCY =
max(1, PALACE_MAX_CONCURRENCY // 2)` → **2** at the default
`PALACE_MAX_CONCURRENCY=4`), so up to two write requests can be in-flight on the
shared library state simultaneously. We must prove the shared state is
thread-safe — or fix it — before letting the supervisors run benches in
parallel.

Sibling work this gates: `techempower-org/palace-daemon` (refcount the
bench-lock) and `techempower-org/multipass-structural-memory-eval` (supervisor
drops the 1-bench mutex).

## Verdict table

| Check | Surface | Verdict | Evidence |
|---|---|---|---|
| (a) | Shared `SentenceTransformer` / ONNX encoder | **SAFE** | `embedding.py` `_EF_CACHE`; `backends/postgres.py:_embed` |
| (b) | `PostgresBackend` connection / cursor scoping | **SAFE (serialized)** | `backends/postgres.py:698` `_get_conn`; psycopg3 `Connection.lock` |
| (c) | Module-level caches in ingest path | **SAFE** | `IdfCache` (locked), `_HALL_KEYWORDS_CACHE`, `validate_room`, `functools.lru_cache` |
| (d) | AGE KG write-through under concurrent ingest | **RACE → FIXED** (LIVE on familiar) | `knowledge_graph_age.py` shared `_conn`, `autocommit=False`; `MEMPALACE_KG_WRITETHROUGH=1` set on familiar; **fixed this PR** |

**Headline: SAFE to run concurrent *retrieval-only* benches at any N the RAM
allows. Concurrent *ingest-heavy* benches are also data-safe, but cap at ~2–3
because of RAM (~6 GB free on familiar), not thread-safety. The one genuine
race — the inline AGE KG write-through — is LIVE on familiar
(`MEMPALACE_KG_WRITETHROUGH=1` is set via the daemon's systemd `EnvironmentFile`,
with `PALACE_MAX_WRITE_CONCURRENCY=2`), so this fix is REQUIRED, not merely
defensive: any two overlapping writes — concurrent benches, or even the live
companion's overlapping silent-saves — can interleave their transaction spans
and silently drop or mis-commit KG writes. This PR's per-instance lock closes
it. Deploying the patched `knowledge_graph_age.py` to familiar (sync + daemon
restart) is the gate before turning on concurrent ingest benches.**

---

## (a) Shared encoder — SAFE

Two encoder caches sit on the write path:

- `embedding.py:64` `_EF_CACHE: dict` — `get_embedding_function()` caches one
  embedding-function instance per `(model, providers)` key
  (`embedding.py:369-414`). All threads share that one instance.
- `backends/postgres.py:35` `_embedder` — a single module-global
  `DefaultEmbeddingFunction`, lazily built once in `_embed()`
  (`backends/postgres.py:59-79`).

**Why safe.** sentence-transformers / ONNX-Runtime inference is stateless per
call: `__call__(input)` tokenizes into call-local arrays and runs the session
forward pass with no shared mutable buffer
(`EmbeddinggemmaONNX.__call__`, `embedding.py:240-254`;
`AdaptMemFTEncoder.__call__`, `embedding.py:346-354`). The model object is read
during inference, never mutated. The GIL is released inside the native
torch/ONNX compute, so concurrent `.encode()` calls genuinely parallelize
across cores without corrupting each other.

**One benign note (not a bug).** The lazy `_lazy_load()` in both ONNX/ST
encoders (`embedding.py:200-238`, `329-344`) and the module-global cache
assignments are unsynchronized check-then-set. Two threads hitting the very
first call simultaneously can both load the model (transient ~300 MB double
allocation) before one wins the assignment. The loaded model is then read-only,
and the assignment itself is atomic in CPython, so the only cost is a one-time
wasted load on a cold race — no data hazard. In the daemon this is moot anyway:
the model is warmed on startup (`run_in_executor(None, _mp._get_collection,
True)` at boot), so no first-call race occurs under bench traffic.

## (b) Postgres connection / cursor scoping — SAFE (serialized)

This was the prime suspect. The chain:

- The daemon caches **one** `PostgresCollection` in the module-global
  `_collection_cache` (`palace-daemon/main.py` →
  `mempalace/mcp_server.py:_get_collection_postgres`, lines 639-705). Every
  threadpool worker reaches the same instance.
- `PostgresCollection` holds **one** psycopg connection on `self._conn`
  (`backends/postgres.py:698-703` `_get_conn`), opened with
  `autocommit = True`.
- Every read/write takes a fresh cursor off that one connection
  (`self._get_conn().cursor()`, e.g. `_insert_rows:276`, `_query_one:419`,
  `get:504`, `delete:553`).

So two concurrent write threads **do** share one psycopg connection.

**Why this is nonetheless safe (not corruption).** Installed driver is
**psycopg 3.3.4, `threadsafety = 2`**. Confirmed by source inspection
(`.venv/.../psycopg/connection.py`): `self.lock = Lock()` at line 77, and
`execute` / `commit` / `rollback` / cursor protocol exchange each run under
`with self.lock:` (lines 302-309 and throughout). The connection lock
serializes the libpq wire protocol, so two threads cannot interleave bytes and
corrupt the stream.

Because `self._conn.autocommit = True`, **each statement is its own
transaction**. Two concurrent `INSERT … ON CONFLICT (id) DO UPDATE`
(`_insert_rows:276-301`) serialize cleanly at the connection lock and commit
independently. pgvector / pg_sorted_heap `INSERT`s are MVCC-safe at the
Postgres layer regardless. The idempotent `ON CONFLICT` and content-hash drawer
ids mean even a duplicate concurrent write of the same drawer is last-write-wins
with identical data.

**Caveat — it's a serialization point, not parallelism.** The shared connection
means "concurrent" Postgres writes are actually *serialized* at the psycopg
connection lock. This is a throughput ceiling on the write path, not a safety
bug. If write throughput ever matters, the fix is a per-thread connection or a
`psycopg_pool.ConnectionPool` in `PostgresCollection` — but that is an
optimization, explicitly **out of scope** for this data-safety gate. Retrieval
benches dominate, and reads serialize the same harmless way.

The `_maybe_create_vector_index` path already does the right thing: it opens a
**dedicated side connection** and serializes the check+create with
`pg_advisory_xact_lock` (`backends/postgres.py:872-977`), so concurrent writers
crossing the HNSW-build threshold cannot stack duplicate `CREATE INDEX` builds.
That hazard was already handled (issue #73).

## (c) Module-level caches in the ingest path — SAFE

The daemon's concurrent write path (`mcp_server.tool_add_drawer`, lines
1766-1900) touches these shared structures; all are safe:

- **`IdfCache`** (`tag_extraction.py:177-235`), reached via the module-global
  `_idf_cache` (`mcp_server.py:954-975`): **properly locked**. `self._lock =
  threading.Lock()` (line 195); every `get` / store / evict / clear / `__len__`
  is wrapped in `with self._lock:` (lines 206-235). Textbook thread-safe.
- **`_HALL_KEYWORDS_CACHE`** (`miner.py:854-871`): benign idempotent lazy-init.
  Two threads can both build the same dict from config; assignment is atomic and
  the value is read-only thereafter (`.items()` only). No mutation after init.
- **`compute_novelty_tag`** → `novelty.py:46` uses `functools.lru_cache`, which
  holds its own internal lock — thread-safe by construction.
- **`validate_room` / `is_canonical_room` / `suggest_canonical`**
  (`room_taxonomy.py:36-68`): pure functions over the immutable
  `CANONICAL_ROOMS` tuple. No shared mutable state.
- **Chunking** (`mcp_server.py:1883-1890`): fully call-local lists. No shared
  state.
- **Idempotency probe-then-upsert** (`mcp_server.py:1835` then `1849`/`1890`):
  the read-then-write is not atomic across threads, but the upsert is `ON
  CONFLICT DO UPDATE` and the drawer id is a content hash, so a concurrent
  duplicate is idempotent at the DB layer.

The constant maps / frozen sets / regex-pattern lists in `entity_registry.py`,
`normalize.py`, `room_detector_local.py`, `miner.py` are read-only module data
— inherently safe.

## (d) AGE KG write-through — RACE (LIVE on familiar) → FIXED

This was the one genuine shared-mutable-state race.

**The bug.** When the inline KG write-through hook is attached
(`MEMPALACE_KG_WRITETHROUGH=1`), `palace._maybe_attach_writethrough` builds
**one** `KnowledgeGraphAGE` and captures it in the hook closure
(`palace.py:160-167`). The hook runs **synchronously on the writer's thread**
inside `PostgresCollection._insert_rows` (`backends/postgres.py:311-327`). So
two concurrent write threads share one `KnowledgeGraphAGE`, which holds **one**
psycopg connection with **`self._conn.autocommit = False`**
(`knowledge_graph_age.py:172,178`).

With `autocommit = False`, psycopg's connection lock keeps each `execute` atomic
on the wire but does **not** isolate the *transaction span*. Interleaving:

1. Thread A: `add_mention` → `_run_cypher` → `execute(MERGE…CREATE)` (lock
   released)
2. Thread B: `add_mention` → `_run_cypher` → `execute(MERGE…CREATE)` (lock
   released) — **lands in the same open transaction as A's write**
3. Thread A: `commit()` — commits **both** A's and B's uncommitted work
4. Thread B: `commit()` — commits an already-empty transaction

Worse, the error/rollback paths clobber across threads: `stats()` does
`self._conn.rollback()` on its fast-path miss (`knowledge_graph_age.py:898`),
which would discard a concurrent writer's not-yet-committed `add_mention`. Net
effect: **silently dropped or mis-attributed KG writes** under concurrency — a
data-integrity bug, exactly the class this gate exists to catch.

**This is LIVE on familiar — the fix is required.** The production daemon's
systemd unit pulls in `EnvironmentFile=/home/jp/.config/palace-daemon/env`,
which sets `MEMPALACE_KG_WRITETHROUGH=1`, `MEMPALACE_KG_EXTRACTION_QUEUE=1`,
`MEMPALACE_KG_BACKEND=age`, and `PALACE_MAX_WRITE_CONCURRENCY=2`. Verified on the
live process (MainPID after the 2026-05-29 18:59 restart): all four are present.
So `make_writethrough_from_env` returns the **chained** hook — the inline
MENTIONS write-through (`make_age_writethrough`, the racy
shared-`KnowledgeGraphAGE`-connection path) *plus* the extraction-queue enqueue
— and it IS attached to the per-request write path (`kg_writethrough.py:340-390`,
`palace._maybe_attach_writethrough`). With `PALACE_MAX_WRITE_CONCURRENCY=2`, two
write requests can be in-flight on the shared connection at once, so the
transaction-span interleave is reachable in current production whenever two
writes overlap — concurrent benches, or even the live companion issuing
overlapping silent-saves. Exposure is intermittent (only on true overlap), which
is why it had not been noticed, but it is real.

(An earlier draft of this audit rated the race "latent" after inspecting an
older daemon process started *before* the `EnvironmentFile` carried the flag.
That snapshot was stale; the corrected reading above is from the live
post-restart process and the env file itself.)

The extraction-queue half of the chained hook (`kg_triple_worker.py`, driven by
the `mempalace-kg-extract@*.service` slice) is concurrency-safe by design (see
d-queue below) — the race is specific to the inline MENTIONS half sharing one
`autocommit=False` connection. Note also that the SQLite KG
(`knowledge_graph.py`) was *already* hardened with a per-instance lock
(`self._lock`, guarding `close`/`add_triple`/etc. from line 139, asserted by
`tests/test_kg_thread_safety.py`); the AGE KG simply never inherited it. This PR
closes that gap and brings the two KG backends to parity.

**The fix (this PR).** Add `self._lock = threading.RLock()` to
`KnowledgeGraphAGE.__init__` and guard every transaction-bearing span with it:
the two chokepoints `_run_cypher` and `_cypher_scalar` (through which
`add_triple`, `add_mention`, `add_entity`, `invalidate`, `delete_drawer(s)`,
`query_*`, `timeline`, `_stats_cypher` all route), plus the direct-cursor
methods `clear`, `commit`, and `stats`. Reentrant lock so a guarded public
method (`add_triple`) can call a guarded helper (`_run_cypher`) on the same
thread without deadlock. This restores one-transaction-per-thread semantics on
the shared connection while preserving the `commit=False` bulk-batching path
that `backfill_age._BulkWriter` depends on. The `_ensure_graph` /
`_ensure_drawer_unique_index` bootstrap commits run only in `__init__`, before
the instance is shared, so they need no guard.

Mirrors the established `KnowledgeGraph` (SQLite) lock idiom exactly.

### (d-queue) Extraction-queue enqueue + async worker — SAFE by design

The extraction-queue half of the production write path (the other stage chained
into the live write-through) is concurrency-safe:

- **Enqueue write-through** (`make_extraction_enqueue_writethrough`,
  `kg_writethrough.py:215-284`): opens a **fresh psycopg connection per drawer
  write** (line 250), does an idempotent `INSERT … ON CONFLICT (drawer_id) DO
  UPDATE` (line 264), commits, closes. No shared connection. The closure-local
  `table_ensured` flag (line 241) can race two threads into running the DDL, but
  it is `CREATE TABLE/INDEX IF NOT EXISTS` — idempotent, benign.
- **Async dequeue worker** (`kg_triple_worker.py`): claims rows with
  `FOR UPDATE SKIP LOCKED` (line 164) so concurrent workers split the queue with
  no double-processing; a lease/visibility-timeout (line 161) reclaims rows from
  crashed workers; `_mark_completed` / `_mark_error` are single-row atomic
  `UPDATE`s. Postgres I/O uses an `AsyncConnectionPool` with **one connection
  per coroutine** — explicitly "no global write lock" (module docstring lines
  6-17). This is the textbook safe-concurrent-queue pattern.

## Files changed

- `mempalace/knowledge_graph_age.py` — add `threading.RLock` to
  `KnowledgeGraphAGE.__init__`; guard `_run_cypher`, `_cypher_scalar`, `clear`,
  `commit`, `stats` transaction spans.
- `tests/test_age_kg_units.py` — add `TestKGAge` thread-safety coverage: source
  introspection for the lock on each chokepoint + a functional test that two
  threads driving `_run_cypher` on one shared (fake) connection never interleave
  their `execute→commit` spans.

## Test result

`pytest tests/test_kg_thread_safety.py tests/test_age_kg_units.py
tests/test_knowledge_graph_age.py -q` → **81 passed, 23 skipped** (the 23 are
postgres-gated AGE integration tests that skip without a live DB). `ruff check`
on the two changed files → clean.

## Recommendation to the concurrency-unlock owners

1. **Retrieval-only benches: unlock to any N the RAM budget allows.** The read
   path is data-safe; the shared-connection serialization is harmless.
2. **Ingest-heavy benches: cap at ~2–3 concurrently** — the limit is RAM (~6 GB
   free on familiar), not thread-safety. Surface a RAM/memcg canary in the
   supervisor before registering, as the sibling specs propose.
3. **The daemon's `_write_sem=2` shared connection is safe** but serializes
   writes; if write throughput becomes a bottleneck, move
   `PostgresCollection` to a per-thread connection or `psycopg_pool` — an
   optimization, not a prerequisite for this gate.
4. **`MEMPALACE_KG_WRITETHROUGH=1` is set on familiar TODAY** (via the daemon's
   systemd `EnvironmentFile`), so the inline MENTIONS write-through is live and
   the transaction-span race is reachable under any two overlapping writes.
   **Merging this PR and deploying the patched `knowledge_graph_age.py` to
   familiar (sync + daemon restart) is the gate before turning on concurrent
   ingest benches** — and it also closes an intermittent corruption window that
   already exists in current production whenever two writes overlap. Without this
   PR, concurrent ingest would silently drop/mis-commit KG writes.
