# `mempalace.encoding_repair`

Source: [`mempalace/encoding_repair.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/encoding_repair.py)

Safely repair high-confidence UTF-8 mojibake in MemPalace drawers.

## Functions

### `repair_mojibake_once`

```python
def repair_mojibake_once(text: str) -> str
```

Repair one layer of high-confidence UTF-8-as-CP1252 mojibake.

### `repair_mojibake`

```python
def repair_mojibake(text: str, *, max_passes: int = 3) -> str
```

Repair repeated high-confidence mojibake layers until stable.

### `repair_collection`

```python
def repair_collection(collection, *, apply: bool = False, page_size: int = 500, backup_path: Optional[Union[str, Path]] = None, on_change: Optional[Callable[[str, str, str], None]] = None) -> dict
```

Scan a collection and optionally repair high-confidence mojibake.

### `restore_collection`

```python
def restore_collection(collection, backup_path: Union[str, Path], *, batch_size: int = 500) -> dict
```

Restore original documents from an encoding-repair backup.
