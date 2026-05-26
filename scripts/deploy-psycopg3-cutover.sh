#!/usr/bin/env bash
# deploy-psycopg3-cutover.sh — cut the KG extractor worker pools over to the
# psycopg3 + AsyncConnectionPool branch and roll back on regression.
#
# Hosts:
#   katana   — local (this machine). Worker runs in tmux session "kg-2080".
#   familiar — remote. Worker runs in tmux session "kg-p102" + checkout at
#              ~/kg-extract-deploy.
#
# Health check: ~/.local/bin/kg-backfill-per-pool.sh returns JSON with
#   total_rate, katana_rate, famili_rate, *_flight, total_done, eta_h.
#
# Rollback trigger: post-cutover total_rate < BASELINE * REGRESSION_FACTOR
#   for ROLLBACK_CONFIRM consecutive samples.
#
# Usage:
#   scripts/deploy-psycopg3-cutover.sh <new-sha>
#   DRY_RUN=1 scripts/deploy-psycopg3-cutover.sh <new-sha>     # plan only
#   ROLLBACK_TO=<old-sha> scripts/deploy-psycopg3-cutover.sh <new-sha>
#
# Env (defaults):
#   KATANA_DIR=$HOME/Projects/kg-extract-katana
#   FAMILIAR_DIR=\$HOME/kg-extract-deploy  (expanded on familiar)
#   BASELINE_SAMPLES=3
#   POST_SAMPLES=5
#   SAMPLE_INTERVAL=20            # seconds between samples
#   REGRESSION_FACTOR=0.85        # rollback if rate drops below 85% of baseline
#   ROLLBACK_CONFIRM=2            # consecutive bad samples before rollback
#   DRY_RUN=0
#   ROLLBACK_TO=                  # if unset, captured from each host pre-pull

set -euo pipefail

NEW_SHA="${1:-}"
[ -n "$NEW_SHA" ] || { echo "usage: $0 <new-sha>" >&2; exit 2; }

KATANA_DIR="${KATANA_DIR:-$HOME/Projects/kg-extract-katana}"
# Quoted single-tick on purpose: this string is sent to familiar over ssh and
# expanded by the remote shell (so $HOME = familiar's $HOME, not katana's).
# shellcheck disable=SC2016
FAMILIAR_DIR_RAW='${FAMILIAR_DIR:-$HOME/kg-extract-deploy}'
BASELINE_SAMPLES="${BASELINE_SAMPLES:-3}"
POST_SAMPLES="${POST_SAMPLES:-5}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-20}"
REGRESSION_FACTOR="${REGRESSION_FACTOR:-0.85}"
ROLLBACK_CONFIRM="${ROLLBACK_CONFIRM:-2}"
DRY_RUN="${DRY_RUN:-0}"
PER_POOL="$HOME/.local/bin/kg-backfill-per-pool.sh"

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1" >&2; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = "1" ]; then printf '  [dry] %s\n' "$*"; else eval "$*"; fi; }

# total_rate of the latest sample, or 0 on parse failure
sample_total_rate() {
  "$PER_POOL" 2>/dev/null | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("total_rate", 0.0))
except Exception: print(0.0)' 2>/dev/null || echo 0.0
}

# Mean of N samples taken SAMPLE_INTERVAL apart. Progress goes to stderr so
# the final mean (stdout) can be captured cleanly via $(mean_rate ...).
mean_rate() {
  local n="$1" sum=0 i rate
  for ((i=1; i<=n; i++)); do
    rate=$(sample_total_rate)
    printf '    sample %d/%d: %s/min\n' "$i" "$n" "$rate" >&2
    sum=$(python3 -c "print($sum + $rate)")
    [ "$i" -lt "$n" ] && sleep "$SAMPLE_INTERVAL"
  done
  python3 -c "print(round($sum / $n, 1))"
}

[ -x "$PER_POOL" ] || fail "$PER_POOL not executable — install/symlink it first"

step "1/6  capture pre-cutover SHAs (for rollback)"
KATANA_OLD=$(git -C "$KATANA_DIR" rev-parse HEAD)
ok "katana   @ $KATANA_OLD"
# shellcheck disable=SC2029  # FAMILIAR_DIR_RAW intentionally expands remote-side
FAMILIAR_OLD=$(ssh familiar "git -C $FAMILIAR_DIR_RAW rev-parse HEAD")
ok "familiar @ $FAMILIAR_OLD"
ROLLBACK_KATANA="${ROLLBACK_TO:-$KATANA_OLD}"
ROLLBACK_FAMILIAR="${ROLLBACK_TO:-$FAMILIAR_OLD}"

step "2/6  baseline throughput ($BASELINE_SAMPLES samples × ${SAMPLE_INTERVAL}s)"
BASELINE=$(mean_rate "$BASELINE_SAMPLES")
ok "baseline total_rate = ${BASELINE}/min"
THRESHOLD=$(python3 -c "print(round($BASELINE * $REGRESSION_FACTOR, 1))")
ok "rollback threshold = ${THRESHOLD}/min"

step "3/6  pull $NEW_SHA on both hosts + reinstall deps"
run "git -C '${KATANA_DIR}' fetch origin && git -C '${KATANA_DIR}' checkout '$NEW_SHA'"
run "git -C '${KATANA_DIR}' submodule update --init --recursive 2>/dev/null || true"
run "'${KATANA_DIR}/.venv/bin/pip' install -q -e '${KATANA_DIR}[kg-extract,postgres]'"
# shellcheck disable=SC2029  # remote-side expansion intentional
run "ssh familiar 'cd ${FAMILIAR_DIR_RAW} && git fetch origin && git checkout ${NEW_SHA} && .venv/bin/pip install -q -e .[kg-extract,postgres]'"
ok "code + deps on $NEW_SHA"

step "4/6  restart worker sessions"
# katana: tmux kg-2080 — Ctrl-C the running python, re-issue the launch line
# from its scrollback (preserves env). Sender of last command is in pane 0.
run "tmux send-keys -t kg-2080 C-c"
run "sleep 3"
run "tmux send-keys -t kg-2080 Up Enter"
ok "katana worker restarted"
run "ssh familiar 'tmux send-keys -t kg-p102 C-c && sleep 3 && tmux send-keys -t kg-p102 Up Enter'"
ok "familiar worker restarted"

step "5/6  post-cutover verification ($POST_SAMPLES samples × ${SAMPLE_INTERVAL}s)"
BAD=0
for ((s=1; s<=POST_SAMPLES; s++)); do
  rate=$(sample_total_rate)
  printf '    sample %d/%d: %s/min  (threshold %s)\n' "$s" "$POST_SAMPLES" "$rate" "$THRESHOLD"
  if python3 -c "import sys; sys.exit(0 if $rate < $THRESHOLD else 1)"; then
    BAD=$((BAD + 1))
    warn "below threshold ($BAD/$ROLLBACK_CONFIRM consecutive)"
    if [ "$BAD" -ge "$ROLLBACK_CONFIRM" ]; then
      warn "regression confirmed — initiating rollback"
      break
    fi
  else
    BAD=0
  fi
  [ "$s" -lt "$POST_SAMPLES" ] && sleep "$SAMPLE_INTERVAL"
done

if [ "$BAD" -ge "$ROLLBACK_CONFIRM" ]; then
  step "6/6  ROLLBACK to $ROLLBACK_KATANA / $ROLLBACK_FAMILIAR"
  run "git -C '${KATANA_DIR}' reset --hard '${ROLLBACK_KATANA}'"
  run "'${KATANA_DIR}/.venv/bin/pip' install -q -e '${KATANA_DIR}[kg-extract,postgres]'"
  run "tmux send-keys -t kg-2080 C-c && sleep 3 && tmux send-keys -t kg-2080 Up Enter"
  # shellcheck disable=SC2029  # remote-side expansion intentional
  run "ssh familiar 'cd ${FAMILIAR_DIR_RAW} && git reset --hard ${ROLLBACK_FAMILIAR} && .venv/bin/pip install -q -e .[kg-extract,postgres] && tmux send-keys -t kg-p102 C-c && sleep 3 && tmux send-keys -t kg-p102 Up Enter'"
  fail "rolled back to pre-cutover SHAs — investigate before retrying"
fi

step "6/6  cutover holds"
FINAL=$(sample_total_rate)
ok "post-cutover total_rate = ${FINAL}/min  (baseline ${BASELINE}/min, threshold ${THRESHOLD}/min)"
ok "psycopg3 cutover landed; rollback SHAs were katana=$ROLLBACK_KATANA familiar=$ROLLBACK_FAMILIAR"
