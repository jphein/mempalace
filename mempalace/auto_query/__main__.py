"""CLI entry point for auto-query integration.

Invoked by the UserPromptSubmit hook::

    $MEMPALACE_PYTHON -m mempalace.auto_query \\
        --prompt "remind me about the metadata bug?" \\
        --wing memorypalace --session-id "sess-abc" --turn 1

Outputs the injection block to stdout (if any), suitable for
``hookSpecificOutput.additionalContext`` in Claude Code hooks.
Exits 0 on success (with or without output), 1 on error.

Wing list is fetched from the daemon at startup via
``mempalace_list_wings``.  If the daemon is unreachable, an empty
set is used (entity scoring degrades gracefully).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from mempalace.auto_query.runner import run_auto_query
from mempalace.config import MempalaceConfig


def _fetch_wings(config):
    # type: (MempalaceConfig) -> set
    """Fetch known wing names from the daemon."""
    daemon_url = config.daemon_url
    if not daemon_url:
        return set()
    url = "{}/mcp".format(daemon_url.rstrip("/"))
    payload = json.dumps(
        {
            "tool": "mempalace_list_wings",
            "args": {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result = body.get("result", body)
            wings = result.get("wings", [])
            return {w.get("name", w) if isinstance(w, dict) else str(w) for w in wings}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError, KeyError):
        return set()


def main(argv=None):
    # type: (list) -> int
    parser = argparse.ArgumentParser(
        prog="mempalace.auto_query",
        description="Auto-query classifier for MemPalace",
    )
    parser.add_argument("--prompt", required=True, help="User message text")
    parser.add_argument("--wing", default="", help="Project wing name")
    parser.add_argument("--session-id", default="cli", help="Session identifier")
    parser.add_argument("--turn", type=int, default=1, help="Turn index (1-based)")
    parser.add_argument(
        "--recent-drawers",
        action="store_true",
        default=False,
        help="Flag: project wing has recent drawers",
    )

    args = parser.parse_args(argv)
    config = MempalaceConfig()

    known_wings = _fetch_wings(config)

    result = run_auto_query(
        prompt=args.prompt,
        session_id=args.session_id,
        turn=args.turn,
        project_wing=args.wing,
        known_wings=known_wings,
        has_recent_drawers=args.recent_drawers,
        config=config,
    )

    if result.injection:
        sys.stdout.write(result.injection)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
