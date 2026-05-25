# `mempalace.write_sanitizer`

Source: [`mempalace/write_sanitizer.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/write_sanitizer.py)

write_sanitizer.py — Observation-grade input hygiene for the write path (#40).

Counterpart to ``query_sanitizer.py`` (read path). The fork is local-only and the
write surface is not adversarial — Claude Code sessions are the only producer —
so this layer is deliberately *observation-grade*:

  * Strip control characters (except newline/tab/carriage-return) and NUL bytes.
  * Collapse runs of 3+ blank lines down to 2 (cosmetic; preserves paragraph breaks).
  * Truncate at ``MAX_CONTENT_LENGTH`` (1 MiB) with an explicit flag, so a
    runaway writer never blocks a save but always leaves a trail.
  * Reject empty content — the only hard error, because storing an empty
    drawer is never useful and the existing ``sanitize_content`` already
    raises here.

Returns a result dict (``cleaned``, ``was_sanitized``, ``flags``) so callers
can record the sanitization signal in metadata or WAL without changing the
write contract. Callers that want strict validation should continue to call
``mempalace.config.sanitize_content`` afterwards.

Per issue #40 this is *flag-grade, not gate-grade* — adversarial content
doesn't reach the local palace unless the user types it, so we observe and
record rather than block.

## Functions

### `sanitize_write_content`

```python
def sanitize_write_content(value: str) -> dict
```

Observation-grade pass over drawer/diary content before write.

Returns:
    dict with:
        cleaned (str): the sanitized content (may equal input if no changes)
        was_sanitized (bool): True if any cleaning was applied
        flags (list[str]): which sanitizers fired, e.g.
            ["control_chars_stripped", "whitespace_normalized", "truncated"]
        original_length (int): length of the input
        cleaned_length (int): length of the output
        error (str|None): set if the input is unsalvageable (empty)

On unsalvageable input the function returns ``cleaned=""`` with ``error``
set; callers should treat that as a write rejection.

### `sanitize_write_name`

```python
def sanitize_write_name(value: str, field_name: str = 'name') -> dict
```

Observation-grade pass over wing/room/agent names.

Strips control characters and truncates at ``MAX_NAME_LENGTH``. The
strict shape check (``sanitize_name`` in config.py) still runs after
this and rejects path traversal, bad characters, etc. — this layer
only handles control-char hygiene and length capping.

Returns the same dict shape as ``sanitize_write_content``.
