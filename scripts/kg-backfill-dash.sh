#!/usr/bin/env bash
# Launch the KG backfill TUI dashboard in a Wave Terminal block.
# Renders live progress via familiar.realm.watch wave-block.py in custom mode.

set -euo pipefail

cd "$(dirname "$0")/.."

# DSN for the production mempalace postgres. Provide via one of:
#   1. Pre-set MEMPALACE_POSTGRES_DSN in the calling shell
#   2. Operator-local env file at ~/.mempalace/dsn.env (gitignored)
#   3. Vaultwarden item "kg-backfill-dsn" (requires `bw unlock` first)
if [ -z "${MEMPALACE_POSTGRES_DSN:-}" ]; then
  if [ -r "$HOME/.mempalace/dsn.env" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.mempalace/dsn.env"
  elif command -v bw >/dev/null 2>&1 && bw status 2>/dev/null | grep -q '"status":"unlocked"'; then
    MEMPALACE_POSTGRES_DSN="$(bw get notes kg-backfill-dsn 2>/dev/null || true)"
  fi
fi
if [ -z "${MEMPALACE_POSTGRES_DSN:-}" ]; then
  echo "MEMPALACE_POSTGRES_DSN not set." >&2
  echo "Either export it, write it to ~/.mempalace/dsn.env, or store it in Vaultwarden as 'kg-backfill-dsn'." >&2
  exit 1
fi
export MEMPALACE_POSTGRES_DSN

# Locate the wave-block.py renderer and the python venv.
WAVE_BLOCK="$HOME/Projects/familiar.realm.watch/ops/scripts/wave-block.py"
PY="$PWD/.venv/bin/python"

exec python3 "$WAVE_BLOCK" custom \
  --title "KG TRIPLE EXTRACTION" \
  --cmd "$PY $PWD/scripts/kg-backfill-status.py" \
  --interval 5
