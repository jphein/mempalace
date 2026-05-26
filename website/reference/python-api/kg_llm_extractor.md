# `mempalace.kg_llm_extractor`

Source: [`mempalace/kg_llm_extractor.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/kg_llm_extractor.py)

LLM-based triple extractor for the async KG worker.

Produces ``(subject, predicate, object)`` triples from drawer text by
calling an OpenAI-compatible chat-completions endpoint (llama-server,
Ollama, vLLM, etc.). Pure module — no DB, no AGE imports — so it can
be unit-tested with a mocked ``httpx.AsyncClient``.

The extraction prompt template lives in ``PROMPT_TEMPLATE`` below and
mirrors the spec at ``docs/specs/kg-triple-extraction.md`` lines 87-102.

The function returns ``[]`` on any parse failure; the worker decides
retry policy. Validation drops triples where subject == object, the
predicate is empty, or either endpoint is a meta-stopword.

## Classes

### `class Triple`

## Functions

### `extract_triples`

```python
async def extract_triples(client: Any, endpoint: str, model: str, drawer_text: str, *, timeout: float = 60.0, use_response_format: bool = True) -> list[Triple]
```

Call the LLM and parse out validated triples.

Args:
    client: An ``httpx.AsyncClient`` (or any object exposing a
        compatible async ``post`` method — useful for tests).
    endpoint: Base URL of the OpenAI-compatible server (e.g.
        ``http://familiar:11436``). The function appends
        ``/v1/chat/completions`` if the path isn't already present.
    model: Model alias (e.g. ``phi-4-mini``).
    drawer_text: Raw drawer document. Tail-truncated to the last
        ``TEXT_TAIL_LIMIT`` characters before prompting.
    timeout: Per-request timeout in seconds.
    use_response_format: First try ``response_format=&#123;"type":"json_object"}``
        for structured output (llama-server supports this via GBNF);
        on 4xx/5xx for that field, retry without it and fall back to
        regex parsing.

Returns ``[]`` on any failure (network, JSON parse, validation). The
worker decides retry behavior — this function never raises.
