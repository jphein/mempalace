# `mempalace.miner`

Source: [`mempalace/miner.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/miner.py)

miner.py — Files everything into the palace.

Reads mempalace.yaml from the project directory to know the wing + rooms.
Routes each file to the right room based on content.
Stores verbatim chunks as drawers. No summaries. Ever.

## Classes

### `class GitignoreMatcher`

Lightweight matcher for one directory's .gitignore patterns.

#### `__init__`

```python
def __init__(self, base_dir: Path, rules: list)
```

#### `from_dir`

```python
def from_dir(cls, dir_path: Path)
```

#### `matches`

```python
def matches(self, path: Path, is_dir: bool = None)
```

## Functions

### `load_gitignore_matcher`

```python
def load_gitignore_matcher(dir_path: Path, cache: dict)
```

Load and cache one directory's .gitignore matcher.

### `is_gitignored`

```python
def is_gitignored(path: Path, matchers: list, is_dir: bool = False) -> bool
```

Apply active .gitignore matchers in ancestor order; last match wins.

### `should_skip_dir`

```python
def should_skip_dir(dirname: str) -> bool
```

Skip known generated/cache directories before gitignore matching.

### `normalize_include_paths`

```python
def normalize_include_paths(include_ignored: list) -> set
```

Normalize comma-parsed include paths into project-relative POSIX strings.

### `is_exact_force_include`

```python
def is_exact_force_include(path: Path, project_path: Path, include_paths: set) -> bool
```

Return True when a path exactly matches an explicit include override.

### `is_force_included`

```python
def is_force_included(path: Path, project_path: Path, include_paths: set) -> bool
```

Return True when a path or one of its ancestors/descendants was explicitly included.

### `load_config`

```python
def load_config(project_dir: str) -> dict
```

Load mempalace.yaml from project directory (falls back to mempal.yaml).

### `detect_room`

```python
def detect_room(filepath: Path, content: str, rooms: list, project_path: Path) -> str
```

Route a file to the right room.
Priority:
1. Folder path exactly matches a room name or keyword
2. Filename exactly matches a room name or keyword
3. Content keyword scoring (word-boundary matching)
4. Fallback: "general"

Fork-ahead: stricter than upstream's substring-match. Fork tests in
test_miner.py guarantee that a folder named ``components`` does NOT
route to a room whose keyword is ``component`` (substring would match);
a folder named ``src`` does not match anything just because ``src`` is
a substring of other words; and content scoring uses word boundaries
so ``api`` in ``capital`` doesn't bump the backend score.

### `chunk_text`

```python
def chunk_text(content: str, source_file: str, chunk_size: int = None, chunk_overlap: int = None, min_chunk_size: int = None, *, symbol_header_prefix = None) -> list
```

Split content into drawer-sized chunks.
Tries to split on paragraph/line boundaries.
Returns list of &#123;"content": str, "chunk_index": int, "line_start": int, "line_end": int}

``line_start`` / ``line_end`` are 1-indexed line numbers in the stripped
source, giving an approximate locator for where the chunk came from.
Closet pointers (Tier 6a) use this to emit ``YYYY-MM-DD:L42-L78`` segments
so retrieval can jump straight to the right span without opening the
whole drawer.

Optional ``chunk_size`` / ``chunk_overlap`` / ``min_chunk_size`` params
override module-level defaults when provided (upstream #1024).

Args:
    content: text to chunk.
    source_file: file path used for room/topic inference and (when
        ``symbol_header_prefix`` is supplied) chunk enrichment.
    chunk_size: max chars per chunk; falls back to ``CHUNK_SIZE``.
    chunk_overlap: chars of overlap between adjacent chunks; falls back
        to ``CHUNK_OVERLAP``.
    min_chunk_size: minimum chunk size; drops trailing fragments shorter
        than this. Falls back to ``MIN_CHUNK_SIZE``.
    symbol_header_prefix: optional callable
        ``(chunk_text, source_file, chunk_index) -> str``. When
        supplied, the returned header is prepended to each chunk
        with a blank line separator before storage. Lets AST-lite
        symbol enrichment (function names, class paths, imports)
        and similar representation-axis experiments stack on this
        code path without forking it. Default ``None`` preserves
        original behavior exactly. Discussed in
        MemPalace/mempalace#1384.

Returns:
    list of ``&#123;"content": str, "chunk_index": int}``.

### `add_to_known_entities`

```python
def add_to_known_entities(entities_by_category: dict, wing: str = None) -> str
```

Union ``entities_by_category`` into ``~/.mempalace/known_entities.json``.

Accepts ``&#123;category: [names]}`` shape as produced by ``mempalace init``
and merges into the registry the miner reads at mine time. Existing
categories are preserved untouched unless also present in the input;
for categories present in both, entries are unioned case-insensitively
without changing the on-disk ordering of pre-existing names.

If a category is stored on-disk as ``&#123;name: code}`` (the alternate
miner-supported shape, used by dialect-style configs), new names are
added as keys with ``None`` values so existing code mappings aren't
overwritten. A later compress pass can assign codes.

When ``wing`` is provided AND ``entities_by_category`` contains a
``topics`` list, those topics are also recorded under
``topics_by_wing[wing]`` (case-insensitive dedup, preserving the
casing of the first observed name). This is the signal source for
``palace_graph.compute_topic_tunnels`` at mine time. Topics for a
wing are *replaced*, not unioned, so a re-run of ``init`` reflects
the user's latest confirmation rather than accumulating stale labels
indefinitely.

The in-process cache is invalidated on write so same-process callers
(notably ``cmd_init`` → ``cmd_mine`` in sequence) see the update
immediately instead of waiting for a mtime re-check.

Returns the registry path as a string for logging.

### `get_topics_by_wing`

```python
def get_topics_by_wing() -> dict
```

Return ``topics_by_wing`` from the global registry as a dict.

Returns ``&#123;}`` if the registry is missing, malformed, or has no
``topics_by_wing`` key. Casing is preserved from disk; callers that
need case-insensitive comparison should normalize themselves.

### `detect_hall`

```python
def detect_hall(content: str) -> str
```

Route content to a hall based on keyword scoring.

Halls connect rooms within a wing — they categorize the TYPE of content
(emotional, technical, family, etc.) while rooms categorize the TOPIC.

### `add_drawer`

```python
def add_drawer(collection, wing: str, room: str, content: str, source_file: str, chunk_index: int, agent: str)
```

Add one drawer to the palace.

Returns a dict ``&#123;"id": drawer_id, "warnings": [...]}``. ``warnings``
is a list of human-readable strings — empty when the room is one of
the canonical 7 (see ``mempalace.room_taxonomy``). Per #86 a non-
canonical room is accepted and surfaced via the warning instead of
rejected at the backend.

### `add_drawers`

```python
def add_drawers(collection, wing, room, chunks, source_file, agent)
```

Batch-insert multiple drawers in one ChromaDB call per sub-batch.

Collects all chunks into batch lists and upserts them in groups of
``DRAWER_UPSERT_BATCH_SIZE`` (alias of ``CHROMA_BATCH_LIMIT``, kept
so existing fork tests that ``monkeypatch.setattr(miner,
"DRAWER_UPSERT_BATCH_SIZE", N)`` still drive the sub-batch loop).
Returns ``(drawers_added, batch_ids, warnings)`` where ``warnings``
is a list of room-taxonomy warning strings (empty when ``room`` is
canonical; see ``mempalace.room_taxonomy``). The room is shared
across every chunk in a batch, so a single warning per call is
sufficient — no per-drawer fan-out.

### `process_file`

```python
def process_file(filepath: Path, project_path: Path, collection, wing: str, rooms: list, agent: str, dry_run: bool, closets_col = None, chunk_size: int = None, chunk_overlap: int = None, min_chunk_size: int = None, max_chunks_per_file: Optional[int] = None, room_resolver: Optional[callable] = None) -> tuple
```

Read, chunk, route, and file one file.

Returns ``(drawer_count, room_name, skip_reason)``. ``skip_reason`` is
``None`` on success and on every non-chunk-cap skip path: already
filed (pre- or post-lock re-check), unreadable (``OSError``), or
too-short content (below ``min_chunk_size``). It is ``"chunk_cap"``
when the per-file chunk cap aborted the file. Callers use the tag to
surface a separate counter in the mine summary (see #1455).

### `scan_project`

```python
def scan_project(project_dir: str, respect_gitignore: bool = True, include_ignored: list = None) -> list
```

Return list of all readable file paths under ``project_dir``.

Skips symlinks and oversized files. Each skipped symlink is logged to
``sys.stderr`` with a ``  SKIP: &lt;relative-path> (symlink)`` line so the
caller can tell why a directory looks empty after walking.

### `mine`

```python
def mine(project_dir: str, palace_path: str, wing_override: str = None, agent: str = 'mempalace', limit: int = 0, dry_run: bool = False, respect_gitignore: bool = True, include_ignored: list = None, files: list = None, max_chunks_per_file: Optional[int] = None, *, collection = None, closets_collection = None)
```

Mine a project directory into the palace.

``files`` may optionally be a pre-scanned list of file paths from
:func:`scan_project`. When provided, the corpus walk is skipped — the
caller (e.g. ``init`` showing a file-count estimate before the mine
prompt) avoids walking the tree twice. When ``None`` (the default),
``mine`` walks the tree itself just like before.

``max_chunks_per_file`` overrides the per-file chunk cap (see
:func:`_resolve_max_chunks_per_file`). ``None`` defers to
``MEMPALACE_MAX_CHUNKS_PER_FILE`` or ``MAX_CHUNKS_PER_FILE``; ``0``
disables the cap entirely (#1455).

``collection`` / ``closets_collection`` let a single-client host
(e.g. palace-daemon) write through its own already-open backend handle
instead of constructing a second one. When ``collection`` is supplied:

  - the internal ``get_collection(palace_path)`` call is skipped;
  - ``mine_palace_lock(palace_path)`` is NOT acquired (the caller
    guarantees exclusivity around its own client);
  - the post-mine FTS5 ``_validate_palace_fts5_after_mine`` step is
    skipped because it would call ``_close_chroma_handles`` against
    the caller's still-open client and reopen sqlite3 read-only —
    the caller can run its own integrity check on its own schedule.

If ``collection`` is supplied but ``closets_collection`` is not, the
closet upserts use the same injected collection's backend the same
way the non-injected path would (via ``get_closets_collection`` on
the live palace path) — so callers that only have a drawers handle
are still served correctly.

Existing positional/keyword callers see no behaviour change: when
both kwargs are omitted, ``mine`` walks exactly the original code
path (construct client, acquire lock, validate at end).

### `status`

```python
def status(palace_path: str)
```

Show what's been filed in the palace.
