#!/usr/bin/env python3
"""One-off wing-hygiene migration for the production drawers table (issue #381).

Two passes, both DRY-RUN BY DEFAULT — nothing mutates without ``--commit``:

1. **Empty-wing pass** — drawers filed under ``wing = ''`` (the schema's
   column default, reachable before the ``_coerce_wing`` write guard landed).
   The 54 known rows are 2026-05-12 smoke-test debris (``cross-thresh-*``,
   ``debug-trigger-*``, ``persist-trigger-*``, ``plarge-*``, ``ptiny-*``);
   those are only deleted with an explicit ``--delete-debris`` (deleting even
   test rows from a production palace is an operator decision). Empty-wing
   rows that do NOT match the debris patterns are re-filed to ``general``.

2. **Merge pass** — collapses known near-duplicate wings into their
   canonical names (separator/case forks and misspellings):

   - ``kiyo-xhci-fix``          → ``kiyo_xhci_fix``
   - ``sdpdisability_appeal``   → ``sdp_disability_appeal``
   - ``mempalace``              → ``memorypalace``
   - ``daemon``                 → ``palace_daemon``

   ``familiar`` (35) vs ``familiar_realm_watch`` (29K+) is deliberately NOT
   merged — the issue flags it as possibly intentional; decide separately.

Usage::

    # Preview everything (read-only):
    MEMPALACE_POSTGRES_DSN=postgresql://... python scripts/wing_hygiene.py

    # Re-file empties + merge near-duplicates:
    python scripts/wing_hygiene.py --commit

    # Also delete the known smoke-test debris:
    python scripts/wing_hygiene.py --commit --delete-debris

    # Extra ad-hoc merge on top of the built-ins:
    python scripts/wing_hygiene.py --commit --merge old_wing=new_wing
"""

from __future__ import annotations

import argparse
import os
import re
import sys

DEFAULT_TABLE = "mempalace_drawers"
REFILE_WING = "general"

# Near-duplicate wings observed in the 2026-08-02 health check (issue #381),
# mapped onto their canonical names. Order is stable for readable output.
DEFAULT_MERGES = [
    ("kiyo-xhci-fix", "kiyo_xhci_fix"),
    ("sdpdisability_appeal", "sdp_disability_appeal"),
    ("mempalace", "memorypalace"),
    ("daemon", "palace_daemon"),
]

# ID shapes of the known 2026-05-12 smoke-test leftovers. Anchored and
# specific on purpose: anything not matching is treated as a real memory
# and re-filed, never deleted.
DEBRIS_ID_RE = re.compile(
    r"^(?:cross-thresh-\d+(?:-\d+)?|debug-trigger-\d+|persist-trigger-\d+"
    r"|plarge-\d+(?:\.\d+)?|ptiny-\d+(?:\.\d+)?)$"
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _connect(dsn: str):
    try:
        import psycopg

        return psycopg.connect(dsn, autocommit=True)
    except ImportError:
        import psycopg2

        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn


def _table_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise SystemExit(f"unsafe table name: {name!r}")
    return f'"{name}"'


def empty_wing_pass(cur, table: str, commit: bool, delete_debris: bool) -> None:
    cur.execute(f"SELECT id FROM {table} WHERE wing = ''")
    rows = [r[0] for r in cur.fetchall()]
    debris = [i for i in rows if DEBRIS_ID_RE.match(i)]
    keep = [i for i in rows if not DEBRIS_ID_RE.match(i)]
    print(
        f"empty-wing rows: {len(rows)} total — {len(debris)} smoke-test debris, "
        f"{len(keep)} real drawers to re-file → '{REFILE_WING}'"
    )

    if debris:
        if commit and delete_debris:
            cur.execute(f"DELETE FROM {table} WHERE wing = '' AND id = ANY(%s)", (debris,))
            print(f"  deleted {cur.rowcount} debris rows")
        else:
            print(
                f"  would delete {len(debris)} debris rows "
                f"(needs --commit --delete-debris); sample: {debris[:5]}"
            )

    if keep:
        if commit:
            cur.execute(
                f"UPDATE {table} SET wing = %s WHERE wing = '' AND id = ANY(%s)",
                (REFILE_WING, keep),
            )
            print(f"  re-filed {cur.rowcount} rows → '{REFILE_WING}'")
        else:
            print(f"  would re-file {len(keep)} rows → '{REFILE_WING}'; sample: {keep[:5]}")


def merge_pass(cur, table: str, merges: list[tuple[str, str]], commit: bool) -> None:
    for src, dst in merges:
        cur.execute(f"SELECT count(*) FROM {table} WHERE wing = %s", (src,))
        n_src = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {table} WHERE wing = %s", (dst,))
        n_dst = cur.fetchone()[0]
        if n_src == 0:
            print(f"merge {src!r} → {dst!r}: nothing to do (0 rows)")
            continue
        if commit:
            cur.execute(f"UPDATE {table} SET wing = %s WHERE wing = %s", (dst, src))
            print(f"merge {src!r} → {dst!r}: moved {cur.rowcount} rows (destination had {n_dst})")
        else:
            print(f"merge {src!r} → {dst!r}: would move {n_src} rows (destination has {n_dst})")
    print(
        "note: 'familiar' vs 'familiar_realm_watch' intentionally NOT merged — "
        "flagged in #381 as possibly intentional; decide separately."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn",
        default=os.environ.get("MEMPALACE_POSTGRES_DSN"),
        help="postgres DSN (default: $MEMPALACE_POSTGRES_DSN)",
    )
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument(
        "--commit", action="store_true", help="actually mutate; default is a read-only preview"
    )
    parser.add_argument(
        "--delete-debris",
        action="store_true",
        help="with --commit: delete the known smoke-test debris rows "
        "instead of leaving them for a later decision",
    )
    parser.add_argument(
        "--merge",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="extra wing merge in addition to the built-in list (repeatable)",
    )
    args = parser.parse_args()

    if not args.dsn:
        print("no DSN: pass --dsn or set MEMPALACE_POSTGRES_DSN", file=sys.stderr)
        return 2

    merges = list(DEFAULT_MERGES)
    for spec in args.merge:
        src, sep, dst = spec.partition("=")
        if not sep or not src or not dst:
            print(f"bad --merge spec (want FROM=TO): {spec!r}", file=sys.stderr)
            return 2
        merges.append((src, dst))

    table = _table_ident(args.table)
    mode = "COMMIT" if args.commit else "DRY-RUN (pass --commit to apply)"
    print(f"wing hygiene on {args.table} — {mode}")

    conn = _connect(args.dsn)
    try:
        cur = conn.cursor()
        empty_wing_pass(cur, table, args.commit, args.delete_debris)
        merge_pass(cur, table, merges, args.commit)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
