"""Filesystem source adapter (RFC 002 §9).

Thin wrapper around ``mempalace.miner`` — the existing filesystem mining
pipeline. Delegates scanning, chunking, room detection, and metadata
construction to miner internals so adapter users get the same behavior as
``mempalace mine`` without coupling to the miner's function signatures.

This adapter does NOT replace ``miner.mine()``; it provides an alternative
entry point via the adapter plugin contract. Both paths coexist.
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


class FilesystemSourceAdapter(BaseSourceAdapter):
    """Ingest project files from a local directory tree.

    Wraps ``miner.scan_project()``, ``miner.chunk_text()``, and
    ``miner.detect_room()`` so the full filesystem mining pipeline is
    available via the adapter contract.
    """

    name = "filesystem"
    adapter_version = "1.0.0"
    capabilities = frozenset({"supports_incremental"})
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset({"chunk_text"})

    def ingest(
        self,
        *,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[DrawerRecord]:
        from ..config import MempalaceConfig
        from ..miner import (
            _build_drawer_metadata,
            _extract_content_date,
            chunk_text,
            detect_room,
            load_config,
            scan_project,
        )

        project_dir = source.local_path
        if not project_dir:
            raise SourceNotFoundError("FilesystemSourceAdapter requires source.local_path")

        project_path = Path(project_dir).expanduser().resolve()
        if not project_path.is_dir():
            raise SourceNotFoundError(f"Not a directory: {project_path}")

        config = load_config(str(project_path))
        palace_config = MempalaceConfig()

        wing = source.options.get("wing") or config.get("wing", project_path.name)
        rooms = config.get("rooms", [{"name": "general", "description": "All project files"}])
        agent = source.options.get("agent", "mempalace")
        respect_gitignore = source.options.get("respect_gitignore", True)
        include_ignored = source.options.get("include_ignored")

        files = scan_project(
            str(project_path),
            respect_gitignore=respect_gitignore,
            include_ignored=include_ignored,
        )

        limit = source.options.get("limit", 0)
        if limit > 0:
            files = files[:limit]

        cfg_chunk_size = palace_config.chunk_size
        cfg_chunk_overlap = palace_config.chunk_overlap
        cfg_min_chunk_size = palace_config.min_chunk_size

        for filepath in files:
            source_file = str(filepath)
            rel_path = filepath.relative_to(project_path).as_posix()

            try:
                stat = filepath.stat()
                version = f"mtime:{stat.st_mtime:.0f}:size:{stat.st_size}"
            except OSError:
                continue

            yield SourceItemMetadata(
                source_file=source_file,
                version=version,
            )

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                logger.debug("Skipping unreadable file: %s", rel_path)
                continue

            if not content.strip():
                continue

            room = detect_room(filepath, content, rooms, project_path)
            room_resolver = palace_config.resolve_room if palace_config.room_aliases else None
            if room_resolver:
                room = room_resolver(room)

            chunks = chunk_text(
                content,
                source_file,
                chunk_size=cfg_chunk_size,
                chunk_overlap=cfg_chunk_overlap,
                min_chunk_size=cfg_min_chunk_size,
            )

            for chunk in chunks:
                yield DrawerRecord(
                    content=chunk["content"],
                    source_file=source_file,
                    chunk_index=chunk["chunk_index"],
                    metadata={
                        "line_start": chunk.get("line_start"),
                        "line_end": chunk.get("line_end"),
                    },
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
                    description="Absolute path to the source file",
                    indexed=True,
                ),
                "line_start": FieldSpec(
                    type="int",
                    required=False,
                    description="1-indexed start line in source (Tier 6a)",
                ),
                "line_end": FieldSpec(
                    type="int",
                    required=False,
                    description="1-indexed end line in source (Tier 6a)",
                ),
            },
            version=self.adapter_version,
        )

    def source_summary(self, *, source: SourceRef) -> SourceSummary:
        from ..miner import scan_project

        if not source.local_path:
            return SourceSummary(description="filesystem (no path)")
        try:
            files = scan_project(source.local_path)
            return SourceSummary(
                description=f"filesystem: {source.local_path}",
                item_count=len(files),
            )
        except Exception:
            return SourceSummary(description=f"filesystem: {source.local_path}")
