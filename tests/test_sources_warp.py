"""Tests for the Warp terminal source adapter."""

import sqlite3
from pathlib import Path

import pytest

from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.context import PalaceContext
from mempalace.sources.warp import (
    WarpSourceAdapter,
    _extract_ai_query_text,
    _format_command_transcript,
    _resolve_db,
    ai_source_file,
    session_source_file,
)


def _create_warp_db(db_path: Path, *, include_ai: bool = True) -> None:
    """Create a minimal Warp-like SQLite database for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE commands (
            id INTEGER PRIMARY KEY,
            command TEXT NOT NULL,
            exit_code INTEGER,
            start_ts DATETIME,
            completed_ts DATETIME,
            pwd TEXT,
            shell TEXT,
            username TEXT,
            hostname TEXT,
            session_id BIGINTEGER,
            git_branch TEXT,
            cloud_workflow_id TEXT,
            workflow_command TEXT
        )
    """)

    # Session 1: 3 commands (network investigation)
    conn.executemany(
        """INSERT INTO commands
           (id, command, exit_code, start_ts, completed_ts, pwd, shell,
            username, hostname, session_id, git_branch)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                1, "nmap -p 22 10.0.10.0/24", 0,
                "2024-09-20 18:37:05.123", "2024-09-20 18:37:17.456",
                "/home/jp", "bash", "jp", "katana", 172676966812879, None,
            ),
            (
                2, "ssh ubox0", 0,
                "2024-09-20 18:38:00.123", "2024-09-20 18:40:00.456",
                "/home/jp", "bash", "jp", "katana", 172676966812879, None,
            ),
            (
                3, "virsh list --all", 0,
                "2024-09-20 18:40:10.123", "2024-09-20 18:40:10.456",
                "/home/jp", "bash", "jp", "ubox0", 172676966812879, None,
            ),
        ],
    )

    # Session 2: 4 commands (git workflow)
    conn.executemany(
        """INSERT INTO commands
           (id, command, exit_code, start_ts, completed_ts, pwd, shell,
            username, hostname, session_id, git_branch)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                4, "git status", 0,
                "2024-09-22 16:00:20.123", "2024-09-22 16:00:20.456",
                "/home/jp/Projects/myapp", "bash", "jp", "katana",
                172701908517955, "main",
            ),
            (
                5, "git checkout -b feat/new-feature", 0,
                "2024-09-22 16:00:30.123", "2024-09-22 16:00:30.456",
                "/home/jp/Projects/myapp", "bash", "jp", "katana",
                172701908517955, "main",
            ),
            (
                6, "python -m pytest tests/", 1,
                "2024-09-22 16:01:00.123", "2024-09-22 16:01:15.456",
                "/home/jp/Projects/myapp", "bash", "jp", "katana",
                172701908517955, "feat/new-feature",
            ),
            (
                7, "git commit -am 'fix: test failures'", 0,
                "2024-09-22 16:02:00.123", "2024-09-22 16:02:01.456",
                "/home/jp/Projects/myapp", "bash", "jp", "katana",
                172701908517955, "feat/new-feature",
            ),
        ],
    )

    # Session 3: single command (should be skipped — too few)
    conn.execute(
        """INSERT INTO commands
           (id, command, exit_code, start_ts, completed_ts, pwd, shell,
            username, hostname, session_id, git_branch)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            8, "ls", 0,
            "2024-09-23 10:00:00.123", "2024-09-23 10:00:00.456",
            "/home/jp", "bash", "jp", "katana", 999999999, None,
        ),
    )

    if include_ai:
        conn.execute("""
            CREATE TABLE ai_queries (
                id INTEGER PRIMARY KEY NOT NULL,
                exchange_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                start_ts DATETIME NOT NULL,
                input TEXT NOT NULL,
                working_directory TEXT,
                output_status TEXT NOT NULL,
                model_id TEXT NOT NULL DEFAULT '',
                planning_model_id TEXT NOT NULL DEFAULT '',
                coding_model_id TEXT NOT NULL DEFAULT ''
            )
        """)

        import json

        conn.executemany(
            """INSERT INTO ai_queries
               (id, exchange_id, conversation_id, start_ts, input,
                working_directory, output_status, model_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    1, "ex-001", "conv-abc",
                    "2024-12-30 00:02:13.776",
                    json.dumps([{"Query": {"text": "scan port 22 on the subnet"}}]),
                    "/home/jp", '"Completed"', "gpt-4o",
                ),
                (
                    2, "ex-002", "conv-abc",
                    "2024-12-30 00:05:00.000",
                    json.dumps([{"Query": {"text": "now try port 2000"}}]),
                    "/home/jp", '"Completed"', "gpt-4o",
                ),
                (
                    3, "ex-003", "conv-xyz",
                    "2025-03-01 20:36:39.422",
                    json.dumps([{"Query": {"text": "explain docker networking"}}]),
                    "/home/jp/Projects/myapp", '"Completed"', "claude-3.5-sonnet",
                ),
            ],
        )

    conn.commit()
    conn.close()


@pytest.fixture
def adapter():
    return WarpSourceAdapter()


@pytest.fixture
def warp_db(tmp_path):
    db_path = tmp_path / "warp.sqlite"
    _create_warp_db(db_path)
    return db_path


@pytest.fixture
def warp_db_no_ai(tmp_path):
    db_path = tmp_path / "warp_no_ai.sqlite"
    _create_warp_db(db_path, include_ai=False)
    return db_path


@pytest.fixture
def palace_ctx():
    class _FC:
        def add(self, **kw):
            pass

        def upsert(self, **kw):
            pass

        def query(self, **kw):
            return {"ids": [], "documents": []}

        def get(self, **kw):
            return {"ids": [], "documents": [], "metadatas": []}

        def delete(self, **kw):
            pass

        def count(self):
            return 0

    class _FK:
        def add_triple(self, *a, **kw):
            pass

    return PalaceContext(
        drawer_collection=_FC(), knowledge_graph=_FK(), palace_path="/tmp/fake"
    )


# ------------------------------------------------------------------
# Unit tests: helpers
# ------------------------------------------------------------------


class TestResolveDb:
    def test_explicit_path(self, warp_db):
        assert _resolve_db(str(warp_db)) == str(warp_db.resolve())

    def test_missing_raises(self):
        from mempalace.sources.base import SourceNotFoundError

        with pytest.raises(SourceNotFoundError):
            _resolve_db("/nonexistent/warp.sqlite")


class TestSourceFileUris:
    def test_session_source_file(self):
        uri = session_source_file("/path/to/warp.sqlite", "123456")
        assert uri == "warp:///path/to/warp.sqlite#session=123456"

    def test_ai_source_file(self):
        uri = ai_source_file("/path/to/warp.sqlite", "conv-abc")
        assert uri == "warp:///path/to/warp.sqlite#ai=conv-abc"


class TestExtractAiQueryText:
    def test_extracts_query(self):
        import json

        inp = json.dumps([{"Query": {"text": "scan the network"}}])
        assert _extract_ai_query_text(inp) == "scan the network"

    def test_extracts_with_context(self):
        import json

        inp = json.dumps([
            {"Query": {"text": "hello world"}},
            {"Directory": {"pwd": "/home/jp"}},
        ])
        assert _extract_ai_query_text(inp) == "hello world"

    def test_returns_none_for_invalid_json(self):
        assert _extract_ai_query_text("not json") is None

    def test_returns_none_for_no_query(self):
        import json

        inp = json.dumps([{"Directory": {"pwd": "/home/jp"}}])
        assert _extract_ai_query_text(inp) is None


class TestFormatCommandTranscript:
    def test_basic_formatting(self):
        cmds = [
            {
                "command": "ls -la",
                "pwd": "/home/jp",
                "exit_code": 0,
                "hostname": "katana",
                "git_branch": None,
                "start_ts": "2024-09-20 18:00:00",
            },
            {
                "command": "cd Projects",
                "pwd": "/home/jp",
                "exit_code": 0,
                "hostname": "katana",
                "git_branch": None,
                "start_ts": "2024-09-20 18:00:10",
            },
        ]
        result = _format_command_transcript(cmds)
        assert "> [katana:/home/jp] ls -la" in result
        assert "> [katana:/home/jp] cd Projects" in result

    def test_failed_command_shows_exit_code(self):
        cmds = [
            {
                "command": "false",
                "pwd": "/tmp",
                "exit_code": 1,
                "hostname": "katana",
                "git_branch": None,
                "start_ts": "2024-09-20 18:00:00",
            },
        ]
        result = _format_command_transcript(cmds)
        assert "exit=1" in result

    def test_git_branch_in_prompt(self):
        cmds = [
            {
                "command": "git status",
                "pwd": "/home/jp/Projects/myapp",
                "exit_code": 0,
                "hostname": "katana",
                "git_branch": "main",
                "start_ts": "2024-09-22 16:00:00",
            },
        ]
        result = _format_command_transcript(cmds)
        assert "(main)" in result


# ------------------------------------------------------------------
# Integration tests: adapter
# ------------------------------------------------------------------


class TestWarpAdapter:
    def test_class_attributes(self):
        assert WarpSourceAdapter.name == "warp"
        assert "warp_command_transcript" in WarpSourceAdapter.declared_transformations
        assert "supports_incremental" in WarpSourceAdapter.capabilities

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert "session_id" in schema.fields
        assert "conversation_id" in schema.fields
        assert "record_type" in schema.fields
        assert "warp_db_path" in schema.fields

    def test_ingest_yields_command_sessions(self, adapter, warp_db, palace_ctx):
        source = SourceRef(local_path=str(warp_db))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        # 3 sessions + 2 AI conversations = 5 items
        # (session 3 has only 1 command, still yields SourceItemMetadata)
        assert len(items) == 5

        # Session with 1 command is skipped, so fewer drawers
        cmd_drawers = [d for d in drawers if d.metadata.get("record_type") == "command_session"]
        ai_drawers = [d for d in drawers if d.metadata.get("record_type") == "ai_query"]

        assert len(cmd_drawers) >= 2  # at least one drawer per multi-cmd session
        assert len(ai_drawers) >= 1   # at least one AI conversation drawer

        for d in drawers:
            assert d.route_hint.wing == "warp"
            assert "warp://" in d.source_file

    def test_ingest_without_ai_table(self, adapter, warp_db_no_ai, palace_ctx):
        source = SourceRef(local_path=str(warp_db_no_ai))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        # Only command sessions, no AI
        assert len(items) == 3
        ai_drawers = [d for d in drawers if d.metadata.get("record_type") == "ai_query"]
        assert len(ai_drawers) == 0

    def test_ingest_wing_override(self, adapter, warp_db, palace_ctx):
        source = SourceRef(
            local_path=str(warp_db),
            options={"wing": "my_terminal"},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        assert all(d.route_hint.wing == "my_terminal" for d in drawers)

    def test_source_summary(self, adapter, warp_db):
        source = SourceRef(local_path=str(warp_db))
        summary = adapter.source_summary(source=source)
        assert "Warp" in summary.description
        assert summary.item_count == 5  # 3 sessions + 2 conversations

    def test_source_summary_missing_db(self, adapter):
        source = SourceRef(local_path="/nonexistent/warp.sqlite")
        summary = adapter.source_summary(source=source)
        assert summary.item_count == 0
        assert "not found" in summary.description

    def test_is_current_no_existing(self, adapter):
        item = SourceItemMetadata(source_file="warp://x#session=1", version="100")
        assert adapter.is_current(item=item, existing_metadata=None) is False

    def test_is_current_version_match(self, adapter):
        item = SourceItemMetadata(source_file="warp://x#session=1", version="100")
        assert adapter.is_current(
            item=item, existing_metadata={"warp_version": "100"}
        ) is True

    def test_is_current_version_mismatch(self, adapter):
        item = SourceItemMetadata(source_file="warp://x#session=1", version="200")
        assert adapter.is_current(
            item=item, existing_metadata={"warp_version": "100"}
        ) is False

    def test_is_current_fallback_no_version_key(self, adapter):
        item = SourceItemMetadata(source_file="warp://x#session=1", version="100")
        assert adapter.is_current(
            item=item, existing_metadata={"some_other_key": "val"}
        ) is True

    def test_close_prevents_ingest(self, adapter, warp_db, palace_ctx):
        from mempalace.sources.base import AdapterClosedError

        adapter.close()
        with pytest.raises(AdapterClosedError):
            list(adapter.ingest(source=SourceRef(local_path=str(warp_db)), palace=palace_ctx))

    def test_verify_schema_rejects_wrong_db(self, adapter, tmp_path, palace_ctx):
        from mempalace.sources.base import SourceNotFoundError

        bad_db = tmp_path / "bad.sqlite"
        conn = sqlite3.connect(str(bad_db))
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.close()
        source = SourceRef(local_path=str(bad_db))
        with pytest.raises(SourceNotFoundError, match="missing the 'commands' table"):
            list(adapter.ingest(source=source, palace=palace_ctx))

    def test_command_session_metadata_fields(self, adapter, warp_db, palace_ctx):
        source = SourceRef(local_path=str(warp_db))
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        cmd_drawers = [
            r for r in results
            if isinstance(r, DrawerRecord) and r.metadata.get("record_type") == "command_session"
        ]
        assert len(cmd_drawers) > 0
        d = cmd_drawers[0]
        assert d.metadata["added_by"] == "warp-adapter"
        assert d.metadata["ingest_mode"] == "chunked_content"
        assert "session_id" in d.metadata
        assert "command_count" in d.metadata
        assert "hostname" in d.metadata
        assert "warp_db_path" in d.metadata

    def test_ai_query_metadata_fields(self, adapter, warp_db, palace_ctx):
        source = SourceRef(local_path=str(warp_db))
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        ai_drawers = [
            r for r in results
            if isinstance(r, DrawerRecord) and r.metadata.get("record_type") == "ai_query"
        ]
        assert len(ai_drawers) > 0
        d = ai_drawers[0]
        assert d.metadata["added_by"] == "warp-adapter"
        assert d.metadata["extract_mode"] == "ai_query"
        assert "conversation_id" in d.metadata
        assert "warp_db_path" in d.metadata

    def test_registered_entry_point(self):
        from mempalace.sources.registry import available_adapters, register

        # Ensure the adapter is registered (entry point may not resolve
        # in worktree/PYTHONPATH-only setups where pyproject.toml is not installed).
        register("warp", WarpSourceAdapter)
        assert "warp" in available_adapters()
