"""Write-path bounds for tags: per-tag length cap and tag-count cap.

Guards against unbounded tag length/count reaching the palace daemon
(LAN-reachable on 0.0.0.0 behind X-API-Key) — see techempower-org/mempalace#203.
"""

from __future__ import annotations

from mempalace.tags import (
    MAX_TAG_COUNT,
    MAX_TAG_LENGTH,
    normalise_tag,
    normalise_tags,
)


def test_long_tag_truncated_to_max_length():
    long_tag = "a" * (MAX_TAG_LENGTH + 50)
    result = normalise_tag(long_tag)
    assert result == "a" * MAX_TAG_LENGTH
    assert len(result) == MAX_TAG_LENGTH


def test_truncation_happens_after_char_class_cleaning():
    # Invalid chars are stripped first, then the cleaned value is truncated.
    # "!!!" is removed, leaving a long valid tail that should truncate to 128.
    raw = "!!!" + "b" * (MAX_TAG_LENGTH + 10)
    result = normalise_tag(raw)
    assert result == "b" * MAX_TAG_LENGTH
    assert len(result) == MAX_TAG_LENGTH


def test_truncation_counts_only_cleaned_chars():
    # A tag whose invalid chars are interspersed: only valid chars count
    # toward the length budget. Here 200 valid chars survive cleaning, then
    # get truncated to MAX_TAG_LENGTH.
    raw = "".join("c?" for _ in range(200))  # 200 'c' chars + 200 '?' stripped
    result = normalise_tag(raw)
    assert result == "c" * MAX_TAG_LENGTH
    assert len(result) == MAX_TAG_LENGTH


def test_tag_count_capped_order_preserving():
    tags = [f"tag-{i}" for i in range(MAX_TAG_COUNT + 20)]
    result = normalise_tags(tags)
    assert len(result) == MAX_TAG_COUNT
    assert result == [f"tag-{i}" for i in range(MAX_TAG_COUNT)]


def test_cap_counts_distinct_tags():
    # MAX_TAG_COUNT unique tags followed by MAX_TAG_COUNT duplicates of them
    # should yield exactly MAX_TAG_COUNT — dedup happens, then the cap.
    unique = [f"u{i}" for i in range(MAX_TAG_COUNT)]
    tags = unique + unique  # 128 entries, 64 distinct
    result = normalise_tags(tags)
    assert len(result) == MAX_TAG_COUNT
    assert result == unique


def test_exactly_max_count_distinct_all_kept():
    unique = [f"k{i}" for i in range(MAX_TAG_COUNT)]
    result = normalise_tags(unique)
    assert result == unique


def test_short_normal_tags_unaffected():
    tags = ["alpha", "beta", "project-x", "v1.2.3"]
    result = normalise_tags(tags)
    assert result == ["alpha", "beta", "project-x", "v1.2.3"]


def test_normal_tag_passes_through_normalise_tag():
    assert normalise_tag("Project X") == "project-x"
    assert normalise_tag("  Hello  ") == "hello"


def test_empty_and_none_return_empty_list():
    assert normalise_tags(None) == []
    assert normalise_tags([]) == []
