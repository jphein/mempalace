# familiar-disk-monitor

On-host disk-headroom alert for the filesystem hosting `/var/lib/docker` on
familiar. Fires WARN-level log entries and optional ntfy pages when usage
crosses thresholds. Closes
[techempower-org/mempalace#233](https://github.com/techempower-org/mempalace/issues/233).

## Why

familiar runs the palace-daemon stack (postgres + pgvector + AGE + kg-extract
workers) out of `/var/lib/docker`. The disk was at 83% used / 40 GB free as
of the 2026-05-26 PG17 upgrade audit. At ~90% postgres autovacuum starts to
fail, which stalls KG backfill, and the failure mode today is "search times
out and we go looking" — there is no proactive signal.

This monitor closes that gap with the smallest possible piece of code:
a 100-line bash script and a 15-minute systemd timer, modelled on the
existing `familiar-watchdog` so JP has one mental model for both.

## What's monitored

- The mount that contains `/var/lib/docker` (resolved at runtime via `df`).
  Today that's `/` on `/dev/nvme0n1p2`; if docker moves to a dedicated
  volume in the future, the monitor follows it without code changes.
- Three thresholds, configurable via environment variables:
  - **85% → notice** (journal-only WARN, no page)
  - **90% → warn** (journal WARN + ntfy page, priority `high`)
  - **95% → critical** (journal WARN + ntfy page, priority `urgent`)

## Alert channel

- **Journal**: every run logs JSON-per-line to the unit's journal. Tail
  with `journalctl -u familiar-disk-monitor -f`.
- **ntfy.sh**: if `/etc/familiar-watchdog/ntfy.env` exists and exports
  `NTFY_TOPIC=<topic>`, warn/critical events POST to
  `https://ntfy.sh/<topic>` with a `Title:` and `Priority:` header. Same
  env file as `familiar-watchdog` — configure the topic once, both
  monitors use it.
- **Silent on the happy path**: under 85% the script writes a single
  `disk_ok` info line per run; no pages.

## Install

From this workstation (passwordless sudo on familiar per CLAUDE.md):

```bash
scripts/ops/install-familiar-disk-monitor.sh
```

The installer copies the script to `/usr/local/sbin/familiar-disk-monitor.sh`,
the unit files to `/etc/systemd/system/`, runs `daemon-reload`, enables
the timer, and fires the service once as a smoke test.

To deploy to a different host (e.g. for testing):

```bash
scripts/ops/install-familiar-disk-monitor.sh <hostname>
```

## Test

Force each tier locally by overriding the thresholds — no need to fill
the disk:

```bash
# notice tier (journal-only)
sudo THRESHOLD_NOTICE=1 THRESHOLD_WARN=99 THRESHOLD_CRIT=100 \
  /usr/local/sbin/familiar-disk-monitor.sh

# warn tier (journal + ntfy page)
sudo THRESHOLD_NOTICE=1 THRESHOLD_WARN=1 THRESHOLD_CRIT=100 \
  /usr/local/sbin/familiar-disk-monitor.sh

# critical tier (journal + ntfy page, urgent)
sudo THRESHOLD_NOTICE=1 THRESHOLD_WARN=1 THRESHOLD_CRIT=1 \
  /usr/local/sbin/familiar-disk-monitor.sh
```

Watch the timer schedule:

```bash
systemctl list-timers familiar-disk-monitor.timer
```

Tail live:

```bash
journalctl -u familiar-disk-monitor -f
```

Query historical events (last 7 days, warn-level only):

```bash
journalctl -u familiar-disk-monitor -p warning --since '7 days ago'
```

Filter to critical events only (parseable JSON):

```bash
journalctl -u familiar-disk-monitor --since '7 days ago' -o cat \
  | grep '"event":"disk_critical"'
```

## Disable

```bash
sudo systemctl disable --now familiar-disk-monitor.timer
```

To uninstall completely:

```bash
sudo systemctl disable --now familiar-disk-monitor.timer
sudo rm /etc/systemd/system/familiar-disk-monitor.{service,timer}
sudo rm /usr/local/sbin/familiar-disk-monitor.sh
sudo systemctl daemon-reload
```

## Out of scope

This deliberately does **not** do:

- Image cleanup or autovacuum tuning (separate action items in #233).
- Per-container or per-volume usage breakdown (use `docker system df`).
- Metrics ingestion or Prometheus export (collectd's `df` plugin already
  publishes filesystem metrics; this monitor is the alert layer on top
  of "is the disk filling," not a replacement for the metrics path).
- Migrating mempalace-db to a dedicated volume (also tracked in #233).

If you reach for any of those, file a new issue.
