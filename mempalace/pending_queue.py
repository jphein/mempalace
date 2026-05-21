"""Append-only journal for mine requests that couldn't reach the daemon.

When ``PALACE_DAEMON_URL`` is set but the daemon (or its backend) is
unreachable, ``_post_daemon_mine`` would previously log the failure and
drop the request. With daemon-strict mode in effect, hooks don't fall
back to a local mine — so a stale daemon means silent write loss for
the duration of the outage.

This module captures the dropped requests as JSONL lines under
``~/.mempalace/pending/YYYY-MM-DD.jsonl`` and exposes a ``replay``
function that re-issues them when the daemon recovers. Lines that
succeed are removed from the file; lines that fail stay for the next
attempt. Atomic rewrite via ``tempfile + os.replace`` prevents partial
file corruption on crash mid-drain.

The request payload is tiny — ``{dir, wing, mode, ts}`` — because the
transcript file the daemon actually mines lives on disk and survives
the outage. We don't archive conversation content here; the file on
disk is the durable source.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

PENDING_DIR = Path.home() / ".mempalace" / "pending"


@dataclass(frozen=True)
class ReplayReport:
    """Outcome of a replay sweep."""

    attempted: int
    succeeded: int
    failed: int
    files_drained: int

    @property
    def is_empty(self) -> bool:
        return self.attempted == 0


def enqueue(request: dict, *, now: datetime | None = None) -> Path:
    """Append a pending mine request to today's queue file.

    ``request`` must contain at minimum ``dir``, ``wing``, ``mode``. A
    UTC ``ts`` field is added automatically. Writes are flushed and
    fsynced before return so a crash immediately after enqueue cannot
    lose the line.
    """
    for required in ("dir", "wing", "mode"):
        if required not in request:
            raise ValueError(f"pending queue request missing field: {required!r}")

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    ts_now = now if now is not None else datetime.now(tz=timezone.utc)
    line_obj = {**request, "ts": ts_now.isoformat()}
    path = PENDING_DIR / f"{ts_now.strftime('%Y-%m-%d')}.jsonl"
    line = json.dumps(line_obj, sort_keys=True, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return path


def pending_count(directory: Path | None = None) -> int:
    """Return the number of queued (not-yet-replayed) requests.

    Useful for the session-start warning. Cheap: counts non-empty
    lines without parsing JSON.
    """
    base = directory if directory is not None else PENDING_DIR
    if not base.is_dir():
        return 0
    total = 0
    for path in base.glob("*.jsonl"):
        try:
            with open(path, "rb") as f:
                for raw in f:
                    if raw.strip():
                        total += 1
        except OSError:
            continue
    return total


def _dedupe(lines: Iterable[str]) -> list[str]:
    """Drop duplicate (dir, wing, mode) requests, keeping the newest ts.

    During a long outage the same (dir, wing, mode) tuple may be
    enqueued dozens of times (one per Stop hook fire). Replaying all
    of them is wasteful — the daemon-side mine is idempotent but each
    call still costs a round-trip. Dedupe on the way out keeps replay
    proportional to the number of *distinct* targets, not the number
    of fires.

    Malformed lines (bad JSON) are dropped silently — they were
    unreplayable anyway.
    """
    seen: dict[tuple[str, str, str], dict] = {}
    for raw in lines:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        key = (obj.get("dir", ""), obj.get("wing", ""), obj.get("mode", ""))
        prev = seen.get(key)
        if prev is None or obj.get("ts", "") >= prev.get("ts", ""):
            seen[key] = obj
    # Preserve oldest-first ordering so older targets are tried first.
    return [json.dumps(obj, sort_keys=True, ensure_ascii=False) for obj in sorted(seen.values(), key=lambda o: o.get("ts", ""))]


def replay(
    post_fn: Callable[[dict], bool],
    *,
    directory: Path | None = None,
) -> ReplayReport:
    """Drain the queue by re-issuing each request via ``post_fn``.

    ``post_fn(request) -> bool`` is the caller's hook to actually
    transmit one request. ``True`` means the daemon accepted it and
    the line should be consumed; ``False`` means keep it for next
    time. Anything that raises is treated as ``False`` — replay is a
    best-effort background sweep and must not abort partway just
    because one request blew up.

    File-level rewrite is atomic via ``tempfile + os.replace`` so a
    crash mid-drain cannot truncate or duplicate lines.
    """
    base = directory if directory is not None else PENDING_DIR
    if not base.is_dir():
        return ReplayReport(0, 0, 0, 0)

    attempted = succeeded = failed = files_drained = 0
    for path in sorted(base.glob("*.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                raw_lines = [line.rstrip("\n") for line in f if line.strip()]
        except OSError:
            continue
        if not raw_lines:
            try:
                path.unlink()
                files_drained += 1
            except OSError:
                pass
            continue

        unique_lines = _dedupe(raw_lines)
        remaining: list[str] = []
        for line in unique_lines:
            attempted += 1
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                failed += 1
                continue
            try:
                ok = bool(post_fn(request))
            except Exception:
                ok = False
            if ok:
                succeeded += 1
            else:
                failed += 1
                remaining.append(line)

        if not remaining:
            try:
                path.unlink()
                files_drained += 1
            except OSError:
                pass
        elif len(remaining) != len(raw_lines):
            # Rewrite atomically with only the still-pending lines.
            try:
                fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(base))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                        tmp.write("\n".join(remaining) + "\n")
                        tmp.flush()
                        os.fsync(tmp.fileno())
                    os.replace(tmp_path, path)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            except OSError:
                # Leave the file as-is; next replay will retry everything.
                pass

    return ReplayReport(attempted, succeeded, failed, files_drained)
