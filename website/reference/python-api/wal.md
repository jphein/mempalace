# `mempalace.wal`

Source: [`mempalace/wal.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/wal.py)

Side-effect-free write-ahead log for MemPalace write operations.

This lives in its own module so callers that only need WAL audit logging — the
CLI ``sync`` path and the daemon's ``service`` layer — can obtain ``_wal_log``
without importing :mod:`mempalace.mcp_server`. Importing ``mcp_server`` runs its
module-level stdio protection (``os.dup2(2, 1)`` and ``sys.stdout = sys.stderr``,
required so the MCP stdio JSON stream isn't corrupted by C-level library
banners). In a non-MCP process — e.g. the daemon worker or ``mempalace sync`` —
that redirect is an unwanted import side effect that misroutes operator output,
so the WAL machinery is kept here, free of any such side effects.
