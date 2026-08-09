# KG Triple Extraction — Async LLM-based Relationship Populator

> **Status:** proposed 2026-05-25
> **Owner:** JP
> **Depends on:** AGE backfill (complete), kg_writethrough (shipping), `add_triple` API (exists)

## Problem

The AGE knowledge graph has two layers:

| Layer | Edge type | Count | Source |
|---|---|---|---|
| Entity mentions | `(Drawer)-[:MENTIONS]->(Entity)` | 263K entities, 54K edges | Regex extractor via kg_writethrough |
| Structured triples | `(Entity)-[:RELATION]->(Entity)` | **1 fact** | Manual `kg_add` MCP calls only |

The MENTIONS layer powers graph-expanded hybrid search (Phase 3 of the
hybrid-search-taxonomy plan). The RELATION layer — typed facts like
`mempalace → depends_on → pgvector` or `JP → works_on → familiar` —
is empty. It would enable:

- Temporal entity queries ("what was JP working on in April?")
- Cross-project dependency maps ("what depends on pgvector?")
- Entity disambiguation ("which 'Max' — the person or the project?")
- Richer graph expansion in hybrid search (follow RELATION edges, not just co-mention)

## Why regex can't do this

The existing regex extractor finds entity *names* (capitalized words,
hyphenated identifiers) but can't infer *relationships* between them.
"We migrated from chromadb to pgvector" contains two entities; only an
LLM can produce `(mempalace, migrated_from, chromadb)`.

## Design: async post-write extraction

### Architecture

```
Drawer write path (existing, unchanged):
  PostgresCollection._insert_rows()
    → kg_writethrough hook
      → regex extractor → MENTIONS edges (fast, ~50ms)
      → enqueue drawer_id to extraction queue (new, ~1ms)

Async extraction worker (new):
  systemd timer or background thread
    → dequeue batch of drawer_ids
    → for each: read document, call LLM, parse triples
    → kg.add_triple() for each extracted fact
```

### Why async, not inline

Inline LLM extraction on every write would add ~2-5s per drawer to
the write path. Session mines produce dozens of drawers per hook
fire — that's minutes of blocking. The async worker decouples
extraction latency from write latency.

### Extraction queue

New postgres table:

```sql
CREATE TABLE IF NOT EXISTS mempalace_kg_extraction_queue (
    drawer_id    TEXT PRIMARY KEY,
    wing         TEXT,
    room         TEXT,
    queued_at    TIMESTAMPTZ DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error        TEXT
);

CREATE INDEX idx_kg_extraction_pending
  ON mempalace_kg_extraction_queue (queued_at)
  WHERE completed_at IS NULL AND started_at IS NULL;
```

The writethrough hook inserts a row on every drawer write (ON CONFLICT
DO NOTHING — re-mines of the same drawer don't re-queue). The worker
claims batches with `UPDATE ... SET started_at = NOW() WHERE
started_at IS NULL ... LIMIT N RETURNING drawer_id`.

### LLM extractor

Prompt template (per drawer):

```
Extract structured facts from this text as JSON triples.
Each triple: {"subject": "...", "predicate": "...", "object": "..."}

Rules:
- subject and object are entity names (people, projects, tools, concepts)
- predicate is a lowercase verb phrase (works_on, depends_on, created_by, migrated_from, etc.)
- Only extract facts explicitly stated, not inferred
- Skip meta-observations about the conversation itself
- Maximum 10 triples per text

Text:
{document}

Triples (JSON array):
```

Model: Phi-4-mini Q4_K_M on the P102 GPU (10GB, currently idle).
~1-2s per drawer at Q4 quantization. Structured output via
`response_format: { type: "json_object" }` if the model supports it,
otherwise regex-parse the response.

### Extractor plugin slot

`MEMPALACE_KG_EXTRACTOR` already has `regex|spacy|llm|null`.
Implement the `llm` variant:

```python
# In kg_writethrough.py, extend make_writethrough_from_env:
elif extractor_name == "llm":
    from .kg_llm_extractor import make_llm_extractor
    extractor = make_llm_extractor(
        endpoint=os.environ.get("MEMPALACE_KG_LLM_ENDPOINT"),
        model=os.environ.get("MEMPALACE_KG_LLM_MODEL", "phi-4-mini"),
    )
```

But the inline extractor only produces MENTIONS. The triple extractor
is a separate hook that writes to the queue, not to AGE directly.

### Worker implementation

New module: `mempalace/kg_triple_worker.py`

```python
def run_worker(
    dsn: str,
    llm_endpoint: str,
    model: str = "phi-4-mini",
    batch_size: int = 20,
    poll_interval: int = 30,
):
    """Poll the extraction queue and process pending drawers."""
```

CLI entry point: `mempalace-kg-extract` (registered in pyproject.toml).

Systemd timer on familiar:

```ini
[Unit]
Description=KG triple extraction worker

[Service]
Type=simple
User=jp
Environment=MEMPALACE_POSTGRES_DSN=...
Environment=MEMPALACE_KG_LLM_ENDPOINT=http://localhost:11434
ExecStart=/path/to/venv/bin/mempalace-kg-extract \
    --dsn "$MEMPALACE_POSTGRES_DSN" \
    --endpoint "$MEMPALACE_KG_LLM_ENDPOINT" \
    --model phi-4-mini \
    --batch-size 20
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Runs as a long-lived process, polling every 30s. On familiar, the P102
GPU handles inference; the worker connects to the local Postgres
(Docker :5433).

### Backfill mode

For the existing 364K drawers, the worker supports a `--backfill` flag
that reads directly from `mempalace_drawers` instead of the queue,
using the same checkpoint table pattern as `backfill_age.py`:

```bash
mempalace-kg-extract --backfill --dsn "$DSN" --batch-size 50
```

Estimated time at ~2s/drawer: 364K × 2s = ~8.4 days with one worker.
Parallelizable across GPUs (P102 + GTX 970) or by wing partitioning.

### Deduplication and quality

- Same triple extracted from multiple drawers: `add_triple` uses MERGE,
  so duplicates are idempotent.
- Low-quality extractions: `confidence` field on RELATION edges. LLM
  extractions start at 0.7 (higher than regex MENTIONS at 0.5). Can
  be tuned after eval.
- Validation: reject triples where subject == object, predicate is
  empty, or either entity is a stopword.
- Temporal: if the LLM extracts a time reference ("in April 2026"),
  pass it as `valid_from` to `add_triple`.

### Observability

- `mempalace-kg-extract --status`: print queue depth, completion rate,
  error count.
- Palace-daemon endpoint: `GET /kg-extract/status` — same counters
  as `/backfill-age/status`.
- Errors written to `extraction_queue.error` column for debugging.

## Phases

| Phase | Description | Effort |
|---|---|---|
| 1 | Queue table + writethrough enqueue hook | Small — SQL + 5-line hook addition |
| 2 | LLM extractor module (`kg_llm_extractor.py`) | Medium — prompt engineering + JSON parsing |
| 3 | Worker process (`kg_triple_worker.py` + CLI) | Medium — polling loop + checkpoint + error handling |
| 4 | Backfill mode for existing 364K drawers | Small — reuse worker with different source query |
| 5 | Systemd unit on familiar + observability | Small — unit file + status endpoint |
| 6 | Eval — sample 100 drawers, human-judge triple quality | Medium — needs a scoring rubric |

## Open questions

1. **Model choice**: Phi-4-mini (3.8B, fits P102 easily) vs Phi-4
   (14B, tighter fit). Smaller model = faster throughput, but may
   produce lower-quality triples. Eval in Phase 6 decides.
2. **Fan-out cap**: How many triples per drawer is reasonable? 10 cap
   in prompt, but some drawers are session transcripts with dozens of
   facts. Consider splitting long drawers before extraction.
3. **Predicate normalization**: LLMs produce varied predicates
   ("works_on", "is_working_on", "working_on"). Normalize to a
   canonical set, or let variety stand and normalize at query time?
4. **Re-extraction on drawer update**: When a drawer is re-mined
   (content changes), should we invalidate its old triples and
   re-extract? Probably yes — re-queue on upsert.

## Success criteria

1. Queue table exists, writethrough enqueues on every new drawer write.
2. Worker extracts triples from queued drawers at >10 drawers/min.
3. After 1000 drawers processed, `kg_stats` shows >500 RELATION edges.
4. Hybrid search with `--mode hybrid` returns results that leverage
   RELATION edges (visible in `include_trace` output).
5. Backfill processes existing palace at sustainable rate without
   impacting daemon latency.
