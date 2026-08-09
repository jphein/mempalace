# `mempalace.hlc`

Source: [`mempalace/hlc.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/hlc.py)

hlc.py — Hybrid Logical Clock for RFC 004 op ordering

Total order across replicas without trusting wall clocks: physical
milliseconds + a logical counter + the replica id as final tiebreak.
Rendered as a fixed-width, lexicographically sortable string so SQLite TEXT
comparison IS the causal comparison:

    &lt;unix_ms:13 decimal digits>-&lt;counter:6 hex digits>-&lt;replica_id>

e.g. ``1783038849123-000000-rep_ab12cd34ef56``.

Semantics (standard HLC):
- ``tick()`` for a local op: physical time if it advanced, else previous
  ms with counter+1 — never goes backwards, even against clock regression.
- ``observe(remote)`` on receiving a remote op: the clock absorbs the
  remote's instant so later local ops sort after everything already seen.

Cursor semantics stay LOCAL (rowid arrival order) per RFC 004 A.4 — a tail
consumer must see late-arriving remote ops even though their HLC is older.
HLC is the *display/merge* order; arrival is the *delivery* order.

## Classes

### `class HybridLogicalClock`

Thread-safe HLC bound to one replica id.

``last`` may be seeded from persisted state (the newest hlc in the
op-log) so monotonicity survives process restarts.

#### `__init__`

```python
def __init__(self, replica_id: str, last: str = None, now_ms = None)
```

#### `tick`

```python
def tick(self) -> str
```

Stamp a new local op; strictly greater than everything seen.

#### `observe`

```python
def observe(self, remote_hlc: str) -> None
```

Absorb a remote op's instant so future ticks sort after it.

## Functions

### `parse`

```python
def parse(hlc: str)
```

Return (ms, counter, replica_id) or raise ValueError.

### `render`

```python
def render(ms: int, counter: int, replica_id: str) -> str
```
