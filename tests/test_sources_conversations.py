"""Tests for the conversation source adapter (RFC 002 §9)."""

import os
from pathlib import Path

import pytest

from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.context import PalaceContext
from mempalace.sources.conversations import ConversationSourceAdapter


@pytest.fixture
def adapter():
    return ConversationSourceAdapter()


@pytest.fixture
def convo_dir(tmp_path):
    """Create a directory with mock conversation session files."""
    session1 = tmp_path / "session_001.jsonl"
    session1.write_text(
        '{"role": "user", "content": "How do I fix this bug?"}\n'
        '{"role": "assistant", "content": "Let me look at the error. The issue is in the auth module."}\n'
        '{"role": "user", "content": "Can you show me the fix?"}\n'
        '{"role": "assistant", "content": "Here is the corrected code for authentication."}\n'
    )
    session2 = tmp_path / "session_002.jsonl"
    session2.write_text(
        '{"role": "user", "content": "What is the project architecture?"}\n'
        '{"role": "assistant", "content": "The project uses a layered architecture with services."}\n'
    )
    return tmp_path


@pytest.fixture
def palace_ctx():
    class _FakeCollection:
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

    class _FakeKG:
        def add_triple(self, *a, **kw):
            pass

    return PalaceContext(
        drawer_collection=_FakeCollection(),
        knowledge_graph=_FakeKG(),
        palace_path="/tmp/fake-palace",
    )


class TestConversationAdapter:
    def test_class_attributes(self):
        assert ConversationSourceAdapter.name == "conversations"
        assert ConversationSourceAdapter.spec_version == "1.0"
        assert ConversationSourceAdapter.adapter_version == "1.0.0"
        assert "supports_incremental" in ConversationSourceAdapter.capabilities
        assert "chunk_exchanges" in ConversationSourceAdapter.declared_transformations

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert schema.version == "1.0.0"
        assert "source_file" in schema.fields
        assert schema.fields["source_file"].indexed is True

    def test_ingest_yields_records(self, adapter, convo_dir, palace_ctx):
        source = SourceRef(local_path=str(convo_dir))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        assert len(items) >= 1
        assert len(drawers) >= 1

        for drawer in drawers:
            assert drawer.content
            assert drawer.source_file
            assert drawer.route_hint is not None
            assert drawer.route_hint.wing == "conversations"

    def test_ingest_respects_wing_override(self, adapter, convo_dir, palace_ctx):
        source = SourceRef(
            local_path=str(convo_dir),
            options={"wing": "my_convos"},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        if drawers:
            assert all(d.route_hint.wing == "my_convos" for d in drawers)

    def test_ingest_requires_local_path(self, adapter, palace_ctx):
        source = SourceRef(uri="https://example.com")
        with pytest.raises(Exception, match="requires source.local_path"):
            list(adapter.ingest(source=source, palace=palace_ctx))

    def test_ingest_raises_on_missing_dir(self, adapter, palace_ctx):
        source = SourceRef(local_path="/nonexistent/path/xyz")
        with pytest.raises(Exception, match="Not a directory"):
            list(adapter.ingest(source=source, palace=palace_ctx))

    def test_ingest_limit(self, adapter, convo_dir, palace_ctx):
        source = SourceRef(
            local_path=str(convo_dir),
            options={"limit": 1},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        assert len(items) == 1

    def test_is_current_returns_false_for_none(self, adapter):
        item = SourceItemMetadata(source_file="/tmp/x.jsonl", version="v1")
        assert adapter.is_current(item=item, existing_metadata=None) is False

    def test_is_current_checks_mtime(self, adapter, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"role": "user", "content": "test"}')
        mtime = os.path.getmtime(str(f))
        item = SourceItemMetadata(source_file=str(f), version="v1")

        assert adapter.is_current(
            item=item, existing_metadata={"source_mtime": mtime}
        ) is True
        assert adapter.is_current(
            item=item, existing_metadata={"source_mtime": mtime + 100}
        ) is False

    def test_source_summary(self, adapter, convo_dir):
        source = SourceRef(local_path=str(convo_dir))
        summary = adapter.source_summary(source=source)
        assert "conversations" in summary.description

    def test_source_summary_no_path(self, adapter):
        source = SourceRef()
        summary = adapter.source_summary(source=source)
        assert "no path" in summary.description


class TestConversationRegistration:
    def test_registered_as_entry_point(self):
        from mempalace.sources.registry import available_adapters, get_adapter_class

        assert "conversations" in available_adapters()
        cls = get_adapter_class("conversations")
        assert cls is ConversationSourceAdapter
