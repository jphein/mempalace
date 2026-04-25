#!/usr/bin/env bash
# Rebase a fork PR branch onto upstream/develop, with fork-collateral cleanup.
#
# Why this script exists: PR branches accumulate fork-only changes
# (`.claude-plugin/hooks/*.sh` venv-aware lookup, `.claude-plugin/.mcp.json`
# daemon URL) that bleed in via the pre-rebase branch state. Rebase only
# replays commits, not the pre-rebase delta — so without explicit cleanup,
# every PR ships with these as collateral and CI starts complaining about
# unrelated changes.
#
# Flow:
#   1. fetch upstream/develop
#   2. checkout BRANCH, fast-forward-pull from origin
#   3. rebase BRANCH onto upstream/develop  (you resolve conflicts)
#   4. on success: revert fork-only collateral to upstream's state
#   5. amend top commit with the revert (if anything changed)
#   6. run preflight (ruff check + format + pytest)
#   7. print push command — never auto-pushes; you confirm
#
# Usage:
#   scripts/rebase-on-develop.sh <branch>           # initial run
#   scripts/rebase-on-develop.sh --finish <branch>  # after manual conflict resolution + git rebase --continue

set -e
cd "$(dirname "$0")/.."

MODE="full"
if [ "$1" = "--finish" ]; then
  MODE="finish"
  shift
fi

BRANCH="${1:?usage: $0 [--finish] <branch>}"

COLLATERAL=(
  .claude-plugin/hooks/mempal-stop-hook.sh
  .claude-plugin/hooks/mempal-precompact-hook.sh
  .claude-plugin/.mcp.json
)

if [ "$MODE" = "full" ]; then
  echo "→ fetching upstream/develop"
  git fetch upstream develop

  echo "→ checking out $BRANCH"
  git checkout "$BRANCH"

  echo "→ syncing with origin/$BRANCH (if remote exists)"
  git pull --ff-only origin "$BRANCH" 2>/dev/null || true

  echo "→ rebasing onto upstream/develop"
  if ! git rebase upstream/develop; then
    echo
    echo "✗ rebase has conflicts. Resolve them, then:"
    echo "    git add <files>"
    echo "    git rebase --continue"
    echo "    scripts/rebase-on-develop.sh --finish $BRANCH"
    exit 1
  fi
fi

echo "→ restoring fork-only collateral to upstream's state"
git checkout upstream/develop -- "${COLLATERAL[@]}" 2>/dev/null || true

if ! git diff --quiet HEAD -- "${COLLATERAL[@]}" 2>/dev/null \
   || ! git diff --cached --quiet -- "${COLLATERAL[@]}" 2>/dev/null; then
  echo "→ amending top commit with collateral revert"
  git add "${COLLATERAL[@]}"
  git commit --amend --no-edit
fi

echo "→ running preflight"
./scripts/preflight.sh

echo
echo "✓ rebase complete. To publish:"
echo "    git push --force-with-lease origin $BRANCH"
