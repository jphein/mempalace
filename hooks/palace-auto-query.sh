#!/usr/bin/env bash
# UserPromptSubmit hook — auto-query MemPalace for context injection.
#
# Pre-filters with shell regex to avoid 250ms Python startup on messages
# that clearly won't trigger. When the filter matches, delegates to
# `python -m mempalace.auto_query` which runs the full signal → route →
# MCP → format pipeline.

set -euo pipefail

MEMPALACE_DIR="${MEMPALACE_DIR:-/home/jp/Projects/memorypalace}"
MEMPALACE_PYTHON="${MEMPALACE_PYTHON:-${MEMPALACE_DIR}/.venv/bin/python3}"
LOG="${PALACE_AUTO_QUERY_LOG:-/tmp/palace-auto-query.log}"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // .userPrompt // ""' 2>/dev/null)

# Fast exit: empty prompt
if [ -z "$PROMPT" ]; then
  exit 0
fi

# Determine project wing from CWD
PROJECT_WING=""
CWD=$(echo "$INPUT" | jq -r '.cwd // ""' 2>/dev/null || echo "")
if [ -n "$CWD" ]; then
  BASENAME=$(basename "$CWD")
  PROJECT_WING="$BASENAME"
fi

# Session ID from env or generate
SESSION_ID="${CLAUDE_SESSION_ID:-$(date +%s)}"

# Turn index — computed BEFORE the pre-filter so turn-1 resumption works
TURN_FILE="/tmp/palace-aq-turn-${SESSION_ID}"
if [ -f "$TURN_FILE" ]; then
  TURN=$(cat "$TURN_FILE")
  TURN=$((TURN + 1))
else
  TURN=1
fi
echo "$TURN" > "$TURN_FILE" 2>/dev/null || true

# Shell-level pre-filter — skip Python entirely if no signal pattern matches.
# Must be a SUPERSET of Python's signal extraction — err on the side of
# letting prompts through. Four checks:
#   1. Temporal/explicit recall patterns (case-insensitive)
#   2. Capitalized entity names (potential wing/entity matches)
#   3. Turn 1 always passes (task resumption signal)
#   4. Every 15th turn passes (periodic depth refresh)
MATCH=0
echo "$PROMPT" | grep -qiE 'remind|remember|do (we|you) (have|know)|did (we|you)|what (did|was|were) |history of|have we|prior to|earlier|last (time|week|session|night|run|month|sprint)|yesterday|previously|recently|while ago|days ago|back when|used to|that time|when (did|we|was)|before we|recall|check (if|whether)|was there|were there' && MATCH=1
[ "$MATCH" -eq 0 ] && echo "$PROMPT" | grep -qE '[A-Z][a-zA-Z]{2,}' && MATCH=1
[ "$MATCH" -eq 0 ] && [ "$TURN" -eq 1 ] && MATCH=1
[ "$MATCH" -eq 0 ] && [ "$((TURN % 15))" -eq 0 ] && MATCH=1
if [ "$MATCH" -eq 0 ]; then
  echo "$(date -Iseconds) skip:no-signal" >> "$LOG" 2>/dev/null || true
  exit 0
fi

# Run the classifier
INJECTION=$(PYTHONPATH="$MEMPALACE_DIR" "$MEMPALACE_PYTHON" -m mempalace.auto_query \
  --prompt "$PROMPT" \
  --wing "$PROJECT_WING" \
  --session-id "$SESSION_ID" \
  --turn "$TURN" \
  2>/dev/null) || true

if [ -n "$INJECTION" ]; then
  # Escape for JSON embedding
  ESCAPED=$(echo "$INJECTION" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":%s}}\n' "$ESCAPED"
  echo "$(date -Iseconds) fire:injected" >> "$LOG" 2>/dev/null || true
else
  echo "$(date -Iseconds) skip:no-injection" >> "$LOG" 2>/dev/null || true
fi
