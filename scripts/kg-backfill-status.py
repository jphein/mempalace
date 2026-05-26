#!/usr/bin/env python3
"""Emit current KG backfill state as JSON for wave-block custom mode."""
from __future__ import annotations

import json
import os
import time

import psycopg

DSN = os.environ["MEMPALACE_POSTGRES_DSN"]
CKPT = "/tmp/kg-backfill-status.ckpt"
WINDOW_S = 90  # smoothing window for rate

QUERY = """
SELECT
  COUNT(*) FILTER (WHERE completed_at IS NULL AND error IS NULL AND started_at IS NULL) AS pending,
  COUNT(*) FILTER (WHERE started_at IS NOT NULL AND completed_at IS NULL) AS in_flight,
  COUNT(*) FILTER (WHERE completed_at IS NOT NULL) AS completed,
  COUNT(*) FILTER (WHERE error IS NOT NULL) AS errors,
  COALESCE(SUM(triples_extracted), 0) AS triples_total,
  COUNT(*) AS total
FROM mempalace_kg_extraction_queue;
"""
with psycopg.connect(DSN, connect_timeout=5) as conn:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        pending, in_flight, completed, errors, triples_total, total = cur.fetchone()

now = time.time()

# Ring-buffer of (t, completed) checkpoints; rate computed from the
# oldest sample inside the WINDOW_S window. This smooths over postgres
# commit batching and worker-burst dynamics that confuse a 5s diff.
history = []
if os.path.exists(CKPT):
    try:
        with open(CKPT) as f:
            history = json.load(f).get("history", [])
    except Exception:
        history = []
history.append({"t": now, "completed": completed})
# drop samples older than 2x WINDOW_S so file does not grow forever
history = [s for s in history if now - s["t"] <= 2 * WINDOW_S]
with open(CKPT, "w") as f:
    json.dump({"history": history}, f)

# Find the oldest sample inside WINDOW_S; if none yet, rate stays 0.
rate = 0.0
oldest = next((s for s in history if now - s["t"] <= WINDOW_S), None)
if oldest and oldest["t"] != now:
    dt = now - oldest["t"]
    rate = (completed - oldest["completed"]) / dt * 60.0

percent = (completed / total * 100.0) if total else 0.0
eta_hours = (pending / rate / 60.0) if rate > 0 else 0.0
out = {
    "completed": int(completed),
    "pending": int(pending),
    "in_flight": int(in_flight),
    "errors": int(errors),
    "triples": int(triples_total),
    "rate_per_min": round(rate, 1),
    "percent": round(percent, 2),
    "eta_hours": round(eta_hours, 1),
    "total": int(total),
}
print(json.dumps(out))
