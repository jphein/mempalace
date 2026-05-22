"""Tests for the RETIRED-marker check in _open_collection_or_explain
and _check_local_palace_retired."""

import os


def test_open_collection_emits_retired_marker_when_default_path_missing(
    tmp_path, monkeypatch, capsys
):
    """If ~/.mempalace/RETIRED exists and palace_path is the default
    (~/.mempalace/palace) and the dir is absent, the helper emits the
    marker content instead of 'Run: mempalace init'."""
    from mempalace import palace

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".mempalace").mkdir()
    (fake_home / ".mempalace" / "RETIRED").write_text(
        "local palace retired 2026-05-14\nset PALACE_DAEMON_URL\n"
    )

    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))

    default_palace = str(fake_home / ".mempalace" / "palace")
    out_lines: list[str] = []
    result = palace._open_collection_or_explain(default_palace, out=out_lines.append)
    assert result is None
    joined = "\n".join(out_lines)
    assert "RETIRED" in joined
    assert "retired 2026-05-14" in joined
    assert "set PALACE_DAEMON_URL" in joined
    assert "Run: mempalace init" not in joined


def test_open_collection_falls_through_to_init_hint_when_no_marker(tmp_path):
    """When no RETIRED marker exists, the helper emits the legacy
    'No palace found / Run: mempalace init' message."""
    from mempalace import palace

    missing = str(tmp_path / "nope")
    out_lines: list[str] = []
    result = palace._open_collection_or_explain(missing, out=out_lines.append)
    assert result is None
    joined = "\n".join(out_lines)
    assert "Run: mempalace init" in joined
    assert "RETIRED" not in joined


def test_open_collection_ignores_marker_for_non_default_path(tmp_path, monkeypatch):
    """The RETIRED marker only gates the DEFAULT path. An explicit
    --palace <other-dir> bypasses it (used for forensic reads of the
    archived palace)."""
    from mempalace import palace

    fake_home = tmp_path / "home"
    (fake_home / ".mempalace").mkdir(parents=True)
    (fake_home / ".mempalace" / "RETIRED").write_text("retired")
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))

    explicit = str(tmp_path / "some-other-palace")
    out_lines: list[str] = []
    palace._open_collection_or_explain(explicit, out=out_lines.append)
    joined = "\n".join(out_lines)
    assert "RETIRED" not in joined  # marker NOT triggered for explicit path
    assert "Run: mempalace init" in joined


def test_check_retired_palace_skipped_with_escape_hatch(tmp_path, monkeypatch):
    """MEMPALACE_ALLOW_RETIRED_PALACE=1 lets forensic reads through."""
    from mempalace.mcp_server import _check_local_palace_retired

    fake_home = tmp_path / "home"
    (fake_home / ".mempalace").mkdir(parents=True)
    (fake_home / ".mempalace" / "RETIRED").write_text("retired")
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
    monkeypatch.setenv("MEMPALACE_ALLOW_RETIRED_PALACE", "1")

    default_palace = str(fake_home / ".mempalace" / "palace")
    _check_local_palace_retired(default_palace)  # must not raise
