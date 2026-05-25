# `mempalace.entity_detector`

Source: [`mempalace/entity_detector.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/entity_detector.py)

entity_detector.py — Auto-detect people and projects from file content.

Uses ``from __future__ import annotations`` so PEP 604 union syntax
(``dict | None``) works on the Python 3.9 baseline.

Two-pass approach:
  Pass 1: scan files, extract entity candidates with signal counts
  Pass 2: score and classify each candidate as person, project, or uncertain

Used by mempalace init before mining begins.
The confirmed entity map feeds the miner as the taxonomy.

Multi-language support:
    All lexical patterns (person verbs, pronouns, dialogue markers, project
    verbs, stopwords, and the candidate-extraction character class) live in
    the ``entity`` section of ``mempalace/i18n/&lt;lang>.json``. Every public
    function accepts a ``languages`` tuple and applies the union of the
    requested locales' patterns. The default is ``("en",)`` — existing
    English-only callers behave exactly as before.

    To add a new language: add an ``entity`` section to that locale's JSON.
    No code changes required.

Usage:
    from mempalace.entity_detector import detect_entities, confirm_entities
    candidates = detect_entities(file_paths)                    # English only
    candidates = detect_entities(paths, languages=("en", "pt-br"))
    confirmed = confirm_entities(candidates)  # interactive review

## Functions

### `extract_candidates`

```python
def extract_candidates(text: str, languages = ('en',)) -> dict
```

Extract all capitalized proper noun candidates from text.
Returns &#123;name: frequency} for names appearing 3+ times.

Each language contributes its own character-class pattern (e.g. ASCII
for English, Latin+diacritics for pt-br, Cyrillic for Russian,
Devanagari for Hindi). Matches from all languages are unioned.

### `score_entity`

```python
def score_entity(name: str, text: str, lines: list, languages = ('en',)) -> dict
```

Score a candidate entity as person vs project.
Returns scores and the signals that fired.

### `classify_entity`

```python
def classify_entity(name: str, frequency: int, scores: dict) -> dict
```

Given scores, classify as person / project / uncertain.
Returns entity dict with confidence.

### `detect_entities`

```python
def detect_entities(file_paths: list, max_files: int = 10, languages = ('en',), corpus_origin: dict | None = None) -> dict
```

Scan files and detect entity candidates.

Args:
    file_paths: List of Path objects to scan
    max_files: Max files to read (for speed)
    languages: Tuple of language codes whose entity patterns should be
        applied (union). Defaults to ``("en",)``.
    corpus_origin: Optional corpus-origin context (the dict produced
        by ``mempalace.corpus_origin`` and persisted to
        ``&lt;palace>/.mempalace/origin.json`` by ``mempalace init``).
        When supplied and the corpus is identified as AI-dialogue with
        known agent persona names, candidates whose name matches an
        agent persona are moved out of ``people``/``uncertain`` and
        into a new ``agent_personas`` bucket. Shape:
        ``&#123;"schema_version": 1, "result": &#123;"agent_persona_names": [...], ...}}``.

Returns:
    &#123;
        "people":   [...entity dicts...],
        "projects": [...entity dicts...],
        "topics":   [...entity dicts...],
        "uncertain":[...entity dicts...],
        # Only present when corpus_origin reclassifies at least one
        # candidate as an agent persona:
        "agent_personas": [...entity dicts...],
    }

### `confirm_entities`

```python
def confirm_entities(detected: dict, yes: bool = False) -> dict
```

Interactive confirmation step.
User reviews detected entities, removes wrong ones, adds missing ones.
Returns confirmed &#123;people: [names], projects: [names], topics: [names]}.

Topics are not surfaced for interactive review — they come from the
LLM-refined ``TOPIC`` bucket and are passed through verbatim. They
feed cross-wing tunnel computation at mine time (see
``palace_graph.compute_topic_tunnels``); a wrong topic at worst adds
a low-traffic tunnel and never alters drawer storage.

Pass yes=True to auto-accept all detected entities without prompting.

### `scan_for_detection`

```python
def scan_for_detection(project_dir: str, max_files: int = 10) -> list
```

Collect prose file paths for entity detection.
Prose only (.txt, .md, .rst, .csv) — code files produce too many false positives.
Falls back to all readable files if no prose found.
