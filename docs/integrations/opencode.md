# OpenCode + MemPalace integration (fork-routed via palace-daemon)

Two-direction integration between [OpenCode](https://opencode.ai) and a MemPalace running behind palace-daemon:

| Direction | Mechanism | What you get |
|---|---|---|
| **Read** (agent → palace) | MCP server entry in `~/.config/opencode/opencode.jsonc` pointing at the daemon-aware stdio wrapper | OpenCode agents can call `mempalace_search`, `mempalace_kg_query`, `mempalace_diary_read`, etc. |
| **Push** (live capture, conversation → palace) | [`opencode-plugin-mempalace`](https://www.npmjs.com/package/opencode-plugin-mempalace) (option-K) installed via npm + a one-line patch for daemon-routed setups | Every N messages (default 15), pre-compaction, session-idle, and SIGINT/SIGTERM all flush to the palace |
| **Pull** (retrospective backfill, OpenCode SQLite → palace) | This fork's `OpenCodeSourceAdapter` (cherry-picked from upstream PR #1484) | One-shot ingest of historical OpenCode sessions from `~/.local/share/opencode/opencode.db` |

Together these match the same shape as MemPalace's Claude Code stop-hook pattern: live capture during use, retrospective fill-in when needed, and read-side MCP for agent recall.

## Why fork-ahead

This fork carries the integration surface ahead of upstream because the canonical merge points are still open:

| Upstream PR | What | Status (last checked 2026-05-21) |
|---|---|---|
| [#1484](https://github.com/MemPalace/mempalace/pull/1484) | `OpenCodeSourceAdapter` (RFC 002) | OPEN — CI green except a transient test-windows runner failure |
| [#1567](https://github.com/MemPalace/mempalace/pull/1567) | `.opencode/opencode.json` MCP config in repo root | OPEN |
| [#23](https://github.com/MemPalace/mempalace/pull/23) | Earlier OpenCode SQLite spadework (JakobSachs) | OPEN, CONFLICTING — superseded by #1484; my comment on #23 offered three coordination paths |
| [#297](https://github.com/MemPalace/mempalace/pull/297) | Milofax's auto-plugin | OPEN — codebase-mining + protocol injection design; not what this fork uses |
| [#1524](https://github.com/MemPalace/mempalace/pull/1524) | geco's npm-plugin integration guide | OPEN — uses opinionated 5-wing taxonomy that doesn't fit project-keyed palaces |

The cherry-picks land #1484 + #1567 onto this fork's `main` so the fork ships with OpenCode support immediately. When upstream merges happen, the cherry-picked commits become no-ops and the fork-changes.yaml entries can be retired.

## Setup recipe

### 1. Install MemPalace CLI

```bash
pipx install "mempalace>=3.3.5"
```

`mempalace` and `mempalace-mcp` end up on PATH. The CLI auto-routes through palace-daemon when `PALACE_DAEMON_URL` is in the env.

### 2. Daemon env file

Put the daemon URL + API key in `~/.config/palace-daemon/env` (mode 600):

```
PALACE_API_KEY=<your-daemon-api-key>
PALACE_DAEMON_URL=http://your-daemon-host:8085
```

### 3. MCP wrapper

The mempalace-mcp stdio bridge needs `PALACE_API_KEY` in its environment, but MCP clients (OpenCode, Claude Code) spawn server subprocesses without inheriting shell rc. The wrapper at `palace-daemon/clients/mempalace-mcp-wrapper.sh` (see [palace-daemon PR #26](https://github.com/techempower-org/palace-daemon/pull/26)) sources the env file before exec'ing the bridge:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mempalace": {
      "type": "local",
      "command": ["/home/<user>/Projects/palace-daemon/clients/mempalace-mcp-wrapper.sh"],
      "enabled": true
    }
  }
}
```

This goes in `~/.config/opencode/opencode.jsonc`.

### 4. Live-capture plugin

```bash
npm install -g opencode-plugin-mempalace
```

Add to the same `opencode.jsonc`:

```jsonc
{
  "plugin": ["opencode-plugin-mempalace"]
}
```

The plugin uses project-basename wings (`wing_<sanitized-dirname>`), default 15-message threshold, session.idle flush, SIGINT/SIGTERM rescue, pre-compaction injection. Closest match to MemPalace's Claude Code stop-hook semantics.

### Daemon-routing patch (option-K plugin v1.2.1)

`opencode-plugin-mempalace` v1.2.1 has a known issue ([upstream #1](https://github.com/option-K/opencode-plugin-mempalace/issues/1)) where `isInitialized()` always returns false when running daemon-routed because the function passes `--palace <local-dir>/.mempalace/palace` to `mempalace status`, forcing a local lookup that bypasses `PALACE_DAEMON_URL`.

Effect without patch: harmless (the plugin re-runs `mempalace init --yes <dir>` on every OpenCode start; init is idempotent against the daemon).

Effect with patch: cleaner; init+wakeup short-circuit when daemon-routed mode is detected.

The patch detects daemon-routed mode via `$PALACE_DAEMON_URL` or `~/.config/palace-daemon/env` and skips the `--palace` argument. See `examples/opencode/option-k-plugin-daemon-routing.patch` for a re-applicable form. Apply with:

```bash
patch -d ~/.npm-global/lib/node_modules/opencode-plugin-mempalace/dist \
  < $REPO/examples/opencode/option-k-plugin-daemon-routing.patch
```

(This needs reapplying after `npm update opencode-plugin-mempalace`.)

### 5. Retrospective backfill

After install, ingest historical OpenCode sessions in one shot:

```bash
mempalace mine --source opencode
```

The `OpenCodeSourceAdapter` reads `~/.local/share/opencode/opencode.db` (or the macOS `~/Library/Application Support/opencode/...` path), yields one drawer per session-exchange-pair, and the daemon-routed `mempalace mine` writes them through the daemon. Wing routes from `session.directory` basename, matching the live-capture plugin's taxonomy.

## What gets stored

A typical OpenCode turn produces a drawer like:

| Field | Value |
|---|---|
| `wing` | `wing_<project-basename>` (matches both adapter and live-capture plugin) |
| `room` | content-detected via `convo_miner.detect_convo_room` (`technical` / `decisions` / `problems` / etc.) |
| `source_file` | `opencode://<absolute-db-path>#session=<sid>` (adapter) or session export path (plugin) |
| `content` | verbatim user + assistant text, no summarization |
| `extract_mode` | `exchange` |
| Adapter-specific metadata | `session_id`, `session_title`, `project_dir`, `session_created_at`, `message_count`, `opencode_session_version`, `opencode_db_path` |

## Read-side recall

OpenCode agents can call any of the 30 MCP tools the daemon exposes:

- `mempalace_search` — semantic search across all drawers
- `mempalace_list_wings` / `mempalace_list_rooms` / `mempalace_get_taxonomy` — palace navigation
- `mempalace_kg_query` / `mempalace_kg_timeline` — knowledge graph
- `mempalace_diary_read` / `mempalace_diary_write` — agent diaries
- `mempalace_traverse` / `mempalace_find_tunnels` — cross-wing connections

The daemon serializes all writes through a single chokepoint, so multiple OpenCode windows + Claude Code + the live-capture plugin all coexist without HNSW corruption.

## Verifying the integration

After setup, in any OpenCode session:

```
> Use mempalace_status to confirm we're connected.
```

Expected: agent calls the tool and reports drawer count + wing list. If you see "no palace found" or auth errors, check that the wrapper script can read `~/.config/palace-daemon/env`.

To confirm live-capture is firing, watch `mempalace status` over a few minutes of OpenCode use — drawer count should increment.

## Coordination notes

- Upstream PR #1484 carries co-authored credit to JakobSachs for the original DB-schema spadework on PR #23.
- This fork-ahead carries 5 commits from #1484 + 2 commits from #1567 (see `docs/fork-changes.yaml` `opencode-adapter-cherry-pick` and `opencode-mcp-config-cherry-pick` entries).
- option-K's plugin v1.2.1 issue #1 (daemon-routing patch) is filed but unresolved; the patch in `examples/opencode/` is our local fix until upstream lands a real one.
