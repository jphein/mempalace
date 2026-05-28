"""Tests for scripts/maintain-fork-changes.py.

Covers the two passes the script provides (#316): HEAD-placeholder
resolution by referenced #NN and id-based de-duplication. The script
loads via ``importlib`` like ``tests/test_render_docs.py`` because
the dotted filename can't be imported normally.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "maintain-fork-changes.py"


@pytest.fixture(scope="module")
def mfc():
    spec = importlib.util.spec_from_file_location("_maintain_fork_changes", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── dedup_by_id ────────────────────────────────────────────────────────


def test_dedup_drops_second_occurrence_of_same_id(mfc):
    """The rebase-chain pattern: same entry inserted twice keeps the first."""
    lines = [
        "entries:\n",
        "\n",
        "  - id: my-feature\n",
        "    bucket: Added\n",
        "    summary: first\n",
        "\n",
        "  - id: my-feature\n",
        "    bucket: Added\n",
        "    summary: second-duplicate\n",
        "\n",
        "  - id: other\n",
        "    bucket: Fixed\n",
        "    summary: keep\n",
    ]
    new, dropped = mfc.dedup_by_id(lines)
    new_text = "".join(new)
    assert dropped == ["my-feature"]
    assert "first" in new_text
    assert "second-duplicate" not in new_text
    assert "other" in new_text


def test_dedup_is_idempotent_on_clean_yaml(mfc):
    lines = [
        "entries:\n",
        "\n",
        "  - id: a\n",
        "    bucket: Added\n",
        "\n",
        "  - id: b\n",
        "    bucket: Fixed\n",
    ]
    new, dropped = mfc.dedup_by_id(lines)
    assert dropped == []
    assert "".join(new) == "".join(lines)


def test_dedup_handles_three_copies(mfc):
    lines = [
        "  - id: x\n",
        "    summary: one\n",
        "  - id: x\n",
        "    summary: two\n",
        "  - id: x\n",
        "    summary: three\n",
    ]
    new, dropped = mfc.dedup_by_id(lines)
    assert dropped == ["x", "x"]
    assert "one" in "".join(new)
    assert "two" not in "".join(new)
    assert "three" not in "".join(new)


# ── resolve_head_placeholders ──────────────────────────────────────────


def test_resolve_head_replaces_when_issue_reference_found(mfc):
    lines = [
        "  - id: my-feature\n",
        "    bucket: Added\n",
        "    commit: HEAD\n",
        "    area: MCP\n",
        '    summary: "fixes the thing (#123)"\n',
    ]
    issue_to_sha = {"123": "abc1234"}
    new, resolved = mfc.resolve_head_placeholders(lines, issue_to_sha)
    new_text = "".join(new)
    assert "    commit: abc1234\n" in new_text
    assert "commit: HEAD" not in new_text
    assert resolved == [("abc1234", "123")]


def test_resolve_head_leaves_placeholder_when_no_match(mfc):
    lines = [
        "  - id: unknown\n",
        "    commit: HEAD\n",
        '    summary: "references no issues"\n',
    ]
    new, resolved = mfc.resolve_head_placeholders(lines, {"99": "deadbee"})
    assert resolved == []
    assert "".join(new) == "".join(lines)


def test_resolve_head_dry_run_does_not_rewrite(mfc):
    lines = [
        "    commit: HEAD\n",
        '    summary: "closes #5"\n',
    ]
    new, resolved = mfc.resolve_head_placeholders(lines, {"5": "f00ba12"}, dry_run=True)
    assert resolved == [("f00ba12", "5")]
    assert "".join(new) == "".join(lines)


def test_resolve_head_picks_first_resolvable_issue_in_window(mfc):
    """When the entry mentions multiple #NN within the 12-line lookahead, the
    first one that resolves wins (closes/fixes have already been ranked
    higher in build_issue_to_sha)."""
    lines = [
        "  - id: x\n",
        "    commit: HEAD\n",
        '    summary: "foo (#101)"\n',
        '    body: "see also #102 and #103"\n',
    ]
    # Only #102 maps — so we should pick its sha even though #101 appears first
    issue_to_sha = {"102": "bbbbbbb"}
    new, resolved = mfc.resolve_head_placeholders(lines, issue_to_sha)
    assert resolved == [("bbbbbbb", "102")]
    assert "commit: bbbbbbb\n" in "".join(new)


def test_resolve_head_first_resolvable_wins_when_multiple_match(mfc):
    lines = [
        "  - id: x\n",
        "    commit: HEAD\n",
        '    summary: "foo (#101) closes #102"\n',
    ]
    issue_to_sha = {"101": "aaaaaaa", "102": "bbbbbbb"}
    new, resolved = mfc.resolve_head_placeholders(lines, issue_to_sha)
    # #101 appears earlier in the line and resolves, so it wins
    assert resolved == [("aaaaaaa", "101")]


def test_resolve_head_indentation_preserved(mfc):
    lines = [
        "  - id: y\n",
        "    commit:   HEAD\n",
        '    summary: "foo (#7)"\n',
    ]
    issue_to_sha = {"7": "ccccccc"}
    new, _ = mfc.resolve_head_placeholders(lines, issue_to_sha)
    # The regex captures the "    commit:   " prefix; verify it's preserved
    assert "    commit:   ccccccc\n" in "".join(new)


# ── build_issue_to_sha ─────────────────────────────────────────────────


def test_build_issue_to_sha_resolves_closing_phrase(mfc, monkeypatch, capfd):
    """build_issue_to_sha walks git log; mock subprocess.check_output to feed
    a synthetic log and verify priority ordering."""
    sample = (
        # PR #200 closes #100 — priority 2, should win
        "abc1234deadbee\x00fix(thing): summary (#200)\x00Closes #100\x01"
        # Another commit later mentions #100 but only via subject paren
        "999deadbeef0000\x00chore: bump deps (#100)\x00body without explicit close\x01"
    )

    import subprocess as real_sp

    monkeypatch.setattr(
        real_sp,
        "check_output",
        lambda *args, **kwargs: sample,
    )
    monkeypatch.setattr(mfc.subprocess, "check_output", lambda *a, **k: sample)

    mapping = mfc.build_issue_to_sha("origin/main", 50)
    # #100: priority 2 (closes) should beat priority 1 (subject paren)
    # Whichever sha was offered first at priority 2 wins
    assert mapping["100"] == "abc1234"
    # #200: only appeared in subject of first commit as "(#200)" — priority 1
    assert mapping["200"] == "abc1234"
