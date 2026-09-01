"""Unit tests for the postgres write-path NUL-byte scrub.

Pure in-memory tests of ``_replace_nul_bytes`` — no live postgres needed,
so deliberately NOT in test_backends_postgres.py (which skips without a
POSTGRES_DSN).
"""


def test_replace_nul_bytes_documents_and_metadata():
    """NUL bytes are replaced with U+FFFD and provenanced, in place.

    Postgres text and jsonb both refuse \\x00 (psycopg.DataError), so a raw
    device log aborts the whole mine at upsert. The write path substitutes
    U+FFFD and records how many bytes were replaced, rather than crashing —
    or worse, silently dropping the file.
    """
    from mempalace.backends.postgres import _replace_nul_bytes

    documents = ["clean text", "log\x00with\x00nuls"]
    metadatas = [
        {"wing": "test"},
        {"wing": "test", "source_file": "/var/log/app\x00.txt"},
    ]
    _replace_nul_bytes(documents, metadatas)

    assert documents[0] == "clean text"
    assert "\x00" not in documents[1]
    assert documents[1] == "log�with�nuls"
    assert metadatas[1]["nul_bytes_replaced"] == 2
    assert metadatas[1]["source_file"] == "/var/log/app�.txt"
    assert "nul_bytes_replaced" not in metadatas[0]


def test_replace_nul_bytes_without_metadata():
    """A NUL-bearing document with no metadata list still gets scrubbed."""
    from mempalace.backends.postgres import _replace_nul_bytes

    documents = ["\x00lead and trail\x00"]
    _replace_nul_bytes(documents, None)
    assert documents[0] == "�lead and trail�"
