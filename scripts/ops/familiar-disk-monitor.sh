#!/usr/bin/env bash
# familiar-disk-monitor.sh — disk-headroom alert for familiar's docker volume.
#
# Runs every 15 min via familiar-disk-monitor.timer. Checks the filesystem
# hosting /var/lib/docker (postgres + pgvector + AGE + kg-extract workers)
# and fires a WARN log entry + optional ntfy page when usage crosses
# thresholds. Stays silent on the happy path.
#
# Thresholds (issue techempower-org/mempalace#233):
#   - 85% → WARN log only (early notice; pg autovacuum still healthy)
#   - 90% → WARN log + ntfy page (autovacuum failure zone; backfill stalls)
#   - 95% → WARN log + ntfy page with critical priority (action required)
#
# /var/lib/docker is on / (single ext4 partition on nvme0n1p2 as of
# 2026-05-26); the script measures the mount that contains it rather
# than the path itself so a future move to a dedicated volume keeps
# working without code changes.
#
# Log format: JSON-per-line so `journalctl -u familiar-disk-monitor`
# is parseable. Each WARN line carries `event`, `mount`, `usage_pct`,
# `free_gb` so alerts can be grepped without parsing prose.
#
# Exits 0 always (timer wants service-level "active") — degradation is
# signalled via WARN log entries, not exit code.
#
# Documentation: docs/operators/familiar-disk-monitor.md

set -u

# Path to monitor. The script resolves this to its mount point at runtime
# so the same code works whether docker lives on / or on a dedicated volume.
WATCH_PATH="${WATCH_PATH:-/var/lib/docker}"

# Threshold percentages — override via environment for testing.
THRESHOLD_NOTICE="${THRESHOLD_NOTICE:-85}"
THRESHOLD_WARN="${THRESHOLD_WARN:-90}"
THRESHOLD_CRIT="${THRESHOLD_CRIT:-95}"

# Optional ntfy paging — loads NTFY_TOPIC if config file exists. Missing
# file is fine; notify_ntfy is then a no-op. Same env file as the
# familiar-watchdog so JP only configures the topic once.
NTFY_TOPIC=""
NTFY_ENV_FILE="${NTFY_ENV_FILE:-/etc/familiar-watchdog/ntfy.env}"
# shellcheck disable=SC1090
[ -r "$NTFY_ENV_FILE" ] && . "$NTFY_ENV_FILE"

# notify_ntfy — POST a single line to ntfy.sh. Backgrounded so a slow
# ntfy.sh doesn't delay the monitor. Priority is set per-call.
notify_ntfy() {
    local msg="$1"
    local priority="${2:-default}"
    [ -z "${NTFY_TOPIC:-}" ] && return
    local event
    event=$(echo "$msg" | grep -oE '"event":"[^"]*"' | head -1 | cut -d'"' -f4)
    local title="familiar-disk-monitor: ${event:-warn}"
    curl -sS --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: floppy_disk,warning" \
        -d "${msg}" \
        "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null 2>&1 &
}

log_warn() {
    local ts msg
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    msg="{\"level\":\"warn\",\"ts\":\"$ts\",$1}"
    echo "$msg"
    notify_ntfy "$msg" "${2:-high}"
}
log_info() {
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "{\"level\":\"info\",\"ts\":\"$ts\",$1}"
}

# Resolve mount point and read usage percentage + free GB in one df call.
# `--output` is GNU coreutils (present on Ubuntu, the target host); `-B1G`
# rounds available space to GiB. Output columns: target, pcent, avail.
read_disk_state() {
    local line
    line=$(df --output=target,pcent,avail -B1G "$WATCH_PATH" 2>/dev/null | tail -1)
    if [ -z "$line" ]; then
        log_warn "\"event\":\"df_failed\",\"path\":\"$WATCH_PATH\""
        exit 0
    fi
    # shellcheck disable=SC2086
    set -- $line
    MOUNT="$1"
    USAGE_PCT="${2%\%}"
    FREE_GB="$3"
}

read_disk_state

# Validate parsing — defensive against future df format changes.
if ! [[ "$USAGE_PCT" =~ ^[0-9]+$ ]]; then
    log_warn "\"event\":\"parse_failed\",\"raw\":\"$USAGE_PCT\""
    exit 0
fi

EVENT_FIELDS="\"mount\":\"$MOUNT\",\"path\":\"$WATCH_PATH\",\"usage_pct\":$USAGE_PCT,\"free_gb\":$FREE_GB"

if [ "$USAGE_PCT" -ge "$THRESHOLD_CRIT" ]; then
    log_warn "\"event\":\"disk_critical\",$EVENT_FIELDS,\"threshold\":$THRESHOLD_CRIT" "urgent"
elif [ "$USAGE_PCT" -ge "$THRESHOLD_WARN" ]; then
    log_warn "\"event\":\"disk_warn\",$EVENT_FIELDS,\"threshold\":$THRESHOLD_WARN" "high"
elif [ "$USAGE_PCT" -ge "$THRESHOLD_NOTICE" ]; then
    # Notice tier: journal-only, no ntfy page.
    echo "{\"level\":\"warn\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"event\":\"disk_notice\",$EVENT_FIELDS,\"threshold\":$THRESHOLD_NOTICE}"
else
    log_info "\"event\":\"disk_ok\",$EVENT_FIELDS"
fi
