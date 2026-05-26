#!/usr/bin/env python3
"""Emit current KG backfill state as JSON for wave-block custom mode."""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import time

import psycopg

DSN = os.environ["MEMPALACE_POSTGRES_DSN"]
CKPT = "/tmp/kg-backfill-status.ckpt"
WINDOW_S = 90  # smoothing window for rate
MTIME_TTL_S = 60  # cache per-host source mtimes

# Where the running mempalace package lives on each host. The "deploy" target
# is what each worker process actually imports from — see
# scripts/deploy-psycopg3-cutover.sh for familiar's layout.
HOST_SRC_DIRS = {
    "katana": "/home/jp/Projects/kg-extract-katana/mempalace",
    "familiar": "/home/jp/kg-extract-deploy/mempalace",
}
SRC_FILES = (
    "kg_llm_extractor.py",
    "kg_triple_worker.py",
    "knowledge_graph_age.py",
)

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

ACTIVE_WORKERS_QUERY = """
SELECT DISTINCT worker_id
FROM mempalace_kg_extraction_queue
WHERE started_at >= now() - interval '5 minutes'
   OR completed_at >= now() - interval '5 minutes';
"""


def _local_mtime_max(host: str) -> float | None:
    """Return the newest mtime among the three watched files on `host`.

    For the local host we stat directly; remote hosts go over SSH. Any error
    returns None so the renderer can show '?' instead of crashing.
    """
    src_dir = HOST_SRC_DIRS.get(host)
    if not src_dir:
        return None
    is_local = host == os.uname().nodename
    paths = [f"{src_dir}/{f}" for f in SRC_FILES]
    if is_local:
        try:
            return max(os.stat(p).st_mtime for p in paths)
        except OSError:
            return None
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=2",
                "-o",
                "BatchMode=yes",
                host,
                f"stat -c %Y {' '.join(paths)}",
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        return max(int(x) for x in r.stdout.split())
    except ValueError:
        return None


def _proc_start_epoch(host: str, pid: int) -> float | None:
    """Return the process start time as a unix timestamp, or None if unknown.

    Uses `ps -p <pid> -o lstart=` which prints e.g. "Tue May 26 05:13:27 2026".
    """
    is_local = host == os.uname().nodename
    cmd_local = ["ps", "-p", str(pid), "-o", "lstart="]
    try:
        if is_local:
            r = subprocess.run(cmd_local, capture_output=True, text=True, timeout=2)
        else:
            r = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=2",
                    "-o",
                    "BatchMode=yes",
                    host,
                    f"ps -p {pid} -o lstart=",
                ],
                capture_output=True,
                text=True,
                timeout=4,
            )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    if not out:
        return None
    try:
        return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _load_state() -> dict:
    if not os.path.exists(CKPT):
        return {}
    try:
        with open(CKPT) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    with open(CKPT, "w") as f:
        json.dump(state, f)


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


with psycopg.connect(DSN, connect_timeout=5) as conn:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        pending, in_flight, completed, errors, triples_total, total = cur.fetchone()
        cur.execute(ACTIVE_WORKERS_QUERY)
        active_workers = [row[0] for row in cur.fetchall() if row[0]]

now = time.time()
state = _load_state()
history = state.get("history", [])
mtime_cache = state.get("mtime_cache", {})  # host -> {"t": epoch, "mtime": epoch}

# Refresh per-host mtime cache (60s TTL) only for hosts we have active workers on.
needed_hosts = {wid.split(":", 1)[0] for wid in active_workers if ":" in wid}
for host in needed_hosts:
    cached = mtime_cache.get(host) or {}
    if now - float(cached.get("t", 0)) < MTIME_TTL_S and cached.get("mtime") is not None:
        continue
    mtime_cache[host] = {"t": now, "mtime": _local_mtime_max(host)}

# Per-worker drift check. Process start time is cheap (ps) and reflects the
# live state, so we do not cache it.
workers_out = []
for wid in sorted(active_workers):
    parts = wid.split(":")
    if len(parts) < 2:
        continue
    host, pid_s = parts[0], parts[1]
    try:
        pid = int(pid_s)
    except ValueError:
        continue
    start_epoch = _proc_start_epoch(host, pid)
    mtime = (mtime_cache.get(host) or {}).get("mtime")
    if start_epoch is not None and mtime is not None:
        stale = start_epoch < mtime
    else:
        stale = None  # unknown — render as '?'
    workers_out.append(
        {
            "id": wid,
            "host": host,
            "pid": pid,
            "started": _iso(start_epoch),
            "src_mtime": _iso(mtime),
            "stale": stale,
        }
    )

# Ring-buffer of (t, completed) checkpoints; rate computed from the
# oldest sample inside the WINDOW_S window. This smooths over postgres
# commit batching and worker-burst dynamics that confuse a 5s diff.
history.append({"t": now, "completed": completed})
# drop samples older than 2x WINDOW_S so file does not grow forever
history = [s for s in history if now - s["t"] <= 2 * WINDOW_S]
_save_state({"history": history, "mtime_cache": mtime_cache})

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
    "workers": workers_out,
}

# wave-block.py custom mode auto-flattens one level but renders only scalar
# values — emit a per-worker scalar row so the dashboard shows drift inline.
stale_count = sum(1 for w in workers_out if w["stale"] is True)
out["workers_stale"] = stale_count
for i, w in enumerate(workers_out):
    prefix = f"w{i}"
    if w["stale"] is True:
        tag = "STALE"
    elif w["stale"] is False:
        tag = "fresh"
    else:
        tag = "?"
    out[f"{prefix}_who"] = f"{w['host']}:{w['pid']}"
    out[f"{prefix}_state"] = tag
print(json.dumps(out))
