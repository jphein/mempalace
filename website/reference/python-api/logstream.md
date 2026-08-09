# `mempalace.logstream`

Source: [`mempalace/logstream.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/logstream.py)

logstream.py — Agent coordination event log for MemPalace (RFC 003)
===================================================================

A small append-only event layer stored in the active palace directory as
``logstream.sqlite3``. Agents use it to delegate work, wait for replies,
and exchange exact patch/file artifacts through the shared MemPalace hub
without a human relaying messages between machines.

Design constraints (RFC 003):

- No Chroma dependency, no vector index open — plain SQLite only.
- Append-only: events are immutable; corrections are new events that
  reference prior events (see :meth:`Logstream.ack_event`).
- Exact payloads: event bodies and artifact content are stored verbatim.
- Safe under concurrent HTTP requests (WAL + per-instance lock, same
  pattern as ``knowledge_graph.py``).
- Explicit size limits with clear errors, never silent truncation.

Usage:
    from mempalace.logstream import Logstream

    ls = Logstream(db_path="/path/to/palace/logstream.sqlite3")
    evt = ls.append_event(
        type="task.request", stream="project/mempalace", room="delegation",
        from_agent="mac-codex", to_agent="windows-codex",
        correlation_id="task_123", body="Please fix search echo ranking.",
    )
    reply = ls.wait_events(correlation_id="task_123", type="patch.ready",
                           timeout_ms=300_000)

## Classes

### `class Logstream`

Durable append-only coordination log (events + artifacts).

Storage and threading mirror ``KnowledgeGraph``: one SQLite file in
WAL mode, a per-instance lock around writes, ``check_same_thread=False``
so the MCP HTTP server can call from worker threads.

#### `__init__`

```python
def __init__(self, db_path: str, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES, max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES, replica_id: str = None)
```

#### `close`

```python
def close(self)
```

#### `append_event`

```python
def append_event(self, type: str, stream: str, room: str, from_agent: str, to_agent: str = None, correlation_id: str = None, branch: str = None, base_commit: str = None, status: str = None, body: str = '', metadata: dict = None, artifact_ids: list = None) -> dict
```

Append one immutable event. Returns the stored event dict.

``artifact_ids`` must reference already-stored artifacts; unknown
ids are rejected so readers never see a dangling reference.

#### `put_artifact`

```python
def put_artifact(self, kind: str, content: str, created_by: str, metadata: dict = None) -> dict
```

Store exact artifact content (v1: UTF-8 text only).

Returns the artifact record without echoing ``content`` back —
callers already hold the content; readers use :meth:`get_artifact`.
For ``kind=patch``, a ``warnings`` list is included when the diff
looks unappliable (missing trailing newline, CRLF endings); the
content itself is still stored verbatim.

#### `ack_event`

```python
def ack_event(self, event_id: str, from_agent: str, status: str = None, body: str = '') -> dict
```

Append an ``event.ack`` referencing a prior event.

The target event is never mutated. The ack copies the target's
stream/room, copies its ``correlation_id`` (falling back to the
target's id so request/ack stay tied together), and routes back
to the target's ``from_agent``.

#### `submit_patch`

```python
def submit_patch(self, content: str, from_agent: str, stream: str, room: str = 'patches', to_agent: str = None, correlation_id: str = None, branch: str = None, base_commit: str = None, body: str = '', metadata: dict = None) -> dict
```

Convenience wrapper: store a patch artifact + ``patch.ready`` event.

Both writes go through the validated single-writer paths; the event
insert re-checks the artifact exists, so readers never observe a
``patch.ready`` event with a dangling artifact id.

#### `get_artifact`

```python
def get_artifact(self, artifact_id: str) -> Optional[dict]
```

Fetch one artifact by id, exact content included. None if missing.

#### `list_events`

```python
def list_events(self, stream: str = None, room: str = None, type: str = None, to_agent: str = None, from_agent: str = None, correlation_id: str = None, status: str = None, since_event_id: str = None, since_created_at: str = None, limit: int = DEFAULT_LIST_LIMIT) -> list[dict]
```

List events matching structured filters, oldest first.

Cursor semantics:

- ``since_event_id`` is the precise cursor: strictly after that
  event in append order (rowid), regardless of timestamp ties.
- ``since_created_at`` is inclusive (``>=``) so second-granularity
  timestamps never skip events; callers dedup by ``id``.
- ``to_agent`` also matches broadcast events (``to_agent='*'``).

#### `latest_event_id`

```python
def latest_event_id(self) -> Optional[str]
```

Id of the newest event, or None on an empty log.

Live-tail consumers (the SSE stream) capture this at connect time
as their starting cursor so they receive only post-connect events.

#### `wait_events`

```python
def wait_events(self, timeout_ms: int = 60000, poll_interval_s: float = None, **filters) -> dict
```

Block until at least one matching event exists or timeout expires.

v1 implementation per RFC 003: a polling loop inside the request,
sleeping 250-1000 ms with jitter. Timeouts are clamped to
``MAX_WAIT_TIMEOUT_MS`` and return ``&#123;"timed_out": True,
"events": []}`` rather than raising.

``filters`` accepts the same keyword filters as :meth:`list_events`.
``poll_interval_s`` pins the sleep (tests); default is jittered.

#### `version_vector`

```python
def version_vector(self) -> dict
```

&#123;origin_replica: highest origin_seq applied locally}.

The complete description of this replica's knowledge — peers diff
their vectors to compute exactly which op ranges are missing.

#### `list_ops`

```python
def list_ops(self, origin: str, after_seq: int = 0, limit: int = 500) -> list[dict]
```

Events authored by ``origin`` with origin_seq > after_seq, in
author order. The anti-entropy pull unit.

#### `apply_remote_event`

```python
def apply_remote_event(self, event: dict) -> bool
```

Fold one remote op into the local log, idempotently.

Verbatim rule: the event is stored exactly as authored (id,
created_at, hlc, origin stamps untouched); only the local rowid —
the arrival cursor — is ours. Referenced artifacts must already be
applied (sync pulls artifacts first) so readers never see a dangling
id, same invariant as append_event. Returns True if inserted, False
if we already had it. Raises ValueError on malformed input or a
missing artifact.

#### `has_artifact`

```python
def has_artifact(self, artifact_id: str) -> bool
```

Cheap existence probe (no content transfer) for the sync engine.

#### `apply_remote_artifact`

```python
def apply_remote_artifact(self, artifact: dict) -> bool
```

Fold one remote artifact in, idempotently, verifying its hash.

Content is verbatim; the sha256 must match or the artifact is
rejected — a corrupt transfer must never enter the store.
