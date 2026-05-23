"""Conversation source adapter (RFC 002 §9).

Thin wrapper around ``mempalace.convo_miner`` — the existing conversation
mining pipeline. Delegates session scanning, exchange chunking, and room
detection to convo_miner internals so adapter users get the same behavior
as ``mempalace mine --mode convos`` without coupling to function signatures.

This adapter does NOT replace ``convo_miner.mine_convos()``; it provides
an alternative entry point via the adapter plugin contract.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator, Optional

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


class ConversationSourceAdapter(BaseSourceAdapter):
    """Ingest AI conversation transcripts from a local directory.

    Wraps ``convo_miner.scan_convos()``, ``convo_miner.chunk_exchanges()``,
    and ``convo_miner.detect_convo_room()`` so the full conversation mining
    pipeline is available via the adapter contract.
    """

    name = "conversations"
    adapter_version = "1.0.0"
    capabilities = frozenset({"supports_incremental"})
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset({"chunk_exchanges"})

    def ingest(
        self,
        *,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[DrawerRecord]:
        from ..convo_miner import chunk_exchanges, detect_convo_room, scan_convos

        convo_dir = source.local_path
        if not convo_dir:
            raise SourceNotFoundError("ConversationSourceAdapter requires source.local_path")

        convo_path = Path(convo_dir).expanduser().resolve()
        if not convo_path.is_dir():
            raise SourceNotFoundError(f"Not a directory: {convo_path}")

        wing = source.options.get("wing", "conversations")
        agent = source.options.get("agent", "mempalace")

        session_files = scan_convos(str(convo_path))

        limit = source.options.get("limit", 0)
        if limit > 0:
            session_files = session_files[:limit]

        for session_file in session_files:
            source_file = str(session_file)

            try:
                stat = Path(session_file).stat()
                version = f"mtime:{stat.st_mtime:.0f}:size:{stat.st_size}"
            except OSError:
                continue

            yield SourceItemMetadata(
                source_file=source_file,
                version=version,
            )

            try:
                content = Path(session_file).read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                logger.debug("Skipping unreadable session: %s", session_file)
                continue

            if not content.strip():
                continue

            room = detect_convo_room(content)
            chunks = chunk_exchanges(content)

            for i, chunk in enumerate(chunks):
                chunk_content = chunk if isinstance(chunk, str) else chunk.get("content", str(chunk))
                yield DrawerRecord(
                    content=chunk_content,
                    source_file=source_file,
                    chunk_index=i,
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
        try:
            current_mtime = os.path.getmtime(item.source_file)
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
                    description="Path to the conversation session file",
                    indexed=True,
                ),
                "extract_mode": FieldSpec(
                    type="string",
                    required=False,
                    description="Extraction mode used for this session",
                ),
            },
            version=self.adapter_version,
        )

    def source_summary(self, *, source: SourceRef) -> SourceSummary:
        from ..convo_miner import scan_convos

        if not source.local_path:
            return SourceSummary(description="conversations (no path)")
        try:
            sessions = scan_convos(source.local_path)
            return SourceSummary(
                description=f"conversations: {source.local_path}",
                item_count=len(sessions),
            )
        except Exception:
            return SourceSummary(description=f"conversations: {source.local_path}")
