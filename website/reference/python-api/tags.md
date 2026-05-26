# `mempalace.tags`

Source: [`mempalace/tags.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/tags.py)

Multi-label tags for drawers (techempower-org/mempalace#39).

Tags are an additive, cross-cutting label layer over the strict wing/room
hierarchy. A drawer belongs to exactly one wing and one room but may carry
zero or more tags. Filtering with multiple tags is AND-conjunctive: a
drawer must carry every requested tag to match.

Storage:
    - Tags live in drawer metadata under the ``tags`` key.
    - Postgres (JSONB) stores them as a JSON array.
    - ChromaDB metadata only accepts scalar values, so the chroma path
      stores a delimited string under ``tags_str`` shaped like
      ``|t1|t2|t3|`` (leading + trailing pipes) alongside the list under
      ``tags``. The list form is the canonical read shape; ``tags_str``
      is an internal index used only by chroma's substring search.

Normalisation:
    Tags are lowercased and stripped of surrounding whitespace; spaces
    inside a tag become hyphens (``"Project X"`` → ``"project-x"``).
    Empty strings, duplicates, and non-string values are dropped.
    A single tag is truncated to ``MAX_TAG_LENGTH`` (128) characters
    after character-class cleaning, and a tag list is capped to the
    first ``MAX_TAG_COUNT`` (64) distinct tags in order. Both bounds are
    applied silently (no exceptions) — they guard write-path bloat/DoS.

## Functions

### `normalise_tag`

```python
def normalise_tag(tag: Any) -> Optional[str]
```

Return the canonical form of a single tag, or ``None`` if invalid.

Rules: lower-case, strip whitespace, spaces → hyphens, drop characters
outside ``[a-z0-9_\-.]``, then truncate to ``MAX_TAG_LENGTH`` chars.
Returns ``None`` for empty/whitespace-only or non-string input.

### `normalise_tags`

```python
def normalise_tags(tags: Optional[Iterable[Any]]) -> list[str]
```

Normalise an iterable of tags, dropping invalid + deduping (order-preserving).

### `tags_to_string`

```python
def tags_to_string(tags: list[str]) -> str
```

Render tags as the pipe-delimited string used by the chroma backend.

Empty input yields ``""`` (not ``"|"``) so unfiltered drawers don't
accidentally match a "has any tag" substring search.

### `string_to_tags`

```python
def string_to_tags(value: Any) -> list[str]
```

Parse the pipe-delimited chroma index back to a tag list.

### `apply_tags_to_metadata`

```python
def apply_tags_to_metadata(metadata: dict, tags: Optional[Iterable[Any]]) -> list[str]
```

In-place: write normalised ``tags`` (list) + ``tags_str`` (string) to ``metadata``.

Returns the normalised list so callers can echo it back to the user.
Passing ``tags=None`` is a no-op (leaves any existing tags untouched).
Passing ``tags=[]`` clears tags from the metadata.

### `extract_tags_from_metadata`

```python
def extract_tags_from_metadata(metadata: Any) -> list[str]
```

Read tags out of a stored metadata dict, tolerating both shapes.

Reads the canonical list form first; falls back to parsing
``tags_str`` when only the chroma index form is present (e.g. for
drawers written via a raw backend call that bypassed the helper).

### `metadata_matches_all_tags`

```python
def metadata_matches_all_tags(metadata: Any, required: list[str]) -> bool
```

Return True when every tag in ``required`` is present in ``metadata``.
