"""LLM-based triple extractor for the async KG worker.

Produces ``(subject, predicate, object)`` triples from drawer text by
calling an OpenAI-compatible chat-completions endpoint (llama-server,
Ollama, vLLM, etc.). Pure module — no DB, no AGE imports — so it can
be unit-tested with a mocked ``httpx.AsyncClient``.

The extraction prompt template lives in ``PROMPT_TEMPLATE`` below and
mirrors the spec at ``docs/specs/kg-triple-extraction.md`` lines 87-102.

The function returns ``[]`` on any parse failure; the worker decides
retry policy. Validation drops triples where subject == object, the
predicate is empty, or either endpoint is a meta-stopword.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("mempalace.kg_llm_extractor")


PROMPT_TEMPLATE = (
    "Extract structured facts from this text as JSON triples.\n"
    'Each triple: {"subject": "...", "predicate": "...", "object": "..."}\n'
    "\n"
    "Rules:\n"
    "- subject and object are entity names (people, projects, tools, concepts)\n"
    "- predicate is a lowercase verb phrase "
    "(works_on, depends_on, created_by, migrated_from, etc.)\n"
    "- Only extract facts explicitly stated, not inferred\n"
    "- Skip meta-observations about the conversation itself\n"
    "- Maximum 10 triples per text\n"
    "\n"
    "Text:\n"
    "{document}\n"
    "\n"
    "Triples (JSON array):"
)


STOPWORDS: frozenset[str] = frozenset(
    {
        "it",
        "this",
        "that",
        "they",
        "the",
        "a",
        "an",
        "user",
        "agent",
        "system",
        "you",
        "i",
        "we",
        "me",
        "us",
        "them",
        "he",
        "she",
        "one",
        "none",
    }
)


MAX_TRIPLES = 10
TEXT_TAIL_LIMIT = 6000


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    extra: dict = field(default_factory=dict)


def _normalize_predicate(raw: str) -> str:
    """Lowercase + snake_case the predicate string.

    LLMs frequently emit ``works on``, ``Works On``, ``works-on``. We
    canonicalize at write time so the same logical relationship doesn't
    fan out across edge labels.
    """
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


def _truncate(text: str, limit: int = TEXT_TAIL_LIMIT) -> str:
    """Keep the trailing ``limit`` characters of ``text``.

    Drawers can be very long (session transcripts). Recency carries more
    structured facts than the front of the transcript, so we tail-truncate.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


_JSON_ARRAY_PATTERN = re.compile(r"\[\s*\{.*?\}\s*(?:,\s*\{.*?\}\s*)*\]", re.DOTALL)


def _parse_json_blob(raw: str) -> list[dict]:
    """Best-effort parse of the LLM's response into a list of dicts.

    Strategy:
    1. Strict ``json.loads`` — works when ``response_format=json_object``
       returns a clean ``{"triples": [...]}`` or bare JSON array.
    2. Regex scan for ``[{...}, {...}]`` substring — handles cases where
       the model leaks prose before/after the JSON.

    Returns ``[]`` rather than raising so the worker can record a
    completion (with zero triples) instead of re-queueing forever.
    """
    if not raw:
        return []
    raw_stripped = raw.strip()
    try:
        parsed = json.loads(raw_stripped)
    except (json.JSONDecodeError, ValueError):
        match = _JSON_ARRAY_PATTERN.search(raw_stripped)
        if not match:
            logger.debug("no JSON array found in LLM response: %r", raw_stripped[:200])
            return []
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("regex-extracted JSON failed to parse: %s", e)
            return []

    if isinstance(parsed, dict):
        for key in ("triples", "facts", "result", "results", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
        else:
            single = {k: parsed.get(k) for k in ("subject", "predicate", "object")}
            if all(isinstance(v, str) for v in single.values()):
                return [single]
            return []

    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _validate(item: dict) -> Optional[Triple]:
    """Coerce a raw dict to a Triple, or return ``None`` if invalid."""
    subject = item.get("subject")
    predicate = item.get("predicate")
    object_ = item.get("object")
    if not (isinstance(subject, str) and isinstance(predicate, str) and isinstance(object_, str)):
        return None

    subject = subject.strip()
    object_ = object_.strip()
    predicate = _normalize_predicate(predicate)

    if not subject or not object_ or not predicate:
        return None
    if subject.lower() == object_.lower():
        return None
    if subject.lower() in STOPWORDS or object_.lower() in STOPWORDS:
        return None

    valid_from = item.get("valid_from")
    if valid_from is not None and not isinstance(valid_from, str):
        valid_from = None

    return Triple(
        subject=subject,
        predicate=predicate,
        object=object_,
        valid_from=valid_from,
    )


_DOCUMENT_PLACEHOLDER = "{document}"


def _build_messages(document: str) -> list[dict]:
    # PROMPT_TEMPLATE contains literal ``{"subject": ...}`` braces, which
    # collide with ``str.format``. Use a single targeted substitution so
    # the JSON-shape examples stay untouched.
    prompt = PROMPT_TEMPLATE.replace(_DOCUMENT_PLACEHOLDER, _truncate(document))
    return [{"role": "user", "content": prompt}]


def _extract_assistant_content(payload: Any) -> str:
    """Pull the message content out of an OpenAI-compatible response.

    llama-server, Ollama, and vLLM all return
    ``{"choices": [{"message": {"content": "..."}}]}``.
    """
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content", "")
    return content if isinstance(content, str) else ""


async def extract_triples(
    client: Any,
    endpoint: str,
    model: str,
    drawer_text: str,
    *,
    timeout: float = 60.0,
    use_response_format: bool = True,
) -> list[Triple]:
    """Call the LLM and parse out validated triples.

    Args:
        client: An ``httpx.AsyncClient`` (or any object exposing a
            compatible async ``post`` method — useful for tests).
        endpoint: Base URL of the OpenAI-compatible server (e.g.
            ``http://familiar.jphe.in:11436``). The function appends
            ``/v1/chat/completions`` if the path isn't already present.
        model: Model alias (e.g. ``phi-4-mini``).
        drawer_text: Raw drawer document. Tail-truncated to the last
            ``TEXT_TAIL_LIMIT`` characters before prompting.
        timeout: Per-request timeout in seconds.
        use_response_format: First try ``response_format={"type":"json_object"}``
            for structured output (llama-server supports this via GBNF);
            on 4xx/5xx for that field, retry without it and fall back to
            regex parsing.

    Returns ``[]`` on any failure (network, JSON parse, validation). The
    worker decides retry behavior — this function never raises.
    """
    if not drawer_text or not drawer_text.strip():
        return []
    if not endpoint:
        return []

    url = endpoint.rstrip("/")
    if not url.endswith("/chat/completions"):
        if "/v1" not in url:
            url = url + "/v1/chat/completions"
        else:
            url = url + "/chat/completions"

    messages = _build_messages(drawer_text)
    base_payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1024,
    }

    async def _post(payload: dict):
        return await client.post(url, json=payload, timeout=timeout)

    payload = dict(base_payload)
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = await _post(payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM request failed: %s", e)
        return []

    status = getattr(resp, "status_code", 500)
    if status >= 400 and use_response_format:
        try:
            resp = await _post(base_payload)
            status = getattr(resp, "status_code", 500)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM retry without response_format failed: %s", e)
            return []

    if status >= 400:
        body_snippet = ""
        try:
            body_snippet = (
                resp.text[:200] if isinstance(getattr(resp, "text", None), str) else ""
            )
        except Exception:  # noqa: BLE001
            pass
        logger.warning("LLM returned HTTP %s: %s", status, body_snippet)
        return []

    try:
        payload_json = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM response not JSON: %s", e)
        return []

    content = _extract_assistant_content(payload_json)
    raw_items = _parse_json_blob(content)

    triples: list[Triple] = []
    for item in raw_items:
        triple = _validate(item)
        if triple is not None:
            triples.append(triple)
        if len(triples) >= MAX_TRIPLES:
            break

    return triples
