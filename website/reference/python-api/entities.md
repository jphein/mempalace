# `mempalace.entities`

Source: [`mempalace/entities.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/entities.py)

No-LLM structural entity extraction for the associative graph.

Pulls deterministic, *structural* tokens from text — author-quoted code spans, URLs,
file paths, qualified identifiers, and CamelCase symbols — to populate the ``entities``
drawer-metadata field that hallways/tunnels consume. Structural-only by design: no
wordlists, no NLP models, no domain vocabulary, so it stays language-neutral and
predictable, and biases to precision (only tokens that are unambiguously "a thing being
referred to") over recall.

The output format matches what ``hallways._parse_entities`` expects: a ``;``-joined string.

## Functions

### `extract_structural_entities`

```python
def extract_structural_entities(text, max_entities = _MAX_ENTITIES)
```

Return up to ``max_entities`` structural entities from ``text``.

Deterministic and order-stable: entities are ranked by occurrence count (ties broken
by first appearance), deduplicated case-insensitively, preserving the first-seen
surface form.

### `entities_metadata`

```python
def entities_metadata(text, max_entities = _MAX_ENTITIES)
```

``;``-joined entity string for drawer metadata, or ``""`` when none are found.
