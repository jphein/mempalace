# `mempalace.transport`

Source: [`mempalace/transport.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/transport.py)

transport.py — The RFC 004 transport seam (Layer 1).

Layer 2 (anti-entropy sync of the logstream and, in step 2a, the memory
op-log) is allowed to see peers ONLY through this interface. Everything
about networks, addressing, keys, and which machines are awake lives
below the seam; nothing about merge semantics leaks down into it. The
full contract, from the RFC:

    self_id()                    stable ReplicaId of this node
    peers()                      membership snapshot
    request(peer, path, params)  one authenticated round-trip
    open_stream(peer, path)      long-lived tail, resumable by cursor
    on_presence_change(cb)       liveness deltas (R5)
    on_inbound(path, handler)    serve anti-entropy pulls

v1 ships the pull half — ``self_id``/``peers``/``request`` — because that
is all the shipping sync engine consumes. The push half raises
``NotImplementedError`` here and lands with the MeshGuard binding, where
SWIM membership supplies presence, the Ed25519 node identity IS the
ReplicaId (provenance and authentication as one fact), and membership in
the mesh replaces bearer tokens as the only ACL.

Two implementations are planned:

- :class:`HttpsBearerTransport` (this module, shipping): tailnet HTTPS
  with per-hub bearer tokens from ``peers.json``. Address discovery and
  channel encryption come from the tailnet; authorization is an
  app-layer token that a human must relay out-of-band for every edge —
  the N-squared-token model the MeshGuard transport retires.
- ``MeshGuardTransport`` (next): binds the meshguard daemon over its FFI.
  Selected via ``MEMPALACE_TRANSPORT=meshguard`` once it exists; the
  factory below is the single swap point, so Layer 2 never changes.

## Classes

### `class TransportError(Exception)`

A peer was unreachable or answered outside the protocol.

### `class Transport(ABC)`

The seam. Layer 2 holds one of these and nothing else network-shaped.

#### `self_id`

```python
def self_id(self) -> str
```

Stable ReplicaId of this node.

#### `peers`

```python
def peers(self) -> list[dict]
```

Current membership snapshot. Each peer dict carries at least
``name``; transport-specific addressing fields are opaque to
Layer 2, which only ever hands the dict back to :meth:`request`.

#### `request`

```python
def request(self, peer: dict, path: str, params: Optional[dict] = None) -> dict
```

One authenticated round-trip to ``peer``. Raises TransportError.

#### `open_stream`

```python
def open_stream(self, peer: dict, path: str)
```

#### `on_presence_change`

```python
def on_presence_change(self, callback)
```

#### `on_inbound`

```python
def on_inbound(self, path: str, handler)
```

### `class HttpsBearerTransport(Transport)`

Shipping transport: tailnet HTTPS + peers.json bearer tokens.

#### `__init__`

```python
def __init__(self, palace_path: str)
```

#### `self_id`

```python
def self_id(self) -> str
```

#### `peers`

```python
def peers(self) -> list[dict]
```

#### `request`

```python
def request(self, peer: dict, path: str, params: Optional[dict] = None) -> dict
```

## Functions

### `load_peers`

```python
def load_peers(palace_path: str) -> list[dict]
```

Read peers.json; [] when absent. Malformed files fail loudly.

peers.json is TRANSPORT configuration — addressing plus app-layer
authorization — which is why it lives on this side of the seam.

### `http_request`

```python
def http_request(base_url: str, token: str, path: str, params: dict = None) -> dict
```

The HTTPS wire primitive: one bearer-authenticated GET, JSON back.

### `get_transport`

```python
def get_transport(palace_path: str) -> Transport
```

The single swap point (MEMPALACE_TRANSPORT; default https).

``meshguard`` is reserved: selecting it before the binding lands
fails loudly rather than silently degrading to bearer tokens —
a user who asked for mesh-identity auth must never unknowingly
run on the token model.
