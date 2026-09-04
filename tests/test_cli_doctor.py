"""#425: `mempalace doctor` — one-screen health check of the memory workflow.

The failure mode it guards against: the MCP bridge was unresolvable on PATH
fleet-wide for days and nothing surfaced it. doctor makes every layer loud.
"""

import json

import pytest

from mempalace import cli


class _Args:
    def __init__(self, wing="testwing", json=False):
        self.wing = wing
        self.json = json


@pytest.fixture
def all_green(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/mempalace-mcp")
    monkeypatch.setattr(cli, "_daemon_url", lambda: "http://daemon:8085")
    monkeypatch.setattr(
        cli,
        "_call_daemon_rest",
        lambda path, params=None: {"total_drawers": 100, "wings": {"testwing": 40, "other": 60}},
    )
    # fresh hook log
    home = tmp_path
    hs = home / ".mempalace" / "hook_state"
    hs.mkdir(parents=True)
    (hs / "hook.log").write_text("x")
    (home / ".mempalace" / "pending").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", str(home)))
    return monkeypatch


def test_all_green_exits_zero(all_green, capsys):
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(_Args())
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "mcp_bridge" in out and "daemon" in out and "wing" in out


def test_json_shape(all_green, capsys):
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(_Args(json=True))
    assert e.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    names = {c["check"] for c in payload["checks"]}
    assert names == {"mcp_bridge", "daemon", "wing", "save_hooks", "replay_queue"}


def test_missing_bridge_is_error_and_nonzero(all_green, capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(_Args(json=True))
    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    bridge = next(c for c in payload["checks"] if c["check"] == "mcp_bridge")
    assert bridge["ok"] is False and bridge["level"] == "error"


def test_unreachable_daemon_is_error(all_green, capsys, monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cli, "_call_daemon_rest", boom)
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(_Args(json=True))
    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    daemon = next(c for c in payload["checks"] if c["check"] == "daemon")
    assert daemon["ok"] is False
