"""Decision logger for the auto-query system.

Append-only JSONL log of every auto-query decision (fire, skip, dry-run-skip).
Supports tail-efficient reads and size-based rotation with 3 kept generations.
"""

import dataclasses
import json
import os
from typing import Optional

from . import Decision

_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".mempalace", "auto_query")
_LOG_NAME = "decisions.jsonl"
_MAX_ROTATIONS = 3
_DEFAULT_MAX_BYTES = 10_000_000  # 10 MB


def _log_path(log_dir: Optional[str] = None) -> str:
    return os.path.join(log_dir or _DEFAULT_DIR, _LOG_NAME)


def _serialize_decision(decision: Decision) -> str:
    """Serialize a Decision to a JSON string.

    Handles set fields in nested dataclasses by converting them to sorted
    lists so the output is deterministic and JSON-safe.
    """
    raw = dataclasses.asdict(decision)

    # Walk the dict looking for sets (e.g. SessionState.queried_entities
    # when embedded in signals).  Convert to sorted lists.
    def _fixup(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, dict):
            return {k: _fixup(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_fixup(v) for v in obj]
        return obj

    return json.dumps(_fixup(raw), separators=(",", ":"))


def append_decision(decision: Decision, log_dir: Optional[str] = None) -> None:
    """Append a decision to the JSONL log file.

    Creates the log directory lazily on first write and sets file
    permissions to 0o600 (owner-only) since the log may contain
    entity names and query text from private conversations.
    """
    path = _log_path(log_dir)
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, mode=0o700, exist_ok=True)

    line = _serialize_decision(decision) + "\n"

    # Open in append mode; create with restricted permissions if new.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_decisions(last_n: int = 50, log_dir: Optional[str] = None) -> list:
    """Read the last *last_n* decisions from the log file.

    Returns a list of dicts (parsed JSON).  Corrupt or partial trailing
    lines are silently skipped so a crash mid-write never prevents reads.
    """
    path = _log_path(log_dir)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    results = []  # type: list[dict]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            # Corrupt / partial line — skip it.
            continue

    return results[-last_n:]


def rotate_log(log_dir: Optional[str] = None, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
    """Rotate the log file if it exceeds *max_bytes*.

    Rotation scheme::

        decisions.jsonl   -> decisions.jsonl.1
        decisions.jsonl.1 -> decisions.jsonl.2
        decisions.jsonl.2 -> decisions.jsonl.3
        decisions.jsonl.3 -> (deleted)

    Keeps at most ``_MAX_ROTATIONS`` (3) old generations.
    """
    path = _log_path(log_dir)

    try:
        size = os.path.getsize(path)
    except OSError:
        return

    if size < max_bytes:
        return

    # Shift existing rotations up: .3 is dropped, .2->.3, .1->.2
    for i in range(_MAX_ROTATIONS, 0, -1):
        src = path if i == 1 else "{}.{}".format(path, i - 1)
        dst = "{}.{}".format(path, i)
        try:
            if i == _MAX_ROTATIONS and os.path.exists(dst):
                os.remove(dst)
            if os.path.exists(src):
                os.rename(src, dst)
        except OSError:
            continue

    # After renaming the current file to .1, the path is free for new writes.
    # Nothing else to do — next append_decision() will recreate the file.
