"""Tests for the Codex CLI source adapter."""

import json

import pytest

from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.codex import CodexSourceAdapter, _parse_codex_jsonl
from mempalace.sources.context import PalaceContext


def _make_session_jsonl(messages, include_meta=True):
    """Build a Codex-format JSONL string."""
    lines = []
    if include_meta:
        lines.append(json.dumps({"type": "session_meta", "session_id": "test-session"}))
    for role, text in messages:
        payload_type = "user_message" if role == "user" else "agent_message"
        lines.append(json.dumps({
            "type": "event_msg",
            "payload": {"type": payload_type, "message": text},
        }))
    return "\n".join(lines)


@pytest.fixture
def adapter():
    return CodexSourceAdapter()


@pytest.fixture
def codex_dir(tmp_path):
    sessions_dir = tmp_path / "sessions" / "2026" / "05" / "23"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "rollout-001.jsonl").write_text(
        _make_session_jsonl([
            ("user", "How do I fix the authentication bug?"),
            ("assistant", "The issue is in the auth middleware. You need to check the token expiry."),
            ("user", "Can you show the fix?"),
            ("assistant", "Here's the corrected code for the auth middleware validation."),
        ])
    )
    (sessions_dir / "rollout-002.jsonl").write_text(
        _make_session_jsonl([
            ("user", "Explain the project architecture."),
            ("assistant", "The project follows a layered architecture with controllers, services, and repositories."),
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


class TestCodexParser:
    def test_parse_valid_session(self):
        content = _make_session_jsonl([
            ("user", "hello"), ("assistant", "hi there")
        ])
        result = _parse_codex_jsonl(content)
        assert result is not None
        assert len(result) == 2
        assert result[0] == ("user", "hello")
        assert result[1] == ("assistant", "hi there")

    def test_parse_rejects_without_session_meta(self):
        content = _make_session_jsonl([
            ("user", "hello"), ("assistant", "hi")
        ], include_meta=False)
        assert _parse_codex_jsonl(content) is None

    def test_parse_rejects_too_few_messages(self):
        content = _make_session_jsonl([("user", "hello")], include_meta=True)
        assert _parse_codex_jsonl(content) is None

    def test_parse_skips_response_items(self):
        lines = [
            json.dumps({"type": "session_meta"}),
            json.dumps({"type": "response_item", "payload": {"text": "ignored"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "a"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "b"}}),
        ]
        result = _parse_codex_jsonl("\n".join(lines))
        assert result == [("user", "a"), ("assistant", "b")]


class TestCodexAdapter:
    def test_class_attributes(self):
        assert CodexSourceAdapter.name == "codex"
        assert "codex_jsonl_parse" in CodexSourceAdapter.declared_transformations

    def test_ingest_yields_records(self, adapter, codex_dir, palace_ctx):
        source = SourceRef(local_path=str(codex_dir / "sessions"))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        assert len(items) == 2
        assert len(drawers) >= 2
        for d in drawers:
            assert d.route_hint.wing == "codex"
            assert "codex://" in d.source_file

    def test_ingest_wing_override(self, adapter, codex_dir, palace_ctx):
        source = SourceRef(
            local_path=str(codex_dir / "sessions"),
            options={"wing": "my_codex"},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        assert all(d.route_hint.wing == "my_codex" for d in drawers)

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert "source_file" in schema.fields
        assert "session_file" in schema.fields

    def test_registered_entry_point(self):
        from mempalace.sources.registry import available_adapters
        assert "codex" in available_adapters()
