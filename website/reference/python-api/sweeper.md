# `mempalace.sweeper`

Source: [`mempalace/sweeper.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sweeper.py)

sweeper.py — Message-granular miner that catches what the file-level
primary miners dropped.

Algorithm, per session:

    cursor = max(timestamp of sweeper-written drawers for this session_id)
    For each user/assistant message in the jsonl:
        if cursor is not None and message.timestamp < cursor: skip
        else: upsert a drawer keyed by (session_id, message_uuid)

Properties:

  - Idempotent on its own writes: rerunning is a no-op because drawer
    IDs are deterministic and existence is pre-checked before counting.
  - Resume-safe: a crash mid-sweep is recovered on the next run — the
    cursor advances to the last ingested timestamp and re-attempts at
    that boundary are de-duped by the deterministic ID.
  - Tie-break safe: uses ``< cursor`` (not ``<=``), so if multiple
    messages share the max timestamp and only some were ingested, the
    rest are still picked up on re-run.
  - No size caps: each drawer holds one exchange, ~1-5 KB.

Coordination with the primary file-level miners (``miner.py`` /
``convo_miner.py``) is limited: those miners chunk at a fixed char size
and do not currently stamp ``session_id``/``timestamp`` metadata that
the sweeper can key off. In practice the sweeper coordinates with its
own prior runs, and may ingest content that also got chunked into
primary-miner drawers (under different IDs). Follow-up: add uniform
``ingest_mode`` + message metadata to the primary miners so dedup spans
both paths.

Usage:
    from mempalace.sweeper import sweep
    result = sweep("/path/to/session.jsonl", "/path/to/palace")

## Functions

### `parse_claude_jsonl`

```python
def parse_claude_jsonl(path: str) -> Iterator[dict]
```

Yield user/assistant records from a Claude Code .jsonl file.

Each yield is:
    &#123;
      "session_id": str,
      "uuid":       str,   # per-message UUID
      "timestamp":  str,   # ISO 8601
      "role":       "user" | "assistant",
      "content":    str,   # flattened text
    }

Non-message records (progress, file-history-snapshot, system,
queue-operation, last-prompt) are filtered out. Malformed lines are
skipped silently — data quality is the transcript writer's problem,
not ours.

### `get_palace_cursor`

```python
def get_palace_cursor(collection, session_id: str) -> Optional[str]
```

Return the max timestamp of drawers for this session_id, or None.

ISO-8601 strings compare lexically in the right order, so we don't
need to parse them. Query scans metadatas for the session via the
backend's where-filter, then reduces.

Backend errors are logged at WARNING and surface as a `None` cursor —
which makes the caller treat the session as empty and ingest every
message. That's intentional: a no-cursor sweep is recovered from on
the next run by deterministic drawer IDs, so a degraded cursor never
causes silent data loss.

### `sweep`

```python
def sweep(jsonl_path: str, palace_path: str, source_label: Optional[str] = None) -> dict
```

Ingest every user/assistant message not already represented.

For each message in the jsonl:
  - If timestamp < cursor for that session, skip (strictly earlier
    than anything already in the palace — already covered).
  - At timestamp == cursor we do NOT skip, because multiple messages
    can share the same ISO-8601 timestamp; if only some of them were
    ingested before a crash, a `<= cursor` skip would lose the rest
    forever. Deterministic drawer IDs make re-attempting at the
    cursor boundary safe (existing rows are found via a pre-flight
    `get(ids=...)` and counted as "already present", not "added").
  - Else, upsert a drawer with deterministic ID so reruns dedupe.

Returns ``&#123;drawers_added, drawers_already_present, drawers_skipped,
drawers_upserted, cursor_by_session}``:

* ``drawers_added`` — rows that did not exist before this sweep.
* ``drawers_already_present`` — rows whose deterministic ID was
  already in the palace and got rewritten idempotently.
* ``drawers_skipped`` — records skipped by the cursor (strictly
  earlier than what's already stored).
* ``drawers_upserted`` — total writes = added + already_present.

### `sweep_directory`

```python
def sweep_directory(dir_path: str, palace_path: str) -> dict
```

Sweep every .jsonl file in a directory (recursive).

Returns aggregated summary across all files. ``files_attempted``
includes files that raised, so the count reflects discovery rather
than only successes; ``files_succeeded`` is the subset that
completed without error.
