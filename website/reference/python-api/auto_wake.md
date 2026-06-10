# `mempalace.auto_wake`

Source: [`mempalace/auto_wake.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_wake.py)

Wake-on-demand for a sleeping palace host.

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

## Functions

### `attempt_wake`

```python
def attempt_wake(daemon_url: str, settings: dict) -> bool
```

Run the wake command, then poll ``/health`` until the deadline.

Returns True once the daemon answers. At most one attempt per
process regardless of outcome.

### `urlopen_with_wake`

```python
def urlopen_with_wake(req, timeout)
```

``urllib.request.urlopen`` with an optional wake-and-retry.

Drop-in replacement for the CLI's daemon calls: on a connection-level
failure with ``auto_wake`` configured, wake the host, wait for
``/health``, and retry the request once. Everything else — HTTP
errors, disabled config, failed wake — re-raises the original error
unchanged so existing ``DaemonError`` handling is untouched.
