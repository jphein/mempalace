"""Tests for the palace-auto-query.sh UserPromptSubmit hook (shell).

The hook derives the per-session turn counter from the session id. It must
read the id the harness actually provides (stdin JSON ``session_id`` /
``CLAUDE_CODE_SESSION_ID``) rather than the never-set ``CLAUDE_SESSION_ID``,
whose time-based fallback minted a new id every prompt and froze the counter
at 1 — disabling the periodic depth signal entirely.
"""

import json
import os
import shutil
import subprocess

import pytest

HOOK = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "hooks", "palace-auto-query.sh")
)

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None,
    reason="hook requires bash + jq",
)


def _run(stdin_obj):
    return subprocess.run(
        ["bash", HOOK],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
    )


class TestSessionIdFromStdin:
    def test_turn_counter_keyed_by_stdin_session_id(self):
        sid = "unit-test-sid-0001"
        turn_file = "/tmp/palace-aq-turn-{}".format(sid)
        with open(turn_file, "w") as f:
            f.write("5")
        try:
            # Lowercase, no recall words, no capitals, turn 6 (not 1, not a
            # multiple of 10) -> the pre-filter skips, so Python/daemon never
            # runs and the counter increment is the only observable effect.
            r = _run({"prompt": "ok thanks", "cwd": "/tmp", "session_id": sid})
            assert r.returncode == 0
            with open(turn_file) as f:
                assert f.read().strip() == "6"
        finally:
            if os.path.exists(turn_file):
                os.remove(turn_file)
