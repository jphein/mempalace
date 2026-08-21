"""
test_cli_diary.py — ``mempalace diary write|read`` (slice of #191, issue #354).

``diary write`` wraps ``mempalace_diary_write``; ``diary read`` wraps
``mempalace_diary_read``. Both route to the daemon when daemon-strict is on
and ``--palace`` was not given, else to the local ``mempalace.mcp_server``
tool function — the ``cmd_wakeup`` / ``cmd_mined`` pattern.

The tool contract requires ``agent_name``, so the CLI adds ``--agent`` (with
a ``MEMPALACE_AGENT_NAME`` fallback) on top of issue #354's sketch. ``read``'s
``--topic`` / ``--since`` filters are applied client-side because the tool
takes only ``(agent_name, last_n, wing)``.
"""

import argparse
import io
import json
from unittest.mock import MagicMock, patch

import pytest


def _write_args(**overrides):
    defaults = {
        "json": False,
        "quiet": False,
        "format": None,
        "palace": None,
        "diary_action": "write",
        "entry": "SESSION:2026-08-20|cli.diary.landed|★★★",
        "agent": "morpheus",
        "topic": None,
        "wing": None,
        "session_id": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _read_args(**overrides):
    defaults = {
        "json": False,
        "quiet": False,
        "format": None,
        "palace": None,
        "diary_action": "read",
        "agent": "morpheus",
        "limit": 10,
        "wing": None,
        "topic": None,
        "since": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


_WRITE_OK = {
    "success": True,
    "entry_id": "diary_20260820_120000_000001",
    "agent": "morpheus",
    "topic": "cli-wave",
    "timestamp": "2026-08-20T12:00:00",
    "warnings": [],
    "chunks": 1,
}

_ENTRIES = [
    {
        "drawer_id": "d3",
        "date": "2026-08-20",
        "timestamp": "2026-08-20T12:00:00",
        "topic": "cli-wave",
        "content": "landed the walk command",
    },
    {
        "drawer_id": "d2",
        "date": "2026-08-19",
        "timestamp": "2026-08-19T09:30:00",
        "topic": "sync",
        "content": "upstream v3.8.0 merge",
    },
    {
        "drawer_id": "d1",
        "date": "2026-08-18",
        "timestamp": "2026-08-18T08:00:00",
        "topic": "cli-wave",
        "content": "read the brief",
    },
]

_READ_OK = {"agent": "morpheus", "entries": _ENTRIES, "total": 3, "showing": 3}


def _daemon(payload):
    """Patch the daemon path on and return the MagicMock standing in for it."""
    fake = MagicMock(return_value=payload)
    return (
        patch("mempalace.cli._daemon_strict", return_value=True),
        patch("mempalace.cli._call_daemon_tool", fake),
        fake,
    )


class TestDiaryWrite:
    def test_daemon_payload_carries_every_flag(self):
        from mempalace import cli

        strict, call, fake = _daemon(_WRITE_OK)
        with strict, call:
            cli.cmd_diary(_write_args(topic="cli-wave", wing="memorypalace", session_id="sess-7"))

        name, payload = fake.call_args[0]
        assert name == "mempalace_diary_write"
        assert payload == {
            "agent_name": "morpheus",
            "entry": "SESSION:2026-08-20|cli.diary.landed|★★★",
            "topic": "cli-wave",
            "wing": "memorypalace",
            "session_id": "sess-7",
        }

    def test_table_output_reports_entry_id(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_WRITE_OK)
        with strict, call:
            cli.cmd_diary(_write_args())

        out = capsys.readouterr().out
        assert "Diary entry filed" in out
        assert "diary_20260820_120000_000001" in out
        assert "morpheus" in out

    def test_json_passthrough(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_WRITE_OK)
        with strict, call:
            cli.cmd_diary(_write_args(format="json"))

        payload = json.loads(capsys.readouterr().out)
        assert payload["entry_id"] == "diary_20260820_120000_000001"
        assert payload["success"] is True

    def test_chunked_write_reports_chunk_count(self, capsys):
        from mempalace import cli

        chunked = dict(_WRITE_OK, chunks=4, chunk_ids=["a", "b", "c", "d"])
        strict, call, _ = _daemon(chunked)
        with strict, call:
            cli.cmd_diary(_write_args())

        assert "4 chunks" in capsys.readouterr().out

    def test_stdin_entry_when_dash(self):
        from mempalace import cli

        strict, call, fake = _daemon(_WRITE_OK)
        with strict, call, patch("sys.stdin", io.StringIO("piped entry text")):
            cli.cmd_diary(_write_args(entry="-"))

        assert fake.call_args[0][1]["entry"] == "piped entry text"

    def test_missing_entry_exits_2(self, capsys):
        from mempalace import cli

        strict, call, fake = _daemon(_WRITE_OK)
        with strict, call, patch("sys.stdin", io.StringIO("   ")):
            with pytest.raises(SystemExit) as exc:
                cli.cmd_diary(_write_args(entry=None))

        assert exc.value.code == 2
        assert fake.call_count == 0
        assert "entry text" in capsys.readouterr().err

    def test_missing_agent_exits_2(self, capsys):
        from mempalace import cli

        strict, call, fake = _daemon(_WRITE_OK)
        with strict, call, patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit) as exc:
                cli.cmd_diary(_write_args(agent=None))

        assert exc.value.code == 2
        assert fake.call_count == 0
        assert "MEMPALACE_AGENT_NAME" in capsys.readouterr().err

    def test_agent_name_from_environment(self):
        from mempalace import cli

        strict, call, fake = _daemon(_WRITE_OK)
        env = {"MEMPALACE_AGENT_NAME": "lucid"}
        with strict, call, patch.dict("os.environ", env, clear=True):
            cli.cmd_diary(_write_args(agent=None))

        assert fake.call_args[0][1]["agent_name"] == "lucid"

    def test_tool_failure_envelope_exits_2(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon({"success": False, "error": "another mine is in progress"})
        with strict, call:
            with pytest.raises(SystemExit) as exc:
                cli.cmd_diary(_write_args())

        assert exc.value.code == 2
        assert "another mine is in progress" in capsys.readouterr().err

    def test_daemon_unreachable_exits_1(self, capsys):
        from mempalace import cli

        with (
            patch("mempalace.cli._daemon_strict", return_value=True),
            patch("mempalace.cli._call_daemon_tool", side_effect=cli.DaemonError("boom")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli.cmd_diary(_write_args())

        assert exc.value.code == 1
        assert "daemon unreachable" in capsys.readouterr().err

    def test_local_path_calls_tool_function(self):
        from mempalace import cli

        with (
            patch("mempalace.cli._daemon_strict", return_value=False),
            patch("mempalace.mcp_server.tool_diary_write", return_value=_WRITE_OK) as local,
        ):
            cli.cmd_diary(_write_args(topic="cli-wave"))

        local.assert_called_once_with(
            agent_name="morpheus",
            entry="SESSION:2026-08-20|cli.diary.landed|★★★",
            topic="cli-wave",
        )


class TestDiaryRead:
    def test_limit_becomes_last_n(self):
        from mempalace import cli

        strict, call, fake = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(limit=3, wing="memorypalace"))

        name, payload = fake.call_args[0]
        assert name == "mempalace_diary_read"
        assert payload == {"agent_name": "morpheus", "last_n": 3, "wing": "memorypalace"}

    def test_limit_clamped_to_tool_maximum(self):
        from mempalace import cli

        strict, call, fake = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(limit=5000))

        assert fake.call_args[0][1]["last_n"] == 100

    def test_table_output_lists_entries_newest_first(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args())

        out = capsys.readouterr().out
        assert "DIARY — morpheus" in out
        assert "landed the walk command" in out
        assert out.index("2026-08-20T12:00:00") < out.index("2026-08-18T08:00:00")

    def test_empty_diary_message(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon({"agent": "morpheus", "entries": []})
        with strict, call:
            cli.cmd_diary(_read_args())

        assert "No diary entries" in capsys.readouterr().out

    def test_topic_filter_is_client_side_over_a_full_page(self, capsys):
        from mempalace import cli

        strict, call, fake = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(topic="sync", limit=2))

        # A client-side filter must not shrink the fetch window, or matching
        # entries just outside --limit would be invisible.
        assert fake.call_args[0][1]["last_n"] == 100
        out = capsys.readouterr().out
        assert "upstream v3.8.0 merge" in out
        assert "landed the walk command" not in out

    def test_since_filter_drops_older_entries(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(since="2026-08-19"))

        out = capsys.readouterr().out
        assert "upstream v3.8.0 merge" in out
        assert "read the brief" not in out

    def test_limit_truncates_after_filtering(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(topic="cli-wave", limit=1))

        out = capsys.readouterr().out
        assert "landed the walk command" in out
        assert "read the brief" not in out

    def test_json_reports_filters_and_showing(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon(_READ_OK)
        with strict, call:
            cli.cmd_diary(_read_args(json=True, topic="cli-wave"))

        payload = json.loads(capsys.readouterr().out)
        assert payload["showing"] == 2
        assert payload["topic_filter"] == "cli-wave"
        assert payload["total"] == 3
        assert [e["drawer_id"] for e in payload["entries"]] == ["d3", "d1"]

    def test_error_envelope_exits_2(self, capsys):
        from mempalace import cli

        strict, call, _ = _daemon({"error": "Failed to read diary entries"})
        with strict, call:
            with pytest.raises(SystemExit) as exc:
                cli.cmd_diary(_read_args())

        assert exc.value.code == 2
        assert "Failed to read diary entries" in capsys.readouterr().err

    def test_local_path_calls_tool_function(self):
        from mempalace import cli

        with (
            patch("mempalace.cli._daemon_strict", return_value=False),
            patch("mempalace.mcp_server.tool_diary_read", return_value=_READ_OK) as local,
        ):
            cli.cmd_diary(_read_args(limit=4))

        local.assert_called_once_with(agent_name="morpheus", last_n=4)

    def test_json_survives_the_mcp_server_stdout_hijack(self, capsys):
        """The local path must not lose stdout to mcp_server's stdio guard.

        ``mempalace.mcp_server`` installs MCP-stdio protection at module
        scope (#225) — ``os.dup2(2, 1)`` and ``sys.stdout = sys.stderr`` —
        and only its own ``main()`` undoes it. Dropping the module from
        ``sys.modules`` makes the local path's import genuinely re-run that
        hijack, so this asserts the routing helper restores stdout before
        the command prints: ``diary read --json`` is exactly what a caller
        pipes, and on stderr it is invisible to them.
        """
        import sys

        from mempalace import cli

        saved_module = sys.modules.pop("mempalace.mcp_server", None)
        saved_stdout = sys.stdout
        try:
            # Importing here is what hijacks stdout for the rest of this test.
            import mempalace.mcp_server  # noqa: F401

            with (
                patch("mempalace.cli._daemon_strict", return_value=False),
                patch("mempalace.mcp_server.tool_diary_read", return_value=_READ_OK),
            ):
                cli.cmd_diary(_read_args(json=True))
        finally:
            sys.stdout = saved_stdout
            if saved_module is not None:
                # Both handles matter: ``patch("mempalace.mcp_server.X")``
                # resolves through sys.modules while ``from . import
                # mcp_server`` reads the package attribute. Leave only one
                # of them pointing at the freshly-imported copy and later
                # tests patch a module the CLI never sees.
                sys.modules["mempalace.mcp_server"] = saved_module
                import mempalace

                mempalace.mcp_server = saved_module

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["agent"] == "morpheus"
        assert "morpheus" not in captured.err

    def test_import_helper_restores_a_hijacked_stdout(self):
        from mempalace import cli

        real = __import__("sys").stdout
        sys_mod = __import__("sys")
        sys_mod.stdout = sys_mod.stderr
        try:
            module = cli._import_mcp_server()
            assert sys_mod.stdout is not sys_mod.stderr
            assert hasattr(module, "tool_diary_read")
            # Idempotent: a second call must not raise on the already-closed
            # duplicate file descriptor.
            cli._import_mcp_server()
            assert sys_mod.stdout is not sys_mod.stderr
        finally:
            sys_mod.stdout = real

    def test_palace_flag_forces_local_path(self, tmp_path):
        from mempalace import cli

        with (
            # --palace seeds MEMPALACE_PALACE_PATH; keep that out of the
            # rest of the session's environment.
            patch.dict("os.environ", {}),
            patch("mempalace.cli._daemon_strict", return_value=True),
            patch("mempalace.cli._call_daemon_tool") as daemon_call,
            patch("mempalace.mcp_server.tool_diary_read", return_value=_READ_OK) as local,
        ):
            cli.cmd_diary(_read_args(palace=str(tmp_path)))

        assert daemon_call.call_count == 0
        assert local.call_count == 1
