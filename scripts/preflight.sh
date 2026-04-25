#!/usr/bin/env bash
# Pre-push verification — runs the same checks CI does, locally.
#
# CI runs both `ruff check` and `ruff format --check`; running only one
# (e.g. via the editor's format-on-save) leaves the other to fail in CI.
# This script catches both — plus pytest — before the push lands.
#
# Usage: scripts/preflight.sh
# Exit code: 0 on success, non-zero on first failure (set -e).

set -e
cd "$(dirname "$0")/.."

if [ -x venv/bin/python ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

echo "→ ruff check ."
ruff check .

echo "→ ruff format --check ."
ruff format --check .

echo "→ pytest"
python -m pytest tests/ -q --ignore=tests/benchmarks

echo "✓ preflight passed"
