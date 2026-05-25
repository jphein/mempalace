"""Smoke tests: OpenCode adapter against a real OpenCode database.

Validates the adapter produces correct output from a real ``opencode.db``
rather than synthetic fixtures. Marked ``slow`` so they are excluded from
normal CI runs — invoke with ``pytest -m slow`` to include.

Requires a real OpenCode SQLite database at the standard XDG path
(``~/.local/share/opencode/opencode.db``). Skipped automatically when the
database is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mempalace.sources.base import (
    DrawerRecord,
    SourceItemMetadata,
    SourceRef,
)
from mempalace.sources.context import PalaceContext
from mempalace.sources.opencode import OpenCodeSourceAdapter, session_source_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The tests/conftest.py redirects HOME to a temp dir before imports, so
# Path("~").expanduser() resolves to the wrong location. We recover the
# real home from pwd (POSIX) or fall back to an env override.
try:
    import pwd

    _REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir
except Exception:
    _REAL_HOME = os.environ.get("MEMPALACE_TEST_REAL_HOME", "/home/jp")

_REAL_DB = Path(_REAL_HOME) / ".local/share/opencode/opencode.db"
_SKIP_REASON = "Real OpenCode database not found (set MEMPALACE_TEST_REAL_HOME if needed)"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _REAL_DB.is_file(), reason=_SKIP_REASON),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubCollection:
    """Minimal stand-in satisfying ``_CollectionLike`` without a real backend."""

    def add(self, **kwargs):
        pass

    def upsert(self, **kwargs):
        pass

    def query(self, **kwargs):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]]}

    def get(self, **kwargs):
        return {"ids": [], "documents": [], "metadatas": []}

    def delete(self, **kwargs):
        pass

    def count(self):
        return 0


class _StubKG:
    def add_triple(self, subject, predicate, obj, **kwargs):
        pass


def _make_palace_context() -> PalaceContext:
    return PalaceContext(
        drawer_collection=_StubCollection(),
        knowledge_graph=_StubKG(),
        palace_path="/tmp/fake-palace",
        adapter_name="opencode",
        adapter_version="0.1.0",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenCodeSmokeRealDB:
    """Run the adapter against the real OpenCode database."""

    def test_source_summary_reports_sessions(self):
        adapter = OpenCodeSourceAdapter()
        try:
            summary = adapter.source_summary(source=SourceRef(local_path=str(_REAL_DB)))
        finally:
            adapter.close()

        assert summary.item_count is not None
        assert summary.item_count > 0, "Expected at least one session in the real DB"
        assert str(_REAL_DB.resolve()) in summary.description

    def test_ingest_yields_records(self):
        """Full ingest: collects all yielded objects and validates shapes."""
        adapter = OpenCodeSourceAdapter()
        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))

        items: list[SourceItemMetadata] = []
        drawers: list[DrawerRecord] = []

        try:
            for obj in adapter.ingest(source=source, palace=palace):
                if isinstance(obj, SourceItemMetadata):
                    items.append(obj)
                elif isinstance(obj, DrawerRecord):
                    drawers.append(obj)
                else:
                    pytest.fail(f"Unexpected yielded type: {type(obj).__name__}")
        finally:
            adapter.close()

        # --- SourceItemMetadata checks ---
        assert len(items) > 0, "Expected at least one SourceItemMetadata"
        for meta in items:
            assert meta.source_file.startswith("opencode://"), (
                f"source_file URI should start with opencode://, got: {meta.source_file}"
            )
            assert "#session=" in meta.source_file, (
                f"source_file URI should contain #session=, got: {meta.source_file}"
            )
            assert meta.version, "version must be non-empty"

        # --- DrawerRecord checks ---
        assert len(drawers) > 0, "Expected at least one DrawerRecord from the real DB"

        for dr in drawers:
            # Content sanity
            assert dr.content, "DrawerRecord content must be non-empty"
            assert len(dr.content) > 10, (
                f"DrawerRecord content suspiciously short ({len(dr.content)} chars)"
            )

            # source_file URI shape
            assert dr.source_file.startswith("opencode://"), (
                f"source_file should start with opencode://, got: {dr.source_file}"
            )
            assert "#session=" in dr.source_file

            # chunk_index is a non-negative int
            assert isinstance(dr.chunk_index, int)
            assert dr.chunk_index >= 0

            # Metadata required fields
            md = dr.metadata
            assert md.get("source_file") == dr.source_file
            assert md.get("session_id"), "session_id must be present"
            assert md.get("project_dir") is not None, "project_dir must be present"
            assert md.get("session_created_at"), "session_created_at must be present"
            assert isinstance(md.get("message_count"), int)
            assert md["message_count"] >= 2, "sessions with < 2 messages should be skipped"
            assert md.get("extract_mode") == "exchange"
            assert md.get("opencode_db_path") == str(_REAL_DB.resolve())
            assert md.get("wing"), "wing must be non-empty"
            assert md.get("room"), "room must be non-empty"
            assert md.get("added_by") == "opencode-adapter"
            assert md.get("ingest_mode") == "chunked_content"
            assert md.get("filed_at"), "filed_at must be present"

            # route_hint consistency
            assert dr.route_hint is not None
            assert dr.route_hint.wing == md["wing"]
            assert dr.route_hint.room == md["room"]

    def test_source_file_stability(self):
        """Verify session_source_file produces consistent URIs."""
        db_path = str(_REAL_DB.resolve())
        sid = "ses_test123"
        uri = session_source_file(db_path, sid)
        assert uri == f"opencode://{db_path}#session={sid}"
        # Calling again should produce the exact same URI
        assert session_source_file(db_path, sid) == uri

    def test_ingest_content_has_exchange_format(self):
        """Verify drawer content uses the exchange-pair format (> user quotes)."""
        adapter = OpenCodeSourceAdapter()
        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))

        sample_drawers: list[DrawerRecord] = []
        try:
            for obj in adapter.ingest(source=source, palace=palace):
                if isinstance(obj, DrawerRecord):
                    sample_drawers.append(obj)
                    if len(sample_drawers) >= 20:
                        break
        finally:
            adapter.close()

        assert sample_drawers, "Expected drawers for exchange format check"

        # At least some drawers should contain the ">" prefix used for
        # user messages in the exchange format
        has_user_quote = any(">" in dr.content for dr in sample_drawers)
        assert has_user_quote, (
            "Expected at least some drawers with '>' user-quote markers from the exchange format"
        )

    def test_wing_derived_from_project_dir(self):
        """Sessions rooted in known project dirs should get meaningful wing names."""
        adapter = OpenCodeSourceAdapter()
        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))

        wings_seen: set[str] = set()
        try:
            for obj in adapter.ingest(source=source, palace=palace):
                if isinstance(obj, DrawerRecord):
                    wings_seen.add(obj.metadata.get("wing", ""))
        finally:
            adapter.close()

        assert wings_seen, "Expected at least one wing"
        # Should not all be the generic fallback
        assert wings_seen != {"opencode_general"}, (
            "Expected some project-specific wings, not all opencode_general"
        )

    def test_adapter_close_prevents_reuse(self):
        """After close(), ingest raises AdapterClosedError."""
        from mempalace.sources.base import AdapterClosedError

        adapter = OpenCodeSourceAdapter()
        adapter.close()

        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))
        with pytest.raises(AdapterClosedError):
            list(adapter.ingest(source=source, palace=palace))

    def test_summary_count_matches_session_table(self):
        """source_summary().item_count should match the session table row count."""
        import sqlite3

        conn = sqlite3.connect(str(_REAL_DB))
        try:
            (expected,) = conn.execute("SELECT COUNT(*) FROM session").fetchone()
        finally:
            conn.close()

        adapter = OpenCodeSourceAdapter()
        try:
            summary = adapter.source_summary(source=SourceRef(local_path=str(_REAL_DB)))
        finally:
            adapter.close()

        assert summary.item_count == expected, (
            f"source_summary reports {summary.item_count} sessions but DB has {expected}"
        )

    def test_ingest_metadata_values_are_flat_scalars(self):
        """All metadata values must be flat scalars (str/int/float/bool)."""
        adapter = OpenCodeSourceAdapter()
        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))

        try:
            for obj in adapter.ingest(source=source, palace=palace):
                if isinstance(obj, DrawerRecord):
                    for key, val in obj.metadata.items():
                        assert isinstance(val, (str, int, float, bool)), (
                            f"metadata[{key!r}] has non-scalar type {type(val).__name__}: {val!r}"
                        )
        finally:
            adapter.close()

    def test_session_ids_are_unique_per_source_item(self):
        """Each SourceItemMetadata should have a unique source_file."""
        adapter = OpenCodeSourceAdapter()
        palace = _make_palace_context()
        source = SourceRef(local_path=str(_REAL_DB))

        source_files: list[str] = []
        try:
            for obj in adapter.ingest(source=source, palace=palace):
                if isinstance(obj, SourceItemMetadata):
                    source_files.append(obj.source_file)
        finally:
            adapter.close()

        assert len(source_files) == len(set(source_files)), (
            "SourceItemMetadata source_files should be unique per session"
        )
