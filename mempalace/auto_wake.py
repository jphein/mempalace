"""Wake-on-demand for a sleeping palace host.

The palace daemon often runs on a host that suspends to save power
(Wake-on-LAN-armed), so "connection refused / no route" is a routine
state, not a fault. When a CLI request hits a connection-level failure,
this module can run a user-configured wake command (a WoL sender, an
IPMI call, anything), wait for the daemon's ``/health`` endpoint to
come back, and retry the original request once.

Strictly opt-in: enabled only by an ``auto_wake`` entry in
``~/.mempalace/config.json`` (see :meth:`MempalaceConfig.auto_wake`);
``PALACE_AUTO_WAKE=0`` force-disables without editing config. The wake
command runs through the shell with the same trust level as the user's
own shell startup files — it comes from their config file, never from
palace content.

Scope: interactive CLI calls only. Hooks deliberately stay out — they
have a latency budget and their failed mines are already journaled and
replayed by :mod:`mempalace.pending_queue`.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request

_WAKE_COMMAND_TIMEOUT = 15  # seconds the wake command itself may take

# One wake attempt per process: a multi-request CLI invocation must not
# stall repeatedly when the host genuinely won't come up.
_attempted = False


def _is_wake_eligible(exc: BaseException) -> bool:
    """True for connection-level failures a host wake could fix.

    ``HTTPError`` means the daemon answered — waking can't help, and
    it must propagate so 404-fallback paths keep working. It subclasses
    both ``URLError`` and ``OSError``, so it is excluded first.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, ConnectionError, OSError))


def _daemon_healthy(daemon_url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{daemon_url.rstrip('/')}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        # Any failure means "not healthy yet": besides the OSError family,
        # urlopen can raise ValueError (malformed URL from config) or
        # http.client.HTTPException mid-resume — none may crash the poll.
        return False


def _run_wake_command(command: str) -> bool:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=_WAKE_COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def attempt_wake(daemon_url: str, settings: dict) -> bool:
    """Run the wake command, then poll ``/health`` until the deadline.

    Returns True once the daemon answers. At most one attempt per
    process regardless of outcome.
    """
    global _attempted
    if _attempted:
        return False
    _attempted = True

    command = settings["command"]
    timeout_s = settings["timeout_seconds"]
    poll_s = settings["poll_interval_seconds"]

    # Deliberately not echoing the command: it comes from the user's
    # config and may embed credentials; stderr ends up in transcripts.
    print(
        f"palace-daemon unreachable — auto_wake: waking palace host "
        f"(waiting up to {timeout_s:.0f}s)",
        file=sys.stderr,
    )
    if not _run_wake_command(command):
        print("auto_wake: wake command failed", file=sys.stderr)
        return False

    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    while time.monotonic() < deadline:
        if _daemon_healthy(daemon_url):
            print(
                f"auto_wake: daemon is back after {time.monotonic() - started:.0f}s — retrying",
                file=sys.stderr,
            )
            return True
        time.sleep(poll_s)
    print(f"auto_wake: daemon still unreachable after {timeout_s:.0f}s", file=sys.stderr)
    return False


def urlopen_with_wake(req, timeout):
    """``urllib.request.urlopen`` with an optional wake-and-retry.

    Drop-in replacement for the CLI's daemon calls: on a connection-level
    failure with ``auto_wake`` configured, wake the host, wait for
    ``/health``, and retry the request once. Everything else — HTTP
    errors, disabled config, failed wake — re-raises the original error
    unchanged so existing ``DaemonError`` handling is untouched.
    """
    from .config import MempalaceConfig

    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as exc:
        if not _is_wake_eligible(exc):
            raise
        config = MempalaceConfig()
        settings = config.auto_wake
        daemon_url = config.daemon_url
        if not settings or not daemon_url:
            raise
        if not attempt_wake(daemon_url, settings):
            raise
        return urllib.request.urlopen(req, timeout=timeout)
