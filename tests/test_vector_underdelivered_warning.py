"""The thin-vector-result warning must not misdiagnose pgvector as a broken HNSW index.

2026-09-03: an identifier-soup query on a healthy 86K-drawer pgvector wing scored
0.359, vector ranked 1, and the ChromaDB-era warning told the agent to run
`mempalace repair` — a long, pointless rebuild under the palace write lock.
"""

from mempalace.searcher import _vector_underdelivered_warning


def test_chroma_keeps_repair_hint(monkeypatch):
    monkeypatch.setenv("MEMPALACE_BACKEND", "chroma")
    msg = _vector_underdelivered_warning(86049, 1)
    assert "mempalace repair" in msg and "HNSW" in msg


def test_postgres_does_not_blame_the_index(monkeypatch):
    monkeypatch.setenv("MEMPALACE_BACKEND", "postgres")
    msg = _vector_underdelivered_warning(86049, 1)
    assert "repair" not in msg and "HNSW" not in msg
    assert "86049" in msg and "identifiers" in msg
    assert "postgres" in msg
