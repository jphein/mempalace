# `mempalace.hook_shell`

Source: [`mempalace/hook_shell.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/hook_shell.py)

Compatibility helpers for legacy shell hooks.

The shell hooks intentionally stay small and portable, but parsing Claude
hook JSON and counting UTF-8 JSONL transcripts is safer in Python than in
inline shell snippets. This module centralizes that behavior for both
hooks/mempal_save_hook.sh and hooks/mempal_precompact_hook.sh.

## Functions

### `sanitize_session_id`

```python
def sanitize_session_id(session_id: object) -> str
```

Keep session ids safe for state-file names.

### `normalize_transcript_path`

```python
def normalize_transcript_path(path: object) -> str
```

Normalize a hook transcript path without destroying Windows paths.

Claude Code on Windows sends paths like:

    C:\Users\me\.claude\projects\&lt;project>\&lt;session>.jsonl

The old shell sanitizer removed both the drive-letter colon and
backslashes. That turned a valid transcript path into a nonexistent path.
For transcript paths, we only remove control characters that would break
newline-delimited shell parsing, and normalize backslashes to forward
slashes so Git Bash can still address the same Windows file.

### `parse_stop_payload`

```python
def parse_stop_payload(payload: dict) -> tuple[str, str, str]
```

### `parse_precompact_payload`

```python
def parse_precompact_payload(payload: dict) -> tuple[str, str]
```

### `count_human_messages`

```python
def count_human_messages(path: str) -> int
```

Count user messages in a Claude transcript JSONL file.

Claude transcripts are UTF-8. Windows Python defaults to cp1252 in many
environments, so the encoding must be explicit. Invalid bytes are ignored
to match the hooks' fail-soft behavior.

### `main`

```python
def main(argv: list[str] | None = None) -> int
```
