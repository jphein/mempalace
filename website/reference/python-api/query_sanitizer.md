# `mempalace.query_sanitizer`

Source: [`mempalace/query_sanitizer.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/query_sanitizer.py)

query_sanitizer.py — Mitigate system prompt contamination in search queries.

Problem: AI agents sometimes prepend system prompts (2000+ chars) to search queries.
Embedding models represent the concatenated string as a single vector where the
system prompt overwhelms the actual question (typically 10-50 chars), causing
near-total retrieval failure (89.8% → 1.0% R@10). See Issue #333.

Approach: "Mitigation" (減災) — not perfect prevention, but prevents the cliff.

Expected recovery:
  Step 1 passthrough (≤200 chars)     → no degradation, ~89.8%
  Step 2 question extraction (？found) → near-full recovery, ~85-89%
  Step 3 tail sentence extraction      → moderate recovery, ~80-89%
  Step 4 tail truncation (fallback)    → minimum viable, ~70-80%

  Without sanitizer: 1.0% (catastrophic silent failure)
  Worst case with sanitizer: ~70-80% (survivable)

## Functions

### `sanitize_query`

```python
def sanitize_query(raw_query: str) -> dict
```

Extract the actual search intent from a potentially contaminated query.

Args:
    raw_query: The raw query string from the AI agent, possibly containing
               system prompt content prepended to the actual question.

Returns:
    dict with keys:
        clean_query (str): The sanitized query to use for embedding search
        was_sanitized (bool): Whether any sanitization was applied
        original_length (int): Length of the raw input
        clean_length (int): Length of the sanitized output
        method (str): Which extraction method was used
            - "passthrough": query was short enough, no action taken
            - "question_extraction": found and extracted a question sentence
            - "tail_sentence": extracted the last meaningful sentence
            - "tail_truncation": fallback — took the last MAX_QUERY_LENGTH chars
