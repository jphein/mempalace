# `mempalace.hooks_cli`

Source: [`mempalace/hooks_cli.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/hooks_cli.py)

Hook logic for MemPalace — Python implementation of session-start, stop, and precompact hooks.

Reads JSON from stdin, outputs JSON to stdout.
Supported hooks: session-start, stop, precompact
Supported harnesses: claude-code, codex (extensible to cursor, gemini, etc.)

## Functions

### `derive_wing`

```python
def derive_wing(transcript_path: str, project_dir: Optional[str] = None, entity_hint: Optional[str] = None) -> str
```

Derive a wing from unambiguous signals, with entity as last-resort hint.

This is the formal derivation contract for #157. The priority order is:

    1. cwd            (from the JSONL transcript — canonical)
    2. transcript path (encoded .claude/projects folder / -Projects- segment)
    3. project directory hint (explicit ``project_dir`` passed by the caller)
    4. entity hint    (optional — only when 1-3 are all absent)
    5. unfiled        (``wing_sessions``)

Signals 1 and 2 are resolved together by :func:`_wing_from_transcript_path`
(cwd first, then the encoded path). Signal 3 is the directory the caller
knows it is operating in, used when the transcript carries no usable path.

The entity hint (4) is a *hint, never a gate*: it is consulted only when
every unambiguous signal above is absent. A confident entity match can
never override a cwd/transcript/project-dir signal. This is the demotion
required by #157 — the entity detector informs, it does not classify.

### `derive_room`

```python
def derive_room(content: str = '', room_hint: Optional[str] = None, entity_hint: Optional[str] = None) -> str
```

Derive a canonical room from unambiguous signals, entity as last resort.

Mirrors :func:`derive_wing`'s contract for the room axis (#157):

    1. explicit room hint (caller-supplied canonical room — unambiguous)
    2. keyword-derived room (content scored against canonical room rules)
    3. entity hint    (optional — only when 1-2 yield nothing)
    4. unfiled        (canonical default room)

As with the wing, the entity hint never gates: a keyword-derived room
always beats an entity guess. The room result is always one of the
canonical rooms (FK-safe), since both the keyword path and the default
come from :mod:`mempalace.convo_miner`'s canonical rule set.

### `hook_stop`

```python
def hook_stop(data: dict, harness: str)
```

Stop hook: block every N messages for auto-save.

### `hook_session_start`

```python
def hook_session_start(data: dict, harness: str)
```

Session start hook: initialize session tracking state.

Also runs a best-effort pending-queue replay and, when the daemon
looks unreachable or the queue has pending entries, emits a one-line
warning via ``systemMessage`` so the user notices within minutes
(rather than days, as happened in the 2026-05-17 power-event
incident). The warning is throttled to once per session via a marker
in ``STATE_DIR``.

### `hook_precompact`

```python
def hook_precompact(data: dict, harness: str)
```

Precompact hook: trigger transcript ingest + project mine, then allow compaction.

Respects the ``hooks.auto_save`` config toggle — when disabled, returns
immediately without mining.

Two write paths fire in sequence:
  1. ``_ingest_transcript(transcript_path)`` — local mode spawns a
     background ``mempalace mine`` Popen (best-effort, non-blocking);
     daemon-strict mode POSTs to ``/mine`` (the daemon serializes
     under its own ``_mine_sem``, replays from a queue if a rebuild
     is in progress).
  2. ``_mine_sync()`` — synchronous mempalace mine of MEMPAL_DIR
     (project files) when set. Blocks until exit (subprocess.run).

### `run_hook`

```python
def run_hook(hook_name: str, harness: str)
```

Main entry point: read stdin JSON, dispatch to hook handler.
