# `mempalace.kg_triple_worker`

Source: [`mempalace/kg_triple_worker.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/kg_triple_worker.py)

Async worker that drains ``mempalace_kg_extraction_queue``.

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

## Classes

### `class WorkerStats`

Lightweight in-process counters that wrap the queue table snapshot.

#### `snapshot`

```python
def snapshot(self) -> dict
```

## Functions

### `run_worker`

```python
async def run_worker(dsn: str, llm_endpoint: str = DEFAULT_ENDPOINT, model: str = DEFAULT_MODEL, *, batch_size: int = DEFAULT_BATCH_SIZE, poll_interval: int = DEFAULT_POLL_INTERVAL, max_concurrency: int = DEFAULT_CONCURRENCY, worker_id: Optional[str] = None, backfill: bool = False, backfill_limit: Optional[int] = None, once: bool = False, pool_factory: Optional[Callable[[str, int, int], _SyncConnPool]] = None, kg_factory: Optional[Callable[[str], Any]] = None, http_client_factory: Optional[Callable[[], Any]] = None, stats: Optional[WorkerStats] = None, stop_event: Optional[asyncio.Event] = None) -> WorkerStats
```

Drain the extraction queue until cancelled (or ``once=True``).

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

### `get_status`

```python
def get_status(dsn: str) -> dict
```

One-shot status query used by the CLI's ``--status`` flag.

### `cli_main`

```python
def cli_main(argv: Optional[list[str]] = None) -> int
```
