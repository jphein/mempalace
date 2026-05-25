# `mempalace.normalize`

Source: [`mempalace/normalize.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/normalize.py)

normalize.py — Convert any chat export format to MemPalace transcript format.

Supported:
    - Plain text with > markers (pass through)
    - Claude.ai JSON export
    - ChatGPT conversations.json
    - Claude Code JSONL (with tool_use/tool_result block capture)
    - OpenAI Codex CLI JSONL
    - Gemini CLI JSONL (~/.gemini/tmp/&lt;project_hash>/chats/session-*.jsonl)
    - Slack JSON export
    - Plain text (pass through for paragraph chunking)

No API key. No internet. Everything local.

## Functions

### `strip_noise`

```python
def strip_noise(text: str, verbatim: bool = False) -> str
```

Remove system tags, hook output, and Claude Code UI chrome from text.

All patterns are line-anchored. User prose that happens to mention these
strings inline (e.g., documenting them) is preserved verbatim.

When ``verbatim=True``, returns ``text`` unchanged (system tags / hook
chrome / collapsed-output markers are real transcript content for
callers who want a full record).

### `normalize`

```python
def normalize(filepath: str, verbatim: bool = False) -> str
```

Load a file and normalize to transcript format if it's a chat export.
Plain text files pass through unchanged.

When ``verbatim=True``, the Claude Code JSONL parser preserves system
tags, hook output, and full tool input/output (no head/tail collapse,
no byte caps, no Read/Edit/Write omission). Other transcript formats
are already verbatim — the flag is a no-op for them.
