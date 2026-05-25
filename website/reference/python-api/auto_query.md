# `mempalace.auto_query`

Source: [`mempalace/auto_query/__init__.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_query/__init__.py)

Auto-query integration for MemPalace.

A context classifier that auto-invokes MemPalace queries during Claude Code
or OpenCode sessions.  It detects entity mentions, temporal references, and
task-resumption patterns in the user's turn and maps them to the appropriate
MCP tool call (mempalace_search, mempalace_kg_query, mempalace_diary_read,
mempalace_traverse).

The classifier is conservative by default: it ships disabled, supports a
dry-run mode, and every decision is logged for offline tuning.

Shared dataclasses live here so all sub-modules import from one place.

## Classes

### `class Signal`

A single signal extracted from the user's turn.

### `class SignalSet`

Aggregated signals from a single turn.

### `class MCPCall`

An MCP tool invocation to be issued.

### `class SessionState`

Mutable per-session state carried across turns.

### `class Decision`

A single auto-query decision, written to the JSONL log.
