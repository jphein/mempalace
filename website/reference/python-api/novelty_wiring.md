# `mempalace.novelty_wiring`

Source: [`mempalace/novelty_wiring.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/novelty_wiring.py)

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

## Functions

### `is_novelty_tagging_enabled`

```python
def is_novelty_tagging_enabled(config: Optional[Any] = None) -> bool
```

Return True when write-time novelty tagging should run.

Resolution order: env ``MEMPALACE_NOVELTY_TAGGING`` > config
``novelty_tagging`` > default True. The env var accepts the usual
falsy strings (``0``, ``false``, ``no``, ``off``); anything else is
treated as enabled.

### `fetch_recent_window`

```python
def fetch_recent_window(collection: Any, wing: str, room: str, window_size: int) -> list[str]
```

Return up to ``window_size`` recent drawer documents from ``wing``/``room``.

"Recent" is approximated as the first ``window_size`` rows the
backend returns for the wing+room filter. Backends do not guarantee
insertion order on a bare ``get(where=...)``, but novelty scoring is
a coarse signal — any reasonably-sized window of peer drawers is
sufficient to discriminate novel content from routine acks.

Returns an empty list on any backend failure (so the caller can fall
through to the "fully novel" empty-window convention).

### `compute_novelty_tag`

```python
def compute_novelty_tag(collection: Any, wing: str, room: str, content: str, *, window_size: int = DEFAULT_WINDOW_SIZE, config: Optional[Any] = None, recent: Optional[list[str]] = None) -> Optional[str]
```

Return a novelty tag for ``content`` relative to recent drawers.

Fetches up to ``window_size`` peer drawers in the same wing+room,
computes the mean NCD novelty score, and classifies it into one of
``"novel"``, ``"routine"``, ``"redundant"``.

When ``recent`` is provided, the DB fetch is skipped and the given
window is used directly — callers that process many chunks in the
same room can pre-fetch once and pass the window to avoid N+1 queries.

Content is truncated to 1 MB before scoring to bound gzip cost on
oversized drawers.

Returns ``None`` when novelty tagging is disabled by env/config —
callers MUST treat ``None`` as "do not add the metadata key" so
operators can run an opt-out palace without an empty/garbage tag
leaking into stored metadata.

Any unexpected failure (collection error, scoring crash) is caught
and degraded to ``"novel"`` so writes never block on novelty.
