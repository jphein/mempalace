# `mempalace.corpus_origin`

Source: [`mempalace/corpus_origin.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/corpus_origin.py)

corpus_origin.py — Detect whether a corpus is an AI-dialogue record and,
if so, what platform and what persona names the user has assigned to the
agent.

This is the first question any downstream Pass 2 classification needs
answered. Without it, a drawer like "my three sons" in a Claude Code
dialogue corpus can't be correctly resolved to "three AI instances"
rather than "three biological children."

Two-tier detection:

  Tier 1 — detect_origin_heuristic(samples)
           Cheap, no API. Grep for well-known AI brand terms + turn
           markers. Always runs. Outputs a hypothesis.

  Tier 2 — detect_origin_llm(samples, provider)
           Uses an LLMProvider (typically Haiku via mempalace.llm_client)
           with the model's pre-trained knowledge of Claude/ChatGPT/Gemini
           etc. Confirms platform, extracts agent persona-names the user
           has assigned. One call, ~$0.01 cost.

Design principle:
  Don't make the classifier re-discover what Claude, ChatGPT, Gemini, MCP,
  or other well-known entities ARE — the LLM already knows them from its
  training. Only corpus-specific entities (e.g. the user's persona-name
  for their Claude instance) need discovery.

Default stance (when evidence is thin):
  "This IS an AI-dialogue corpus" — false-negative is catastrophic for
  downstream classification; false-positive is recoverable via per-drawer
  voice-profile detection in later passes.

## Classes

### `class CorpusOriginResult`

Structured output from corpus-origin detection.

Fields:
  likely_ai_dialogue — best hypothesis about whether this is AI-dialogue
  confidence — 0.0 to 1.0
  primary_platform — e.g. "Claude Code (Anthropic CLI)" or None
  user_name — the corpus author's name if identifiable from context, else None
  agent_persona_names — names the user has assigned to the AI agent(s)
                        (e.g. ["Echo", "Sparrow"]). Does NOT include the user's own name.
  evidence — human-readable reasons for the classification

#### `to_dict`

```python
def to_dict(self) -> dict
```

## Functions

### `detect_origin_heuristic`

```python
def detect_origin_heuristic(samples: list[str]) -> CorpusOriginResult
```

Fast grep-based detection. No API calls.

Scores AI-dialogue likelihood by counting:
  - occurrences of well-known AI brand terms
  - turn-marker patterns (user:, assistant:, etc.)

Returns a CorpusOriginResult with confidence derived from signal density.

### `detect_origin_llm`

```python
def detect_origin_llm(samples: list[str], provider) -> CorpusOriginResult
```

LLM-assisted detection. Takes samples (list of drawer-text excerpts)
and an LLMProvider (mempalace.llm_client.LLMProvider). Returns the
same CorpusOriginResult shape as the heuristic.

Falls back conservatively (default-stance ai=True, low confidence)
on any LLM error or malformed response — never raises.
