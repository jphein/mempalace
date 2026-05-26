"""search_memories confidence integration (techempower-org/mempalace#167).

Verifies the plumbing — not the calibration quality:

* no calibrator configured → hits carry no ``confidence`` field
* calibrator configured (via MEMPALACE_CALIBRATION_PATH) → vector hits
  carry a ``confidence`` in [0, 1]
* confidence is a monotone function of the calibrator applied to similarity

These open a real (tiny) chromadb palace but no daemon, no network, no
LLM — safe to run while a GPU backfill is live.
"""

import mempalace.searcher as searcher
from mempalace.calibration import fit_calibrator
from mempalace.palace import get_collection
from mempalace.searcher import search_memories


def _seed(palace_path):
    col = get_collection(palace_path, create=True)
    col.upsert(
        ids=["D1", "D2", "D3"],
        documents=[
            "We switched the auth service to use JWT tokens with a 24h expiry.",
            "Database migration to PostgreSQL 15 completed last Tuesday.",
            "The frontend team is debating whether to adopt TanStack Query.",
        ],
        metadatas=[
            {"wing": "backend", "room": "auth", "source_file": "fixture_D1.md"},
            {"wing": "backend", "room": "db", "source_file": "fixture_D2.md"},
            {"wing": "frontend", "room": "state", "source_file": "fixture_D3.md"},
        ],
    )


def _clear_calibrator_cache():
    searcher._CALIBRATOR_CACHE.clear()


def test_no_calibrator_means_no_confidence_field(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMPALACE_CALIBRATION_PATH", raising=False)
    _clear_calibrator_cache()
    palace = str(tmp_path / "palace")
    _seed(palace)

    result = search_memories("auth JWT tokens", palace, n_results=3)
    hits = result["results"]
    assert hits, "expected at least one hit"
    assert all("confidence" not in h for h in hits)


def test_configured_calibrator_adds_confidence(tmp_path, monkeypatch):
    palace = str(tmp_path / "palace")
    _seed(palace)

    # Fit a simple monotone calibrator and point config at it.
    cal = fit_calibrator(
        [(0.1, False), (0.3, False), (0.6, True), (0.9, True)],
        source="unit",
    )
    cal_path = tmp_path / "cal.json"
    cal.save(cal_path)
    monkeypatch.setenv("MEMPALACE_CALIBRATION_PATH", str(cal_path))
    _clear_calibrator_cache()

    result = search_memories("auth JWT tokens", palace, n_results=3)
    hits = result["results"]
    assert hits, "expected at least one hit"

    # Every vector hit (similarity is not None) should carry a confidence
    # equal to the calibrator applied to its similarity.
    saw_confidence = False
    for h in hits:
        sim = h.get("similarity")
        if sim is None:
            assert "confidence" not in h
            continue
        saw_confidence = True
        assert "confidence" in h
        assert 0.0 <= h["confidence"] <= 1.0
        assert h["confidence"] == round(cal.apply(sim), 3)
    assert saw_confidence, "expected at least one vector hit with similarity"

    _clear_calibrator_cache()


def test_missing_calibrator_file_omits_confidence(tmp_path, monkeypatch):
    # Path configured but file absent → no confidence (never faked).
    monkeypatch.setenv("MEMPALACE_CALIBRATION_PATH", str(tmp_path / "absent.json"))
    _clear_calibrator_cache()
    palace = str(tmp_path / "palace")
    _seed(palace)

    result = search_memories("auth JWT tokens", palace, n_results=3)
    hits = result["results"]
    assert hits, "expected at least one hit"
    assert all("confidence" not in h for h in hits)

    _clear_calibrator_cache()
