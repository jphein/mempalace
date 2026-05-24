"""Tests for the Gemini CLI source adapter."""

import json

import pytest

from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.context import PalaceContext
from mempalace.sources.gemini import GeminiSourceAdapter, _parse_gemini_jsonl


def _make_session_jsonl(messages, include_meta=True):
    """Build a Gemini-format JSONL string."""
    lines = []
    if include_meta:
        lines.append(json.dumps({"type": "session_metadata", "session_id": "test"}))
    for role, text in messages:
        entry_type = "user" if role == "user" else "gemini"
        lines.append(json.dumps({
            "type": entry_type,
            "content": [{"text": text}],
        }))
    return "\n".join(lines)


@pytest.fixture
def adapter():
    return GeminiSourceAdapter()


@pytest.fixture
def gemini_dir(tmp_path):
    chats_dir = tmp_path / "project_hash" / "chats"
    chats_dir.mkdir(parents=True)
    (chats_dir / "session-001.jsonl").write_text(
        _make_session_jsonl([
            ("user", "How do I deploy this?"),
            ("gemini", "You can deploy using the provided Dockerfile."),
            ("user", "What about CI/CD?"),
            ("gemini", "Set up a GitHub Actions workflow with the docker build step."),
        ])
    )
    (chats_dir / "session-002.jsonl").write_text(
        _make_session_jsonl([
            ("user", "Explain the database schema."),
            ("gemini", "The schema has users, sessions, and events tables."),
        ])
    )
    return tmp_path


@pytest.fixture
def palace_ctx():
    class _FC:
        def add(self, **kw): pass
        def upsert(self, **kw): pass
        def query(self, **kw): return {"ids": [], "documents": []}
        def get(self, **kw): return {"ids": [], "documents": [], "metadatas": []}
        def delete(self, **kw): pass
        def count(self): return 0

    class _FK:
        def add_triple(self, *a, **kw): pass

    return PalaceContext(
        drawer_collection=_FC(), knowledge_graph=_FK(), palace_path="/tmp/fake"
    )


class TestGeminiParser:
    def test_parse_valid_session(self):
        content = _make_session_jsonl([
            ("user", "hello"), ("gemini", "hi there")
        ])
        result = _parse_gemini_jsonl(content)
        assert result is not None
        assert len(result) == 2
        assert result[0] == ("user", "hello")
        assert result[1] == ("assistant", "hi there")

    def test_parse_rejects_without_metadata(self):
        content = _make_session_jsonl([
            ("user", "hello"), ("gemini", "hi")
        ], include_meta=False)
        assert _parse_gemini_jsonl(content) is None

    def test_parse_skips_message_update(self):
        lines = [
            json.dumps({"type": "session_metadata"}),
            json.dumps({"type": "message_update", "tokens": 42}),
            json.dumps({"type": "user", "content": [{"text": "a"}]}),
            json.dumps({"type": "gemini", "content": [{"text": "b"}]}),
        ]
        result = _parse_gemini_jsonl("\n".join(lines))
        assert result == [("user", "a"), ("assistant", "b")]

    def test_parse_concatenates_content_blocks(self):
        lines = [
            json.dumps({"type": "session_metadata"}),
            json.dumps({"type": "user", "content": [{"text": "part1"}, {"text": "part2"}]}),
            json.dumps({"type": "gemini", "content": [{"text": "reply"}]}),
        ]
        result = _parse_gemini_jsonl("\n".join(lines))
        assert result[0] == ("user", "part1\npart2")

    def test_parse_discards_pre_metadata_turns(self):
        lines = [
            json.dumps({"type": "user", "content": [{"text": "preamble"}]}),
            json.dumps({"type": "session_metadata"}),
            json.dumps({"type": "user", "content": [{"text": "real"}]}),
            json.dumps({"type": "gemini", "content": [{"text": "response"}]}),
        ]
        result = _parse_gemini_jsonl("\n".join(lines))
        assert len(result) == 2
        assert result[0] == ("user", "real")


class TestGeminiAdapter:
    def test_class_attributes(self):
        assert GeminiSourceAdapter.name == "gemini"
        assert "gemini_jsonl_parse" in GeminiSourceAdapter.declared_transformations

    def test_ingest_yields_records(self, adapter, gemini_dir, palace_ctx):
        source = SourceRef(local_path=str(gemini_dir))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        assert len(items) == 2
        assert len(drawers) >= 2
        for d in drawers:
            assert d.route_hint.wing == "gemini"
            assert "gemini://" in d.source_file

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert "source_file" in schema.fields

    def test_registered_entry_point(self):
        from mempalace.sources.registry import available_adapters
        assert "gemini" in available_adapters()
