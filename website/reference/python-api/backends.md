# `mempalace.backends`

Source: [`mempalace/backends/__init__.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/__init__.py)

Storage backend implementations for MemPalace (RFC 001).

Public surface:

* :class:`BaseCollection` — per-collection read/write contract.
* :class:`BaseBackend` — per-palace factory contract.
* :class:`PalaceRef` — value object identifying a palace for a backend.
* :class:`QueryResult` / :class:`GetResult` — typed read returns.
* Error classes: :class:`PalaceNotFoundError`, :class:`BackendClosedError`,
  :class:`UnsupportedFilterError`, :class:`DimensionMismatchError`,
  :class:`EmbedderIdentityMismatchError`.
* Registry: :func:`get_backend`, :func:`register`, :func:`available_backends`,
  :func:`resolve_backend_for_palace`.
* In-tree Chroma default: :class:`ChromaBackend`, :class:`ChromaCollection`.
* Optional PostgreSQL backend: :class:`PostgresBackend`, :class:`PostgresCollection`.
