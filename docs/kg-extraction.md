# KG Triple Extraction

LLM-based extraction of typed relationship facts from drawer content
into the AGE knowledge graph. Complements the existing regex
`MENTIONS` extractor with structured `(subject)-[:RELATION]->(object)`
triples that enable temporal queries, dependency maps, and
relationship-aware graph search.

## Architecture

```
┌──────────────────────┐
│ Drawer write path    │  PostgresCollection._insert_rows()
│ (kg_writethrough)    │  ├─ regex → MENTIONS edges (50ms, inline)
│                      │  └─ enqueue drawer_id → extraction queue (1ms)
└──────────────────────┘
                │
                ▼
┌──────────────────────┐   mempalace_kg_extraction_queue
│ Queue table          │   (drawer_id, queued_at, started_at,
│                      │    completed_at, error)
└──────────────────────┘
                │
                ▼
┌──────────────────────┐   asyncio + semaphore(N)
│ Worker (systemd)     │   ├─ UPDATE ... SKIP LOCKED claim
│ kg_triple_worker.py  │   ├─ POST to llama-server
│                      │   ├─ parse JSON triples
│                      │   └─ kg.add_triple() per fact
└──────────────────────┘
                │
                ▼
┌──────────────────────┐
│ AGE knowledge graph  │   (Entity)-[:RELATION {confidence}]->(Entity)
└──────────────────────┘
```

Two key invariants:

- **Idempotent.** `add_triple` uses `MERGE`, so re-processing a drawer
  is a no-op. SIGTERM and SIGKILL are both safe.
- **Resumable.** The queue table is the cursor. Stop and restart the
  worker; it picks up where it left off via `SKIP LOCKED`.

## Install

On familiar (where the worker and llama-server both run):

```bash
# 1. install the package
cd /opt/mempalace
sudo -u jp pip install -e .

# 2. create the env file
sudo install -d -m 0750 -o root -g jp /etc/mempalace
sudo install -m 0640 -o root -g jp \
  deploy/systemd/kg-extract.env.example /etc/mempalace/kg-extract.env
sudo editor /etc/mempalace/kg-extract.env   # set MEMPALACE_POSTGRES_DSN

# 3. install + enable the unit (llama-server-extractor must be up first)
sudo install -m 0644 deploy/systemd/mempalace-kg-extract.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mempalace-kg-extract.service
```

The unit `Wants=llama-server-extractor.service` so starting it brings
llama-server up if it isn't already.

## Backfill the existing palace

The writethrough hook only enqueues drawers written *after* it was
installed. For the existing 364K drawers, run the backfill driver:

```bash
# default — 24 in-flight workers, batch of 100
python scripts/backfill_kg_triples.py

# custom tuning
python scripts/backfill_kg_triples.py --workers 16 --batch-size 50 --poll-interval 30
```

The driver:

- Wraps `mempalace-kg-extract --backfill --workers N --batch-size N`.
- Emits one-line progress every 60s:
  ```
  drawers_completed=12345 in_flight=7 pending=350000 rate=24.6/min errors=12 eta=10.2d elapsed=1800s
  ```
- Releases in-flight queue rows on SIGTERM so a restart re-claims them.
- Is resumable — the queue table itself is the cursor. Kill and re-run.

For true CPU parallelism, the queue claim uses `UPDATE ... SKIP LOCKED`,
so multiple processes can run side-by-side trivially:

```bash
# four parallel backfill processes
for i in 1 2 3 4; do
  python scripts/backfill_kg_triples.py --workers 8 \
    > /var/log/mempalace/backfill-$i.log 2>&1 &
done
```

At ~25 drawers/min per worker, 364K drawers takes ~10 days with a
single 24-worker process — or ~2.5 days with four parallel processes.

## Observability

### Worker status

```bash
mempalace-kg-extract --status
```

Prints queue depth, in-flight, completed today, errors, and recent
throughput in plain text.

### Daemon endpoint

```bash
curl -H "X-API-Key: $PALACE_API_KEY" \
  http://familiar:8085/kg-extract/status | jq
```

Returns JSON with queue depth, in-flight workers, and throughput.
Pair with the existing `/backfill-age/status` endpoint for a
complete picture of graph-population state.

### Journal tail

```bash
# systemd-managed worker
journalctl -u mempalace-kg-extract.service -f

# backfill driver (foreground or via systemd run)
journalctl -t backfill-kg-triples -f
```

### AGE-side counters

```bash
mempalace kg-stats | jq '.relationships'
```

After a successful backfill on the full palace, expect 100K+ RELATION
edges across the typical predicate set (`works_on`, `depends_on`,
`migrated_from`, `lives_in`, …). Tune from there.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `error: connection refused` on extractor | llama-server not running | `systemctl status llama-server-extractor` and start it |
| Queue stalls (no `completed_at` advancing) | Worker crashed leaving in-flight claims | Backfill driver SIGTERM hook releases these; SIGKILL needs manual `UPDATE ... SET started_at = NULL WHERE started_at < NOW() - INTERVAL '10 minutes'` |
| Duplicate-looking triples | Different drawers, same fact | Expected — `MERGE` makes it idempotent at the AGE layer. Confidence is averaged across sources. |
| `cuda: out of memory` in llama-server log | `--parallel` too high for VRAM | Lower llama-server `--parallel` (currently 8 on P102 10GB) |
| `errors_total` climbing in status endpoint | Malformed LLM JSON output | Check `error` column on the queue table; usually a context-length overflow. Drawer-splitter (spec open question #2) addresses this. |

## Tuning

| Knob | Default | Notes |
|---|---|---|
| `--workers` (driver / unit) | 24 (driver), 8 (unit) | In-flight HTTP requests to llama-server. Above llama-server's `--parallel` (8), excess requests queue at the server with no extra throughput. |
| `--batch-size` | 100 (driver), 20 (unit) | Drawers claimed per dequeue round-trip. Larger = fewer round-trips but bigger claim window (more rows orphaned on SIGKILL). |
| `--poll-interval` | 30 | Seconds the worker sleeps between dequeues when the queue is empty. Lower = quicker resumption after a write burst, higher = lighter DB load. |
| llama-server `--parallel` | 8 | Concurrent inference slots on the P102. Bumping above 8 risks OOM at Q4. |
| DB connection pool | psycopg2 default | Each worker opens 1-2 connections. With 24 in-flight + 4 backfill processes that's ~100 connections — well under postgres's default `max_connections=100` but worth bumping if running many parallel backfills. |

## See also

- `mempalace/kg_writethrough.py` — extraction-queue enqueue hook
- `mempalace/kg_llm_extractor.py` — LLM client + JSON parsing
- `mempalace/kg_triple_worker.py` — worker loop + atomic queue claim
- `scripts/backfill_kg_triples.py` — 24-thread backfill driver
