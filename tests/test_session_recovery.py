"""Tests for the session-recovery collection split.

Stop-hook auto-save checkpoint diary entries are routed to a dedicated
``mempalace_session_recovery`` collection so they don't dominate
``mempalace_search`` results in the main ``mempalace_drawers``
collection. See:

- ``docs/superpowers/specs/2026-04-25-checkpoint-collection-split.md``
- ``docs/superpowers/plans/2026-04-25-checkpoint-collection-split-impl.md``
"""

from mempalace.palace import (
    _SESSION_RECOVERY_COLLECTION,
    get_collection,
    get_session_recovery_collection,
)


class TestSessionRecoveryCollection:
    """Phase A — scaffolding. No behavior change yet; just the new
    collection adapter exists alongside the main collection."""

    def test_constant_name(self):
        assert _SESSION_RECOVERY_COLLECTION == "mempalace_session_recovery"

    def test_creates_with_correct_metadata(self, tmp_path):
        """Mirrors ``get_collection``'s shape: cosine space, thread-pin."""
        palace_path = str(tmp_path / "palace")
        col = get_session_recovery_collection(palace_path)
        assert col.metadata.get("hnsw:space") == "cosine"
        assert col.metadata.get("hnsw:num_threads") == 1

    def test_coexists_with_main_collection(self, tmp_path):
        """Both collections live in the same ChromaDB client without
        interfering. ChromaDB supports multi-collection per palace
        natively; this is a sanity check that we haven't accidentally
        wired them to share state."""
        palace_path = str(tmp_path / "palace")
        main = get_collection(palace_path, create=True)
        recovery = get_session_recovery_collection(palace_path)

        main.add(
            ids=["m1"],
            documents=["main collection drawer"],
            metadatas=[{"topic": "general"}],
        )
        recovery.add(
            ids=["r1"],
            documents=["recovery collection drawer"],
            metadatas=[{"topic": "checkpoint"}],
        )

        assert main.count() == 1
        assert recovery.count() == 1

        # Cross-collection isolation: a recovery ID is not visible
        # from the main collection and vice-versa.
        assert main.get(ids=["r1"])["ids"] == []
        assert recovery.get(ids=["m1"])["ids"] == []
