# `mempalace.room_taxonomy`

Source: [`mempalace/room_taxonomy.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/room_taxonomy.py)

Canonical room taxonomy — soft-warn validation.

Palace storage organizes drawers under ``wing`` (project) + ``room``
(topic). The 7 canonical rooms are the recommended taxonomy per
``docs/superpowers/specs/2026-05-13-palace-room-taxonomy.md``:

    architecture, decisions, problems, planning,
    sessions, references, discoveries

Historically, the postgres backend enforced this list via a foreign-key
constraint on ``mempalace_drawers.room`` referencing
``mempalace_canonical_rooms``. Per techempower-org/mempalace#86 that FK
has been relaxed: non-canonical room names are now ACCEPTED and a
warning is emitted in the write-path response so the caller (and
ultimately the user) is informed instead of silently failing.

This module provides:

- ``CANONICAL_ROOMS`` — the canonical 7-tuple as a Python constant.
- ``validate_room(room)`` — return a list of warning strings for the
  given room name. Empty list when the room is canonical.

The check is intentionally case-sensitive: the canonical names are
lowercase, and ``sanitize_name`` already lowercases room values before
storage.

## Functions

### `is_canonical_room`

```python
def is_canonical_room(room: str) -> bool
```

Return True iff ``room`` is one of the canonical 7.

### `suggest_canonical`

```python
def suggest_canonical(room: str, *, choices: Optional[Iterable[str]] = None, cutoff: float = 0.6) -> Optional[str]
```

Return the closest canonical match for ``room`` or ``None``.

Thin wrapper around ``difflib.get_close_matches`` so callers can
surface a "did you mean X?" hint when a non-canonical name lands.

``choices`` defaults to ``CANONICAL_ROOMS``; pass a different
iterable to widen the lookup (e.g. against a runtime list that
includes installation-specific custom rooms).

### `validate_room`

```python
def validate_room(room: str) -> List[str]
```

Return warning strings for ``room``; empty list when canonical.

Per #86 — non-canonical rooms are accepted, not rejected. The
warning shape is stable and machine-parseable: the canonical list
is rendered inline so the caller does not have to import this
module to render a useful message.
