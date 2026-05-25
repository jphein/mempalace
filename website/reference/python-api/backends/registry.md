# `mempalace.backends.registry`

Source: [`mempalace/backends/registry.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backends/registry.py)

Backend registry + entry-point discovery (RFC 001 §3).

Third-party backends ship as installable packages that declare a
``mempalace.backends`` entry point::

    # pyproject.toml of mempalace-postgres
    [project.entry-points."mempalace.backends"]
    postgres = "mempalace_postgres:PostgresBackend"

MemPalace discovers them at process start. In-tree tests and local development
can register manually via :func:`register`. Explicit registration wins on
name conflict (matches RFC 001 §3.2).

## Functions

### `register`

```python
def register(name: str, backend_cls: Type[BaseBackend]) -> None
```

Register ``backend_cls`` under ``name``.

Explicit registration wins over entry-point discovery on conflict
(RFC 001 §3.2).

### `unregister`

```python
def unregister(name: str) -> None
```

Remove a backend registration (primarily for tests).

### `available_backends`

```python
def available_backends() -> list[str]
```

Return sorted list of all registered backend names.

### `get_backend_class`

```python
def get_backend_class(name: str) -> Type[BaseBackend]
```

Return the registered backend class for ``name``.

### `get_backend`

```python
def get_backend(name: str) -> BaseBackend
```

Return a long-lived instance of the named backend.

Instances are cached per-name; repeated calls return the same object.
Call :func:`reset_backends` in tests that need isolation.

### `reset_backends`

```python
def reset_backends() -> None
```

Close and drop all cached backend instances (primarily for tests).

### `resolve_backend_for_palace`

```python
def resolve_backend_for_palace(*, explicit: Optional[str] = None, config_value: Optional[str] = None, env_value: Optional[str] = None, palace_path: Optional[str] = None, default: str = 'chroma') -> str
```

Resolve the backend name for a palace per RFC 001 §3.3 priority order.

1. Explicit kwarg / CLI flag
2. Per-palace config value
3. ``MEMPALACE_BACKEND`` env var
4. Auto-detect from on-disk artifacts (migration/upgrade path only)
5. Default (``chroma``)

Auto-detection is strictly a migration aid: it fires only when a local path
is presented, no earlier rule has chosen a backend, AND the path already
contains backend-identifiable artifacts. For new palaces, (5) wins.
