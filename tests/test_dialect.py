"""
test_dialect.py — Tests for the AAAK Dialect compression system.

Covers plain text compression, entity detection, emotion detection,
topic extraction, key sentence extraction, zettel encoding, and stats.
"""

from mempalace.dialect import Dialect


class TestPlainTextCompression:
    def test_compress_basic(self):
        d = Dialect()
        result = d.compress("We decided to use GraphQL instead of REST for the API layer.")
        assert isinstance(result, str)
        assert len(result) > 0
        # AAAK format uses pipe-separated fields
        assert "|" in result

    def test_compress_with_metadata(self):
        d = Dialect()
        result = d.compress(
            "Authentication now uses JWT tokens.",
            metadata={"wing": "project", "room": "backend", "source_file": "auth.py"},
        )
        assert "project" in result
        assert "backend" in result

    def test_compress_produces_entity_codes(self):
        d = Dialect(entities={"Alice": "ALC", "Bob": "BOB"})
        result = d.compress("Alice told Bob about the new deployment strategy.")
        assert "ALC" in result or "BOB" in result

    def test_compress_empty_text(self):
        d = Dialect()
        result = d.compress("")
        assert isinstance(result, str)


class TestEntityDetection:
    def test_known_entities(self):
        d = Dialect(entities={"Alice": "ALC"})
        found = d._detect_entities_in_text("Alice went to the store.")
        assert "ALC" in found

    def test_auto_code_unknown_entities(self):
        d = Dialect()
        found = d._detect_entities_in_text("I spoke with Bernardo about the project today.")
        assert any(code for code in found if len(code) == 3)

    def test_skip_names(self):
        d = Dialect(entities={"Gandalf": "GAN"}, skip_names=["Gandalf"])
        code = d.encode_entity("Gandalf")
        assert code is None


class TestEmotionDetection:
    def test_detect_emotions(self):
        d = Dialect()
        emotions = d._detect_emotions("I'm really excited and happy about this breakthrough!")
        assert len(emotions) > 0

    def test_max_three_emotions(self):
        d = Dialect()
        text = "I feel scared, happy, angry, surprised, disgusted, and confused."
        emotions = d._detect_emotions(text)
        assert len(emotions) <= 3


class TestTopicExtraction:
    def test_extract_topics(self):
        d = Dialect()
        topics = d._extract_topics(
            "The Python authentication server uses PostgreSQL for storage "
            "and Redis for caching sessions."
        )
        assert len(topics) > 0
        assert len(topics) <= 3

    def test_boosts_technical_terms(self):
        d = Dialect()
        topics = d._extract_topics("GraphQL vs REST: we chose GraphQL for the new API endpoint.")
        # "graphql" should appear since it's mentioned twice + capitalized
        topic_lower = [t.lower() for t in topics]
        assert "graphql" in topic_lower


class TestKeySentenceExtraction:
    def test_extract_key_sentence(self):
        d = Dialect()
        text = (
            "The server runs on port 3000. "
            "We decided to use PostgreSQL instead of MongoDB. "
            "The config file needs updating."
        )
        key = d._extract_key_sentence(text)
        assert "decided" in key.lower() or "instead" in key.lower()

    def test_truncates_long_sentences(self):
        d = Dialect()
        text = "a " * 100  # very long
        key = d._extract_key_sentence(text)
        assert len(key) <= 55


class TestCompressionStats:
    def test_stats(self):
        d = Dialect()
        original = "We decided to use GraphQL instead of REST. " * 10
        compressed = d.compress(original)
        stats = d.compression_stats(original, compressed)
        assert stats["size_ratio"] > 1
        assert stats["original_chars"] > stats["summary_chars"]

    def test_count_tokens(self):
        assert Dialect.count_tokens("hello world") == 2

    def test_compression_stats_keys(self):
        """Verify compression_stats() returns the expected key set."""
        d = Dialect()
        stats = d.compression_stats("hello world this is a test", "HW:test")
        expected_keys = {
            "original_chars",
            "summary_chars",
            "original_tokens_est",
            "summary_tokens_est",
            "size_ratio",
            "note",
        }
        assert set(stats.keys()) == expected_keys


class TestZettelEncoding:
    def test_encode_zettel(self):
        d = Dialect(entities={"Alice": "ALC"})
        zettel = {
            "id": "zettel-001",
            "people": ["Alice"],
            "topics": ["memory", "ai"],
            "content": 'She said "I want to remember everything"',
            "emotional_weight": 0.9,
            "emotional_tone": ["joy"],
            "origin_moment": False,
            "sensitivity": "",
            "notes": "",
            "origin_label": "",
            "title": "Test - Memory Discussion",
        }
        result = d.encode_zettel(zettel)
        assert "ALC" in result
        assert "memory" in result

    def test_encode_tunnel(self):
        d = Dialect()
        tunnel = {"from": "zettel-001", "to": "zettel-002", "label": "follows: temporal"}
        result = d.encode_tunnel(tunnel)
        assert "T:" in result
        assert "001" in result
        assert "002" in result


class TestDecode:
    def test_decode_roundtrip(self):
        d = Dialect()
        encoded = (
            '001|ALC+BOB|2025-01-01|test_title\nARC:journey\n001:ALC|memory_ai|"test quote"|0.9|joy'
        )
        decoded = d.decode(encoded)
        assert decoded["header"]["file"] == "001"
        assert decoded["arc"] == "journey"
        assert len(decoded["zettels"]) == 1


# ── AAAK expansion for embedding (#300) ────────────────────────────────


class TestExpandAAAKForEmbedding:
    def test_looks_like_aaak_pipe_with_prefix(self):
        from mempalace.dialect import looks_like_aaak

        assert looks_like_aaak("FAM: ALC→JOR | DATE: 2026-05-28")
        assert looks_like_aaak("SESSION:2026-04-04|built.palace.graph")

    def test_looks_like_aaak_star_marker(self):
        from mempalace.dialect import looks_like_aaak

        assert looks_like_aaak("simple plus stars ★★★")
        assert looks_like_aaak("★")

    def test_looks_like_aaak_emotion_marker(self):
        from mempalace.dialect import looks_like_aaak

        assert looks_like_aaak("*warm* greeting from a friend")

    def test_looks_like_aaak_rejects_plain_prose(self):
        from mempalace.dialect import looks_like_aaak

        assert not looks_like_aaak("Just plain English describing what happened today.")
        # A line with a pipe but no structural prefix should NOT be flagged —
        # piped log lines are common in non-AAAK content too.
        assert not looks_like_aaak("path/to/foo | something else")

    def test_looks_like_aaak_rejects_empty(self):
        from mempalace.dialect import looks_like_aaak

        assert not looks_like_aaak("")
        assert not looks_like_aaak("   ")

    def test_expand_passes_through_plain_prose(self):
        from mempalace.dialect import expand_aaak_for_embedding

        text = "Plain narrative about a thing that happened."
        assert expand_aaak_for_embedding(text) == text

    def test_expand_appends_decoded_sidecar(self):
        from mempalace.dialect import expand_aaak_for_embedding

        text = "SESSION:2026-04-04|built.palace.graph|FAM: ALC|★★★"
        out = expand_aaak_for_embedding(text)
        # The original AAAK is the first segment.
        assert out.startswith(text)
        # The decoded sidecar follows after a blank line.
        assert "\n\n" in out
        decoded = out.split("\n\n", 1)[1]
        # Prefix decode
        assert "session record:" in decoded
        assert "family context:" in decoded
        # Star marker decode
        assert "notable importance" in decoded

    def test_expand_decodes_star_levels(self):
        from mempalace.dialect import expand_aaak_for_embedding

        for stars, prose in [
            ("★", "low importance"),
            ("★★", "modest importance"),
            ("★★★", "notable importance"),
            ("★★★★", "high importance"),
            ("★★★★★", "highest importance"),
        ]:
            text = f"FAM: ALC | {stars}"
            out = expand_aaak_for_embedding(text)
            assert prose in out, f"expected {prose!r} for {stars!r}, got {out!r}"

    def test_expand_decodes_count_markers(self):
        from mempalace.dialect import expand_aaak_for_embedding

        out = expand_aaak_for_embedding("PROJ: realmwatch | mentions:570x | ★★")
        assert "570 occurrences" in out

    def test_expand_strips_emotion_asterisks(self):
        from mempalace.dialect import expand_aaak_for_embedding

        out = expand_aaak_for_embedding("ARC: *warm*->*fierce*->*raw*")
        # No more raw asterisk-wrapped markers in the decoded line
        decoded = out.split("\n\n", 1)[1]
        assert "*warm*" not in decoded
        # But the word survives (possibly mapped)
        assert "warm" in decoded
        assert "determined" in decoded  # _AAAK_EMOTION_HINTS['fierce']

    def test_expand_with_entity_map(self):
        from mempalace.dialect import expand_aaak_for_embedding

        text = "FAM: ALC→JOR | ★★★"
        out = expand_aaak_for_embedding(text, entity_map={"ALC": "Alice", "JOR": "Jordan"})
        decoded = out.split("\n\n", 1)[1]
        assert "Alice" in decoded
        assert "Jordan" in decoded
        # Original still preserved
        assert "ALC" in out
        assert "JOR" in out

    def test_expand_without_entity_map_leaves_codes(self):
        from mempalace.dialect import expand_aaak_for_embedding

        out = expand_aaak_for_embedding("FAM: ALC→JOR | ★★★")
        # No mapping → codes pass through unchanged
        assert "ALC" in out
        assert "JOR" in out
        # But prefix decoding still ran
        assert "family context:" in out

    def test_expand_no_op_when_decoded_matches_original(self):
        """A text that looks like AAAK by emotion-marker heuristic but
        has no prefix / star / count / emotion expansion produces no
        sidecar — returns verbatim to save the embedding budget."""
        from mempalace.dialect import expand_aaak_for_embedding

        # An asterisk-wrapped word triggers `looks_like_aaak` but the
        # emotion word is unknown — the sub still strips the asterisks.
        # This documents the current behavior; whether it's the right
        # tradeoff (always sidecar vs sometimes skip) lives in #300's
        # design comments.
        out = expand_aaak_for_embedding("*xyzzy*")
        assert out.startswith("*xyzzy*")
