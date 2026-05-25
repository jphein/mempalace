# `mempalace.auto_query.signals`

Source: [`mempalace/auto_query/signals.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_query/signals.py)

Signal extraction for auto-query context classifier.

Pure functions — no I/O, no MCP calls. All external state
(wings, entities, drawer existence) is passed in by the caller.

## Functions

### `extract_signals`

```python
def extract_signals(text, session_state, project_wing, known_wings, known_entities = None, has_recent_drawers = False)
```

Extract auto-query signals from a user message.

Pure function — no I/O, no MCP calls. All external state
(wings, entities, drawer existence) is passed in by the caller.
