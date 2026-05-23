"""Tests for mempalace.auto_query.runner — pipeline orchestrator."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import pytest

from mempalace.auto_query.decisions import read_decisions
from mempalace.auto_query.runner import run_auto_query
from mempalace.config import MempalaceConfig


def _make_config(tmp_path, auto_query=None, daemon_url=None):
    """Create a MempalaceConfig with auto_query settings in a temp dir."""
    cfg = {}
    if auto_query:
        cfg["auto_query"] = auto_query
    if daemon_url:
        cfg["daemon_url"] = daemon_url
    config_dir = str(tmp_path / "config")
    import os

    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump(cfg, f)
    return MempalaceConfig(config_dir=config_dir)


# ── disabled / off ──────────────────────────────────────────────


class TestDisabled:
    def test_not_enabled_returns_empty(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": False, "mode": "balanced"})
        result = run_auto_query(
            prompt="tell me about MemPalace",
            session_id="s1",
            turn=1,
            config=config,
            log_dir=str(tmp_path / "log"),
        )
        assert result.injection is None
        assert result.decision is None

    def test_mode_off_returns_empty(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": True, "mode": "off"})
        result = run_auto_query(
            prompt="tell me about MemPalace",
            session_id="s1",
            turn=1,
            config=config,
            log_dir=str(tmp_path / "log"),
        )
        assert result.injection is None
        assert result.decision is None


# ── skip (below threshold) ──────────────────────────────────────


class TestSkipBelowThreshold:
    def test_low_score_skips(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": True, "mode": "conservative"})
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="hello world",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.injection is None
        assert result.decision is not None
        assert result.decision.decision == "skip"

    def test_skip_is_logged(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": True, "mode": "conservative"})
        log_dir = str(tmp_path / "log")
        run_auto_query(
            prompt="hello",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        entries = read_decisions(log_dir=log_dir)
        assert len(entries) == 1
        assert entries[0]["decision"] == "skip"


# ── dry-run mode ────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_logs_would_fire(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": True, "mode": "dry-run"})
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="remind me what we did with metadata reshape?",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.injection is None
        assert result.decision is not None
        assert result.decision.decision == "dry-run-skip"
        assert result.decision.tool == "mempalace_search"
        assert result.tool_call is not None

    def test_dry_run_no_mcp_call(self, tmp_path):
        config = _make_config(tmp_path, auto_query={"enabled": True, "mode": "dry-run"})
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="remind me what we did with metadata reshape?",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.mcp_result is None


# ── live fire (mocked daemon) ───────────────────────────────────


class _MockDaemonHandler(BaseHTTPRequestHandler):
    """Minimal palace-daemon /mcp mock."""

    response_body = {}  # type: dict

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_len)

        rpc_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(self.response_body)}]},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(rpc_response).encode())

    def log_message(self, format, *args):
        pass  # suppress stderr


@pytest.fixture()
def mock_daemon():
    """Start a local HTTP server mimicking the palace-daemon /mcp endpoint."""
    server = HTTPServer(("127.0.0.1", 0), _MockDaemonHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, port
    server.shutdown()


class TestLiveFire:
    def test_fire_with_search_results(self, tmp_path, mock_daemon):
        server, port = mock_daemon
        _MockDaemonHandler.response_body = {
            "results": [
                {
                    "wing": "wing_mempalace",
                    "room": "decisions",
                    "drawer_id": "d-001",
                    "text": "auto-query decision logging",
                    "created_at": "2026-05-22",
                },
            ]
        }
        config = _make_config(
            tmp_path,
            auto_query={"enabled": True, "mode": "balanced"},
            daemon_url="http://127.0.0.1:{}".format(port),
        )
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="remind me what we did with metadata reshape?",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.injection is not None
        assert "[mempalace:auto-query]" in result.injection
        assert result.decision.decision == "fire"
        assert result.decision.result_drawers == 1
        assert result.decision.latency_ms >= 0

    def test_fire_with_no_results_skips(self, tmp_path, mock_daemon):
        server, port = mock_daemon
        _MockDaemonHandler.response_body = {"results": []}
        config = _make_config(
            tmp_path,
            auto_query={"enabled": True, "mode": "balanced"},
            daemon_url="http://127.0.0.1:{}".format(port),
        )
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="remind me what we did with metadata reshape?",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.injection is None
        assert result.decision.decision == "skip"
        assert result.decision.reason == "no results from MCP"

    def test_daemon_unreachable_logs_skip(self, tmp_path):
        config = _make_config(
            tmp_path,
            auto_query={"enabled": True, "mode": "balanced"},
            daemon_url="http://127.0.0.1:1",
        )
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="remind me what we did with metadata reshape?",
            session_id="s1",
            turn=2,
            config=config,
            log_dir=log_dir,
        )
        assert result.injection is None
        assert result.decision.decision == "skip"
        assert result.decision.reason == "daemon unreachable"


# ── entity deduplication ────────────────────────────────────────


class TestEntityDedup:
    def test_queried_entities_passed_through(self, tmp_path, mock_daemon):
        server, port = mock_daemon
        _MockDaemonHandler.response_body = {
            "results": [{"wing": "w", "room": "r", "drawer_id": "d-1", "text": "x"}]
        }
        config = _make_config(
            tmp_path,
            auto_query={"enabled": True, "mode": "aggressive"},
            daemon_url="http://127.0.0.1:{}".format(port),
        )
        log_dir = str(tmp_path / "log")
        # First call: entity fires.
        result1 = run_auto_query(
            prompt="What about MemPalace?",
            session_id="s1",
            turn=2,
            config=config,
            known_wings={"wing_mempalace"},
            queried_entities=set(),
            log_dir=log_dir,
        )
        # Collect queried entities from result1 signals.
        fired_entities = set()
        if result1.decision:
            for ent in result1.decision.signals.get("entity", []):
                fired_entities.add(ent["name"])

        # Second call: pass fired entities as already-queried.
        result2 = run_auto_query(
            prompt="What about MemPalace?",
            session_id="s1",
            turn=3,
            config=config,
            known_wings={"wing_mempalace"},
            queried_entities=fired_entities,
            log_dir=log_dir,
        )
        # Entity was already queried; score should be lower.
        if result2.decision:
            entity_signals = result2.decision.signals.get("entity", [])
            entity_names = [e["name"] for e in entity_signals]
            assert "MemPalace" not in entity_names


# ── resumption signal ───────────────────────────────────────────


class TestResumption:
    def test_resumption_on_turn_1(self, tmp_path, mock_daemon):
        server, port = mock_daemon
        _MockDaemonHandler.response_body = {
            "entries": [
                {"topic": "general", "timestamp": "2026-05-22", "entry": "worked on router"}
            ]
        }
        config = _make_config(
            tmp_path,
            auto_query={"enabled": True, "mode": "balanced"},
            daemon_url="http://127.0.0.1:{}".format(port),
        )
        log_dir = str(tmp_path / "log")
        result = run_auto_query(
            prompt="let's continue",
            session_id="s1",
            turn=1,
            project_wing="wing_mempalace",
            known_wings={"wing_mempalace"},
            has_recent_drawers=True,
            config=config,
            log_dir=log_dir,
        )
        assert result.decision is not None
        assert result.decision.tool == "mempalace_diary_read"


# ── config properties ──────────────────────────────────────────


class TestConfigProperties:
    def test_defaults(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.auto_query_enabled is False
        assert config.auto_query_mode == "off"
        assert config.auto_query_max_per_turn == 1
        assert config.auto_query_max_per_minute == 6

    def test_config_file_values(self, tmp_path):
        config = _make_config(
            tmp_path,
            auto_query={
                "enabled": True,
                "mode": "conservative",
                "max_per_turn": 2,
                "max_per_minute": 10,
            },
        )
        assert config.auto_query_enabled is True
        assert config.auto_query_mode == "conservative"
        assert config.auto_query_max_per_turn == 2
        assert config.auto_query_max_per_minute == 10

    def test_env_overrides_config(self, tmp_path, monkeypatch):
        config = _make_config(
            tmp_path,
            auto_query={"enabled": False, "mode": "off"},
        )
        monkeypatch.setenv("AUTO_QUERY_ENABLED", "1")
        monkeypatch.setenv("AUTO_QUERY_MODE", "aggressive")
        monkeypatch.setenv("AUTO_QUERY_MAX_PER_TURN", "3")
        monkeypatch.setenv("AUTO_QUERY_MAX_PER_MINUTE", "12")
        assert config.auto_query_enabled is True
        assert config.auto_query_mode == "aggressive"
        assert config.auto_query_max_per_turn == 3
        assert config.auto_query_max_per_minute == 12
