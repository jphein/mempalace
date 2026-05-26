# Power-event resilience — design

- **Project:** `mempalace` (jphein fork) + `palace-daemon` (system unit on disks)
- **Date:** 2026-05-21
- **Status:** Spec — JP approved verbally in conversation. Implementation in flight on `feat/power-resilience`.
- **Trigger:** Power outage on 2026-05-17. UPS gracefully shut down `mempalace-db` postgres at 23:27 UTC. Disks rebooted 2026-05-18 22:46 UTC. `restart: unless-stopped` honored the prior stop across the reboot, so the container stayed down. `PALACE_DAEMON_URL` is set in JP's env, so hooks are in "daemon owns writes" mode — local fallback ingest was skipped. **Net effect: ~3 days of conversation data silently dropped before JP asked "is the palace working?"**

## One-line summary

Make the postgres-backed palace stack survive a power event end-to-end: auto-recover on reboot, never silently drop writes during the outage window, surface degraded state to a human within minutes, and stop misreporting "no palace found" when the real cause is a stopped backend.

## Goals

1. **Auto-recovery.** `mempalace-db` and `palace-daemon` come back without manual intervention after any host reboot, including UPS-triggered graceful shutdowns.
2. **No silent write loss.** When the daemon's backend is unreachable, hooks write to a local append-only journal that replays on recovery. Preserves the fork's "verbatim always" promise.
3. **Visibility within minutes.** A failure shows up in `status.realm.watch` and as a one-line terminal warning on the next conversation start.
4. **Truthful error surfacing.** Daemon distinguishes "no palace configured" from "backend unreachable"; CLI renders the real cause instead of a misleading "Run: mempalace init" hint.

## Non-goals

- Hot HA / standby postgres. The fix is "come back cleanly + don't lose writes during the gap," not "stay up during the outage."
- Cross-host failover. Single host (disks), single daemon, single postgres.
- New monitoring service (Prometheus, alertmanager, etc.). `status.realm.watch` + the session-start warning are the alert surface.
- Retry-with-backoff inside the hook hot path. Retries live in the replay worker, off the <500ms hook budget.
- Conflict resolution for the queue. The daemon's existing per-drawer idempotency handles replay duplicates.

## Architecture

### Pillar 1 — Auto-recovery (infra)

Three small infra changes on disks:

- `/opt/mediaserver/docker-compose.yml`: `mempalace-db.restart: unless-stopped` → `restart: always`. Adds `healthcheck:` block (`pg_isready -U palace -d mempalace_test`, 30s interval, 5 retries, 30s start_period).
- `/etc/systemd/system/palace-daemon.service`: new `ExecStartPre=/bin/sh -c 'for i in $(seq 1 60); do nc -z 10.0.6.120 5433 && exit 0; sleep 1; done; exit 1'` before `ExecStart`. Prevents the daemon from cold-spamming connection-refused logs when it races postgres on boot.
- No `--no-deps` issues: the daemon is a host-level systemd unit, not in compose. Container and daemon are independently restartable.

`restart: always` is the right policy here because `unless-stopped` deliberately honors any prior stop — including ones initiated by `docker compose down`, NUT-triggered graceful shutdowns, or manual maintenance. The container should always come back on boot; if JP wants it down, `docker compose stop` while the system is running is still effective during a session.

### Pillar 2 — Pending-writes journal + replay

New module `mempalace/pending_queue.py`. Two functions:

- `enqueue(payload: dict) -> Path` — appends one JSON line to `~/.mempalace/pending/YYYY-MM-DD.jsonl`, fsync, returns the file path. Single append+fsync; well under the 500ms hook budget.
- `replay(daemon_url: str, api_key: str, timeout: float = 10.0) -> ReplayReport` — walks `pending/*.jsonl` in oldest-first order, posts each line to the daemon's `/mcp` endpoint, on 2xx rewrites the file with the consumed lines removed (atomic via `tempfile + os.replace`). Returns counts: `attempted, succeeded, failed, files_drained`.

Daemon-health check is a `GET /health` with a 200ms timeout — failure ⇒ degraded ⇒ enqueue.

Hook integration: the existing silent-save path (`hooks/stop.py`, `hooks/pre_compact.py`) gets a `try_daemon_then_queue(payload)` wrapper. It POSTs to `/mcp`; on `ConnectionError | Timeout | non-2xx | health != ok`, falls back to `pending_queue.enqueue`. The save marker advances only after either the daemon-write succeeded OR the enqueue fsync returned.

CLI: new `mempalace replay` subcommand, plus replay is best-effort invoked at the top of `mempalace status` and inside `hooks/session_start.py` (off the hot path, behind a 2s timeout).

### Pillar 3 — Visibility

- `~/Projects/status.realm.watch/checks.json`: add `http://familiar.jphe.in:8085/health` under the `version` array per CLAUDE.md's realm-sigil convention. HTTP 200 = healthy on the dashboard; HTTP 503 = red.
- `hooks/session_start.py`: after the (best-effort) replay attempt, check daemon `/health` (200ms timeout) and pending-queue size. If either signals trouble, emit one `systemMessage` line: `"⚠ palace-daemon degraded (N pending writes)"`. Capped at one warning per session via a marker in `~/.mempalace/hook_state/`.

### Pillar 4 — Error surfacing

Two surgical edits in the fork:

- `mempalace/mcp_server.py` — wrap the `psycopg2.OperationalError` catch in `_get_collection_postgres` (or the closest equivalent in the routing layer) so that connection-refused / connection-timeout / authentication failures map to a new MCP error code `palace.backend_unreachable` with the underlying message attached. Genuine empty-collection / missing-palace conditions continue to map to "No palace found."
- `mempalace/cli.py` — render `palace.backend_unreachable` with: `daemon up, but its postgres backend is unreachable (<dsn-host>:<port>). Last error: <message>. Check: docker ps mempalace-db`. Replaces the misleading `Run: mempalace init <dir> && mempalace mine <dir>` hint.

## Data flow (write path)

```
Stop hook fires
    ↓
build payload (verbatim diary entry)
    ↓
GET /health  (200ms timeout) ── degraded ──→ pending_queue.enqueue(payload)  ──→ done
    ↓ ok
POST /mcp diary_write  (10s timeout)
    ↓ 2xx          ↓ error
   done       pending_queue.enqueue(payload) ──→ done
```

## Data flow (recovery path)

```
session_start hook
    ↓
pending_queue.replay() (best-effort, 2s timeout, swallow exceptions)
    ↓                                 ↓
GET /health ok + queue empty?    queue non-empty OR /health != 200?
    ↓ yes                              ↓ yes
proceed silently                  emit one systemMessage warning, then proceed
```

## Where it lives

- Pillar 1: on disks (already shipped: compose yaml + systemd unit edited 2026-05-21).
- Pillars 2 + 4: fork branch `feat/power-resilience` in `jphein/mempalace`. PR target: `jphein/mempalace:main` (per "Own-projects PR workflow" memory). Candidate for upstream PR against `MemPalace/mempalace:develop` after the fork PR settles — the daemon-unreachable case is universal.
- Pillar 3: `~/Projects/status.realm.watch/checks.json` + hook code in the fork.

## Testing

- Unit tests for `pending_queue.enqueue` (fsync, formatting, recovery after partial write).
- Unit tests for `pending_queue.replay` (drain, atomic rewrite on partial 2xx, idempotency).
- Integration test for the hook fallback path using a stub daemon that returns 503.
- Manual: stop `mempalace-db` on disks, do a Stop hook, confirm the entry lands in `~/.mempalace/pending/`, restart the container, run `mempalace replay`, confirm drainage.

## Open questions

None blocking. Tags / categorization of pending entries (vs raw replay) is out of scope.
