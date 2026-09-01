# `mempalace.tasks`

Source: [`mempalace/tasks.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/tasks.py)

High-level task envelopes shared by CLI and remote MCP clients.

## Functions

### `validate_task_base_commit`

```python
def validate_task_base_commit(value: str) -> str
```

Require an immutable abbreviated or full Git object id.

### `validate_task_request`

```python
def validate_task_request(task, *, source: str = 'task request') -> dict
```

Validate every field the controlled launcher relies on.

### `task_slug`

```python
def task_slug(value: str, fallback: str = 'work') -> str
```

Return a short routing-safe label for task ids and project streams.

### `task_handoff`

```python
def task_handoff(correlation_id: str, agent: str) -> str
```

Render the portable one-line wake-up prompt for a stored task.

### `create_task`

```python
def create_task(logstream, *, project: str, from_agent: str, to_agent: str, goal: str, branch: str, base_commit: str, done: str) -> dict
```

Append one canonical task request and return it with its handoff line.
