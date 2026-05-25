# `mempalace.fact_checker`

Source: [`mempalace/fact_checker.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/fact_checker.py)

fact_checker.py — Verify text against known facts in the palace.

Checks AI responses, diary entries, and new content against the entity
registry and knowledge graph for three classes of issue:

  * similar_name          — text mentions a name that's one/two edits
                            away from *another* registered name, raising
                            the possibility of a typo or mix-up.
  * relationship_mismatch — text asserts a role between two entities
                            (e.g. "Bob is Alice's brother") while the KG
                            records a *different* current role for the
                            same subject/object pair.
  * stale_fact            — text asserts a fact that the KG marks closed
                            (``valid_to`` in the past).

Purely offline. Inputs: entity_registry JSON + KG SQLite. No network.

Usage:
    from mempalace.fact_checker import check_text
    issues = check_text("Bob is Alice's brother", palace_path)

    # CLI
    python -m mempalace.fact_checker "Bob is Alice's brother" \
        --palace ~/.mempalace/palace

## Functions

### `check_text`

```python
def check_text(text: str, palace_path: str = None, config = None) -> list
```

Return a list of issues detected in ``text``.

Empty list means "no contradictions found" — absence of evidence, not
evidence of absence. The detector is deliberately conservative:
every issue is anchored to a specific KG fact or registry entry.
