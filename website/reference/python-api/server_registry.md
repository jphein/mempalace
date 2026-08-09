# `mempalace.server_registry`

Source: [`mempalace/server_registry.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/server_registry.py)

Per-palace discovery registry for the MemPalace HTTP MCP hub.

``mempalace serve`` (and ``mempalace-mcp --transport http``) is meant to be
the single long-lived writer for a shared palace: once it performs its first
mutating tool call it holds the per-palace MCP writer lease (#1818) for its
whole lifetime, and every other process is refused palace writes. That is
correct for agents — they talk to the hub — but the background save hooks
and the plain CLI still spawn short-lived ``mempalace mine`` processes,
which would be refused while a hub is up and silently stop transcript
capture on the hub machine.

This module gives those local processes a way to find the hub instead of
fighting it: the HTTP transport records ``&#123;pid, host, port, scheme,
read_only}`` next to the per-palace bearer token
(``~/.mempalace/server/&lt;key>/``), and callers use
:func:`read_live_serverinfo` to decide "forward this write over HTTP" vs
"no hub — do the write directly".

The registry is local-machine only by design: it lives under the user's
home, is keyed by the canonical palace path, and a record is trusted only
while the recorded pid is still alive — a crashed hub leaves a stale file
that every reader ignores and the next hub overwrites.

## Functions

### `server_state_dir`

```python
def server_state_dir(palace_path: str) -> Path
```

Per-palace state directory shared by the token and the serverinfo.

Keyed by the canonical palace path so one hub per palace reuses a stable
directory across restarts. Must stay in sync with the token location used
by ``mempalace serve`` (see ``cli._server_token_path``, which delegates
here).

### `server_token_path`

```python
def server_token_path(palace_path: str) -> Path
```

### `serverinfo_path`

```python
def serverinfo_path(palace_path: str) -> Path
```

### `write_serverinfo`

```python
def write_serverinfo(palace_path: str, *, host: str, port: int, scheme: str, read_only: bool)
```

Record this process as the palace's HTTP hub. Returns the file path.

0600 like the token: the record itself is not secret, but the directory
convention is "private to the user" and there is no reason to relax it.

### `clear_serverinfo`

```python
def clear_serverinfo(palace_path: str) -> None
```

Remove this process's serverinfo record, if it is still ours.

Guarded on the recorded pid so a slow atexit from an old hub cannot
delete the record a newer hub just wrote for the same palace.

### `read_live_serverinfo`

```python
def read_live_serverinfo(palace_path: str)
```

Return the hub record for this palace, or None.

None when no record exists, the record is unreadable, or the recorded
pid is no longer alive (crashed hub — stale file, ignore it).

### `client_base_url`

```python
def client_base_url(info: dict) -> str
```

Dial address for a local client, from a serverinfo record.

A wildcard bind ("all interfaces") is dialed via loopback — the record
is only ever read on the hub's own machine.

### `load_server_token`

```python
def load_server_token(palace_path: str) -> str
```

The palace's hub bearer token, or "" when none was ever generated.
