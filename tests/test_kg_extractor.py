"""Unit tests for mempalace.kg_llm_extractor.

The extractor module is HTTP-only and contains no DB writes, so all
tests here use a fake async client. The real httpx integration is
covered indirectly via the worker's integration tests on familiar.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from mempalace.kg_llm_extractor import (
    MAX_TRIPLES,
    PROMPT_TEMPLATE,
    STOPWORDS,
    TEXT_TAIL_LIMIT,
    Triple,
    _build_messages,
    _normalize_predicate,
    _parse_json_blob,
    _truncate,
    _validate,
    extract_triples,
)


@dataclass
class _FakeResponse:
    """Mimics the bits of ``httpx.Response`` the extractor touches."""

    status_code: int = 200
    payload: dict | None = None
    text_body: str = ""

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("no JSON")
        return self.payload

    @property
    def text(self) -> str:
        return self.text_body


class _FakeClient:
    """Async client stub: each ``post`` returns the next queued response."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url: str, *, json: dict, timeout: float = 60.0) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if not self._responses:
            raise RuntimeError("FakeClient ran out of responses")
        return self._responses.pop(0)


def _llm_response(content: str, status: int = 200) -> _FakeResponse:
    return _FakeResponse(
        status_code=status,
        payload={"choices": [{"message": {"content": content}}]},
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── Pure helpers ──────────────────────────────────────────────────────


def test_normalize_predicate_lowercases_and_snakes():
    assert _normalize_predicate("Works On") == "works_on"
    assert _normalize_predicate("  depends-on  ") == "depends_on"
    assert _normalize_predicate("migrated_from") == "migrated_from"


def test_truncate_keeps_tail():
    text = "abc" * 5000
    out = _truncate(text)
    assert len(out) == TEXT_TAIL_LIMIT
    assert out == text[-TEXT_TAIL_LIMIT:]


def test_truncate_short_passthrough():
    text = "hello world"
    assert _truncate(text) == text


def test_build_messages_uses_template():
    msgs = _build_messages("hi")
    assert msgs[0]["role"] == "user"
    assert "Extract structured facts" in msgs[0]["content"]
    assert "hi" in msgs[0]["content"]
    # Confirm we didn't accidentally change the spec'd prompt.
    assert "Maximum 10 triples per text" in PROMPT_TEMPLATE


# ── JSON parsing paths ────────────────────────────────────────────────


def test_parse_json_blob_clean_array():
    raw = '[{"subject": "a", "predicate": "b", "object": "c"}]'
    out = _parse_json_blob(raw)
    assert out == [{"subject": "a", "predicate": "b", "object": "c"}]


def test_parse_json_blob_object_wrapping_triples():
    raw = json.dumps({"triples": [{"subject": "a", "predicate": "b", "object": "c"}]})
    out = _parse_json_blob(raw)
    assert out == [{"subject": "a", "predicate": "b", "object": "c"}]


def test_parse_json_blob_prose_leakage():
    raw = (
        "Sure, here are the triples:\n"
        '[{"subject": "mempalace", "predicate": "depends_on", "object": "pgvector"}]\n'
        "Let me know if you need more!"
    )
    out = _parse_json_blob(raw)
    assert out == [{"subject": "mempalace", "predicate": "depends_on", "object": "pgvector"}]


def test_parse_json_blob_malformed_returns_empty():
    raw = "not json at all"
    assert _parse_json_blob(raw) == []


def test_parse_json_blob_empty_string():
    assert _parse_json_blob("") == []


def test_parse_json_blob_no_redos_on_pathological_input():
    """Regression: the previous regex (\\[\\s*\\{.*?\\}...\\]) backtracked
    catastrophically on long open-bracket-only inputs, freezing the event
    loop. The bracket-counting scanner is O(n) and must finish promptly.
    """
    import time

    raw = "[" + "{" * 5000 + " no closing"
    t0 = time.monotonic()
    out = _parse_json_blob(raw)
    elapsed = time.monotonic() - t0
    assert out == []
    assert elapsed < 0.1, f"parser took {elapsed:.3f}s on pathological input"


def test_parse_json_blob_brackets_inside_strings():
    """Bracket-counting scanner must respect string literals so a ``[``
    inside a string doesn't unbalance the count.
    """
    raw = 'Here you go: [{"subject": "a", "predicate": "uses_tag", "object": "[draft]"}]'
    out = _parse_json_blob(raw)
    assert out == [{"subject": "a", "predicate": "uses_tag", "object": "[draft]"}]


# ── Validation paths ──────────────────────────────────────────────────


def test_validate_rejects_self_loop():
    triple = _validate({"subject": "X", "predicate": "is", "object": "x"})
    assert triple is None


def test_validate_rejects_empty_predicate():
    triple = _validate({"subject": "X", "predicate": "  ", "object": "Y"})
    assert triple is None


def test_validate_rejects_stopword_subject():
    for stop in list(STOPWORDS)[:5]:
        assert _validate({"subject": stop, "predicate": "loves", "object": "Y"}) is None


def test_validate_rejects_stopword_object():
    for stop in list(STOPWORDS)[:5]:
        assert _validate({"subject": "Alice", "predicate": "loves", "object": stop}) is None


def test_validate_normalizes_predicate():
    triple = _validate({"subject": "Alice", "predicate": "Works On", "object": "MemPalace"})
    assert triple is not None
    assert triple.predicate == "works_on"


def test_validate_preserves_valid_from():
    triple = _validate(
        {
            "subject": "Alice",
            "predicate": "joined",
            "object": "TechEmpower",
            "valid_from": "2024-01-15",
        }
    )
    assert triple is not None
    assert triple.valid_from == "2024-01-15"


def test_validate_drops_non_string_valid_from():
    triple = _validate(
        {"subject": "Alice", "predicate": "joined", "object": "X", "valid_from": 123}
    )
    assert triple is not None
    assert triple.valid_from is None


# ── End-to-end extract_triples ───────────────────────────────────────


def test_extract_triples_happy_path():
    content = json.dumps(
        [
            {"subject": "mempalace", "predicate": "depends_on", "object": "pgvector"},
            {"subject": "JP", "predicate": "works_on", "object": "familiar"},
        ]
    )
    client = _FakeClient([_llm_response(content)])
    triples = _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            "We use pgvector. JP works on familiar.",
        )
    )
    assert len(triples) == 2
    assert isinstance(triples[0], Triple)
    assert triples[0].subject == "mempalace"
    assert triples[0].predicate == "depends_on"
    assert triples[0].object == "pgvector"
    # URL composition: should hit /v1/chat/completions exactly once
    # because the LLM accepted response_format on the first attempt.
    assert len(client.calls) == 1
    assert client.calls[0]["url"].endswith("/v1/chat/completions")


def test_extract_triples_malformed_response_returns_empty():
    client = _FakeClient([_llm_response("not json")])
    triples = _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            "Some drawer.",
        )
    )
    assert triples == []


def test_extract_triples_caps_at_max():
    items = [{"subject": f"S{i}", "predicate": "rel", "object": f"O{i}"} for i in range(15)]
    client = _FakeClient([_llm_response(json.dumps(items))])
    triples = _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            "lots of facts",
        )
    )
    assert len(triples) == MAX_TRIPLES


def test_extract_triples_truncates_long_input():
    captured: dict = {}

    class _CaptureClient(_FakeClient):
        async def post(self, url, *, json, timeout=60.0):
            captured["content"] = json["messages"][0]["content"]
            return _llm_response("[]")

    client = _CaptureClient([])
    huge = "x" * 20_000 + "TAIL_MARKER_END"
    _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            huge,
        )
    )
    sent_text = captured["content"]
    # Prompt header is small; sent_text - template length ~= 6000.
    assert "TAIL_MARKER_END" in sent_text
    # The prompt template plus 6000 chars of document; nothing longer.
    prompt_overhead = len(PROMPT_TEMPLATE.replace("{document}", ""))
    assert len(sent_text) <= prompt_overhead + TEXT_TAIL_LIMIT + 10


def test_extract_triples_retries_without_response_format_on_4xx():
    """If the first call (with response_format) fails 400, retry without it."""
    client = _FakeClient(
        [
            _FakeResponse(status_code=400, payload=None, text_body="unsupported response_format"),
            _llm_response(json.dumps([{"subject": "Alice", "predicate": "rel", "object": "Bob"}])),
        ]
    )
    triples = _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            "Some text.",
        )
    )
    assert len(triples) == 1
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]["json"]
    assert "response_format" not in client.calls[1]["json"]


def test_extract_triples_http_500_returns_empty():
    client = _FakeClient(
        [
            _FakeResponse(status_code=500, payload=None, text_body="ouch"),
            _FakeResponse(status_code=500, payload=None, text_body="ouch"),
        ]
    )
    triples = _run(
        extract_triples(
            client,
            "http://localhost:11436",
            "phi-4-mini",
            "Some text.",
        )
    )
    assert triples == []


def test_extract_triples_empty_input_returns_empty():
    client = _FakeClient([])
    triples = _run(extract_triples(client, "http://localhost:11436", "phi-4-mini", ""))
    assert triples == []
    assert client.calls == []


def test_extract_triples_validation_filters_bad_items():
    items = [
        {"subject": "A", "predicate": "rel", "object": "A"},  # self-loop
        {"subject": "user", "predicate": "rel", "object": "B"},  # stopword
        {"subject": "X", "predicate": "  ", "object": "Y"},  # empty predicate
        {"subject": "OK", "predicate": "good_rel", "object": "Valid"},  # keep
    ]
    client = _FakeClient([_llm_response(json.dumps(items))])
    triples = _run(extract_triples(client, "http://localhost:11436", "phi-4-mini", "x"))
    assert len(triples) == 1
    assert triples[0].subject == "OK"


def test_extract_triples_network_exception_returns_empty():
    class _BadClient:
        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    triples = _run(extract_triples(_BadClient(), "http://localhost:11436", "phi-4-mini", "x"))
    assert triples == []


def test_extract_triples_endpoint_normalization():
    """Endpoint URL should resolve to /v1/chat/completions regardless of input."""
    cases = [
        ("http://localhost:11436", "/v1/chat/completions"),
        ("http://localhost:11436/", "/v1/chat/completions"),
        ("http://localhost:11436/v1", "/v1/chat/completions"),
        ("http://localhost:11436/v1/chat/completions", "/v1/chat/completions"),
    ]
    for endpoint, expected_suffix in cases:
        client = _FakeClient([_llm_response("[]")])
        _run(extract_triples(client, endpoint, "phi-4-mini", "some text"))
        assert client.calls, f"no call made for endpoint={endpoint}"
        assert client.calls[0]["url"].endswith(expected_suffix), (
            f"endpoint={endpoint} -> {client.calls[0]['url']}"
        )


@pytest.mark.parametrize(
    "raw,expected_subjects",
    [
        ('[{"subject": "A", "predicate": "r", "object": "B"}]', ["A"]),
        ('{"triples": [{"subject": "C", "predicate": "r", "object": "D"}]}', ["C"]),
    ],
)
def test_parse_json_blob_shapes(raw, expected_subjects):
    items = _parse_json_blob(raw)
    assert [item["subject"] for item in items] == expected_subjects
