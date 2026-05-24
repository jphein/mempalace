"""
test_cli_stats.py — ``mempalace stats`` analytics dashboard (#191).

The ``stats`` subcommand composes ``mempalace_status`` +
``mempalace_kg_stats`` + ``mempalace_graph_stats`` (and optionally
``mempalace_list_tags``) into a single read-only view. These tests mock
``urllib.request.urlopen`` the same way ``test_cli_daemon.py`` does so
the dashboard logic is exercised without touching a real daemon.
"""

import argparse
import json
from unittest.mock import patch

import pytest


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _envelope(payload: dict) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        }
    ).encode()


def _make_dispatcher(responses: dict):
    """Return a fake urlopen that maps tool name → payload from ``responses``.

    Any tool not in ``responses`` returns an empty object. The dispatcher
    inspects the JSON-RPC request body to pick the right response so the
    same fixture can serve multiple tools fired by a single command.
    """

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        name = body["params"]["name"]
        return _FakeResp(_envelope(responses.get(name, {})))

    return fake_urlopen


# ── happy-path render ──────────────────────────────────────────────────


class TestCmdStatsDaemon:
    """Daemon-routed dashboard rendering."""

    def _args(self, **overrides):
        defaults = {
            "json": False,
            "quiet": False,
            "top": 10,
            "tags": False,
            "palace": None,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_renders_human_dashboard(self, capsys):
        from mempalace import cli

        responses = {
            "mempalace_status": {
                "total_drawers": 42,
                "wings": {"projects": 30, "sessions": 12},
            },
            "mempalace_kg_stats": {
                "entities": 7,
                "triples": 11,
                "current_facts": 9,
                "expired_facts": 2,
                "relationship_types": ["loves", "works_on"],
            },
            "mempalace_graph_stats": {
                "total_rooms": 5,
                "tunnel_rooms": 2,
                "total_edges": 4,
                "rooms_per_wing": {"projects": 3, "sessions": 2},
                "top_tunnels": [
                    {"room": "decisions", "wings": ["projects", "sessions"], "count": 2}
                ],
            },
        }

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=_make_dispatcher(responses)):
                cli.cmd_stats(self._args())

        out = capsys.readouterr().out
        assert "MemPalace Stats — 42 drawers" in out
        assert "WINGS" in out and "projects" in out and "30" in out
        assert "KNOWLEDGE GRAPH" in out
        assert "entities" in out and "7" in out
        assert "GRAPH" in out
        assert "tunnel rooms" in out
        # Tags section should be hidden by default.
        assert "TAGS" not in out

    def test_includes_tags_section_when_flag_set(self, capsys):
        from mempalace import cli

        responses = {
            "mempalace_status": {"total_drawers": 1, "wings": {"projects": 1}},
            "mempalace_kg_stats": {
                "entities": 0,
                "triples": 0,
                "current_facts": 0,
                "expired_facts": 0,
                "relationship_types": [],
            },
            "mempalace_graph_stats": {
                "total_rooms": 0,
                "tunnel_rooms": 0,
                "total_edges": 0,
                "rooms_per_wing": {},
                "top_tunnels": [],
            },
            "mempalace_list_tags": {
                "tags": [{"tag": "rust", "count": 5}, {"tag": "go", "count": 3}],
                "total_unique_tags": 2,
            },
        }

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=_make_dispatcher(responses)):
                cli.cmd_stats(self._args(tags=True))

        out = capsys.readouterr().out
        assert "TAGS" in out
        assert "rust" in out and "5" in out
        assert "go" in out and "3" in out

    def test_json_output_shape(self, capsys):
        from mempalace import cli

        responses = {
            "mempalace_status": {"total_drawers": 9, "wings": {"projects": 9}},
            "mempalace_kg_stats": {
                "entities": 1,
                "triples": 1,
                "current_facts": 1,
                "expired_facts": 0,
                "relationship_types": ["loves"],
            },
            "mempalace_graph_stats": {
                "total_rooms": 1,
                "tunnel_rooms": 0,
                "total_edges": 0,
                "rooms_per_wing": {"projects": 1},
                "top_tunnels": [],
            },
        }

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=_make_dispatcher(responses)):
                cli.cmd_stats(self._args(json=True))

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["total_drawers"] == 9
        assert payload["wings"] == {"projects": 9}
        assert payload["kg"]["entities"] == 1
        assert payload["graph"]["total_rooms"] == 1
        # Tags omitted when --tags wasn't passed.
        assert "tags" not in payload

    def test_json_output_includes_tags_when_requested(self, capsys):
        from mempalace import cli

        responses = {
            "mempalace_status": {"total_drawers": 0, "wings": {}},
            "mempalace_kg_stats": {
                "entities": 0,
                "triples": 0,
                "current_facts": 0,
                "expired_facts": 0,
                "relationship_types": [],
            },
            "mempalace_graph_stats": {
                "total_rooms": 0,
                "tunnel_rooms": 0,
                "total_edges": 0,
                "rooms_per_wing": {},
                "top_tunnels": [],
            },
            "mempalace_list_tags": {"tags": [], "total_unique_tags": 0},
        }

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=_make_dispatcher(responses)):
                cli.cmd_stats(self._args(json=True, tags=True))

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["tags"] == {"tags": [], "total_unique_tags": 0}

    def test_top_zero_shows_every_wing(self, capsys):
        """``--top 0`` is the explicit "show all" sentinel; the renderer
        must not truncate when it's passed."""
        from mempalace import cli

        wings = {f"wing{i:02}": (20 - i) for i in range(15)}
        responses = {
            "mempalace_status": {"total_drawers": sum(wings.values()), "wings": wings},
            "mempalace_kg_stats": {
                "entities": 0,
                "triples": 0,
                "current_facts": 0,
                "expired_facts": 0,
                "relationship_types": [],
            },
            "mempalace_graph_stats": {
                "total_rooms": 0,
                "tunnel_rooms": 0,
                "total_edges": 0,
                "rooms_per_wing": {},
                "top_tunnels": [],
            },
        }
        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=_make_dispatcher(responses)):
                cli.cmd_stats(self._args(top=0))

        out = capsys.readouterr().out
        # Every wing label appears.
        for label in wings:
            assert label in out
        # No "more wings" tail message when nothing is truncated.
        assert "more wings" not in out


# ── failure modes ──────────────────────────────────────────────────────


class TestCmdStatsErrors:
    def _args(self, **overrides):
        defaults = {
            "json": False,
            "quiet": False,
            "top": 10,
            "tags": False,
            "palace": None,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_exits_2_without_daemon_url(self, capsys):
        """Daemon-only command — refuse to run when daemon URL is unset."""
        from mempalace import cli
        from mempalace.config import MempalaceConfig

        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "mempalace.cli.MempalaceConfig",
                lambda: MempalaceConfig(config_dir="/tmp/empty-mempalace-config-stats"),
            ):
                with pytest.raises(SystemExit) as ex:
                    cli.cmd_stats(self._args())
        assert ex.value.code == 2
        err = capsys.readouterr().err
        assert "PALACE_DAEMON_URL" in err

    def test_exits_2_when_daemon_unreachable(self, capsys):
        from mempalace import cli

        def boom(req, timeout=None):
            raise ConnectionError("daemon down")

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=boom):
                with pytest.raises(SystemExit) as ex:
                    cli.cmd_stats(self._args())
        assert ex.value.code == 2
        err = capsys.readouterr().err
        assert "daemon" in err.lower()

    def test_kg_failure_does_not_blank_the_dashboard(self, capsys):
        """One failed sub-call must inline its error in the section, not
        abort the whole render — JP needs the surviving wing/graph data."""
        from mempalace import cli

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode())
            name = body["params"]["name"]
            if name == "mempalace_kg_stats":
                # JSON-RPC error envelope — triggers DaemonError downstream.
                return _FakeResp(
                    b'{"jsonrpc":"2.0","id":1,"error":'
                    b'{"code":-32603,"message":"KG offline"}}'
                )
            if name == "mempalace_status":
                return _FakeResp(_envelope({"total_drawers": 5, "wings": {"projects": 5}}))
            return _FakeResp(
                _envelope(
                    {
                        "total_rooms": 1,
                        "tunnel_rooms": 0,
                        "total_edges": 0,
                        "rooms_per_wing": {"projects": 1},
                        "top_tunnels": [],
                    }
                )
            )

        env = {"PALACE_DAEMON_URL": "http://daemon.example:8085"}
        with patch.dict("os.environ", env, clear=True):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                cli.cmd_stats(self._args())

        out = capsys.readouterr().out
        assert "MemPalace Stats — 5 drawers" in out
        assert "WINGS" in out and "projects" in out
        assert "KNOWLEDGE GRAPH" in out
        assert "unavailable" in out
        # Graph section still rendered because only KG failed.
        assert "GRAPH" in out
        assert "total rooms" in out


# ── parser wiring ──────────────────────────────────────────────────────


class TestParserAcceptsStats:
    """Sanity checks for the argparse wiring — ``mempalace stats --json``
    and ``mempalace --json stats`` both end up with ``args.command ==
    "stats"`` and the flags propagated, matching the pattern other
    subcommands established via the per-subparser ``--json`` shim.
    """

    def _parse(self, argv):
        from mempalace import cli

        with patch("sys.argv", argv):
            with patch.object(cli, "cmd_stats") as mock:
                # Stop dispatch from actually running by raising once
                # called. We only care about the parsed Namespace.
                mock.side_effect = SystemExit(0)
                with pytest.raises(SystemExit):
                    cli.main()
                return mock.call_args.args[0] if mock.call_args else None

    def test_post_subcommand_json_flag(self):
        ns = self._parse(["mempalace", "stats", "--json"])
        assert ns is not None
        assert ns.command == "stats"
        assert ns.json is True

    def test_pre_subcommand_json_flag(self):
        ns = self._parse(["mempalace", "--json", "stats"])
        assert ns is not None
        assert ns.command == "stats"
        assert ns.json is True

    def test_tags_flag(self):
        ns = self._parse(["mempalace", "stats", "--tags"])
        assert ns is not None
        assert ns.tags is True

    def test_top_flag(self):
        ns = self._parse(["mempalace", "stats", "--top", "3"])
        assert ns is not None
        assert ns.top == 3

    def test_top_rejects_negative(self):
        from mempalace import cli

        with patch("sys.argv", ["mempalace", "stats", "--top", "-1"]):
            with pytest.raises(SystemExit) as ex:
                cli.main()
        # argparse exits with code 2 on argument errors.
        assert ex.value.code == 2
