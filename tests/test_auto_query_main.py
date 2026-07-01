"""Tests for the auto-query CLI entry point (``__main__``).

Covers the wing fetch + on-disk cache (so a slow/asleep daemon can't zero out
``known_wings``, which previously silently disabled all wing scoring) and the
optional known-entities registry load.
"""

import json
import os

from mempalace.auto_query import __main__ as aq_main


class _Cfg:
    """Minimal config stub carrying a daemon_url."""

    def __init__(self, daemon_url="http://familiar:8085"):
        self.daemon_url = daemon_url


class TestWingCache:
    def test_caches_on_successful_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            aq_main, "_fetch_wings_from_daemon", lambda cfg: {"candela", "memorypalace"}
        )
        cache = str(tmp_path / "wings.json")
        result = aq_main._fetch_wings(_Cfg(), cache_path=cache)
        assert result == {"candela", "memorypalace"}
        assert os.path.exists(cache)
        with open(cache) as f:
            assert set(json.load(f)) == {"candela", "memorypalace"}

    def test_falls_back_to_cache_on_failure(self, tmp_path, monkeypatch):
        cache = str(tmp_path / "wings.json")
        with open(cache, "w") as f:
            json.dump(["candela", "realmwatch"], f)
        monkeypatch.setattr(aq_main, "_fetch_wings_from_daemon", lambda cfg: set())
        result = aq_main._fetch_wings(_Cfg(), cache_path=cache)
        assert result == {"candela", "realmwatch"}

    def test_empty_when_fetch_fails_and_no_cache(self, tmp_path, monkeypatch):
        cache = str(tmp_path / "nonexistent.json")
        monkeypatch.setattr(aq_main, "_fetch_wings_from_daemon", lambda cfg: set())
        result = aq_main._fetch_wings(_Cfg(), cache_path=cache)
        assert result == set()

    def test_cache_round_trip(self, tmp_path):
        cache = str(tmp_path / "wings.json")
        aq_main._write_wings_cache({"a", "b", "c"}, cache)
        assert aq_main._read_wings_cache(cache) == {"a", "b", "c"}

    def test_read_missing_cache_returns_empty(self, tmp_path):
        assert aq_main._read_wings_cache(str(tmp_path / "missing.json")) == set()


class TestKnownEntitiesLoad:
    def test_loads_list(self, tmp_path):
        path = str(tmp_path / "known_entities.json")
        with open(path, "w") as f:
            json.dump(["candela", "morpheus"], f)
        assert aq_main._load_known_entities(path) == {"candela", "morpheus"}

    def test_loads_dict_keys(self, tmp_path):
        path = str(tmp_path / "known_entities.json")
        with open(path, "w") as f:
            json.dump({"candela": 5, "morpheus": 2}, f)
        assert aq_main._load_known_entities(path) == {"candela", "morpheus"}

    def test_absent_returns_none(self, tmp_path):
        assert aq_main._load_known_entities(str(tmp_path / "missing.json")) is None
