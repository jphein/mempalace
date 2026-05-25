# `mempalace.room_detector_local`

Source: [`mempalace/room_detector_local.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/room_detector_local.py)

room_detector_local.py — Local setup, no API required.

Two ways to define rooms without calling any AI:
  1. Auto-detect from folder structure (zero config)
  2. Define manually in mempalace.yaml

No internet. No API key. Your files stay on your machine.

## Functions

### `detect_rooms_from_folders`

```python
def detect_rooms_from_folders(project_dir: str) -> list
```

Walk the project folder structure.
Find top-level subdirectories that match known room patterns.
Returns list of room dicts.

### `detect_rooms_from_files`

```python
def detect_rooms_from_files(project_dir: str) -> list
```

Fallback: if folder structure gives no signal,
detect rooms from recurring filename patterns.

### `print_proposed_structure`

```python
def print_proposed_structure(project_name: str, rooms: list, total_files: int, source: str)
```

### `get_user_approval`

```python
def get_user_approval(rooms: list) -> list
```

Same approval flow as AI version.

### `save_config`

```python
def save_config(project_dir: str, project_name: str, rooms: list)
```

### `detect_rooms_local`

```python
def detect_rooms_local(project_dir: str, yes: bool = False)
```

Main entry point for local setup.
