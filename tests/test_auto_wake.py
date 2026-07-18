"""Tests for wake-on-demand (mempalace.auto_wake + MempalaceConfig.auto_wake).

The palace daemon may live on a suspend-to-RAM host where "connection
refused" routinely means "asleep". With ``auto_wake`` configured, the
CLI's daemon calls run a user-supplied wake command, poll ``/health``,
and retry once. These tests cover the config normalization (fail-open
to *off*), the failure-classification gate (HTTP errors from a live
daemon never wake; a proxy's 502/504 for a sleeping upstream does),
the retry path, and the once-per-process guard.
"""

import json
import urllib.error

import pytest

from mempalace import auto_wake
from mempalace.config import MempalaceConfig


@pytest.fixture(autouse=True)
def _reset_attempt_state(monkeypatch):
    """Each test starts with the once-per-process guard cleared."""
    monkeypatch.setattr(auto_wake, "_attempted", False)


@pytest.fixture
def config_dir(tmp_path):
    def _write(payload):
        (tmp_path / "config.json").write_text(json.dumps(payload))
        return tmp_path

    return _write


# ---------------------------------------------------------------- config


class TestAutoWakeConfig:
    def test_disabled_when_key_absent(self, config_dir):
        cfg = MempalaceConfig(config_dir=config_dir({}))
        assert cfg.auto_wake is None

    def test_string_shorthand_normalizes_with_defaults(self, config_dir):
        cfg = MempalaceConfig(config_dir=config_dir({"auto_wake": "wakeonlan aa:bb"}))
        settings = cfg.auto_wake
        assert settings["command"] == "wakeonlan aa:bb"
        assert settings["timeout_seconds"] == 45.0
        assert settings["poll_interval_seconds"] == 2.0

    def test_dict_form_with_bounds_clamped(self, config_dir):
        cfg = MempalaceConfig(
            config_dir=config_dir(
                {
                    "auto_wake": {
                        "command": "  ./wake.sh  ",
                        "timeout_seconds": 9999,
                        "poll_interval_seconds": 0.01,
                    }
                }
            )
        )
        settings = cfg.auto_wake
        assert settings["command"] == "./wake.sh"
        assert settings["timeout_seconds"] == 300.0
        assert settings["poll_interval_seconds"] == 0.5

    @pytest.mark.parametrize(
        "raw",
        [None, True, 42, [], {"command": ""}, {"command": "   "}, {"command": 7}, {}],
    )
    def test_garbage_or_empty_command_disables(self, config_dir, raw):
        cfg = MempalaceConfig(config_dir=config_dir({"auto_wake": raw}))
        assert cfg.auto_wake is None

    def test_garbage_numbers_fall_back_to_defaults(self, config_dir):
        cfg = MempalaceConfig(
            config_dir=config_dir({"auto_wake": {"command": "w", "timeout_seconds": "soon"}})
        )
        assert cfg.auto_wake["timeout_seconds"] == 45.0

    @pytest.mark.parametrize("env_val", ["0", "false", "NO"])
    def test_env_escape_hatch_force_disables(self, config_dir, monkeypatch, env_val):
        monkeypatch.setenv("PALACE_AUTO_WAKE", env_val)
        cfg = MempalaceConfig(config_dir=config_dir({"auto_wake": "wake"}))
        assert cfg.auto_wake is None

    def test_env_other_values_leave_config_active(self, config_dir, monkeypatch):
        monkeypatch.setenv("PALACE_AUTO_WAKE", "1")
        cfg = MempalaceConfig(config_dir=config_dir({"auto_wake": "wake"}))
        assert cfg.auto_wake is not None


# ------------------------------------------------------- classification


class TestWakeEligibility:
    @pytest.mark.parametrize("code", [404, 500, 503])
    def test_http_error_from_live_daemon_is_not_eligible(self, code):
        # 503 stays ineligible on purpose: the daemon itself emits it under
        # crash-loop protection, and a spurious wake would stall the CLI for
        # the full poll deadline against an already-awake host.
        err = urllib.error.HTTPError("http://d", code, "nope", {}, None)
        assert not auto_wake._is_wake_eligible(err)

    @pytest.mark.parametrize("code", [502, 504])
    def test_proxy_upstream_down_statuses_are_eligible(self, code):
        # A forward/reverse proxy between the CLI and the palace host
        # answers for a sleeping upstream with its OWN 502/504 — the exact
        # asleep case auto_wake exists for, disguised as an HTTP response.
        err = urllib.error.HTTPError("http://d", code, "bad gateway", {}, None)
        assert auto_wake._is_wake_eligible(err)

    @pytest.mark.parametrize(
        "exc",
        [
            urllib.error.URLError(ConnectionRefusedError(111, "refused")),
            ConnectionRefusedError(111, "refused"),
            OSError(113, "No route to host"),
            TimeoutError("timed out"),
        ],
    )
    def test_connection_level_failures_are_eligible(self, exc):
        assert auto_wake._is_wake_eligible(exc)

    def test_value_error_is_not_eligible(self):
        assert not auto_wake._is_wake_eligible(ValueError("boom"))


# ------------------------------------------------------------ attempt


_SETTINGS = {"command": "wake", "timeout_seconds": 10.0, "poll_interval_seconds": 0.5}


class TestAttemptWake:
    def test_success_runs_command_then_polls_until_healthy(self, monkeypatch):
        ran = []
        health = iter([False, False, True])
        monkeypatch.setattr(auto_wake, "_run_wake_command", lambda c: ran.append(c) or True)
        monkeypatch.setattr(auto_wake, "_daemon_healthy", lambda url, timeout=3.0: next(health))
        monkeypatch.setattr(auto_wake.time, "sleep", lambda s: None)

        assert auto_wake.attempt_wake("http://d", _SETTINGS) is True
        assert ran == ["wake"]

    def test_failed_command_short_circuits(self, monkeypatch):
        monkeypatch.setattr(auto_wake, "_run_wake_command", lambda c: False)
        polls = []
        monkeypatch.setattr(
            auto_wake, "_daemon_healthy", lambda url, timeout=3.0: polls.append(url) or False
        )
        assert auto_wake.attempt_wake("http://d", _SETTINGS) is False
        assert polls == []

    def test_deadline_expiry_returns_false(self, monkeypatch):
        monkeypatch.setattr(auto_wake, "_run_wake_command", lambda c: True)
        monkeypatch.setattr(auto_wake, "_daemon_healthy", lambda url, timeout=3.0: False)
        monkeypatch.setattr(auto_wake.time, "sleep", lambda s: None)
        clock = iter(range(0, 100))
        monkeypatch.setattr(auto_wake.time, "monotonic", lambda: float(next(clock)))
        assert auto_wake.attempt_wake("http://d", _SETTINGS) is False

    def test_only_one_attempt_per_process(self, monkeypatch):
        calls = []
        monkeypatch.setattr(auto_wake, "_run_wake_command", lambda c: calls.append(c) or False)
        assert auto_wake.attempt_wake("http://d", _SETTINGS) is False
        assert auto_wake.attempt_wake("http://d", _SETTINGS) is False
        assert calls == ["wake"], "second attempt must not re-run the wake command"


# --------------------------------------------------- urlopen_with_wake


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestUrlopenWithWake:
    def _patch_config(self, monkeypatch, settings, daemon_url="http://d"):
        class _Cfg:
            auto_wake = settings

            def __init__(self):
                self.daemon_url = daemon_url

        monkeypatch.setattr("mempalace.config.MempalaceConfig", _Cfg)

    def test_success_passes_through_without_config_read(self, monkeypatch):
        monkeypatch.setattr(
            auto_wake.urllib.request, "urlopen", lambda req, timeout: _FakeResponse()
        )
        with auto_wake.urlopen_with_wake("req", timeout=5) as resp:
            assert resp.status == 200

    def test_http_error_propagates_without_wake(self, monkeypatch):
        def _raise(req, timeout):
            raise urllib.error.HTTPError("http://d", 404, "nope", {}, None)

        monkeypatch.setattr(auto_wake.urllib.request, "urlopen", _raise)
        woke = []
        monkeypatch.setattr(auto_wake, "attempt_wake", lambda *a: woke.append(a) or True)
        with pytest.raises(urllib.error.HTTPError):
            auto_wake.urlopen_with_wake("req", timeout=5)
        assert woke == []

    def test_disabled_config_reraises_original(self, monkeypatch):
        self._patch_config(monkeypatch, settings=None)
        original = urllib.error.URLError(ConnectionRefusedError(111, "refused"))

        def _raise(req, timeout):
            raise original

        monkeypatch.setattr(auto_wake.urllib.request, "urlopen", _raise)
        with pytest.raises(urllib.error.URLError) as excinfo:
            auto_wake.urlopen_with_wake("req", timeout=5)
        assert excinfo.value is original

    def test_wake_success_retries_once(self, monkeypatch):
        self._patch_config(monkeypatch, settings=dict(_SETTINGS))
        attempts = []

        def _flaky(req, timeout):
            attempts.append(req)
            if len(attempts) == 1:
                raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
            return _FakeResponse()

        monkeypatch.setattr(auto_wake.urllib.request, "urlopen", _flaky)
        monkeypatch.setattr(auto_wake, "attempt_wake", lambda url, s: True)

        with auto_wake.urlopen_with_wake("req", timeout=5) as resp:
            assert resp.status == 200
        assert len(attempts) == 2

    def test_proxy_502_triggers_wake_and_retry(self, monkeypatch):
        self._patch_config(monkeypatch, settings=dict(_SETTINGS))
        attempts = []

        def _proxied(req, timeout):
            attempts.append(req)
            if len(attempts) == 1:
                raise urllib.error.HTTPError("http://d", 502, "Bad Gateway", {}, None)
            return _FakeResponse()

        monkeypatch.setattr(auto_wake.urllib.request, "urlopen", _proxied)
        monkeypatch.setattr(auto_wake, "attempt_wake", lambda url, s: True)

        with auto_wake.urlopen_with_wake("req", timeout=5) as resp:
            assert resp.status == 200
        assert len(attempts) == 2

    def test_wake_failure_reraises_original(self, monkeypatch):
        self._patch_config(monkeypatch, settings=dict(_SETTINGS))
        original = urllib.error.URLError(OSError(113, "No route to host"))

        def _raise(req, timeout):
            raise original

        monkeypatch.setattr(auto_wake.urllib.request, "urlopen", _raise)
        monkeypatch.setattr(auto_wake, "attempt_wake", lambda url, s: False)
        with pytest.raises(urllib.error.URLError) as excinfo:
            auto_wake.urlopen_with_wake("req", timeout=5)
        assert excinfo.value is original
