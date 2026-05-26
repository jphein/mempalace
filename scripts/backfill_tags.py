#!/usr/bin/env python3
"""Backfill TF-IDF tags onto existing drawers (techempower-org/mempalace#201).

Walks the palace via the daemon's MCP interface, skips drawers that
already carry tags, computes per-(wing, room) IDF tables, scores each
untagged drawer, and updates it via ``mempalace_update_drawer``.

Resumable: each pass picks up where the last one stopped because the
filter is "no ``tags`` metadata key yet", which only shrinks as the
backfill progresses. A cursor file records the last offset for fast
restart on huge palaces.

Usage::

    # Dry-run, show what would change
    python scripts/backfill_tags.py --dry-run

    # Apply, default daemon URL from env
    python scripts/backfill_tags.py --apply

    # Targeted run on a single wing
    python scripts/backfill_tags.py --apply --wing wing_code

    # Custom daemon
    PALACE_DAEMON_URL=http://familiar:8085 PALACE_API_KEY=... \\
        python scripts/backfill_tags.py --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Optional


logger = logging.getLogger("mempalace.backfill_tags")


DEFAULT_DAEMON_URL = os.environ.get("PALACE_DAEMON_URL", "http://localhost:8085")
DEFAULT_API_KEY = os.environ.get("PALACE_API_KEY", "")
DEFAULT_CURSOR = Path(
    os.environ.get("MEMPALACE_BACKFILL_TAGS_CURSOR", "~/.mempalace/backfill_tags.cursor")
).expanduser()


def _mcp_call(daemon_url: str, tool: str, args: dict, api_key: str = "") -> dict:
    """Single JSON-RPC call to the palace daemon's MCP endpoint."""
    url = f"{daemon_url.rstrip('/')}/mcp"
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
            "id": 1,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        rpc = json.loads(resp.read().decode("utf-8"))
        if "error" in rpc:
            raise RuntimeError(f"JSON-RPC error from {tool}: {rpc['error']}")
        text = rpc.get("result", {}).get("content", [{}])[0].get("text", "")
        return json.loads(text) if text else {}


def _iter_drawers(daemon_url: str, api_key: str, wing: Optional[str], offset: int, batch_size: int):
    """Yield drawer dicts one batch at a time. Stops when the daemon runs out."""
    while True:
        args = {"limit": batch_size, "offset": offset}
        if wing:
            args["wing"] = wing
        result = _mcp_call(daemon_url, "mempalace_list_drawers", args, api_key)
        drawers = result.get("drawers") or []
        if not drawers:
            return
        for drawer in drawers:
            yield drawer
        if len(drawers) < batch_size:
            return
        offset += batch_size


def _idf_for_scope(corpus_docs: list[str]) -> dict:
    """Local fallback IDF builder so the backfill doesn't need a daemon roundtrip."""
    from mempalace.tag_extraction import build_idf

    return build_idf(corpus_docs)


def _extract(content: str, idf: dict, k: int) -> list[str]:
    from mempalace.tag_extraction import extract_tags

    return extract_tags(content, idf, k=k)


def _load_cursor(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except (ValueError, OSError):
        return 0


def _save_cursor(path: Path, offset: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(offset))
    except OSError as exc:
        logger.warning("cursor save failed at %s: %s", path, exc)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--wing", help="restrict backfill to one wing")
    parser.add_argument(
        "--batch-size", type=int, default=200, help="drawers per list_drawers call"
    )
    parser.add_argument(
        "--k", type=int, default=5, help="tags per drawer (default 5, spec band 3-8)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually update drawers (default: dry-run preview)",
    )
    parser.add_argument(
        "--cursor", default=str(DEFAULT_CURSOR), help="resume offset file"
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="ignore and overwrite the resume cursor",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cursor_path = Path(args.cursor).expanduser()
    start_offset = 0 if args.reset_cursor else _load_cursor(cursor_path)
    if start_offset:
        logger.info("resuming from cursor offset=%d", start_offset)

    # Pass 1 — bucket drawers by (wing, room) and decide which need tags.
    # We don't materialise the whole corpus in memory; per-bucket lists
    # only hold ``content`` strings for the bucket we're about to score.
    untagged_by_scope: dict[tuple[str, str], list[dict]] = defaultdict(list)
    corpus_by_scope: dict[tuple[str, str], list[str]] = defaultdict(list)

    seen = 0
    skipped_tagged = 0
    for drawer in _iter_drawers(
        args.daemon_url, args.api_key, args.wing, start_offset, args.batch_size
    ):
        seen += 1
        scope = (drawer.get("wing") or "", drawer.get("room") or "")
        content = drawer.get("content_preview") or drawer.get("content") or ""
        corpus_by_scope[scope].append(content)
        if drawer.get("tags"):
            skipped_tagged += 1
            continue
        untagged_by_scope[scope].append(
            {
                "drawer_id": drawer.get("drawer_id") or drawer.get("id"),
                "content": content,
            }
        )
        if seen % 500 == 0:
            logger.info(
                "scanned=%d tagged=%d untagged=%d",
                seen,
                skipped_tagged,
                seen - skipped_tagged,
            )

    logger.info(
        "scan complete: scanned=%d already-tagged=%d untagged=%d scopes=%d",
        seen,
        skipped_tagged,
        seen - skipped_tagged,
        len(untagged_by_scope),
    )

    # Pass 2 — score and (optionally) update.
    updated = 0
    extraction_empty = 0
    update_errors = 0
    start = time.monotonic()
    for scope, untagged in untagged_by_scope.items():
        if not untagged:
            continue
        idf = _idf_for_scope(corpus_by_scope[scope])
        logger.info(
            "scope=%s/%s untagged=%d idf_terms=%d",
            scope[0],
            scope[1],
            len(untagged),
            len(idf),
        )
        for drawer in untagged:
            tags = _extract(drawer["content"], idf, args.k)
            if not tags:
                extraction_empty += 1
                continue
            if not args.apply:
                logger.info(
                    "[dry-run] %s/%s drawer=%s -> %s",
                    scope[0],
                    scope[1],
                    drawer["drawer_id"],
                    tags,
                )
                updated += 1
                continue
            try:
                _mcp_call(
                    args.daemon_url,
                    "mempalace_update_drawer",
                    {"drawer_id": drawer["drawer_id"], "tags": tags},
                    args.api_key,
                )
                updated += 1
            except (urllib.error.URLError, RuntimeError) as exc:
                update_errors += 1
                logger.warning("update failed for %s: %s", drawer["drawer_id"], exc)
            if updated and updated % 250 == 0:
                logger.info(
                    "progress: updated=%d errors=%d rate=%.1f/s",
                    updated,
                    update_errors,
                    updated / max(time.monotonic() - start, 1e-3),
                )

    if args.apply:
        _save_cursor(cursor_path, start_offset + seen)

    logger.info(
        "done: updated=%d empty-extractions=%d errors=%d dry_run=%s elapsed=%.1fs",
        updated,
        extraction_empty,
        update_errors,
        not args.apply,
        time.monotonic() - start,
    )
    return 0 if update_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
