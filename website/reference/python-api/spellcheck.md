# `mempalace.spellcheck`

Source: [`mempalace/spellcheck.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/spellcheck.py)

spellcheck.py — Spell-correct user messages before palace filing.

Preserves:
  - Technical terms (words with digits, hyphens, underscores)
  - CamelCase and ALL_CAPS identifiers
  - Known entity names (from EntityRegistry if available)
  - URLs and file paths
  - Words shorter than 3 chars (common abbreviations, pronouns, etc.)
  - Proper nouns already capitalized in context

Corrects:
  - Genuine typos in lowercase, flowing text
  - Common fat-finger words (3am → 3am, knoe → know)

Usage:
    from mempalace.spellcheck import spellcheck_user_text
    corrected = spellcheck_user_text("lsresdy knoe the question befor")
    # → "already know the question before"  (best effort)

## Functions

### `spellcheck_user_text`

```python
def spellcheck_user_text(text: str, known_names: Optional[set] = None) -> str
```

Spell-correct a user message.

Args:
    text: Raw user message text.
    known_names: Set of lowercase names/terms to preserve. If None,
                 attempts to load from EntityRegistry automatically.

Returns:
    Corrected text. Falls back to original if autocorrect not installed.

### `spellcheck_transcript_line`

```python
def spellcheck_transcript_line(line: str) -> str
```

Spell-correct a single transcript line.
Only touches lines that start with '>' (user turns).
Assistant turns are never modified.

### `spellcheck_transcript`

```python
def spellcheck_transcript(content: str) -> str
```

Spell-correct all user turns in a full transcript.
Only lines starting with '>' are touched.
