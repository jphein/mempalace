# `mempalace.sources.registry`

Source: [`mempalace/sources/registry.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/registry.py)

Source adapter registry + entry-point discovery (RFC 002 §3).

Third-party adapters ship as installable packages that declare a
``mempalace.sources`` entry point::

    # pyproject.toml of mempalace-source-cursor
    [project.entry-points."mempalace.sources"]
    cursor = "mempalace_source_cursor:CursorAdapter"

MemPalace discovers them at process start. In-tree tests and local
development can register manually via :func:`register`. Explicit
registration wins on name conflict (RFC 002 §3.2).

Unlike storage backends (RFC 001 §3.3), source adapters are never auto-
detected — the user selects the adapter explicitly via ``--source NAME``
or config (§3.3). The default when no adapter is named is ``filesystem``
(to preserve current ``mempalace mine &lt;path>`` behavior).

## Functions

### `register`

```python
def register(name: str, adapter_cls: Type[BaseSourceAdapter]) -> None
```

Register ``adapter_cls`` under ``name``.

Explicit registration wins over entry-point discovery on conflict (§3.2).

### `unregister`

```python
def unregister(name: str) -> None
```

Remove an adapter registration (primarily for tests).

### `available_adapters`

```python
def available_adapters() -> list[str]
```

Return sorted list of all registered adapter names.

### `get_adapter_class`

```python
def get_adapter_class(name: str) -> Type[BaseSourceAdapter]
```

Return the registered adapter class for ``name``.

### `get_adapter`

```python
def get_adapter(name: str) -> BaseSourceAdapter
```

Return a long-lived instance of the named adapter.

Instances are cached per-name; repeated calls return the same object.
Call :func:`reset_adapters` in tests that need isolation.

### `reset_adapters`

```python
def reset_adapters() -> None
```

Close and drop all cached adapter instances (primarily for tests).

### `resolve_adapter_for_source`

```python
def resolve_adapter_for_source(*, explicit: str | None = None, config_value: str | None = None, default: str = _DEFAULT_ADAPTER) -> str
```

Resolve the adapter name per RFC 002 §3.3 priority order.

1. Explicit ``--source`` flag or kwarg
2. Per-source config value
3. Default (``filesystem``)

Auto-detection is *intentionally* absent on the read side (§3.3); a
directory containing ``.git`` + ``workspaceStorage/`` + an ``mbox`` file
is not a signal of user intent.
