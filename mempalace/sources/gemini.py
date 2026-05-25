"""Gemini CLI source adapter (RFC 002).

Ingests Gemini CLI session transcripts from the JSONL format at
``~/.gemini/tmp/<project_hash>/chats/session-*.jsonl``. Extracts
``user``/``gemini`` entries after the ``session_metadata`` sentinel.
Parsing logic extracted from ``normalize._try_gemini_jsonl()``.
"""

from __future__ import annotations

import json
import logging
import os
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

_DEFAULT_PATHS: Tuple[str, ...] = ("~/.gemini/tmp",)


def _find_session_files(base_dir: str) -> List[Path]:
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []
    return sorted(base.rglob("session-*.jsonl"))


def _parse_gemini_jsonl(content: str) -> Optional[List[Tuple[str, str]]]:
    """Parse Gemini JSONL into (role, text) pairs. Returns None if not Gemini format."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    messages = []
    has_session_metadata = False

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        entry_type = entry.get("type", "")
        if entry_type == "session_metadata":
            has_session_metadata = True
            continue

        if not has_session_metadata:
            continue

        if entry_type not in ("user", "gemini"):
            continue

        content_blocks = entry.get("content", [])
        if not isinstance(content_blocks, list):
            continue

        parts = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if not parts:
            continue
        joined = "\n".join(parts)

        if entry_type == "user":
            messages.append(("user", joined))
        else:
            messages.append(("assistant", joined))

    if len(messages) >= 2 and has_session_metadata:
        return messages
    return None


def _messages_to_transcript(messages: List[Tuple[str, str]]) -> str:
    parts = []
    for role, text in messages:
        label = "Human" if role == "user" else "Assistant"
        parts.append(f"{label}: {text}")
    return "\n\n".join(parts)


class GeminiSourceAdapter(BaseSourceAdapter):
    """Ingest Gemini CLI conversation sessions."""

    name = "gemini"
    adapter_version = "1.0.0"
    capabilities = frozenset({"supports_incremental"})
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset({"chunk_exchanges", "gemini_jsonl_parse"})

    def ingest(
        self,
        *,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[DrawerRecord]:
        base_dir = source.local_path
        if not base_dir:
            for default in _DEFAULT_PATHS:
                expanded = str(Path(default).expanduser())
                if Path(expanded).is_dir():
                    base_dir = expanded
                    break
        if not base_dir:
            raise SourceNotFoundError(
                "GeminiSourceAdapter: no Gemini sessions dir found. "
                "Provide source.local_path or install Gemini CLI."
            )

        wing = source.options.get("wing", "gemini")

        session_files = _find_session_files(base_dir)
        limit = source.options.get("limit", 0)
        if limit > 0:
            session_files = session_files[:limit]

        for filepath in session_files:
            source_file = f"gemini://{filepath}"
            try:
                stat = filepath.stat()
                version = f"mtime:{stat.st_mtime:.0f}:size:{stat.st_size}"
            except OSError:
                continue

            yield SourceItemMetadata(source_file=source_file, version=version)

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            messages = _parse_gemini_jsonl(content)
            if not messages:
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
                    metadata={"session_file": str(filepath)},
                    route_hint=RouteHint(wing=wing, room=room),
                )

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
                    description="gemini:// URI for the session file",
                    indexed=True,
                ),
                "session_file": FieldSpec(
                    type="string",
                    required=True,
                    description="Absolute path to the JSONL session file",
                ),
            },
            version=self.adapter_version,
        )

    def source_summary(self, *, source: SourceRef) -> SourceSummary:
        base = source.local_path
        if not base:
            for default in _DEFAULT_PATHS:
                expanded = str(Path(default).expanduser())
                if Path(expanded).is_dir():
                    base = expanded
                    break
        if not base:
            return SourceSummary(description="gemini (not installed)")
        files = _find_session_files(base)
        return SourceSummary(
            description=f"gemini: {base}",
            item_count=len(files),
        )
