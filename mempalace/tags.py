"""Multi-label tags for drawers (techempower-org/mempalace#39).

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
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

TAGS_METADATA_KEY = "tags"
TAGS_STRING_METADATA_KEY = "tags_str"
TAG_DELIMITER = "|"

_TAG_INVALID_RE = re.compile(r"[^a-z0-9_\-.]")


def normalise_tag(tag: Any) -> Optional[str]:
    """Return the canonical form of a single tag, or ``None`` if invalid.

    Rules: lower-case, strip whitespace, spaces → hyphens, drop characters
    outside ``[a-z0-9_\\-.]``. Returns ``None`` for empty/whitespace-only
    or non-string input.
    """
    if not isinstance(tag, str):
        return None
    value = tag.strip().lower().replace(" ", "-")
    value = _TAG_INVALID_RE.sub("", value)
    return value or None


def normalise_tags(tags: Optional[Iterable[Any]]) -> list[str]:
    """Normalise an iterable of tags, dropping invalid + deduping (order-preserving)."""
    if not tags:
        return []
    seen: dict[str, None] = {}
    for raw in tags:
        clean = normalise_tag(raw)
        if clean and clean not in seen:
            seen[clean] = None
    return list(seen.keys())


def tags_to_string(tags: list[str]) -> str:
    """Render tags as the pipe-delimited string used by the chroma backend.

    Empty input yields ``""`` (not ``"|"``) so unfiltered drawers don't
    accidentally match a "has any tag" substring search.
    """
    if not tags:
        return ""
    return TAG_DELIMITER + TAG_DELIMITER.join(tags) + TAG_DELIMITER


def string_to_tags(value: Any) -> list[str]:
    """Parse the pipe-delimited chroma index back to a tag list."""
    if not isinstance(value, str) or not value:
        return []
    return [part for part in value.split(TAG_DELIMITER) if part]


def apply_tags_to_metadata(metadata: dict, tags: Optional[Iterable[Any]]) -> list[str]:
    """In-place: write normalised ``tags`` (list) + ``tags_str`` (string) to ``metadata``.

    Returns the normalised list so callers can echo it back to the user.
    Passing ``tags=None`` is a no-op (leaves any existing tags untouched).
    Passing ``tags=[]`` clears tags from the metadata.
    """
    if tags is None:
        return list(metadata.get(TAGS_METADATA_KEY, []) or [])

    normalised = normalise_tags(tags)
    if normalised:
        metadata[TAGS_METADATA_KEY] = normalised
        metadata[TAGS_STRING_METADATA_KEY] = tags_to_string(normalised)
    else:
        metadata.pop(TAGS_METADATA_KEY, None)
        metadata.pop(TAGS_STRING_METADATA_KEY, None)
    return normalised


def extract_tags_from_metadata(metadata: Any) -> list[str]:
    """Read tags out of a stored metadata dict, tolerating both shapes.

    Reads the canonical list form first; falls back to parsing
    ``tags_str`` when only the chroma index form is present (e.g. for
    drawers written via a raw backend call that bypassed the helper).
    """
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get(TAGS_METADATA_KEY)
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str) and t]
    if isinstance(raw, str) and raw:
        # JSONB sometimes round-trips a list as a string when the caller
        # did the JSON encoding themselves; try to recover.
        return string_to_tags(raw) or [raw]
    return string_to_tags(metadata.get(TAGS_STRING_METADATA_KEY))


def metadata_matches_all_tags(metadata: Any, required: list[str]) -> bool:
    """Return True when every tag in ``required`` is present in ``metadata``."""
    if not required:
        return True
    stored = set(extract_tags_from_metadata(metadata))
    return all(tag in stored for tag in required)
