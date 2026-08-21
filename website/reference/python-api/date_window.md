# `mempalace.date_window`

Source: [`mempalace/date_window.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/date_window.py)

Shared date-window parsing for read-path filters.

``list_drawers`` (#1128) and ``search`` (#463) accept the same optional
``since``/``before`` bounds and compare them against drawer ``filed_at``
metadata. The parsing and window semantics live here — a side-effect-free
module — so ``searcher`` can use them without importing ``mcp_server``
(whose import installs MCP stdio protection) and ``mcp_server`` keeps its
existing behavior by aliasing these functions.

Semantics (issue spec, #1128):
* ``since`` is inclusive, ``before`` is exclusive: ``[since, before)``.
* Comparison is wall-clock and timezone-naive. ``filed_at`` values are
  written as naive local ISO strings (``datetime.now().isoformat()``)
  almost everywhere; the one aware writer (``diary_ingest``, UTC) is
  compared on its wall-clock fields after the offset is dropped.
* A drawer whose ``filed_at`` is missing or unparseable is EXCLUDED
  whenever a bound is active — a date-filtered result must never
  silently include rows of unknown age.

## Functions

### `parse_date_bound`

```python
def parse_date_bound(value: Optional[str] = None, field_name: str = 'date') -> Optional[datetime]
```

Parse an optional ISO-8601 date/datetime filter bound.

Accepts a date (``"2026-04-01"``), a naive timestamp
(``"2026-04-01T09:30:00"``), or one carrying a ``Z``/``+HH:MM`` offset.
Returns a naive ``datetime`` for wall-clock
comparison against drawer ``filed_at`` values, which are stored as naive
local ISO strings (``datetime.now().isoformat()``). Any timezone offset on
the input is dropped so an aware bound never raises a ``TypeError`` against
a naive ``filed_at``. Comparison is therefore wall-clock, which is what the
local-first single-machine model wants; an offset bound is matched on its
wall-clock fields, not its absolute instant, so a bound whose offset differs
from the zone ``filed_at`` was recorded in is matched by clock time.
The accepted grammar is a date, an ISO timestamp (optionally fractional),
and an optional ``Z``/``±HH:MM`` offset; other ISO 8601 forms (basic format,
week dates) are outside the contract and are rejected on the Python 3.9 floor
even where a newer ``fromisoformat`` would accept them.
Blank / whitespace-only means "no filter" (``None``).
Raises ``ValueError`` on an unparseable value so the caller can surface a
clear error, mirroring the wing/room sanitizers.

### `parse_window`

```python
def parse_window(since: Optional[str] = None, before: Optional[str] = None)
```

Parse a ``[since, before)`` pair, rejecting an inverted window.

Returns ``(since_dt, before_dt)`` — either side ``None`` when absent.
Raises ``ValueError`` (naming the offending field or the inversion) so
callers surface the same message everywhere a window is accepted.

### `filed_at_in_window`

```python
def filed_at_in_window(filed_at, since_dt: Optional[datetime], before_dt: Optional[datetime]) -> bool
```

True if a drawer's ``filed_at`` falls in ``[since, before)``.

``since`` is inclusive and ``before`` is exclusive, matching the issue spec.
Parsing (``Z``/offset normalization, tz drop) is delegated to
``parse_date_bound`` so a bound and a ``filed_at`` are compared
identically. A drawer whose ``filed_at`` is missing or unparseable cannot
be confirmed in-window, so it is EXCLUDED whenever a bound is active — a
date-filtered listing must never silently include rows of unknown age.
