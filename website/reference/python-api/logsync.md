# `mempalace.logsync`

Source: [`mempalace/logsync.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/logsync.py)

logsync.py — Anti-entropy sync engine for the logstream (RFC 004 step 0)

Pull-based convergence between palace replicas: diff version vectors, pull
missing per-origin op ranges (artifacts first, so referenced ids never
dangle), fold with the idempotent apply primitives. Each replica pulls from
its peers; two replicas pulling from each other converge without any push
path or coordinator.

Transport for the pilot rides the RFC 004 transport seam's `request` shape over
plain HTTPS (Tailscale or any mutually-reachable network) using the peer's
hub bearer token. Peers are configured in ``peers.json`` in the palace dir:

    &#123;
      "peers": [
        &#123;"name": "windows", "url": "https://host.example.ts.net", "token": "..."}
      ]
    }

This module lives ABOVE the RFC 004 transport seam (mempalace/transport.py):
peers come from the transport's membership snapshot and every wire call goes
through its ``request``. Swapping the link (HTTPS bearer today, MeshGuard
next) never touches the sync logic here.

## Functions

### `sync_with_peer`

```python
def sync_with_peer(ls, url: str, token: str = '') -> dict
```

One anti-entropy round against one peer. Returns pull stats.

Never partially applies an event: artifacts are fetched and folded
before the event that references them, and every apply is idempotent,
so a crash mid-round just means the next round re-pulls the tail.

### `sync_all`

```python
def sync_all(ls, palace_path: str, transport = None) -> list[dict]
```

One round against every peer in the transport's membership snapshot;
per-peer errors are reported, never raised — one dead peer must not
block the others (R1: only convergence waits).
