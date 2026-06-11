# `mempalace.backends._sidecar`

Source: [`mempalace/backends/_sidecar.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/_sidecar.py)

Shared embedder-identity sidecar (RFC 001).

A small JSON file in the palace directory, keyed by collection name, recording
the embedder identity (``model_name`` / ``dimension``). It is deliberately
*separate* from a backend's mismatch marker: a marker's presence signals
"palace initialized" (reads raise ``CollectionNotInitializedError`` when the
marker exists but the store doesn't), so recording identity at first empty open
must not create one. The sidecar is unguarded, so a brand-new palace can record
identity immediately — the same approach the chroma backend uses.

## Functions

### `read_embedder_sidecar`

```python
def read_embedder_sidecar(path: Optional[str], collection_name: Optional[str])
```

Return the recorded :class:`EmbedderIdentity` for ``collection_name``, or None.

Robust to a missing, unreadable, or malformed (non-dict) sidecar — any of
those degrade to ``None`` (the ``unknown`` state) rather than raising.

### `write_embedder_sidecar`

```python
def write_embedder_sidecar(path: Optional[str], collection_name: Optional[str], identity) -> None
```

Record ``identity`` for ``collection_name`` in the sidecar, creating it if needed.

No-ops for a missing path, missing collection name, or a nameless identity.
Preserves other collections' entries; never raises on I/O failure.
