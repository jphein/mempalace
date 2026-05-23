"""Warp terminal source adapter.

Ingests Warp terminal command history and AI queries from Warp's local
SQLite database (default ``~/.local/state/warp-terminal/warp.sqlite``)
into the palace as :class:`DrawerRecord` instances.

Warp stores two classes of valuable data:

1. **Commands** — shell command history grouped by ``session_id``. Each
   session becomes one ``source_file`` of the shape
   ``warp://<absolute-db-path>#session=<session_id>``. Commands within a
   session are rendered chronologically as a terminal transcript and
   chunked for palace storage.

2. **AI queries** — Warp AI conversations keyed by ``conversation_id``.
   Each conversation becomes one ``source_file`` of the shape
   ``warp://<absolute-db-path>#ai=<conversation_id>``. Query/response
   pairs are rendered as exchange-pair markdown.

Both sources support incremental ingest via file mtime versioning.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ..config import normalize_wing_name
from ..convo_miner import chunk_exchanges, detect_convo_room
from .base import (
    AdapterClosedError,
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

_DEFAULT_DB_PATHS: Tuple[str, ...] = (
    "~/.local/state/warp-terminal/warp.sqlite",
)


def _detect_hall(content: str) -> str:
    from ..convo_miner import _detect_hall_cached

    return _detect_hall_cached(content)


def _resolve_db(local_path: Optional[str] = None) -> str:
    """Resolve a concrete SQLite path for the Warp database.

    Order:
        1. ``local_path`` if it points at an existing file.
        2. Each entry of :data:`_DEFAULT_DB_PATHS` in declaration order.

    Raises :class:`SourceNotFoundError` if no candidate resolves.
    """
    candidates: List[str] = []
    if local_path:
        candidates.append(local_path)
    candidates.extend(_DEFAULT_DB_PATHS)
    for raw in candidates:
        p = Path(raw).expanduser()
        if p.is_file():
            return str(p.resolve())
    raise SourceNotFoundError(
        f"No Warp SQLite database found (searched {candidates}). "
        f"Pass SourceRef(local_path=<path>) or install Warp terminal."
    )


def _db_mtime_version(db_path: str) -> str:
    """Return file mtime as a string version for incremental support."""
    try:
        return str(int(os.path.getmtime(db_path)))
    except OSError:
        return "0"


def _format_command_transcript(commands: List[dict]) -> str:
    """Render a list of command dicts as a terminal session transcript.

    Format matches exchange-pair markdown for downstream chunking:
    each command is rendered with its context (pwd, exit code, timestamps).
    """
    lines: List[str] = []
    for cmd in commands:
        command_text = cmd["command"]
        pwd = cmd.get("pwd") or ""
        exit_code = cmd.get("exit_code")
        host = cmd.get("hostname") or ""
        git_branch = cmd.get("git_branch") or ""
        start_ts = cmd.get("start_ts") or ""

        # Build a prompt-like prefix
        prompt_parts = []
        if host:
            prompt_parts.append(host)
        if pwd:
            prompt_parts.append(pwd)
        if git_branch:
            prompt_parts.append(f"({git_branch})")
        prompt = ":".join(prompt_parts) if prompt_parts else "$"

        lines.append(f"> [{prompt}] {command_text}")
        exit_info = []
        if exit_code is not None and exit_code != 0:
            exit_info.append(f"exit={exit_code}")
        if start_ts:
            exit_info.append(f"at {start_ts}")
        if exit_info:
            lines.append(" ".join(exit_info))
        lines.append("")

    return "\n".join(lines).strip()


def _extract_ai_query_text(input_json: str) -> Optional[str]:
    """Extract the user query text from the ai_queries input JSON.

    Warp stores ai_queries.input as a JSON array of context items;
    the actual query is in ``{"Query": {"text": "..."}}``.
    """
    try:
        items = json.loads(input_json)
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and "Query" in item:
                query_obj = item["Query"]
                if isinstance(query_obj, dict) and "text" in query_obj:
                    return query_obj["text"]
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return None


def session_source_file(db_path: str, session_id: str) -> str:
    """Construct the stable per-session ``source_file`` identifier."""
    return f"warp://{db_path}#session={session_id}"


def ai_source_file(db_path: str, conversation_id: str) -> str:
    """Construct the stable per-AI-conversation ``source_file`` identifier."""
    return f"warp://{db_path}#ai={conversation_id}"


class WarpSourceAdapter(BaseSourceAdapter):
    """Mine Warp terminal command history and AI queries into the palace."""

    name = "warp"
    adapter_version = "0.1.0"
    capabilities = frozenset(
        {
            "supports_incremental",
            "supports_structured_metadata",
            "requires_local_tool",
            "adapter_owns_routing",
        }
    )
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset(
        {
            "warp_command_transcript",
            "warp_ai_exchange",
            "newline_normalize",
            "whitespace_trim",
        }
    )
    default_privacy_class = "pii_potential"

    DECLARED_TRANSFORMATION_ORDER: Tuple[str, ...] = (
        "warp_command_transcript",
        "warp_ai_exchange",
        "newline_normalize",
        "whitespace_trim",
    )

    def __init__(self) -> None:
        self._closed = False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def describe_schema(self) -> AdapterSchema:
        return AdapterSchema(
            version="1.0",
            fields={
                "session_id": FieldSpec(
                    type="string",
                    required=False,
                    description="Warp terminal session id (numeric string)",
                    indexed=True,
                ),
                "conversation_id": FieldSpec(
                    type="string",
                    required=False,
                    description="Warp AI conversation id (UUID)",
                    indexed=True,
                ),
                "command_count": FieldSpec(
                    type="int",
                    required=False,
                    description="Number of commands in the session",
                ),
                "hostname": FieldSpec(
                    type="string",
                    required=False,
                    description="Hostname where commands were run",
                ),
                "working_directory": FieldSpec(
                    type="string",
                    required=False,
                    description="Primary working directory of the session",
                ),
                "session_start": FieldSpec(
                    type="string",
                    required=False,
                    description="ISO-8601 UTC of the first command in the session",
                ),
                "session_end": FieldSpec(
                    type="string",
                    required=False,
                    description="ISO-8601 UTC of the last command in the session",
                ),
                "record_type": FieldSpec(
                    type="string",
                    required=True,
                    description="Either 'command_session' or 'ai_query'",
                ),
                "warp_db_path": FieldSpec(
                    type="string",
                    required=True,
                    description="Absolute path of the Warp SQLite database",
                ),
            },
        )

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(
        self,
        *,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[object]:
        if self._closed:
            raise AdapterClosedError("WarpSourceAdapter is closed")
        db_path = _resolve_db(source.local_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            self._verify_schema(conn, db_path)
            version = _db_mtime_version(db_path)

            # --- Command sessions ---
            yield from self._ingest_command_sessions(conn, db_path, version, source, palace)

            # --- AI queries ---
            yield from self._ingest_ai_queries(conn, db_path, version, source, palace)
        finally:
            conn.close()

    def _ingest_command_sessions(
        self,
        conn: sqlite3.Connection,
        db_path: str,
        version: str,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[object]:
        """Ingest command history grouped by session_id."""
        session_rows = conn.execute(
            """
            SELECT session_id, COUNT(*) as cmd_count,
                   MIN(start_ts) as first_ts, MAX(COALESCE(completed_ts, start_ts)) as last_ts,
                   GROUP_CONCAT(DISTINCT hostname) as hosts
            FROM commands
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            ORDER BY MIN(start_ts)
            """
        ).fetchall()

        for row in session_rows:
            sid = str(row["session_id"])
            src_file = session_source_file(db_path, sid)

            yield SourceItemMetadata(
                source_file=src_file,
                version=version,
                size_hint=row["cmd_count"],
                route_hint=self._route_hint_for(source),
            )
            if palace.is_skip_requested():
                continue

            commands = conn.execute(
                """
                SELECT command, exit_code, start_ts, completed_ts,
                       pwd, shell, username, hostname, git_branch
                FROM commands
                WHERE session_id = ?
                ORDER BY start_ts
                """,
                (row["session_id"],),
            ).fetchall()

            cmd_list = [dict(c) for c in commands]
            if len(cmd_list) < 2:
                logger.debug("warp adapter: skipping session %s (%d commands)", sid, len(cmd_list))
                continue

            transcript = _format_command_transcript(cmd_list)
            if not transcript:
                continue

            chunks = chunk_exchanges(transcript)
            if not chunks:
                # If chunking fails (e.g., no exchange markers), treat
                # the whole transcript as a single chunk.
                chunks = [{"content": transcript, "chunk_index": "0"}]

            wing = self._wing_for(source)
            room = detect_convo_room(transcript)
            first_ts = row["first_ts"] or ""
            last_ts = row["last_ts"] or ""
            primary_pwd = cmd_list[0].get("pwd") or ""
            primary_host = cmd_list[0].get("hostname") or ""
            filed_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

            for chunk in chunks:
                content = chunk["content"]
                chunk_index = int(chunk["chunk_index"])
                metadata = {
                    "source_file": src_file,
                    "chunk_index": chunk_index,
                    "filed_at": filed_at,
                    "added_by": "warp-adapter",
                    "wing": wing,
                    "room": room,
                    "hall": _detect_hall(content),
                    "ingest_mode": "chunked_content",
                    "extract_mode": "command_session",
                    "privacy_class": self.default_privacy_class,
                    "record_type": "command_session",
                    "session_id": sid,
                    "command_count": len(cmd_list),
                    "hostname": primary_host,
                    "working_directory": primary_pwd,
                    "session_start": first_ts,
                    "session_end": last_ts,
                    "warp_db_path": db_path,
                    "warp_version": version,
                }
                yield DrawerRecord(
                    content=content,
                    source_file=src_file,
                    chunk_index=chunk_index,
                    metadata=metadata,
                    route_hint=RouteHint(wing=wing, room=room, hall=metadata["hall"]),
                )

    def _ingest_ai_queries(
        self,
        conn: sqlite3.Connection,
        db_path: str,
        version: str,
        source: SourceRef,
        palace: PalaceContext,
    ) -> Iterator[object]:
        """Ingest Warp AI query/response pairs grouped by conversation_id."""
        # Check if ai_queries table exists (may not on older Warp versions)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "ai_queries" not in tables:
            return

        conversations = conn.execute(
            """
            SELECT conversation_id, COUNT(*) as query_count,
                   MIN(start_ts) as first_ts, MAX(start_ts) as last_ts
            FROM ai_queries
            GROUP BY conversation_id
            ORDER BY MIN(start_ts)
            """
        ).fetchall()

        for row in conversations:
            conv_id = row["conversation_id"]
            src_file = ai_source_file(db_path, conv_id)

            yield SourceItemMetadata(
                source_file=src_file,
                version=version,
                size_hint=row["query_count"],
                route_hint=self._route_hint_for(source),
            )
            if palace.is_skip_requested():
                continue

            queries = conn.execute(
                """
                SELECT input, output_status, model_id, working_directory, start_ts
                FROM ai_queries
                WHERE conversation_id = ?
                ORDER BY start_ts
                """,
                (conv_id,),
            ).fetchall()

            # Build an exchange-pair transcript from AI queries
            lines: List[str] = []
            for q in queries:
                user_text = _extract_ai_query_text(q["input"])
                if not user_text:
                    continue
                status = (q["output_status"] or "").strip('"')
                model = q["model_id"] or "unknown"
                wd = q["working_directory"] or ""

                lines.append(f"> {user_text}")
                context_parts = []
                if wd:
                    context_parts.append(f"dir={wd}")
                context_parts.append(f"model={model}")
                context_parts.append(f"status={status}")
                lines.append(f"[{', '.join(context_parts)}]")
                lines.append("")

            transcript = "\n".join(lines).strip()
            if not transcript:
                continue

            chunks = chunk_exchanges(transcript)
            if not chunks:
                chunks = [{"content": transcript, "chunk_index": "0"}]

            wing = self._wing_for(source)
            room = detect_convo_room(transcript)
            first_ts = row["first_ts"] or ""
            last_ts = row["last_ts"] or ""
            wd = (queries[0]["working_directory"] or "") if queries else ""
            filed_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

            for chunk in chunks:
                content = chunk["content"]
                chunk_index = int(chunk["chunk_index"])
                metadata = {
                    "source_file": src_file,
                    "chunk_index": chunk_index,
                    "filed_at": filed_at,
                    "added_by": "warp-adapter",
                    "wing": wing,
                    "room": room,
                    "hall": _detect_hall(content),
                    "ingest_mode": "chunked_content",
                    "extract_mode": "ai_query",
                    "privacy_class": self.default_privacy_class,
                    "record_type": "ai_query",
                    "conversation_id": conv_id,
                    "working_directory": wd,
                    "session_start": first_ts,
                    "session_end": last_ts,
                    "warp_db_path": db_path,
                    "warp_version": version,
                }
                yield DrawerRecord(
                    content=content,
                    source_file=src_file,
                    chunk_index=chunk_index,
                    metadata=metadata,
                    route_hint=RouteHint(wing=wing, room=room, hall=metadata["hall"]),
                )

    # ------------------------------------------------------------------
    # Incremental ingest
    # ------------------------------------------------------------------

    def is_current(
        self,
        *,
        item: SourceItemMetadata,
        existing_metadata: Optional[dict],
    ) -> bool:
        if not existing_metadata:
            return False
        stored_version = existing_metadata.get("warp_version")
        if stored_version is not None:
            return str(stored_version) == item.version
        # Fallback: if we have drawers for this source_file, assume current
        return True

    def source_summary(self, *, source: SourceRef) -> SourceSummary:
        try:
            db_path = _resolve_db(source.local_path)
        except SourceNotFoundError:
            return SourceSummary(description="Warp database not found", item_count=0)
        conn = sqlite3.connect(db_path)
        try:
            self._verify_schema(conn, db_path)
            (cmd_count,) = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM commands WHERE session_id IS NOT NULL"
            ).fetchone()
            # Check for ai_queries table
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            ai_count = 0
            if "ai_queries" in tables:
                (ai_count,) = conn.execute(
                    "SELECT COUNT(DISTINCT conversation_id) FROM ai_queries"
                ).fetchone()
        finally:
            conn.close()
        total = int(cmd_count) + int(ai_count)
        return SourceSummary(
            description=(
                f"Warp terminal database at {db_path} "
                f"({cmd_count} command sessions, {ai_count} AI conversations)"
            ),
            item_count=total,
        )

    def close(self) -> None:
        self._closed = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_schema(conn: sqlite3.Connection, db_path: str) -> None:
        """Confirm the SQLite has the tables the adapter relies on."""
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "commands" not in tables:
            raise SourceNotFoundError(
                f"Warp database at {db_path} is missing the 'commands' table"
            )

    def _wing_for(self, source: SourceRef) -> str:
        """Resolve the wing for Warp records.

        1. Explicit ``options["wing"]`` from the SourceRef
        2. Adapter fallback: ``"warp"``
        """
        explicit = (source.options or {}).get("wing")
        if explicit:
            return normalize_wing_name(str(explicit))
        return "warp"

    def _route_hint_for(self, source: SourceRef) -> Optional[RouteHint]:
        wing = self._wing_for(source)
        return RouteHint(wing=wing, room=None, hall=None)


__all__ = [
    "WarpSourceAdapter",
    "session_source_file",
    "ai_source_file",
]
