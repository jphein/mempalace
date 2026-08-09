# Palace Context Recovery Spec

- **Status:** Draft
- **Date:** 2026-06-29
- **Related:**
  - [`auto-query-integration.md`](auto-query-integration.md) — the auto-query classifier this builds on
  - `palace-daemon/clients/mempalace-mcp.py` — the stdio MCP proxy (search-only mode lands here)
  - `palace-daemon/clients/hook.py` — hook handlers (PostCompact + SessionStart changes land here)
  - `~/.claude/plugins/cache/mempalace/mempalace/3.3.7/hooks/hooks.json` — plugin hook wiring

## Problem

Three complementary failures prevent Claude Code sessions from using MemPalace's
410K+ drawers of verbatim history:

1. **No search tool.** MCP tools were retired 2026-06-10 (`mcp_mode: cli-only`,
   palace-daemon #214). The full 39-tool surface cost ~9k tokens of always-on
   schema context. With zero tools, the model has no native way to search. CLI
   via Bash exists but sees near-zero voluntary usage (3 searches in 7 days).

2. **No post-compaction recovery.** The PreCompact hook saves transcripts to the
   palace, but nothing reads them back. After compaction, the model wakes up
   with a lossy 9-section LLM summary and zero palace context. The `PostCompact`
   and `SessionStart(source="compact")` hook events exist but are unwired.

3. **Auto-query fires too rarely.** The UserPromptSubmit auto-query hook has a
   score threshold of 4 and signal patterns that only match explicit recall
   phrases. Hit rate: 1/241 (0.4%) over 2 days. Most engineering messages
   score 0.

## Solution Overview

Four changes across three files, each independently valuable and compounding:

| # | Change | File | Tokens | Effect |
|---|--------|------|--------|--------|
| 1 | `search-only` MCP mode | `mempalace-mcp.py` | ~800/turn | Model can search on demand |
| 2 | PostCompact handler | `hook.py` | 0 | Saves compaction metadata to palace |
| 3 | SessionStart(compact) branch | `hook.py` | ~500-1000 inject | Auto-injects context after compaction |
| 4 | Compact instructions | `CLAUDE.md` + `hooks.json` | 0 | Guides compactor + wires PostCompact |

## 1. search-only MCP Mode

### Location
`palace-daemon/clients/mempalace-mcp.py`

### Design

Add `"search-only"` to `VALID_MCP_MODES` (line ~49). Add a third branch in the
proxy's `run_daemon_mode()` handle function alongside `"all"` and `"cli-only"`.

**Handshake (local, no daemon contact):**
- `initialize` → same as cli-only (local response with protocol version + capabilities)
- `ping` → same as cli-only (local `{}`)
- `tools/list` → return a **hardcoded** single-tool list containing only `mempalace_search`

**Tool calls:**
- `tools/call` where tool name == `"mempalace_search"` → forward to daemon via
  existing `forward()` function
- `tools/call` where tool name != `"mempalace_search"` → reject with error code
  -32601 and message "Only mempalace_search is available in search-only mode.
  Use the mempalace CLI for other operations."
- `resources/list`, `prompts/list` → empty (same as cli-only)

**Schema (hardcoded inline):**

```python
SEARCH_ONLY_TOOLS = [
    {
        "name": "mempalace_search",
        "description": (
            "Search your memory palace. Returns verbatim drawer content "
            "with similarity scores. Use short keyword queries (not full "
            "sentences). 410K+ drawers across 100+ wings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short search query — keywords or a question. Max 250 chars.",
                    "maxLength": 250,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5, max 20)",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
                "wing": {
                    "type": "string",
                    "description": "Filter by wing (optional). Use for project-scoped searches.",
                },
            },
            "required": ["query"],
        },
    }
]
```

Note: the schema is deliberately **slimmed** vs the full `mempalace_search` schema
(which has 10 parameters including `candidate_strategy`, `fusion_mode`,
`include_trace`, etc.). The search-only surface exposes only `query`, `limit`,
and `wing` — the three that matter for conversational recall. Advanced parameters
are still available via the CLI. This further reduces the per-turn token cost
from ~800 to ~300 tokens.

**Error handling on forward (sleeping familiar):**

```python
try:
    return forward(daemon_url, request)
except urllib.error.URLError:
    # Try auto_wake if configured
    wake_cmd = _load_auto_wake_command()
    if wake_cmd:
        subprocess.run(wake_cmd, shell=True, timeout=15, capture_output=True)
        time.sleep(3)
        try:
            return forward(daemon_url, request)
        except Exception:
            pass
    # Graceful fallback
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "content": [{"type": "text", "text":
                "Palace host is waking up (WoL sent). "
                "Try again in ~20 seconds, or use `mempalace search` CLI."}],
            "isError": False,
        },
    }
```

The `_load_auto_wake_command()` reads `auto_wake.command` from
`~/.mempalace/config.json` (same config the CLI uses). This is stdlib-only —
no mempalace imports.

### Config change (post-deploy)

```bash
# In ~/.mempalace/config.json, change:
"mcp_mode": "cli-only"
# to:
"mcp_mode": "search-only"
```

No MCP server restart needed — the proxy reads `mcp_mode` at startup, and
Claude Code restarts the MCP server per session.

### Testing

```bash
# Verify the mode is recognized
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  PALACE_MCP_MODE=search-only python3 palace-daemon/clients/mempalace-mcp.py --daemon http://familiar:8085

# Should return exactly 1 tool: mempalace_search
# Should NOT contact the daemon for this call

# Verify search forwarding
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mempalace_search","arguments":{"query":"test","limit":3}}}' | \
  PALACE_MCP_MODE=search-only python3 palace-daemon/clients/mempalace-mcp.py --daemon http://familiar:8085

# Should forward to daemon and return search results

# Verify non-search rejection
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mempalace_diary_write","arguments":{"entry":"test"}}}' | \
  PALACE_MPC_MODE=search-only python3 palace-daemon/clients/mempalace-mcp.py --daemon http://familiar:8085

# Should return -32601 error
```

## 2. PostCompact Handler

### Location
`palace-daemon/clients/hook.py`

### Design

Add `hook_postcompact(data, harness)` function (after `hook_precompact`).
Wire `"postcompact": hook_postcompact` into the `hooks` dict at line ~1768.

**Input payload** (from Claude Code v2.1.187):
```json
{
  "trigger": "auto" | "manual",
  "compact_summary": "The conversation summary produced by compaction...",
  "session_id": "...",
  "transcript_path": "..."
}
```

**Behavior:**
1. Parse input with `_parse_harness_input(data, harness)`.
2. Extract `compact_summary` from data (may be under `data["compact_summary"]`
   or the parsed result — check both paths).
3. Emit a themed `systemMessage` notification: "Context compacted — palace has
   your history. Use mempalace_search to retrieve what you need."
4. Detach for async work (`_detach_for_async_work()`).
5. In the detached child:
   - Save the compact_summary as a diary entry with topic `"compaction"` and
     the session's wing via `_post_mcp(daemon_url, "mempalace_diary_write", {...})`.
   - Log outcome to hook.log.

**Key constraint:** PostCompact is **informational only** — it cannot return
`additionalContext` or `decision: "block"`. It can only return `systemMessage`
(user-visible notification). The actual context injection happens in step 3
(SessionStart compact branch).

### Theme function

```python
def _theme_postcompact(wing, trigger):
    icon = "🔄" if trigger == "auto" else "📋"
    return (
        f"{icon} Context compacted ({trigger}). "
        f"Your verbatim history is in the palace — "
        f"use mempalace_search to retrieve what you need."
    )
```

## 3. SessionStart(compact) Branch

### Location
`palace-daemon/clients/hook.py`, within existing `hook_session_start()`.

### Design

The existing `hook_session_start()` gives the same greeting regardless of how
the session started. Claude Code fires SessionStart with a `source` field:
`"startup"`, `"resume"`, `"clear"`, or `"compact"`.

**Add a branch** early in `hook_session_start()`:

```python
source = data.get("source") or parsed.get("source", "startup")
if source == "compact":
    return _handle_compact_resume(data, parsed, harness)
```

**`_handle_compact_resume()` behavior:**

1. Query the palace for the session's recent state:
   - `GET /search/fast?q=session+state+{wing}&limit=3` — recent session drawers
   - `GET /search/fast?q=checkpoint+{session_id}&limit=2` — the stop-hook diary checkpoints
2. Format results into a compact context packet (target: 500-1000 tokens):

```
[mempalace:compact-recovery]
Context was just compacted. Here is your recent session state from the palace:

Wing: {wing} ({drawer_count} drawers)
Last checkpoint: {checkpoint_summary}

Recent context (top 3 matches):
1. {drawer_1_summary} (wing/{room}, {timestamp})
2. {drawer_2_summary} (wing/{room}, {timestamp})
3. {drawer_3_summary} (wing/{room}, {timestamp})

Use mempalace_search for anything else you need.
[/mempalace:compact-recovery]
```

3. Return via `_output()`:
```python
_output({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context_packet,
    }
})
```

**Fallback (daemon unreachable):**
Return a minimal nudge without palace data:
```python
_output({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": (
            "[mempalace:compact-recovery]\n"
            "Context was just compacted. Your verbatim history is in the "
            "palace (410K+ drawers). Use mempalace_search to retrieve "
            "what you need — don't guess from the summary alone.\n"
            "[/mempalace:compact-recovery]"
        ),
    }
})
```

**Latency budget:** SessionStart hooks have a 5000ms timeout (from hooks.json).
The `/search/fast` endpoint is BM25-only at ~280ms. Two fast searches = ~560ms.
Formatting is negligible. Total: well within budget. Do NOT use hybrid search
here — it's 1100ms and would risk timeout.

**Important:** Unlike stop/precompact, this handler must NOT detach — it needs
to return `additionalContext` synchronously via stdout. The detach pattern
redirects stdout to /dev/null, which would lose the injection.

### Config interaction

This handler reads `auto_wake` from `~/.mempalace/config.json`. If the daemon
is unreachable and auto_wake is configured, it attempts one wake cycle (same
pattern as the search-only MCP mode). If still unreachable after wake, falls
back to the minimal nudge.

## 4. Compact Instructions + Hook Wiring

### Location
- `~/Projects/memorypalace/CLAUDE.md` — add compact instructions section
- `~/.claude/plugins/cache/mempalace/mempalace/3.3.7/hooks/hooks.json` — add PostCompact wiring
- Source truth: check if `~/Projects/memorypalace/.claude-plugin/hooks/hooks.json` exists

### CLAUDE.md addition

Add after the "## Integration" section:

```markdown
## Compact instructions

When compacting this conversation, preserve:
- MemPalace search results and drawer IDs referenced in this session
- Active dream team agent names, assignments, worktree paths, and task status
- Any structured session state, pinboard content, or roster files
- The current wing context and palace connection state
- Specific mempalace CLI commands or search queries that produced results
```

### hooks.json addition

Add PostCompact entry to the hooks object:

```json
"PostCompact": [
    {
        "hooks": [
            {
                "type": "command",
                "command": "python3 /home/jp/Projects/palace-daemon/clients/hook.py --hook postcompact --harness claude-code",
                "timeout": 10000
            }
        ]
    }
]
```

Timeout is 10s (shorter than stop/precompact's 30s because PostCompact only
does a diary write, no transcript mining).

## Rollout

1. Deploy changes to `palace-daemon/clients/mempalace-mcp.py` (search-only mode)
2. Deploy changes to `palace-daemon/clients/hook.py` (PostCompact + SessionStart)
3. Update `hooks.json` (both plugin cache and source)
4. Update `CLAUDE.md` (compact instructions)
5. Flip `~/.mempalace/config.json`: `"mcp_mode": "search-only"`
6. Start a new Claude Code session — verify:
   - `mempalace_search` tool appears in tool list
   - Search returns results
   - No other MCP tools appear
7. Trigger `/compact` manually — verify:
   - PostCompact fires and saves to palace
   - SessionStart(compact) injects additionalContext
   - Model receives palace context after compaction

## Non-goals

- **Restoring the full MCP tool surface.** search-only is deliberate — 1 tool
  at ~300 tokens vs 39 tools at ~9k tokens.
- **Auto-query threshold tuning.** Important but separate work (Phase 2).
- **Session pinboard drawer type.** Valuable but depends on the search tool
  existing first (Phase 3).
- **Modifying the compaction algorithm.** We work with the existing 9-section
  template and supplement it, not replace it.
