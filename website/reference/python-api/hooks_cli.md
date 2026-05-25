# `mempalace.hooks_cli`

Source: [`mempalace/hooks_cli.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/hooks_cli.py)

Hook logic for MemPalace — Python implementation of session-start, stop, and precompact hooks.

Reads JSON from stdin, outputs JSON to stdout.
Supported hooks: session-start, stop, precompact
Supported harnesses: claude-code, codex (extensible to cursor, gemini, etc.)

## Functions

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
