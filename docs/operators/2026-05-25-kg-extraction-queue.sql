-- KG triple extraction queue
--
-- Created 2026-05-25 for the async LLM-based RELATION-edge populator
-- (see docs/specs/kg-triple-extraction.md, Phase 1).
--
-- The writethrough hook in mempalace/kg_writethrough.py inserts a row
-- here on every drawer write when MEMPALACE_KG_EXTRACTION_QUEUE=1.
-- An out-of-process worker (mempalace/kg_triple_worker.py, Phase 3)
-- claims pending rows, calls a local LLM, and writes resulting
-- (subject, predicate, object) triples into AGE.
--
-- The hook also calls _ensure_extraction_queue_table() lazily, so this
-- file is mostly a paper trail / runbook artefact — applying it by
-- hand is safe and idempotent (IF NOT EXISTS everywhere).

CREATE TABLE IF NOT EXISTS mempalace_kg_extraction_queue (
    drawer_id         TEXT PRIMARY KEY,
    wing              TEXT,
    room              TEXT,
    queued_at         TIMESTAMPTZ DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    error             TEXT,
    worker_id         TEXT,
    triples_extracted INT
);

-- Pending-only partial index: the worker's "claim next batch" query
-- scans this. Keeping it partial means it stays tiny no matter how
-- many drawers we've already processed.
CREATE INDEX IF NOT EXISTS idx_kg_extraction_pending
  ON mempalace_kg_extraction_queue (queued_at)
  WHERE completed_at IS NULL AND started_at IS NULL;
