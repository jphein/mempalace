# `.sh` shim delegation strategy

**Status:** active fork-ahead decision
**First filed:** 2026-05-22 ([issue #69](https://github.com/techempower-org/mempalace/issues/69))
**Counter-position to:** upstream [MemPalace/mempalace#1069](https://github.com/MemPalace/mempalace/issues/1069)
**Related discussion:** [MemPalace/mempalace#1497](https://github.com/MemPalace/mempalace/issues/1497) — gateway pattern recommendation

## TL;DR

Upstream wants to consolidate the per-event `.sh` hook wrappers
(`mempal_save_hook.sh`, `mempal_precompact_hook.sh`, …) into thin
shims that delegate to **`mempalace hook run --hook <name>`** — i.e.
move all hook logic into the `mempalace` Python CLI.

This fork went the **opposite direction**: the `.sh` shims delegate
to **`palace-daemon/clients/hook.py`** (a stdlib-only Python script
in a separate repo) which talks HTTP to a single FastAPI gateway
(`familiar.jphe.in:8085`). The `mempalace` Python package is no longer
in the hook call path at all.

We are not asking upstream to do this. We are documenting the
divergence so future contributors don't "fix" the shims back to
the upstream shape and so the upstream discussion in #1497 has a
working reference.

## Why we keep `.sh` shims

The temptation is to delete them: `hooks.json` can call `python3
/path/to/hook.py` directly, and recent Claude Code releases honor
that. We keep the shims for three concrete reasons:

1. **Backward compatibility with stale Claude Code sessions.** A
   Claude Code session loads its hook config once, at startup.
   Sessions that started before the 2026-05-11 split-brain fix
   still have the *old* hook config in memory — the one that
   points at `.claude-plugin/hooks/mempal-stop-hook.sh`. Keeping
   the shim as a thin pass-through means those sessions still
   route through the daemon instead of erroring out or, worse,
   silently writing to a stale local palace.
2. **Operational simplicity.** The shim is the one knob a host
   admin can flip without editing JSON. Override `PALACE_DAEMON_HOOK_PY`
   to point at a different `hook.py` location (CI fixture,
   non-default install path) and every harness that calls the
   shim picks it up. No need to chase `hooks.json` per repo.
3. **Graceful absence.** If `palace-daemon/clients/hook.py` isn't
   present on this host (fresh checkout, CI runner without the
   sibling repo cloned), the shim exits 0. The harness keeps
   working; hooks become no-ops. The Python-CLI direction has
   no equivalent escape — a missing `mempalace` binary surfaces
   as a hook timeout.

## Why delegate to palace-daemon

Three constraints made the daemon the right write authority:

| Upstream #1069 direction | This fork's direction |
|---|---|
| `mempalace hook run` (Python CLI) | `palace-daemon/clients/hook.py` (stdlib Python) |
| Requires `mempalace` import at hook time | Hook has no `mempalace` dep |
| Optional daemon routing via `PALACE_DAEMON_URL` | Always-on, via HTTP |
| Library-level lock in `mempalace` | Single-writer gateway (daemon `_mine_sem`) |
| Vulnerable to CLI version-mismatch hangs (upstream #1465) | Sidestepped — no CLI in the call path |
| Failure mode if daemon down: undefined | Hook returns `{}` (silent), no error |

The single-writer-gateway property is the load-bearing one.
With 300K+ drawers and multiple harnesses (Claude Code, codex,
gemini-cli, opencode, the MCP server) all writing concurrently,
serialization through a single FastAPI process running on
`familiar.jphe.in:8085` is the only thing that keeps ChromaDB's
HNSW from corrupting under concurrent mine jobs (upstream #1161).

## The delegation pattern

```
Claude Code Stop event
        │
        ▼
hooks.json: "command": "/path/to/mempal-stop-hook.sh"
        │
        ▼
mempal-stop-hook.sh   ◄── thin .sh shim, ~5 lines of logic
        │
        │   exec python3 $HOOK_PY --hook stop --harness claude-code
        ▼
palace-daemon/clients/hook.py   ◄── stdlib Python, no mempalace import
        │
        │   urllib.request → POST http://familiar.jphe.in:8085/mine
        ▼
palace-daemon (FastAPI)   ◄── single writer, _mine_sem=1
        │
        ▼
mempalace.mcp_server (in-process)
        │
        ▼
postgres + pgvector + AGE  (on familiar.jphe.in)
```

Two properties matter:

- **The shim doesn't know about MemPalace internals.** It just
  forwards `argv` to `hook.py` and respects the
  `PALACE_DAEMON_HOOK_PY` override.
- **`hook.py` doesn't know about the database.** It speaks HTTP
  to the daemon. Schema changes, backend swaps (chroma→pgvector,
  pgvector→whatever's next) don't ripple through hook code.

## Shim template

Three live shims in this repo follow the same shape — copy
them when adding a new harness:

```bash
#!/bin/bash
# MemPalace <event> Hook — thin wrapper delegating to palace-daemon's hook.py.
#
# Override the hook.py location with PALACE_DAEMON_HOOK_PY=/path/to/hook.py
# — required on hosts where palace-daemon lives somewhere other than the
# default below (e.g. CI fixtures, alternate install paths).
#
# If palace-daemon's hook.py is missing on this machine, we exit 0 (not
# a hard error) so a <event> event from a host without palace-daemon
# doesn't gum up the harness.

HOOK_PY="${PALACE_DAEMON_HOOK_PY:-/home/jp/Projects/palace-daemon/clients/hook.py}"
if [ -x "$(command -v python3)" ] && [ -f "$HOOK_PY" ]; then
    exec python3 "$HOOK_PY" --hook <event> --harness <harness> "$@"
fi
exit 0
```

Live examples:

- `.claude-plugin/hooks/mempal-stop-hook.sh` — `--hook stop --harness claude-code`
- `.claude-plugin/hooks/mempal-precompact-hook.sh` — `--hook precompact --harness claude-code`
- `.codex-plugin/hooks/mempal-hook.sh` — generic codex shim (hook name from `$1`)

`SessionStart` is registered directly in `hooks.json` (no shim) —
it predates the split-brain fix and didn't have a stale-session
problem to solve. New harnesses can follow either pattern.

## Sample: search-via-daemon

For CLI use beyond hooks, the same delegation pattern works for
read operations. `scripts/mempalace-search.sh` (see file) is a
1:1 demo: HTTP GET against `/search`, fall back silently if the
daemon is unreachable. The daemon's API surface (from
`palace-daemon/main.py`) covers everything a shim might want to
expose: `/search`, `/list`, `/stats`, `/mine`, `/silent-save`,
`/memory`, `/graph`, `/cypher`, `/health`.

## When to re-converge

This decision is reversible. Re-converge with upstream's #1069
if any of the following becomes true:

- Upstream's gateway recommendation in #1497 lands with the
  same single-writer property and a Python entry point that's
  cheap to call (no cold-start cost).
- The `mempalace` CLI grows a daemon-aware mode that doesn't
  require a separate sibling repo to install.
- All harnesses we care about migrate off `.sh` hook configs
  (so the back-compat reason evaporates) AND the daemon's
  HTTP entry point becomes the *upstream* recommended pattern.

Until then, the shims stay thin, the daemon stays the writer,
and the `mempalace` package stays out of the hook call path.
