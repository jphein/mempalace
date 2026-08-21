# `mempalace.mcp_proxy`

Source: [`mempalace/mcp_proxy.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/mcp_proxy.py)

Thin stdio front end for the MemPalace MCP server.

``mempalace-mcp`` is spawned once per agent session, and when a hub is
running every one of those processes is a pure proxy: ``_dispatch_stdio_request``
forwards each JSON-RPC request over HTTP and the local storage stack is never
touched. Importing :mod:`mempalace.mcp_server` to do that costs ~77 MB anyway,
because chromadb (+61 MB on its own), numpy, pydantic, grpc and opentelemetry
are all pulled in at module scope. A fleet of 50 agents therefore paid ~3.9 GB
to hold proxies that do no work.

This module is the entry point instead. It imports only the standard library
plus :mod:`mempalace.config` and :mod:`mempalace.server_registry` (~5 MB each),
so a proxied session runs at roughly 22 MB. The full server is imported lazily,
and only when this process actually has to serve a request itself.

The fallback is deliberately preserved: a session whose hub dies keeps working.
It just stops being free at that point, so it says so — once to the log, and on
the tool result itself, because the agent driving the session is the one who
needs to know its memory backend changed shape underneath it.

## Functions

### `main`

```python
def main() -> None
```

Entry point for ``mempalace-mcp``.

Delegates to the full server for anything but a plain stdio session, and
for a plain stdio session with no hub to proxy to — in both cases the
heavy import was going to happen regardless.
