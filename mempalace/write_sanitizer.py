"""
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
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("mempalace_mcp")

# 1 MiB. Above this we truncate and flag rather than reject; the existing
# config-layer ``sanitize_content`` enforces the strict 100K limit on the
# MCP boundary, but the daemon and direct callers can pass larger blobs
# and we want a defined upper bound for those paths too.
MAX_CONTENT_LENGTH = 1024 * 1024

# Wing/room names: 256 chars is generous. The strict ``sanitize_name`` in
# config.py uses 128 chars; this is the looser observation-layer cap that
# only fires when something has gone deeply wrong.
MAX_NAME_LENGTH = 256

# Control chars to strip: everything in C0 except \t (0x09), \n (0x0A),
# \r (0x0D), and DEL (0x7F). NUL bytes are included so callers that
# bypass the strict layer still get cleaned input.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

# 3+ consecutive blank lines collapse to 2 (one blank line between paragraphs).
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def sanitize_write_content(value: str) -> dict:
    """Observation-grade pass over drawer/diary content before write.

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
    """
    if not isinstance(value, str):
        return {
            "cleaned": "",
            "was_sanitized": False,
            "flags": [],
            "original_length": 0,
            "cleaned_length": 0,
            "error": "content must be a string",
        }

    original_length = len(value)
    flags: list[str] = []
    cleaned = value

    stripped = _CONTROL_CHARS.sub("", cleaned)
    if stripped != cleaned:
        flags.append("control_chars_stripped")
        cleaned = stripped

    normalized = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    if normalized != cleaned:
        flags.append("whitespace_normalized")
        cleaned = normalized

    if len(cleaned) > MAX_CONTENT_LENGTH:
        cleaned = cleaned[:MAX_CONTENT_LENGTH]
        flags.append("truncated")
        logger.warning(
            "write_sanitizer: content truncated from %d to %d chars",
            original_length,
            MAX_CONTENT_LENGTH,
        )

    if not cleaned.strip():
        return {
            "cleaned": "",
            "was_sanitized": True,
            "flags": flags,
            "original_length": original_length,
            "cleaned_length": 0,
            "error": "content is empty after sanitization",
        }

    return {
        "cleaned": cleaned,
        "was_sanitized": bool(flags),
        "flags": flags,
        "original_length": original_length,
        "cleaned_length": len(cleaned),
        "error": None,
    }


def sanitize_write_name(value: str, field_name: str = "name") -> dict:
    """Observation-grade pass over wing/room/agent names.

    Strips control characters and truncates at ``MAX_NAME_LENGTH``. The
    strict shape check (``sanitize_name`` in config.py) still runs after
    this and rejects path traversal, bad characters, etc. — this layer
    only handles control-char hygiene and length capping.

    Returns the same dict shape as ``sanitize_write_content``.
    """
    if not isinstance(value, str):
        return {
            "cleaned": "",
            "was_sanitized": False,
            "flags": [],
            "original_length": 0,
            "cleaned_length": 0,
            "error": f"{field_name} must be a string",
        }

    original_length = len(value)
    flags: list[str] = []
    cleaned = value

    stripped = _CONTROL_CHARS.sub("", cleaned)
    if stripped != cleaned:
        flags.append("control_chars_stripped")
        cleaned = stripped

    if len(cleaned) > MAX_NAME_LENGTH:
        cleaned = cleaned[:MAX_NAME_LENGTH]
        flags.append("truncated")
        logger.warning(
            "write_sanitizer: %s truncated from %d to %d chars",
            field_name,
            original_length,
            MAX_NAME_LENGTH,
        )

    if not cleaned.strip():
        return {
            "cleaned": "",
            "was_sanitized": True,
            "flags": flags,
            "original_length": original_length,
            "cleaned_length": 0,
            "error": f"{field_name} is empty after sanitization",
        }

    return {
        "cleaned": cleaned,
        "was_sanitized": bool(flags),
        "flags": flags,
        "original_length": original_length,
        "cleaned_length": len(cleaned),
        "error": None,
    }
