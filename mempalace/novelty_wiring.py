"""
novelty_wiring.py — write-time novelty tagging
==============================================

Glue that connects :mod:`mempalace.novelty` (gzip NCD scoring) to the
three drawer write paths (MCP ``tool_add_drawer``, filesystem miner,
conversation miner). At write time, the new drawer's content is scored
against a small window of recent drawers in the same wing/room and
tagged ``novel`` / ``routine`` / ``redundant`` in metadata.

Design constraints from issue #178:
- This is a TAG, not a GATE — writes never block on novelty.
- Any failure fetching the window or scoring fails open to ``"novel"``
  so the write still lands with a defensible default tag.
- The window is intentionally small (default 15 drawers) so the inline
  gzip compression cost stays under the per-write performance budget.
- Opt-out via env ``MEMPALACE_NOVELTY_TAGGING=0`` or config
  ``"novelty_tagging": false`` — when disabled, ``compute_novelty_tag``
  returns ``None`` and callers MUST NOT add the metadata key.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .novelty import classify_novelty, novelty_score


__all__ = [
    "DEFAULT_WINDOW_SIZE",
    "compute_novelty_tag",
    "is_novelty_tagging_enabled",
]


logger = logging.getLogger(__name__)


DEFAULT_WINDOW_SIZE = 15


def is_novelty_tagging_enabled(config: Optional[Any] = None) -> bool:
    """Return True when write-time novelty tagging should run.

    Resolution order: env ``MEMPALACE_NOVELTY_TAGGING`` > config
    ``novelty_tagging`` > default True. The env var accepts the usual
    falsy strings (``0``, ``false``, ``no``, ``off``); anything else is
    treated as enabled.
    """
    env_val = os.environ.get("MEMPALACE_NOVELTY_TAGGING")
    if env_val is not None:
        return env_val.strip().lower() not in ("0", "false", "no", "off", "")
    if config is not None:
        file_cfg = getattr(config, "_file_config", None)
        if isinstance(file_cfg, dict) and "novelty_tagging" in file_cfg:
            return bool(file_cfg["novelty_tagging"])
    return True


def _fetch_recent_window(
    collection: Any, wing: str, room: str, window_size: int
) -> list[str]:
    """Return up to ``window_size`` recent drawer documents from ``wing``/``room``.

    "Recent" is approximated as the first ``window_size`` rows the
    backend returns for the wing+room filter. Backends do not guarantee
    insertion order on a bare ``get(where=...)``, but novelty scoring is
    a coarse signal — any reasonably-sized window of peer drawers is
    sufficient to discriminate novel content from routine acks.

    Returns an empty list on any backend failure (so the caller can fall
    through to the "fully novel" empty-window convention).
    """
    if collection is None or not wing or not room:
        return []
    try:
        result = collection.get(
            where={"$and": [{"wing": wing}, {"room": room}]},
            limit=window_size,
            include=["documents"],
        )
    except Exception:
        logger.debug(
            "novelty window fetch failed for %s/%s", wing, room, exc_info=True
        )
        return []

    documents = _extract_documents(result)
    return [doc for doc in documents if isinstance(doc, str) and doc]


def _extract_documents(result: Any) -> list[str]:
    """Pull a ``documents`` list out of a backend GetResult shape.

    Both dict-shaped Chroma results and dataclass-shaped backend results
    expose ``documents``; tolerate both without importing either type.
    """
    if result is None:
        return []
    if isinstance(result, dict):
        return result.get("documents") or []
    docs = getattr(result, "documents", None)
    return docs or []


def compute_novelty_tag(
    collection: Any,
    wing: str,
    room: str,
    content: str,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    config: Optional[Any] = None,
) -> Optional[str]:
    """Return a novelty tag for ``content`` relative to recent drawers.

    Fetches up to ``window_size`` peer drawers in the same wing+room,
    computes the mean NCD novelty score, and classifies it into one of
    ``"novel"``, ``"routine"``, ``"redundant"``.

    Returns ``None`` when novelty tagging is disabled by env/config —
    callers MUST treat ``None`` as "do not add the metadata key" so
    operators can run an opt-out palace without an empty/garbage tag
    leaking into stored metadata.

    Any unexpected failure (collection error, scoring crash) is caught
    and degraded to ``"novel"`` so writes never block on novelty.
    """
    if not is_novelty_tagging_enabled(config):
        return None
    try:
        recent = _fetch_recent_window(collection, wing, room, window_size)
        score = novelty_score(content or "", recent)
        return classify_novelty(score)
    except Exception:
        logger.debug(
            "novelty tag computation failed for %s/%s — defaulting to 'novel'",
            wing,
            room,
            exc_info=True,
        )
        return "novel"
