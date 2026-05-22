#!/usr/bin/env bash
# mempalace-search.sh — thin .sh shim demonstrating palace-daemon delegation
# for a non-hook use case (CLI search).
#
# See docs/fork-decisions/sh-shim-strategy.md for the broader rationale.
# This script exists as a copy-paste template for adding new delegating
# shims, not as a daily-driver search command — most users will hit the
# daemon through the mempalace MCP server or `mempalace search` CLI.
#
# Usage:
#   mempalace-search.sh <query> [limit]
#
# Examples:
#   mempalace-search.sh "pgvector cutover"
#   mempalace-search.sh "Claude Code hook timeout" 20
#
# Env:
#   PALACE_DAEMON_URL   default http://disks.jphe.in:8085
#   PALACE_API_KEY      optional Bearer token; omitted from header if empty
#
# Exit codes:
#   0  daemon answered (results printed as JSON on stdout)
#   1  bad usage (missing query)
#   2  daemon unreachable or returned non-2xx
#
# Requires: curl, jq (optional; raw JSON if jq missing).

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <query> [limit]" >&2
    exit 1
fi

query="$1"
limit="${2:-5}"
daemon_url="${PALACE_DAEMON_URL:-http://disks.jphe.in:8085}"
api_key="${PALACE_API_KEY:-}"

# Encode the query — daemon /search reads it from a query parameter.
# python3 is more portable than relying on jq for url-encoding.
encoded_query=$(python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))' "$query")

auth_header=()
if [ -n "$api_key" ]; then
    auth_header=(-H "Authorization: Bearer $api_key")
fi

# --fail makes curl exit non-zero on 4xx/5xx; -s silences progress;
# --max-time keeps a stuck daemon from hanging the caller.
response=$(curl -sS --fail --max-time 10 \
    "${auth_header[@]}" \
    "${daemon_url}/search?q=${encoded_query}&limit=${limit}" 2>&1) || {
    echo "palace-daemon unreachable at ${daemon_url}: ${response}" >&2
    exit 2
}

if command -v jq >/dev/null 2>&1; then
    echo "$response" | jq .
else
    echo "$response"
fi
