# `mempalace.rrf`

Source: [`mempalace/rrf.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/rrf.py)

Reciprocal Rank Fusion — combine ranked lists from N retrievers.

RRF is a classical fusion algorithm (Cormack, Clarke & Buettcher 2009):
given N ranked lists over the same item universe, score each item by
``sum(1/(k + rank_i))`` across the lists where it appears. Items absent
from a list contribute 0. ``k=60`` is the conventional smoothing
constant — large enough that the top-1 advantage (1/61 vs 1/62) is
small relative to the gap between top-K and absent.

We use this to fuse vector-search rankings from multiple encoders. The
existing :func:`mempalace.searcher._hybrid_rank` already fuses BM25
with vector cosine via a convex combination on shared candidates; RRF
solves a different problem — fusing *ranked lists* whose underlying
score scales are not comparable. Different encoders' cosine spaces
have different distributions, so direct distance-averaging is
unprincipled; RRF only requires the rank ordering.

This module is pure and dependency-free; the multi-encoder retrieval
glue lives in :mod:`mempalace.multi_encoder`.

Used by
-------

* :func:`mempalace.multi_encoder.fused_query` — query-time fusion
  across N encoder-bound palaces (research feature, gated by
  ``PALACE_USE_MULTI_ENCODER_RRF``).
* ``scripts/eval_multi_encoder_rrf.py`` — evaluation harness.

References
----------

* Cormack et al. 2009, "Reciprocal Rank Fusion outperforms Condorcet
  and individual rank learning methods", SIGIR '09.
* ``scripts/verify_rrf_ftcode5k.py`` — the surrogate-RRF probe that
  motivated this implementation. It only knows rank-of-expected, so
  fuses with ``min(rank)``; this module does proper score-based
  fusion across the full ranked lists.

## Functions

### `rrf_scores`

```python
def rrf_scores(rank_lists: Sequence[Sequence[T]], key: Callable[[T], Hashable] | None = None, k: int = DEFAULT_K) -> dict[Hashable, float]
```

Compute RRF scores for items across ``rank_lists``.

Parameters
----------
rank_lists
    Sequence of ranked lists. Each inner sequence is ordered best
    → worst. The rank an item contributes is its 1-indexed
    position within its containing list.
key
    Function mapping an item to a hashable identity used for
    cross-list aggregation. Defaults to the item itself, which
    works for strings/ints/tuples but not dicts.
k
    RRF smoothing constant. Default 60 per Cormack et al.

Returns
-------
dict
    ``&#123;identity: aggregate_score}`` where score is
    ``sum(1/(k + rank_i))`` over the lists where the identity
    appears.

Notes
-----
Within a single list, only the *first* occurrence of an identity
counts toward the rank. This matters when a downstream caller
feeds a list with duplicates (e.g. two chunks from the same
source file collapsed by source-file identity); the better-ranked
duplicate wins, the worse-ranked is ignored.

### `rrf_fuse`

```python
def rrf_fuse(rank_lists: Sequence[Sequence[T]], key: Callable[[T], Hashable] | None = None, k: int = DEFAULT_K, representative: Callable[[Sequence[T]], T] | None = None) -> list[tuple[Hashable, float, T]]
```

Return RRF-fused items sorted best-first.

Parameters
----------
rank_lists
    Same as :func:`rrf_scores`.
key
    Same as :func:`rrf_scores`. Required when items are not
    natively hashable (e.g. dicts).
k
    RRF smoothing constant.
representative
    Given the list of all occurrences of one identity (across
    all input lists, in input-order), return the item to surface
    in the fused output. Defaults to the first occurrence —
    which corresponds to the best-ranked-list/highest-rank
    version of the item. Useful when one list carries more
    metadata than another and you want to prefer that copy.

Returns
-------
list of (identity, rrf_score, representative_item)
    Sorted descending by ``rrf_score``. Ties broken by
    first-seen order (stable sort).

### `explain_fusion`

```python
def explain_fusion(rank_lists: Sequence[Sequence[T]], list_names: Sequence[str] | None = None, key: Callable[[T], Hashable] | None = None, k: int = DEFAULT_K) -> list[dict[str, Any]]
```

Diagnostic — return per-identity rank breakdown for debugging.

Useful when an item appears in the fused top-K and you want to
see which encoders ranked it where. Not used in the hot path.
