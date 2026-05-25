"""Tests for the Aider source adapter."""

import pytest

from mempalace.sources.aider import (
    AiderSourceAdapter,
    _parse_session,
    _split_sessions,
)
from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.context import PalaceContext

_SAMPLE_HISTORY = """\
# aider chat started at 2026-03-11 17:28:46

> /home/jp/.local/bin/aider --model azure/o1
> Aider v0.86.2
> Model: azure/o1 with whole edit format

#### hi

Ok.

> Tokens: 592 sent, 2 received.

#### are you working?

No changes needed. Let me know if you have any files you'd like me to edit!

> Tokens: 605 sent, 19 received.

#### /exit

# aider chat started at 2026-03-11 17:29:41

> /home/jp/.local/bin/aider --model azure/o1
> Aider v0.86.2

#### can we attach mcp server to this aider?

I'm not sure what you mean by "attach mcp server." Could you clarify what exactly you want to achieve?

> Tokens: 800 sent, 50 received.

#### I want to add MCP support

That would require significant changes. Here's an approach using the FastMCP framework for the integration.

> Tokens: 1200 sent, 200 received.
"""


@pytest.fixture
def adapter():
    return AiderSourceAdapter()


@pytest.fixture
def aider_dir(tmp_path):
    (tmp_path / ".aider.chat.history.md").write_text(_SAMPLE_HISTORY)
    return tmp_path


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

    return PalaceContext(drawer_collection=_FC(), knowledge_graph=_FK(), palace_path="/tmp/fake")


class TestAiderParser:
    def test_split_sessions(self):
        sessions = _split_sessions(_SAMPLE_HISTORY)
        assert len(sessions) == 2
        assert sessions[0][0] == "2026-03-11 17:28:46"
        assert sessions[1][0] == "2026-03-11 17:29:41"

    def test_parse_session_extracts_turns(self):
        sessions = _split_sessions(_SAMPLE_HISTORY)
        messages = _parse_session(sessions[0][1])
        assert len(messages) >= 2
        assert any(role == "user" for role, _ in messages)
        assert any(role == "assistant" for role, _ in messages)

    def test_parse_session_filters_system_lines(self):
        sessions = _split_sessions(_SAMPLE_HISTORY)
        messages = _parse_session(sessions[0][1])
        for _, text in messages:
            assert not text.startswith("> ")

    def test_split_empty_content(self):
        assert _split_sessions("") == []

    def test_split_single_session(self):
        content = "# aider chat started at 2026-01-01 10:00:00\n\n#### hello\n\nworld\n"
        sessions = _split_sessions(content)
        assert len(sessions) == 1


class TestAiderAdapter:
    def test_class_attributes(self):
        assert AiderSourceAdapter.name == "aider"
        assert "aider_markdown_parse" in AiderSourceAdapter.declared_transformations

    def test_ingest_yields_records(self, adapter, aider_dir, palace_ctx):
        source = SourceRef(local_path=str(aider_dir))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        assert len(items) >= 1
        assert len(drawers) >= 1
        for d in drawers:
            assert d.route_hint.wing == "aider"
            assert "aider://" in d.source_file

    def test_ingest_single_file(self, adapter, aider_dir, palace_ctx):
        source = SourceRef(local_path=str(aider_dir / ".aider.chat.history.md"))
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        assert len(items) >= 1

    def test_ingest_wing_override(self, adapter, aider_dir, palace_ctx):
        source = SourceRef(
            local_path=str(aider_dir),
            options={"wing": "my_aider"},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        if drawers:
            assert all(d.route_hint.wing == "my_aider" for d in drawers)

    def test_ingest_limit(self, adapter, aider_dir, palace_ctx):
        source = SourceRef(
            local_path=str(aider_dir),
            options={"limit": 1},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        # Should stop after 1 session
        drawers_per_session = [r for r in results if isinstance(r, DrawerRecord)]
        session_sources = set(d.source_file for d in drawers_per_session)
        assert len(session_sources) <= 1

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert "source_file" in schema.fields
        assert "session_timestamp" in schema.fields

    def test_source_summary(self, adapter, aider_dir):
        source = SourceRef(local_path=str(aider_dir))
        summary = adapter.source_summary(source=source)
        assert "aider" in summary.description
        assert summary.item_count == 2  # 2 sessions in sample

    def test_registered_entry_point(self):
        from mempalace.sources.registry import available_adapters

        assert "aider" in available_adapters()
