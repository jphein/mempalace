"""Aider source adapter (RFC 002).

Ingests Aider chat history files (``.aider.chat.history.md``). Format:
- ``# aider chat started at YYYY-MM-DD HH:MM:SS`` — session headers
- ``#### <text>`` — user turns (H4 headers)
- ``> <text>`` — system/aider output (blockquotes)
- Plain text — assistant responses

Each session (delimited by ``# aider chat started at``) becomes one
source_file. Real test data at
``~/Projects/openwrt/openwrt-backups/.aider.chat.history.md``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ..convo_miner import chunk_exchanges, detect_convo_room
from .base import (
    AdapterSchema,
    BaseSourceAdapter,
    DrawerRecord,
    FieldSpec,
    RouteHint,
    SourceItemMetadata,
    SourceNotFoundError,
    SourceRef,
    SourceSummary,
)
from .context import PalaceContext

logger = logging.getLogger(__name__)

_SESSION_HEADER_RE = re.compile(r"^# aider chat started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_USER_TURN_RE = re.compile(r"^#### (.+)")
_SYSTEM_LINE_RE = re.compile(r"^> (.+)")

_DEFAULT_FILENAMES = (".aider.chat.history.md",)


def _find_history_files(base_dir: str) -> List[Path]:
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []
    results = []
    for name in _DEFAULT_FILENAMES:
        results.extend(sorted(base.rglob(name)))
    return results


def _split_sessions(content: str) -> List[Tuple[str, str]]:
    """Split a history file into (timestamp, session_text) pairs."""
    sessions = []
    current_ts = None
    current_lines = []

    for line in content.split("\n"):
        m = _SESSION_HEADER_RE.match(line)
        if m:
            if current_ts and current_lines:
                sessions.append((current_ts, "\n".join(current_lines)))
            current_ts = m.group(1)
            current_lines = []
        else:
            current_lines.append(line)

    if current_ts and current_lines:
        sessions.append((current_ts, "\n".join(current_lines)))

    return sessions


def _parse_session(text: str) -> List[Tuple[str, str]]:
    """Parse an aider session into (role, text) message pairs."""
    messages = []
    current_role = None
    current_lines = []

    for line in text.split("\n"):
        user_m = _USER_TURN_RE.match(line)
        if user_m:
            if current_role and current_lines:
                messages.append((current_role, "\n".join(current_lines).strip()))
            current_role = "user"
            current_lines = [user_m.group(1)]
            continue

        if _SYSTEM_LINE_RE.match(line):
            continue

        if line.strip() and current_role == "user" and not user_m:
            if current_lines:
                messages.append((current_role, "\n".join(current_lines).strip()))
            current_role = "assistant"
            current_lines = [line]
            continue

        if current_role:
            current_lines.append(line)

    if current_role and current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            messages.append((current_role, content))

    return messages


def _messages_to_transcript(messages: List[Tuple[str, str]]) -> str:
    parts = []
    for role, text in messages:
        label = "Human" if role == "user" else "Assistant"
        parts.append(f"{label}: {text}")
    return "\n\n".join(parts)


class AiderSourceAdapter(BaseSourceAdapter):
    """Ingest Aider chat history files."""

    name = "aider"
    adapter_version = "1.0.0"
    capabilities = frozenset({"supports_incremental"})
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset({"chunk_exchanges", "aider_markdown_parse"})

    def ingest(
        self,
        *,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[DrawerRecord]:
        local_path = source.local_path
        if not local_path:
            raise SourceNotFoundError(
                "AiderSourceAdapter requires source.local_path pointing to a "
                "directory containing .aider.chat.history.md files."
            )

        base_path = Path(local_path).expanduser().resolve()

        if base_path.is_file() and base_path.name in _DEFAULT_FILENAMES:
            history_files = [base_path]
        elif base_path.is_dir():
            history_files = _find_history_files(str(base_path))
        else:
            raise SourceNotFoundError(f"Not a file or directory: {base_path}")

        wing = source.options.get("wing", "aider")
        limit = source.options.get("limit", 0)

        sessions_yielded = 0
        for filepath in history_files:
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            sessions = _split_sessions(content)

            for ts, session_text in sessions:
                if limit > 0 and sessions_yielded >= limit:
                    return

                source_file = f"aider://{filepath}#session={ts.replace(' ', 'T')}"

                yield SourceItemMetadata(
                    source_file=source_file,
                    version=f"mtime:{os.path.getmtime(str(filepath)):.0f}:ts:{ts}",
                )

                messages = _parse_session(session_text)
                if len(messages) < 2:
                    continue

                transcript = _messages_to_transcript(messages)
                room = detect_convo_room(transcript)
                chunks = chunk_exchanges(transcript)

                for i, chunk in enumerate(chunks):
                    chunk_content = (
                        chunk if isinstance(chunk, str) else chunk.get("content", str(chunk))
                    )
                    yield DrawerRecord(
                        content=chunk_content,
                        source_file=source_file,
                        chunk_index=i,
                        metadata={
                            "session_file": str(filepath),
                            "session_timestamp": ts,
                        },
                        route_hint=RouteHint(wing=wing, room=room),
                    )

                sessions_yielded += 1

    def is_current(
        self,
        *,
        item: SourceItemMetadata,
        existing_metadata: Optional[dict],
    ) -> bool:
        if existing_metadata is None:
            return False
        session_file = existing_metadata.get("session_file")
        if not session_file:
            return False
        try:
            current_mtime = os.path.getmtime(session_file)
        except OSError:
            return False
        existing_mtime = existing_metadata.get("source_mtime")
        if existing_mtime is None:
            return False
        return abs(current_mtime - existing_mtime) < 0.01

    def describe_schema(self) -> AdapterSchema:
        return AdapterSchema(
            fields={
                "source_file": FieldSpec(
                    type="string",
                    required=True,
                    description="aider:// URI with session timestamp",
                    indexed=True,
                ),
                "session_file": FieldSpec(
                    type="string",
                    required=True,
                    description="Absolute path to the .aider.chat.history.md file",
                ),
                "session_timestamp": FieldSpec(
                    type="string",
                    required=True,
                    description="Session start timestamp (YYYY-MM-DD HH:MM:SS)",
                ),
            },
            version=self.adapter_version,
        )

    def source_summary(self, *, source: SourceRef) -> SourceSummary:
        if not source.local_path:
            return SourceSummary(description="aider (no path)")
        try:
            files = _find_history_files(source.local_path)
            total_sessions = 0
            for f in files:
                content = f.read_text(encoding="utf-8", errors="replace")
                total_sessions += len(_split_sessions(content))
            return SourceSummary(
                description=f"aider: {source.local_path}",
                item_count=total_sessions,
            )
        except Exception:
            return SourceSummary(description=f"aider: {source.local_path}")
