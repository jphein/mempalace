# `mempalace.searcher`

Source: [`mempalace/searcher.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/searcher.py)

searcher.py — Find anything. Exact words.

Hybrid search: BM25 keyword matching + vector semantic similarity. The
drawer query is the floor — always runs — and closet hits add a rank-based
boost when they agree. Closets are a ranking *signal*, never a gate, so
weak closets (regex extraction on narrative content) can only help, never
hide drawers the direct path would have found.

## Classes

### `class SearchError(Exception)`

Raised when search cannot proceed (e.g. no palace found).

## Functions

### `build_where_filter`

```python
def build_where_filter(wing: str = None, room: str = None, tags: list = None, source_file: str = None) -> dict
```

Build ChromaDB-style where filter for wing/room/tag/source_file filtering.

``tags`` requires drawers to carry EVERY listed tag (AND logic). On the
postgres backend the filter is pushed down via the ``$contains_all``
JSONB operator; for chroma it's stripped here and applied as a
post-filter by the caller (see ``search_memories``). ChromaDB needs a
``$and`` only when ≥2 clauses are present; a single clause is returned
bare and zero clauses yield an empty filter (#1815).

### `search`

```python
def search(query: str, palace_path: str, wing: str = None, room: str = None, tags: list = None, n_results: int = 5, since: str = None, before: str = None, collection = None)
```

Search the palace. Returns verbatim drawer content.
Optionally filter by wing (project) or room (aspect), and/or narrow to
drawers whose ``filed_at`` falls in the ``[since, before)`` window —
same semantics as ``search_memories``/``list_drawers`` (#1128/#463).
Optionally filter by wing (project) or room (aspect).

Delegates to ``search_memories`` so CLI and MCP callers share the same
hybrid ranking, sqlite-BM25 fallback, and scope-aware warnings.

### `search_memories`

```python
def search_memories(query: str, palace_path: str, wing: str = None, room: str = None, tags: list = None, source_file: str = None, since: str = None, before: str = None, n_results: int = 5, max_distance: float = 0.0, vector_disabled: bool = False, candidate_strategy: str = 'vector', fusion_mode: str = 'convex', collection_name: str = None, lang: Optional[str] = None) -> dict
```

Programmatic search — returns a dict instead of printing.

Used by the MCP server and other callers that need data.

Hybrid search: BM25 keyword matching + vector semantic similarity.
The drawer query is the floor — always runs — and closet hits add a
rank-based boost when they agree.

Args:
    query: Natural language search query.
    palace_path: Path to the ChromaDB palace directory.
    wing: Optional wing filter.
    room: Optional room filter.
    source_file: Optional exact source_file filter. Matches the full
        stored source_file value verbatim (#1815).
    since: Optional inclusive ISO date/datetime lower bound on a
        drawer's ``filed_at`` (ingest time, the ``created_at`` shown in
        results) — ``[since, before)`` window semantics shared with
        ``list_drawers`` (#1128): wall-clock naive comparison, drawers
        with missing/unparseable ``filed_at`` excluded while a bound is
        active. Filtering happens after retrieval (ChromaDB rejects
        string operands for ``$gte``/``$lt``), so the candidate pool is
        widened via ``_candidate_pool_size`` — see
        ``date_filter_pool_truncated`` in the response.
    before: Optional exclusive ISO upper bound; see ``since``.
    n_results: Max results to return.
    max_distance: Max cosine distance threshold. The palace collection uses
        cosine distance (hnsw:space=cosine) — 0 = identical, 2 = opposite.
        Results with distance > this value are filtered out. A value of
        0.0 disables filtering. Typical useful range: 0.3–1.0.
    vector_disabled: When True, route to the sqlite-only BM25 fallback
        (#1222). Set by the MCP server when the HNSW capacity probe
        detects a divergence that would segfault chromadb on segment
        load.
    candidate_strategy: How candidates for the hybrid re-rank are gathered.

        * ``"vector"`` (default) — preserves historical behavior: top
          ``n_results * 4`` rows from the vector index are the rerank pool.
          Cheap; works well when query and target docs agree in the
          embedding space.
        * ``"union"`` — also pull top ``n_results * 3`` lexical candidates
          through the backend's ``lexical_search`` capability and merge
          them into the rerank pool (deduped by source_file). Catches docs
          with strong BM25 signal that are vector-distant from the query.
          Perf depends on the selected backend; opt in until the cost is
          characterized.

          When ``max_distance > 0.0`` is also set, BM25-only candidates
          are admitted only if their stored embeddings can be loaded and
          their computed vector distance satisfies that threshold.
    lang: Locale code for BM25 stop-word filtering (opt-in). When
        omitted, reads ``MempalaceConfig().lang_explicit`` — returns an
        empty set unless the user has set ``MEMPALACE_LANG`` /
        ``MEMPAL_LANG`` or ``config.json["lang"]``. Palaces without an
        explicit language skip filtering entirely, preserving pre-PR
        byte-identical scoring.
    fusion_mode: How the final candidate pool is ranked.

        * ``"convex"`` (default) — historical behavior: a weighted blend
          of normalized vector similarity and BM25 (``_hybrid_rank``).
        * ``"rrf"`` — Reciprocal Rank Fusion of the vector ordering and
          the BM25 ordering (``_rrf_rank``). Score-scale agnostic; only
          the rank orderings matter. Selectable for the #162 A/B study.

### `render_with_line_numbers`

```python
def render_with_line_numbers(text: 'str | None', start_line: int = 1) -> str
```

Prefix each line of ``text`` with ``[N] `` for read-time grid display.

Lines that already begin with ``[&lt;digits>]`` pass through unchanged,
but the counter still advances on them so callers can rely on positional
alignment with the original line indices.

``None`` is treated as empty string. Pure function.

### `extract_line_range`

```python
def extract_line_range(text: str, line_start: int, line_end: int) -> str
```

Return the 1-indexed inclusive slice ``[line_start, line_end]`` rendered with line numbers.

This is the closet-pointer read path. A pointer like ``→2026-01-18:L55-L72``
resolves by opening the day-drawer and calling ``extract_line_range(drawer_text, 55, 72)``.
Out-of-bounds ranges are clamped. Invalid ranges return ``""``.
