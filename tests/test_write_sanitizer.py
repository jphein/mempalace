"""
Tests for write_sanitizer.py — observation-grade write-path hygiene (#40).

Counterpart to test_query_sanitizer.py. Verifies:
  * Clean inputs pass through unchanged.
  * Control characters get stripped (NUL bytes, \\x01-\\x08, \\x0B-\\x0C, \\x0E-\\x1F, \\x7F).
  * Whitespace newline runs of 3+ collapse to 2.
  * Oversize content truncates at MAX_CONTENT_LENGTH with a flag.
  * Names truncate at MAX_NAME_LENGTH with a flag.
  * Empty / whitespace-only / non-string input surfaces an error.
  * Flag accounting is correct (one flag per sanitizer that fired).
"""

import pytest

from mempalace.write_sanitizer import (
    MAX_CONTENT_LENGTH,
    MAX_NAME_LENGTH,
    sanitize_write_content,
    sanitize_write_name,
)


class TestContentPassthrough:
    def test_short_clean_content(self):
        result = sanitize_write_content("hello world")
        assert result["cleaned"] == "hello world"
        assert result["was_sanitized"] is False
        assert result["flags"] == []
        assert result["error"] is None

    def test_newlines_and_tabs_preserved(self):
        text = "line one\nline two\tindented\rwith carriage"
        result = sanitize_write_content(text)
        assert result["cleaned"] == text
        assert result["was_sanitized"] is False

    def test_two_blank_lines_preserved(self):
        text = "para one\n\npara two"
        result = sanitize_write_content(text)
        assert result["cleaned"] == text
        assert result["was_sanitized"] is False


class TestControlCharStripping:
    def test_nul_byte_stripped(self):
        result = sanitize_write_content("before\x00after")
        assert result["cleaned"] == "beforeafter"
        assert result["was_sanitized"] is True
        assert "control_chars_stripped" in result["flags"]

    def test_form_feed_stripped(self):
        result = sanitize_write_content("a\x0cb")
        assert result["cleaned"] == "ab"
        assert "control_chars_stripped" in result["flags"]

    def test_del_stripped(self):
        result = sanitize_write_content("a\x7fb")
        assert result["cleaned"] == "ab"
        assert "control_chars_stripped" in result["flags"]

    def test_low_control_range_stripped(self):
        # \x01 through \x08 (skipping \t at \x09)
        for ch in [chr(i) for i in range(1, 9)]:
            result = sanitize_write_content(f"a{ch}b")
            assert result["cleaned"] == "ab", f"failed for {hex(ord(ch))}"
            assert "control_chars_stripped" in result["flags"]

    def test_high_control_range_stripped(self):
        # \x0E through \x1F
        for ch in [chr(i) for i in range(0x0E, 0x20)]:
            result = sanitize_write_content(f"a{ch}b")
            assert result["cleaned"] == "ab", f"failed for {hex(ord(ch))}"
            assert "control_chars_stripped" in result["flags"]

    def test_tab_newline_cr_NOT_stripped(self):
        for ch in ["\t", "\n", "\r"]:
            result = sanitize_write_content(f"a{ch}b")
            assert result["cleaned"] == f"a{ch}b"
            assert "control_chars_stripped" not in result["flags"]


class TestWhitespaceNormalization:
    def test_three_blank_lines_collapsed(self):
        result = sanitize_write_content("a\n\n\n\nb")
        assert result["cleaned"] == "a\n\nb"
        assert "whitespace_normalized" in result["flags"]

    def test_many_blank_lines_collapsed(self):
        result = sanitize_write_content("a" + "\n" * 20 + "b")
        assert result["cleaned"] == "a\n\nb"
        assert "whitespace_normalized" in result["flags"]

    def test_exact_two_newlines_untouched(self):
        result = sanitize_write_content("a\n\nb")
        assert result["cleaned"] == "a\n\nb"
        assert "whitespace_normalized" not in result["flags"]


class TestTruncation:
    def test_content_under_limit_unchanged(self):
        text = "x" * (MAX_CONTENT_LENGTH - 1)
        result = sanitize_write_content(text)
        assert result["cleaned_length"] == MAX_CONTENT_LENGTH - 1
        assert "truncated" not in result["flags"]

    def test_content_at_limit_unchanged(self):
        text = "x" * MAX_CONTENT_LENGTH
        result = sanitize_write_content(text)
        assert result["cleaned_length"] == MAX_CONTENT_LENGTH
        assert "truncated" not in result["flags"]

    def test_content_over_limit_truncated(self):
        text = "x" * (MAX_CONTENT_LENGTH + 100)
        result = sanitize_write_content(text)
        assert result["cleaned_length"] == MAX_CONTENT_LENGTH
        assert result["was_sanitized"] is True
        assert "truncated" in result["flags"]
        assert result["original_length"] == MAX_CONTENT_LENGTH + 100


class TestContentErrors:
    def test_empty_string(self):
        result = sanitize_write_content("")
        assert result["error"] is not None
        assert result["cleaned"] == ""

    def test_whitespace_only(self):
        result = sanitize_write_content("   \n\t  ")
        assert result["error"] is not None

    def test_non_string_input(self):
        result = sanitize_write_content(None)
        assert result["error"] is not None
        result = sanitize_write_content(123)
        assert result["error"] is not None

    def test_only_control_chars_becomes_empty(self):
        # If a string is *only* stripped control chars + whitespace,
        # the cleaned output is empty → error.
        result = sanitize_write_content("\x00\x01\x02   ")
        assert result["error"] is not None
        assert "control_chars_stripped" in result["flags"]


class TestMultipleFlags:
    def test_control_chars_and_whitespace(self):
        result = sanitize_write_content("a\x00b\n\n\n\nc")
        assert result["cleaned"] == "ab\n\nc"
        assert "control_chars_stripped" in result["flags"]
        assert "whitespace_normalized" in result["flags"]

    def test_all_three_flags_fire(self):
        text = "a\x00b\n\n\n\nc" + "x" * MAX_CONTENT_LENGTH
        result = sanitize_write_content(text)
        assert "control_chars_stripped" in result["flags"]
        assert "whitespace_normalized" in result["flags"]
        assert "truncated" in result["flags"]
        assert result["cleaned_length"] == MAX_CONTENT_LENGTH


class TestNameSanitization:
    def test_clean_name_passthrough(self):
        result = sanitize_write_name("wing_jp", "wing")
        assert result["cleaned"] == "wing_jp"
        assert result["was_sanitized"] is False
        assert result["error"] is None

    def test_name_nul_stripped(self):
        result = sanitize_write_name("wing\x00jp", "wing")
        assert result["cleaned"] == "wingjp"
        assert "control_chars_stripped" in result["flags"]

    def test_name_truncated(self):
        long = "x" * (MAX_NAME_LENGTH + 50)
        result = sanitize_write_name(long, "wing")
        assert result["cleaned_length"] == MAX_NAME_LENGTH
        assert "truncated" in result["flags"]

    def test_empty_name_errors(self):
        result = sanitize_write_name("", "wing")
        assert result["error"] is not None
        assert "wing" in result["error"]

    def test_non_string_name_errors(self):
        result = sanitize_write_name(None, "wing")
        assert result["error"] is not None
        assert "wing" in result["error"]

    def test_name_under_limit_unchanged(self):
        name = "x" * MAX_NAME_LENGTH
        result = sanitize_write_name(name, "wing")
        assert result["cleaned_length"] == MAX_NAME_LENGTH
        assert "truncated" not in result["flags"]


class TestResultShape:
    def test_all_required_keys_present(self):
        result = sanitize_write_content("hello")
        expected = {
            "cleaned",
            "was_sanitized",
            "flags",
            "original_length",
            "cleaned_length",
            "error",
        }
        assert set(result.keys()) == expected

    def test_name_result_shape_matches(self):
        result = sanitize_write_name("hello", "wing")
        expected = {
            "cleaned",
            "was_sanitized",
            "flags",
            "original_length",
            "cleaned_length",
            "error",
        }
        assert set(result.keys()) == expected

    def test_lengths_recorded(self):
        result = sanitize_write_content("hello world")
        assert result["original_length"] == 11
        assert result["cleaned_length"] == 11

    def test_lengths_after_strip(self):
        result = sanitize_write_content("a\x00b")
        assert result["original_length"] == 3
        assert result["cleaned_length"] == 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hi", "hi"),
        ("hi\x00", "hi"),
        ("hi\n\n\n\nthere", "hi\n\nthere"),
        ("\x01hello\x02world\x03", "helloworld"),
    ],
)
def test_parametrized_content_cleanup(raw, expected):
    result = sanitize_write_content(raw)
    assert result["cleaned"] == expected
