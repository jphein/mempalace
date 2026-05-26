#!/usr/bin/env bash
# install-familiar-disk-monitor.sh — deploy the disk monitor to familiar.
#
# Run from this workstation: scripts/ops/install-familiar-disk-monitor.sh
# Requires SSH access to familiar and passwordless sudo there (per CLAUDE.md).
#
# Idempotent — re-run after editing the script or unit files to redeploy.

set -euo pipefail

HOST="${1:-familiar}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying familiar-disk-monitor to ${HOST}"

# Copy script + unit files to a staging dir, then sudo-install on target.
ssh "$HOST" 'mkdir -p /tmp/familiar-disk-monitor-staging'
scp -q \
    "$SCRIPT_DIR/familiar-disk-monitor.sh" \
    "$SCRIPT_DIR/familiar-disk-monitor.service" \
    "$SCRIPT_DIR/familiar-disk-monitor.timer" \
    "$HOST:/tmp/familiar-disk-monitor-staging/"

ssh "$HOST" 'bash -s' <<'EOF'
set -euo pipefail
STAGE=/tmp/familiar-disk-monitor-staging

sudo install -m 0755 "$STAGE/familiar-disk-monitor.sh" /usr/local/sbin/familiar-disk-monitor.sh
sudo install -m 0644 "$STAGE/familiar-disk-monitor.service" /etc/systemd/system/familiar-disk-monitor.service
sudo install -m 0644 "$STAGE/familiar-disk-monitor.timer"   /etc/systemd/system/familiar-disk-monitor.timer

sudo systemctl daemon-reload
sudo systemctl enable --now familiar-disk-monitor.timer

echo "==> Installed. Running one-shot test:"
sudo systemctl start familiar-disk-monitor.service
sleep 1
journalctl -u familiar-disk-monitor.service -n 5 --no-pager

echo "==> Timer status:"
systemctl list-timers familiar-disk-monitor.timer --no-pager

rm -rf "$STAGE"
EOF

echo "==> Done. Tail with: ssh ${HOST} journalctl -u familiar-disk-monitor -f"
