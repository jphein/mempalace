# `mempalace.multi_encoder`

Source: [`mempalace/multi_encoder.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/multi_encoder.py)

Multi-encoder retrieval — query N encoder-bound palaces, RRF-fuse.

RESEARCH FEATURE. Default off. Gated by ``PALACE_USE_MULTI_ENCODER_RRF=1``.

Background
----------

The 2026-05-15 chunking×encoder reproduction (`techempower-org/mempalace#82`)
measured **+0.0841 MRR vs. best solo** when fusing default ONNX MiniLM
with two adaptmem FT-Code SentenceTransformer checkpoints under 3-way
RRF on the n=200 git-derived probe set. HyDE — the cheaper alternative
for the same vocabulary-bridging problem — was ruled out for our
institutional-memory corpus (see ``reference-hyde-institutional-memory``
memo). This module is the next lever.

Design
------

Multi-encoder retrieval requires that each encoder's query embedding
land in a vector space populated with vectors *from that same encoder*.
That means **one mined palace per encoder** — single-palace mode would
have FT-encoded queries cosine-matched against ONNX-encoded drawer
vectors, which is noise. Production deployment therefore requires
N parallel mines. The eval harness automates this; production users
opt-in via env.

This module covers the *query side* only:

1. Read the encoder roster from env.
2. For each encoder: load model, encode the query, call
   ``collection.query(query_embeddings=[v], n_results=K)`` against the
   matching palace.
3. Fuse the resulting ranked lists via Reciprocal Rank Fusion (60/k).
4. Return a result shaped like a single ``chromadb`` query result —
   one ``documents``/``metadatas``/``distances`` row — so the caller
   (``mempalace.searcher.search_memories``) can stay unchanged
   downstream.

Configuration
-------------

* ``PALACE_USE_MULTI_ENCODER_RRF=1`` — master switch.
* ``PALACE_RRF_ENCODERS=default,ft-code-1000,ft-code-5000`` — encoder
  names. The literal ``default`` is the built-in ONNX MiniLM (no model
  path needed). Other names map to model paths via the next variable.
* ``PALACE_RRF_ENCODER_PATHS=ft-code-1000=/path/to/ft1000/model,ft-code-5000=/path/to/adaptmem-cache/model``
  — comma-separated ``name=path`` pairs. The path is loaded as a
  ``sentence_transformers.SentenceTransformer``.
* ``PALACE_RRF_PALACES=default=/var/lib/palace/main,ft-code-1000=/var/lib/palace/ft1000,ft-code-5000=/var/lib/palace/ft5k``
  — comma-separated ``name=palace_path`` pairs. Each palace was mined
  with the matching encoder. If a name is missing here, the request
  falls back to the palace passed into ``search_memories`` (with a
  warning the first time per process) — useful for benchmarking.
* ``PALACE_RRF_K=60`` — RRF smoothing constant. Cormack 2009 default.
* ``PALACE_RRF_OVERFETCH=3`` — per-encoder ``n_results`` multiplier.
  Larger overfetch = more chance the right doc shows up in at least one
  list; rapidly diminishing returns past 3x.

Cost
----

Query latency goes up roughly Nx — each encoder embeds the query, each
palace runs a vector probe. Encoders run in series (cheap; query
encoding is one forward pass through a 22M-parameter model, ~50ms CPU).
Storage: Nx (one palace per encoder); ingest is Nx (mine once per
encoder). These costs make this a **research lever**, not a default.

## Classes

### `class EncoderSpec`

One encoder roster entry.

## Functions

### `is_enabled`

```python
def is_enabled() -> bool
```

True iff the multi-encoder feature flag is set.

### `load_roster`

```python
def load_roster() -> list[EncoderSpec]
```

Read encoder roster from env.

Returns a list of :class:`EncoderSpec`. Always includes
``default`` first if the env roster is empty; otherwise honors
the requested order (RRF doesn't depend on order, but consistent
ordering helps reproducibility of debug logs).

### `get_encoder`

```python
def get_encoder(spec: EncoderSpec) -> Callable[[str], list[float]]
```

Return a cached query-encoding callable for ``spec``.

Loading a SentenceTransformer costs ~1.5s and ~100MB RAM per
model; cache aggressively. Cache key is the encoder name, not the
model path — two specs that disagree on path under the same name
is a config bug we'd rather catch than silently honor.

### `reset_encoder_cache`

```python
def reset_encoder_cache() -> None
```

Drop all cached encoders. Test/eval-only — not used in the hot path.

### `fused_query`

```python
def fused_query(query: str, palace_path: str, n_results: int, where: Optional[dict] = None, collection_getter: Optional[Callable[[str], Any]] = None) -> dict
```

Run one query across N encoders, fuse via RRF, return chromadb-shape result.

Parameters
----------
query
    Query text. Encoded once per encoder.
palace_path
    Fallback palace path when an encoder spec has no ``palace_path``.
    In eval mode every spec has its own palace and this fallback is
    unused.
n_results
    Final desired result size. Per-encoder fetch is ``overfetch *
    n_results``.
where
    ChromaDB metadata filter dict. Forwarded unchanged to each
    backend ``.query()``.
collection_getter
    ``palace_path -> collection`` resolver. Defaults to
    :func:`mempalace.palace.get_collection`. Injected for tests.

Returns
-------
dict
    ``&#123;"documents": [[...]], "metadatas": [[...]], "distances":
    [[...]], "ids": [[...]]}`` — the same shape ChromaDB's
    ``collection.query()`` returns for one query text. The outer
    list has one element (one query). Order matches the fused
    ranking; size is ``min(len(fused), n_results)``.

Notes
-----
Fusion identity is ``meta.source_file + meta.chunk_index`` when
chunk_index is present; otherwise the drawer id. This collapses
duplicate drawers across encoders (the same source_file at the
same chunk_index lands in multiple encoder palaces with the same
text but potentially different cosine distances).

Distance handling: each encoder's cosine space is its own. We
surface the distance from whichever encoder ranked the winning
item *highest* (i.e. the representative chosen by ``rrf_fuse``).
Downstream code uses distance for the ``max_distance`` gate and
for ``similarity = 1 - distance``; the convention here is
consistent with single-encoder mode for the default encoder and
approximate but useful for the others.
