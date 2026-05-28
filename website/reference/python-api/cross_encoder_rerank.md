# `mempalace.cross_encoder_rerank`

Source: [`mempalace/cross_encoder_rerank.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/cross_encoder_rerank.py)

Optional cross-encoder reranking for the retrieval path.

OPT-IN FEATURE. Default off. Gated by ``MEMPALACE_RERANK_CROSS_ENCODER=1`` or
``config.json &#123;"cross_encoder_rerank": true}``.

Background
----------

Re-scores the top-N candidates produced by the existing hybrid pipeline
(vector + BM25 + AGE fusion) using a lightweight cross-encoder. The
True Memory comparison (`docs/research/2026-05-24-true-memory-comparison.md`)
showed a cheap reranker captures most of the rerank value — upgrading
from 22M (``ms-marco-MiniLM-L-6-v2``) to 149M (``ms-marco-MiniLM-L-12-v2``)
only moves the needle 1.3pp within the 256d subfamily. We therefore ship
the smallest effective model and let operators upgrade via env if their
workload justifies it.

Design
------

The rerank runs *after* the existing fusion (convex / RRF) — it never
replaces fusion, it reorders the already-fused top-N. This composes with
every ``candidate_strategy`` (vector, union, hybrid) and with every
``fusion_mode`` (convex, rrf). When disabled, this module imports nothing
heavy and adds zero query-time cost.

Constraints (from techempower-org/mempalace#179)
------------------------------------------------

* **Local-first.** ``sentence-transformers`` is an optional ``[rerank]``
  extra; the import is lazy. Operators who opt in install the extra.
* **CPU-only by default.** ``ms-marco-MiniLM-L-6-v2`` is 22M parameters
  — fits on CPU with a sub-200ms ceiling for top-25 rerank batches.
* **Default off.** Per JP's no-model-at-query-time default — no external
  model loads unless the env or config flag is set.

Configuration
-------------

* ``MEMPALACE_RERANK_CROSS_ENCODER=1`` (env) or
  ``"cross_encoder_rerank": true`` (config.json) — enable the stage.
* ``MEMPALACE_RERANK_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2``
  — override the model. Default is the 22M MiniLM-L-6 cross-encoder.
* ``MEMPALACE_RERANK_TOP_N=25`` — rerank ceiling. Latency scales linearly
  with this; the rerank only reorders, so the ceiling is a quality/cost
  knob, not a recall floor.

## Functions

### `is_enabled`

```python
def is_enabled(file_config: Optional[dict] = None) -> bool
```

True iff the cross-encoder rerank flag is set in env or config.

Env wins over file config (matches the rest of MempalaceConfig).

### `get_model_name`

```python
def get_model_name(file_config: Optional[dict] = None) -> str
```

Return the cross-encoder model name to load.

### `get_top_n`

```python
def get_top_n(file_config: Optional[dict] = None) -> int
```

Return the rerank ceiling (top-N candidates to rerank).

### `get_scorer`

```python
def get_scorer(model_name: str) -> Callable[[list[tuple[str, str]]], list[float]]
```

Return a cached scorer for ``model_name``.

Cross-encoder load is ~90MB and a few seconds; cache aggressively.
The cache survives the life of the process and is process-local
(no cross-process sharing required at this scale).

### `reset_model_cache`

```python
def reset_model_cache() -> None
```

Drop all cached cross-encoder models. Test/eval-only.

### `rerank`

```python
def rerank(query: str, hits: list[dict], *, model_name: str = DEFAULT_MODEL, top_n: int = DEFAULT_TOP_N, scorer: Optional[Callable[[list[tuple[str, str]]], list[float]]] = None) -> list[dict]
```

Reorder ``hits`` by cross-encoder relevance to ``query``.

Only the top ``top_n`` hits are rescored — anything past that keeps its
fused position. This bounds latency on large candidate pools while
still letting the rerank correct the head, which is where it matters
for R@k.

Returns a new list (does not mutate the input list). Each rescored hit
gains a ``cross_encoder_score`` key carrying the raw model output for
downstream observability. Hits without text are scored as ``-inf`` so
they sink to the bottom of the reranked window — keeping recall
invariant (no hit is dropped).

``scorer`` is an injection seam for tests so unit tests don't need to
load the real model.
