# `mempalace.update_awareness`

Source: [`mempalace/update_awareness.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/update_awareness.py)

Opt-in, cached release awareness without automatic installation.

## Functions

### `configure_updates`

```python
def configure_updates(*, enabled: bool, interval_days: int = 7, installer: str | None = None, config_dir = None) -> dict
```

Persist explicit release-check consent and return the resulting policy.

### `fetch_latest_stable`

```python
def fetch_latest_stable() -> str
```

Return the latest stable version advertised by the official PyPI project.

### `check_updates`

```python
def check_updates(*, force: bool = False, config_dir = None, installed_version: str = __version__, fetch_latest = fetch_latest_stable, now: datetime | None = None) -> dict
```

Check or read cached stable-release state without ever installing it.

### `cached_update_status`

```python
def cached_update_status(*, config_dir = None, installed_version: str = __version__) -> dict
```

Return agent-safe cached state without performing network access.

### `prepare_upgrade`

```python
def prepare_upgrade(*, config_dir = None, installed_version: str = __version__, installer: str | None = None) -> dict
```

Describe an upgrade without executing or authorizing it.

### `schedule_update_check`

```python
def schedule_update_check(*, config_dir = None, fetch_latest = fetch_latest_stable, now: datetime | None = None) -> bool
```

Start one non-blocking due check when the user opted in.
