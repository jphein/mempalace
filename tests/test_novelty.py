"""Tests for mempalace.novelty — gzip-based novelty scoring."""

import time

import pytest

from mempalace.novelty import classify_novelty, ncd, novelty_score


# ── ncd ───────────────────────────────────────────────────────────────


def test_ncd_identical_strings_is_near_zero():
    text = "Riley started Year 7 at Lincoln Middle School on 2026-09-01."
    score = ncd(text, text)
    # Gzip framing means even identical inputs aren't exactly 0; tolerate a
    # small positive value but ensure it's tightly clustered near 0.
    assert score < 0.1, f"identical strings scored {score}, expected near 0"


def test_ncd_completely_different_strings_is_high():
    a = "The quick brown fox jumps over the lazy dog. " * 20
    b = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20
    score = ncd(a, b)
    assert score > 0.5, f"different strings scored {score}, expected > 0.5"


def test_novelty_score_treats_repeated_ack_as_low_novelty():
    # The True Memory paper's key insight: cosine flags "ok" as
    # *novel* because it's semantically distant from factual content,
    # but it's actually low-novelty filler we don't want to surface.
    # NCD with a realistic window catches this: once "ok" appears in
    # history, the next "ok" should score very low novelty even though
    # the surrounding factual drawers are semantically distant from it.
    history = [
        "Riley started Year 7 at Lincoln Middle School on 2026-09-01.",
        "ok",
        "Riley joined the cross-country team on 2026-09-05.",
    ]
    ack_score = novelty_score("ok", history)
    factual_score = novelty_score(
        "Bought a 1972 Pearson 30 sailboat for the Chesapeake on 2026-04-12.",
        history,
    )
    assert ack_score < factual_score, (
        f"expected repeated 'ok' ({ack_score:.3f}) to score lower novelty "
        f"than a new factual update ({factual_score:.3f}) — that's the gzip "
        f"insight from the True Memory paper"
    )


def test_ncd_symmetric_within_tolerance():
    # NCD is not strictly symmetric because compression of (a+b) and
    # (b+a) can differ, but the asymmetry should be small.
    a = "The mitochondrion is the powerhouse of the cell."
    b = "Photosynthesis converts light energy into chemical energy."
    forward = ncd(a, b)
    reverse = ncd(b, a)
    assert abs(forward - reverse) < 0.1


def test_ncd_repeated_content_is_low_novelty():
    # Concatenating a string with itself should compress almost as
    # well as one copy, yielding a very low NCD.
    text = "Project Phoenix Q3 review notes: revenue up 12%, churn flat. "
    score = ncd(text, text * 2)
    assert score < 0.3, f"self-repeating content scored {score}, expected < 0.3"


# ── ncd: edge cases ──────────────────────────────────────────────────


def test_ncd_both_empty():
    assert ncd("", "") == 0.0


def test_ncd_one_empty():
    assert ncd("", "hello") == 1.0
    assert ncd("hello", "") == 1.0


def test_ncd_single_char():
    # Should not crash; gzip overhead dominates so the value isn't
    # meaningful, but the function must remain defined.
    score = ncd("a", "b")
    assert isinstance(score, float)
    assert score >= 0.0


def test_ncd_very_long_strings():
    # 100 KB inputs — make sure we don't blow up on size.
    a = ("alpha " * 20_000).strip()
    b = ("beta " * 20_000).strip()
    score = ncd(a, b)
    assert 0.0 <= score <= 1.5


# ── novelty_score ─────────────────────────────────────────────────────


def test_novelty_score_empty_window_is_fully_novel():
    # First drawer in a fresh wing has nothing to compare against; we
    # convention this as fully novel rather than fully redundant.
    assert novelty_score("anything goes here", []) == 1.0


def test_novelty_score_window_with_identical_text():
    text = "Riley started Year 7 at Lincoln Middle School on 2026-09-01."
    score = novelty_score(text, [text, text, text])
    assert score < 0.1, f"identical-window scored {score}, expected near 0"


def test_novelty_score_new_topic_beats_routine_update():
    history = [
        "Riley started Year 7 at Lincoln Middle School on 2026-09-01.",
        "Riley joined the cross-country team on 2026-09-05.",
        "Riley's first algebra test scored 92% on 2026-09-15.",
    ]
    novel = novelty_score(
        "Bought a new sailboat — a 1972 Pearson 30 for the Chesapeake.",
        history,
    )
    routine = novelty_score(
        "Riley's second algebra test scored 88% on 2026-09-22.",
        history,
    )
    assert novel > routine, (
        f"expected unrelated topic ({novel:.3f}) to outscore on-topic update ({routine:.3f})"
    )


def test_novelty_score_returns_mean():
    # Two-entry window: score should equal the mean of the two NCDs.
    a = "alpha one two three four five"
    b = "beta one two three four five"
    c = "gamma one two three four five"

    score = novelty_score(a, [b, c])
    expected = (ncd(a, b) + ncd(a, c)) / 2
    assert score == pytest.approx(expected)


# ── classify_novelty ──────────────────────────────────────────────────


def test_classify_novelty_default_threshold():
    assert classify_novelty(0.9) == "novel"
    assert classify_novelty(0.5) == "novel"  # boundary inclusive
    assert classify_novelty(0.4) == "routine"
    assert classify_novelty(0.25) == "routine"  # threshold/2 boundary inclusive
    assert classify_novelty(0.1) == "redundant"
    assert classify_novelty(0.0) == "redundant"


def test_classify_novelty_custom_threshold():
    # Stricter threshold — only highly novel content qualifies.
    assert classify_novelty(0.7, threshold=0.8) == "routine"
    assert classify_novelty(0.85, threshold=0.8) == "novel"


# ── performance budget ────────────────────────────────────────────────


def test_ncd_is_fast_on_short_strings():
    # CLAUDE.md performance budget: hooks under 500ms. Single NCD on
    # typical drawer-sized strings should be well under a millisecond.
    a = "The quick brown fox jumps over the lazy dog. " * 10
    b = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10

    start = time.perf_counter()
    for _ in range(100):
        ncd(a, b)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / 100) * 1000
    assert per_call_ms < 10, f"NCD averaged {per_call_ms:.2f}ms/call, expected < 10ms"
