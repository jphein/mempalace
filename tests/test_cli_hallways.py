"""Tests for the `hallways` CLI command."""

from argparse import Namespace

import mempalace.hallways as hallways_mod
from mempalace.cli import cmd_hallways


def test_lists_sorted_by_count(monkeypatch, capsys):
    rows = [
        {
            "entity_a": "C",
            "entity_b": "D",
            "co_occurrence_count": 1,
            "wing": "w",
            "label": "C <-> D (x1)",
        },
        {
            "entity_a": "A",
            "entity_b": "B",
            "co_occurrence_count": 3,
            "wing": "w",
            "label": "A <-> B (x3)",
        },
    ]
    monkeypatch.setattr(hallways_mod, "list_hallways", lambda wing=None, config=None: list(rows))
    cmd_hallways(Namespace(wing=None, limit=50))
    out = capsys.readouterr().out
    assert "2 hallway(s)" in out
    assert "A <-> B (x3)" in out
    # Highest co-occurrence first.
    assert out.index("A <-> B") < out.index("C <-> D")


def test_respects_limit(monkeypatch, capsys):
    rows = [
        {"entity_a": f"E{i}", "entity_b": "X", "co_occurrence_count": i, "label": f"E{i} <-> X"}
        for i in range(5)
    ]
    monkeypatch.setattr(hallways_mod, "list_hallways", lambda wing=None, config=None: list(rows))
    cmd_hallways(Namespace(wing=None, limit=2))
    assert capsys.readouterr().out.count("<->") == 2


def test_negative_limit_shows_nothing_not_tail(monkeypatch, capsys):
    rows = [
        {"entity_a": f"E{i}", "entity_b": "X", "co_occurrence_count": i, "label": f"E{i} <-> X"}
        for i in range(5)
    ]
    monkeypatch.setattr(hallways_mod, "list_hallways", lambda wing=None, config=None: list(rows))
    cmd_hallways(Namespace(wing=None, limit=-2))
    # A negative limit must not slice from the end (which would print all-but-2).
    assert capsys.readouterr().out.count("<->") == 0


def test_empty_message(monkeypatch, capsys):
    monkeypatch.setattr(hallways_mod, "list_hallways", lambda wing=None, config=None: [])
    cmd_hallways(Namespace(wing="x", limit=50))
    assert "No hallways yet" in capsys.readouterr().out


def test_explicit_palace_scopes_hallway_listing(monkeypatch, tmp_path):
    calls = []

    def fake_list(wing=None, config=None):
        calls.append((wing, config.palace_path))
        return []

    selected = tmp_path / "selected" / "palace"
    monkeypatch.setattr(hallways_mod, "list_hallways", fake_list)

    cmd_hallways(Namespace(wing="wing_aya", limit=50, palace=str(selected)))

    assert calls == [("wing_aya", str(selected))]


def test_legacy_hallways_prints_deprecation_and_honours_json(monkeypatch, capsys):
    """#407: the legacy verb warns on stderr, and --json emits JSON (it was
    silently ignored — a jq pipeline got human text and exit 0)."""
    import json as _json

    from mempalace import cli

    rows = [
        {"label": "a <-> b", "entity_a": "a", "entity_b": "b", "co_occurrence_count": 3},
        {"label": "c <-> d", "entity_a": "c", "entity_b": "d", "co_occurrence_count": 1},
    ]
    monkeypatch.setattr("mempalace.hallways.list_hallways", lambda wing, config=None: list(rows))
    monkeypatch.setattr(
        cli, "MempalaceConfig", lambda *a, **k: type("C", (), {"palace_path": "/p"})()
    )

    class Args:
        palace = None
        wing = None
        limit = 1
        json = False
        format = None

    cli.cmd_hallways(Args())
    out = capsys.readouterr()
    assert "deprecated" in out.err and "hallway list" in out.err
    assert "1 hallway" not in out.out or "2 hallway(s)" in out.out

    Args.json = True
    cli.cmd_hallways(Args())
    out = capsys.readouterr()
    payload = _json.loads(out.out)
    assert payload["total"] == 2 and len(payload["hallways"]) == 1
    assert payload["hallways"][0]["label"] == "a <-> b"
    assert out.err == "", "no chrome on the JSON surface"
