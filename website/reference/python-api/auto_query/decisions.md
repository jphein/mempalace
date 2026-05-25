# `mempalace.auto_query.decisions`

Source: [`mempalace/auto_query/decisions.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_query/decisions.py)

Decision logger for the auto-query system.

Append-only JSONL log of every auto-query decision (fire, skip, dry-run-skip).
Supports tail-efficient reads and size-based rotation with 3 kept generations.

## Functions

### `append_decision`

```python
def append_decision(decision: Decision, log_dir: Optional[str] = None) -> None
```

Append a decision to the JSONL log file.

Creates the log directory lazily on first write and sets file
permissions to 0o600 (owner-only) since the log may contain
entity names and query text from private conversations.

### `read_decisions`

```python
def read_decisions(last_n: int = 50, log_dir: Optional[str] = None) -> list
```

Read the last *last_n* decisions from the log file.

Returns a list of dicts (parsed JSON).  Corrupt or partial trailing
lines are silently skipped so a crash mid-write never prevents reads.

### `rotate_log`

```python
def rotate_log(log_dir: Optional[str] = None, max_bytes: int = _DEFAULT_MAX_BYTES) -> None
```

Rotate the log file if it exceeds *max_bytes*.

Rotation scheme::

    decisions.jsonl   -> decisions.jsonl.1
    decisions.jsonl.1 -> decisions.jsonl.2
    decisions.jsonl.2 -> decisions.jsonl.3
    decisions.jsonl.3 -> (deleted)

Keeps at most ``_MAX_ROTATIONS`` (3) old generations.
