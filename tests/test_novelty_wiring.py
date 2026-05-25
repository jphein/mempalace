"""Tests for mempalace.novelty_wiring — write-time novelty tagging glue.

Covers the three drawer write paths (MCP tool_add_drawer, filesystem
miner, conversation miner) plus the helper module's own edge cases:
fail-open behaviour, opt-out via env/config, recent-window scoping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mempalace import novelty_wiring


# ── helper module: opt-out via env/config ─────────────────────────────


def test_is_novelty_tagging_enabled_default_true(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    assert novelty_wiring.is_novelty_tagging_enabled() is True


@pytest.mark.parametrize("falsy", ["0", "false", "FALSE", "no", "off", ""])
def test_is_novelty_tagging_enabled_env_falsy_disables(monkeypatch, falsy):
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", falsy)
    assert novelty_wiring.is_novelty_tagging_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "anything"])
def test_is_novelty_tagging_enabled_env_truthy_enables(monkeypatch, truthy):
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", truthy)
    assert novelty_wiring.is_novelty_tagging_enabled() is True


def test_is_novelty_tagging_enabled_config_false_disables(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    class FakeConfig:
        novelty_tagging = False

    assert novelty_wiring.is_novelty_tagging_enabled(FakeConfig()) is False


def test_env_overrides_config(monkeypatch):
    """Env var resolution must win over config setting."""
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", "1")

    class FakeConfig:
        novelty_tagging = False

    assert novelty_wiring.is_novelty_tagging_enabled(FakeConfig()) is True


# ── compute_novelty_tag: opt-out returns None ─────────────────────────


def test_compute_novelty_tag_opt_out_returns_none(monkeypatch):
    """Callers MUST treat None as 'do not add the metadata key' so an
    opt-out palace never sees a stale or empty novelty_tag value."""
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", "0")
    tag = novelty_wiring.compute_novelty_tag(MagicMock(), "w", "r", "anything", config=None)
    assert tag is None


# ── compute_novelty_tag: fail-open behaviour ──────────────────────────


def test_compute_novelty_tag_fails_open_on_collection_error(monkeypatch):
    """A collection.get() that raises must not bubble — we still want a
    write to land. The convention is to score against an empty window
    (novelty_score returns 1.0 → 'novel')."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    class BrokenCollection:
        def get(self, **kwargs):
            raise RuntimeError("backend down")

    tag = novelty_wiring.compute_novelty_tag(BrokenCollection(), "w", "r", "new content")
    assert tag == "novel"


def test_compute_novelty_tag_none_collection_treated_as_empty_window(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    tag = novelty_wiring.compute_novelty_tag(None, "w", "r", "new content")
    assert tag == "novel"


def test_compute_novelty_tag_empty_wing_room_treated_as_empty_window(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    col = MagicMock()
    tag = novelty_wiring.compute_novelty_tag(col, "", "", "new content")
    assert tag == "novel"
    col.get.assert_not_called()


# ── compute_novelty_tag: novelty classification ───────────────────────


def test_compute_novelty_tag_novel_against_unrelated_history(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    class StubCollection:
        def get(self, **kwargs):
            return {
                "documents": [
                    "The mitochondrion is the powerhouse of the cell.",
                    "Photosynthesis converts light energy into chemical energy.",
                ]
            }

    tag = novelty_wiring.compute_novelty_tag(
        StubCollection(),
        "w",
        "r",
        "Bought a 1972 Pearson 30 sailboat for the Chesapeake on 2026-04-12.",
    )
    assert tag in ("novel", "routine")


def test_compute_novelty_tag_redundant_against_identical_history(monkeypatch):
    """Identical-to-history content compresses with near-zero NCD and
    should classify as 'redundant' under the default threshold."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    text = "Riley started Year 7 at Lincoln Middle School on 2026-09-01."

    class StubCollection:
        def get(self, **kwargs):
            return {"documents": [text, text, text]}

    tag = novelty_wiring.compute_novelty_tag(StubCollection(), "w", "r", text)
    assert tag == "redundant"


def test_compute_novelty_tag_filters_non_string_docs(monkeypatch):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    class StubCollection:
        def get(self, **kwargs):
            return {"documents": [None, "", 42, "ok"]}

    tag = novelty_wiring.compute_novelty_tag(StubCollection(), "w", "r", "hello world")
    assert tag in ("novel", "routine", "redundant")


def test_compute_novelty_tag_passes_window_filter(monkeypatch):
    """Recent-window fetch must scope by wing+room and bound by window_size."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    captured = {}

    class SpyCollection:
        def get(self, **kwargs):
            captured.update(kwargs)
            return {"documents": []}

    novelty_wiring.compute_novelty_tag(
        SpyCollection(), "myproject", "decisions", "new", window_size=7
    )
    assert captured["where"] == {"$and": [{"wing": "myproject"}, {"room": "decisions"}]}
    assert captured["limit"] == 7


def test_compute_novelty_tag_handles_dataclass_get_result(monkeypatch):
    """RFC 001 backend GetResult is a dataclass with a .documents attr,
    not a dict. The helper must tolerate both shapes."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)

    class FakeResult:
        documents = ["ok", "ok", "ok"]

    class StubCollection:
        def get(self, **kwargs):
            return FakeResult()

    tag = novelty_wiring.compute_novelty_tag(StubCollection(), "w", "r", "ok")
    assert tag in ("novel", "routine", "redundant")


# ── mcp_server.tool_add_drawer integration ────────────────────────────


def _patch_mcp_server(monkeypatch, config, kg):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


def _open_collection(palace_path, create=False):
    import chromadb

    client = chromadb.PersistentClient(path=palace_path)
    if create:
        return client, client.get_or_create_collection(
            "mempalace_drawers", metadata={"hnsw:space": "cosine"}
        )
    return client, client.get_collection("mempalace_drawers")


def test_tool_add_drawer_stamps_novelty_tag(monkeypatch, config, palace_path, kg):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    _patch_mcp_server(monkeypatch, config, kg)
    _client, _col = _open_collection(palace_path, create=True)
    del _client
    from mempalace.mcp_server import tool_add_drawer

    result = tool_add_drawer(
        wing="testing_novelty",
        room="decisions",
        content="A unique decision recorded for the first time in this wing.",
    )
    assert result["success"] is True

    _client2, col = _open_collection(palace_path)
    del _client2
    stored = col.get(ids=[result["drawer_id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    assert "novelty_tag" in meta
    assert meta["novelty_tag"] in ("novel", "routine", "redundant")


def test_tool_add_drawer_omits_tag_when_disabled(monkeypatch, config, palace_path, kg):
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", "0")
    _patch_mcp_server(monkeypatch, config, kg)
    _client, _col = _open_collection(palace_path, create=True)
    del _client
    from mempalace.mcp_server import tool_add_drawer

    result = tool_add_drawer(
        wing="testing_novelty",
        room="decisions",
        content="Off switch — no novelty tag should be stamped.",
    )
    assert result["success"] is True

    _client2, col = _open_collection(palace_path)
    del _client2
    stored = col.get(ids=[result["drawer_id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    assert "novelty_tag" not in meta


def test_tool_add_drawer_first_drawer_in_fresh_wing_is_novel(monkeypatch, config, palace_path, kg):
    """Empty-window convention from novelty_score: the first drawer in a
    fresh wing scores 1.0 and classifies as 'novel'."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    _patch_mcp_server(monkeypatch, config, kg)
    _client, _col = _open_collection(palace_path, create=True)
    del _client
    from mempalace.mcp_server import tool_add_drawer

    result = tool_add_drawer(
        wing="brand_new_wing",
        room="people",
        content="First drawer ever in this wing.",
    )
    assert result["success"] is True

    _client2, col = _open_collection(palace_path)
    del _client2
    stored = col.get(ids=[result["drawer_id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    assert meta["novelty_tag"] == "novel"


def test_tool_add_drawer_does_not_block_on_novelty_failure(monkeypatch, config, palace_path, kg):
    """If the novelty scoring path raises, the write must still succeed —
    novelty is a TAG, not a GATE."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    _patch_mcp_server(monkeypatch, config, kg)
    _client, _col = _open_collection(palace_path, create=True)
    del _client

    def boom(*args, **kwargs):
        raise RuntimeError("scoring is on fire")

    monkeypatch.setattr(
        "mempalace.novelty_wiring.novelty_score",
        boom,
    )

    from mempalace.mcp_server import tool_add_drawer

    result = tool_add_drawer(
        wing="testing_novelty",
        room="decisions",
        content="Scoring will explode — write should still succeed.",
    )
    assert result["success"] is True

    _client2, col = _open_collection(palace_path)
    del _client2
    stored = col.get(ids=[result["drawer_id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    # Fail-open default is 'novel' so retrieval filters that key on the
    # tag still surface unscored drawers rather than dropping them.
    assert meta["novelty_tag"] == "novel"


# ── miner.add_drawer integration ──────────────────────────────────────


def test_miner_add_drawer_stamps_novelty_tag(monkeypatch, palace_path, tmp_path):
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    import chromadb

    from mempalace.miner import add_drawer

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_or_create_collection("mempalace_drawers", metadata={"hnsw:space": "cosine"})

    src = tmp_path / "notes.md"
    src.write_text("hello")

    result = add_drawer(
        col,
        wing="files",
        room="notes",
        content="First filesystem-mined drawer in this wing.",
        source_file=str(src),
        chunk_index=0,
        agent="miner",
    )
    stored = col.get(ids=[result["id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    assert meta["novelty_tag"] == "novel"
    del client


def test_miner_add_drawer_disabled_omits_tag(monkeypatch, palace_path, tmp_path):
    monkeypatch.setenv("MEMPALACE_NOVELTY_TAGGING", "0")
    import chromadb

    from mempalace.miner import add_drawer

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_or_create_collection("mempalace_drawers", metadata={"hnsw:space": "cosine"})
    src = tmp_path / "notes.md"
    src.write_text("hello")

    result = add_drawer(
        col,
        wing="files",
        room="notes",
        content="Tagging off — no novelty_tag should be stamped.",
        source_file=str(src),
        chunk_index=0,
        agent="miner",
    )
    stored = col.get(ids=[result["id"]], include=["metadatas"])
    meta = stored["metadatas"][0]
    assert "novelty_tag" not in meta
    del client


# ── miner.add_drawers integration ─────────────────────────────────────


def test_miner_add_drawers_stamps_shared_novelty_tag(monkeypatch, palace_path, tmp_path):
    """When a file mines into multiple chunks, all chunks share one
    file-level novelty_tag — computed once against the joined content."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    import chromadb

    from mempalace.miner import add_drawers

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_or_create_collection("mempalace_drawers", metadata={"hnsw:space": "cosine"})
    src = tmp_path / "doc.md"
    src.write_text("hello")

    chunks = [
        {"content": "first chunk of the file", "chunk_index": 0},
        {"content": "second chunk of the file", "chunk_index": 1},
    ]
    drawers_added, batch_ids, _warnings = add_drawers(
        col, "files", "notes", chunks, str(src), "miner"
    )
    assert drawers_added == 2

    stored = col.get(ids=batch_ids, include=["metadatas"])
    tags = {m.get("novelty_tag") for m in stored["metadatas"]}
    assert tags == {"novel"}
    del client


# ── convo_miner._file_chunks_locked integration ───────────────────────


def test_convo_miner_stamps_novelty_tag(monkeypatch, palace_path, tmp_path):
    """Conversation mining writes through _file_chunks_locked. Each
    chunk gets its own novelty tag (per-chunk so general-mode routes can
    score against the destination room, not the file-level room)."""
    monkeypatch.delenv("MEMPALACE_NOVELTY_TAGGING", raising=False)
    import chromadb

    from mempalace.convo_miner import _file_chunks_locked

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_or_create_collection("mempalace_drawers", metadata={"hnsw:space": "cosine"})
    src = tmp_path / "convo.jsonl"
    src.write_text("placeholder")

    chunks = [
        {"content": "first user turn of the conversation", "chunk_index": 0},
        {"content": "second user turn covering a totally different topic", "chunk_index": 1},
    ]

    drawers_added, _room_counts, skipped = _file_chunks_locked(
        col,
        str(src),
        chunks,
        wing="convos",
        room="planning",
        agent="convo_miner",
        extract_mode="legacy",
    )
    assert skipped is False
    assert drawers_added == 2

    stored = col.get(where={"wing": "convos"}, include=["metadatas"])
    tags = [m.get("novelty_tag") for m in stored["metadatas"]]
    assert all(t in ("novel", "routine", "redundant") for t in tags)
    assert len(tags) == 2
    del client
