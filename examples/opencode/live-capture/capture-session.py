#!/usr/bin/env python3
"""OpenCode → palace-daemon live capture (POST one session to /silent-save).

Reads OpenCode's local SQLite session DB, extracts the role-pair transcript
for one session using the in-tree ``OpenCodeSourceAdapter`` (RFC 002 contract),
and POSTs the result to the daemon's ``/silent-save`` endpoint.

Why this exists
---------------

``opencode-plugin-mempalace`` v1.2.1's mining path is broken in two ways
for daemon-routed setups:

1. It subscribes to ``chat.message``, which OpenCode never publishes. The
   message counter never increments and the plugin never mines. (Filed
   upstream as `option-K#4 <https://github.com/option-K/opencode-plugin-mempalace/issues/4>`_.)
2. Even with #1 patched, it shells out to ``mempalace mine <dir>``. With a
   remote palace-daemon, the daemon evaluates ``<dir>`` against ITS OWN
   filesystem — not the client's — and returns 400 because the local path
   doesn't exist there. (Filed upstream as `option-K#5
   <https://github.com/option-K/opencode-plugin-mempalace/issues/5>`_.)

This script sidesteps both bugs by reading opencode.db client-side and
POSTing drawer content directly. It's intentionally minimal:

* No CLI deps beyond Python stdlib + the daemon URL/API key from env.
* Idempotent: the daemon uses the ``session_id + entry hash`` as the
  silent-save key, so re-runs don't duplicate drawers (this is also how
  Claude Code's stop hook behaves).
* Wing routing: takes ``--wing`` or derives ``wing_<sanitized_basename>``
  from ``--cwd``, matching the live-capture plugin's taxonomy.

Usage
-----

::

    capture-session.py --session-id ses_abc123 --cwd /home/jp/Projects/foo

    # Or by latest N sessions:
    capture-session.py --recent 5

Env
---

* ``PALACE_DAEMON_URL`` — required (e.g. ``http://localhost:8085``).
* ``PALACE_API_KEY``    — required (passed as ``X-API-Key`` header).

Exit codes
----------

* 0 — success (drawer POST returned 200)
* 1 — daemon error / network / missing env
* 2 — session not found in opencode.db
* 3 — empty transcript (nothing to save)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

# We rely on the in-tree adapter helpers (the same code the
# OpenCodeSourceAdapter calls). Importing keeps the transcript exactly
# what `mempalace mine --source opencode` would emit when that CLI lands.
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[2]  # examples/opencode/live-capture -> repo root
sys.path.insert(0, str(REPO_ROOT))

from mempalace.sources.opencode import (  # noqa: E402
    _resolve_db,
    _extract_session_messages,
    _session_transcript,
)


def _sanitize_wing(name: str) -> str:
    """Normalize a string into a wing name (mirrors plugin getWingFromPath)."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
    return f"wing_{cleaned}" if cleaned else "wing_opencode"


def _wing_from_cwd(cwd: Optional[str]) -> str:
    if not cwd:
        return "wing_opencode"
    return _sanitize_wing(Path(cwd).name)


def _recent_session_ids(conn: sqlite3.Connection, limit: int) -> List[str]:
    rows = conn.execute(
        "SELECT id FROM session ORDER BY time_created DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def _session_meta(conn: sqlite3.Connection, session_id: str) -> Tuple[Optional[str], int]:
    """Return (directory, message_count) for the session, or (None, 0).

    The OpenCode session schema stores ``directory`` as a top-level column
    (verified on opencode-ai 1.15.7). Older versions kept it inside a
    ``data`` JSON column; fall back gracefully if the column isn't present.
    """
    directory: Optional[str] = None
    try:
        row = conn.execute("SELECT directory FROM session WHERE id=?", (session_id,)).fetchone()
        if row:
            directory = row[0]
    except sqlite3.OperationalError:
        # Older schema with `data` JSON column
        row = conn.execute("SELECT data FROM session WHERE id=?", (session_id,)).fetchone()
        if row:
            try:
                sj = json.loads(row[0])
                directory = sj.get("directory")
            except (json.JSONDecodeError, TypeError):
                pass
    n = conn.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (session_id,)).fetchone()[0]
    return directory, int(n)


def _post_silent_save(
    daemon_url: str,
    api_key: str,
    session_id: str,
    wing: str,
    entry: str,
    topic: Optional[str],
    message_count: int,
) -> dict:
    body = json.dumps(
        {
            "session_id": session_id,
            "wing": wing,
            "entry": entry,
            "topic": topic or "opencode session",
            "agent_name": "opencode-live-capture",
            "message_count": message_count,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{daemon_url.rstrip('/')}/silent-save",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _capture_one(
    conn: sqlite3.Connection,
    daemon_url: str,
    api_key: str,
    session_id: str,
    wing_override: Optional[str],
    cwd_override: Optional[str],
    dry_run: bool,
) -> int:
    pairs = _extract_session_messages(conn, session_id)
    if not pairs:
        print(f"[capture] {session_id}: empty transcript — skipped", file=sys.stderr)
        return 3
    transcript = _session_transcript(pairs)
    directory, total_messages = _session_meta(conn, session_id)
    wing = wing_override or _wing_from_cwd(cwd_override or directory)
    # Daemon's silent_save rejects topics with path separators (validates as
    # a path-safe slug). Use a flat session id form.
    topic = f"opencode_session_{session_id}"
    if dry_run:
        print(
            f"[capture] DRY-RUN session={session_id} wing={wing} "
            f"messages={total_messages} entry_chars={len(transcript)}"
        )
        return 0
    try:
        result = _post_silent_save(
            daemon_url, api_key, session_id, wing, transcript, topic, total_messages
        )
    except urllib.error.HTTPError as e:
        print(
            f"[capture] {session_id}: HTTP {e.code} — {e.read().decode('utf-8', 'replace')[:300]}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as e:
        print(f"[capture] {session_id}: network error — {e}", file=sys.stderr)
        return 1
    entry_id = result.get("entry_id", "?")
    count = result.get("count", "?")
    print(f"[capture] {session_id}: saved entry_id={entry_id} count={count} wing={wing}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--session-id", help="OpenCode session id (e.g. ses_abc123)")
    parser.add_argument(
        "--recent",
        type=int,
        default=0,
        help="Capture the N most recent sessions (skip --session-id when set)",
    )
    parser.add_argument(
        "--cwd",
        help="Project working directory (used to derive wing if no --wing given)",
    )
    parser.add_argument("--wing", help="Explicit wing override")
    parser.add_argument("--db", help="Path to opencode.db (default: auto-detect)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be POSTed; don't write"
    )
    args = parser.parse_args(argv)

    daemon_url = os.environ.get("PALACE_DAEMON_URL")
    api_key = os.environ.get("PALACE_API_KEY")
    if not daemon_url or not api_key:
        print(
            "PALACE_DAEMON_URL and PALACE_API_KEY must be set (try `source ~/.config/palace-daemon/env`).",
            file=sys.stderr,
        )
        return 1

    try:
        db_path = _resolve_db(args.db)
    except Exception as e:
        print(f"could not locate opencode.db: {e}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        if args.recent > 0:
            sids = _recent_session_ids(conn, args.recent)
        elif args.session_id:
            sids = [args.session_id]
        else:
            parser.error("--session-id or --recent N is required")
            return 1
        rc = 0
        for sid in sids:
            rc = (
                _capture_one(conn, daemon_url, api_key, sid, args.wing, args.cwd, args.dry_run)
                or rc
            )
        return rc
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
