"""Rename a palace wing via the daemon's MCP interface.

Usage::

    python scripts/wing_rename.py --from wing_opencode --to opencode [--dry-run]

Paginates through all drawers in the source wing and updates each one
to the target wing name via ``mempalace_update_drawer``.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _mcp_call(daemon_url, tool, args, api_key=""):
    url = "{}/mcp".format(daemon_url.rstrip("/"))
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        rpc = json.loads(resp.read().decode("utf-8"))
        if "error" in rpc:
            raise RuntimeError("JSON-RPC error: {}".format(rpc["error"]))
        text = rpc.get("result", {}).get("content", [{}])[0].get("text", "")
        return json.loads(text) if text else {}


def main():
    parser = argparse.ArgumentParser(description="Rename a palace wing")
    parser.add_argument("--from", dest="from_wing", required=True, help="Source wing name")
    parser.add_argument("--to", dest="to_wing", required=True, help="Target wing name")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be renamed")
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Drawers per page (default 100)"
    )
    parser.add_argument("--daemon-url", default="", help="Daemon URL (default from config)")
    args = parser.parse_args()

    daemon_url = args.daemon_url
    if not daemon_url:
        try:
            config_path = os.path.expanduser("~/.mempalace/config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            daemon_url = cfg.get("daemon_url", "")
        except (OSError, json.JSONDecodeError):
            pass
    if not daemon_url:
        daemon_url = os.environ.get("PALACE_DAEMON_URL", "")
    if not daemon_url:
        print("ERROR: No daemon URL. Set --daemon-url, config.json, or PALACE_DAEMON_URL.")
        return 1

    api_key = os.environ.get("PALACE_API_KEY", "")

    print("Wing rename: {} -> {}".format(args.from_wing, args.to_wing))
    print("Daemon: {}".format(daemon_url))
    if args.dry_run:
        print("DRY RUN — no changes will be made")
    print()

    total = 0
    updated = 0
    errors = 0
    offset = 0

    while True:
        result = _mcp_call(
            daemon_url,
            "mempalace_list_drawers",
            {"wing": args.from_wing, "limit": args.batch_size, "offset": offset},
            api_key,
        )

        drawers = result.get("drawers", [])
        batch_total = result.get("total", 0)

        if not drawers:
            break

        if total == 0:
            print("Found {} drawers in wing '{}'".format(batch_total, args.from_wing))

        for drawer in drawers:
            did = drawer["drawer_id"]
            total += 1

            if args.dry_run:
                print("  [dry-run] {} -> {}".format(did, args.to_wing))
                continue

            try:
                _mcp_call(
                    daemon_url,
                    "mempalace_update_drawer",
                    {"drawer_id": did, "wing": args.to_wing},
                    api_key,
                )
                updated += 1
                if updated % 50 == 0:
                    print("  Updated {}/{}...".format(updated, batch_total))
            except Exception as e:
                errors += 1
                print("  ERROR updating {}: {}".format(did, e), file=sys.stderr)

        if len(drawers) < args.batch_size:
            break

        if args.dry_run:
            offset += args.batch_size
        else:
            pass

    print()
    if args.dry_run:
        print(
            "Would rename {} drawers from '{}' to '{}'".format(total, args.from_wing, args.to_wing)
        )
    else:
        print("Done: {}/{} updated, {} errors".format(updated, updated + errors, errors))

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
