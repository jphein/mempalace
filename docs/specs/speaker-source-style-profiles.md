# Speaker / Source Style Profiles — per-source retrieval weighting

> **Status:** proposed 2026-05-29 (gated — not to be built until the trigger fires)
> **Owner:** JP
> **Issue:** [techempower-org/mempalace#180](https://github.com/techempower-org/mempalace/issues/180)
> **Trigger:** a genuinely multi-speaker corpus where source disambiguation
> measurably improves retrieval
> **Lower priority than:** novelty scoring (#178), cross-encoder rerank (#179, shipped)

## Problem & motivation

The palace holds 335K+ drawers mined from conversations with several distinct
AI agents (Claude, Gemini, Copilot, GPT) and from many session/project
contexts. Each drawer already records *who produced it* in `added_by`
metadata (`convo_miner.py:96`, `miner.py:1262`, `format_miner.py:570`,
`mcp_server.py:1790`) and *where it came from* in `source_file`. But
retrieval ignores both: a query is matched against the whole corpus uniformly
by vector + BM25 + AGE fusion (`searcher.py:_hybrid_rank`, `_rrf_rank`).

That uniform treatment adds noise to context-specific queries:

- "What did **Claude** say about the daemon cutover?" returns Gemini and
  Copilot fragments that mention the daemon equally well.
- "What did I tell **Gemini** about the realm map?" pulls in Claude sessions
  on the same topic.

True Memory's L0 "Speaker Engram" layer
([arXiv:2605.04897](https://arxiv.org/abs/2605.04897), Section 3.1) builds
lightweight char-n-gram style vectors per speaker and uses them to weight
retrieval. The fork's own comparison
(`docs/research/2026-05-24-true-memory-comparison.md`, "What MemPalace could
learn → Speaker profiles") flags this as plausibly useful at our scale —
*with an important caveat the issue itself states*: the single-user palace
with wing/room scoping already provides most of the disambiguation True
Memory's speaker profiles offer. This spec therefore designs the feature
**and** the test that decides whether it earns its place.

## Trigger condition (what must exist before this is built)

This feature is filed-for-future. It should only be built once **all** of the
following hold — because below that bar, wing/room scoping plus an explicit
`added_by` metadata filter already solves the problem more cheaply:

1. **A genuinely multi-speaker corpus** where `added_by` carries real
   discriminative signal across many sources — not the de-facto single-author
   pattern most installs have. (Today's corpus qualifies on volume; the open
   question is whether the *styles* are separable — see Open Questions.)
2. **A measured retrieval gap** that source-style weighting closes: a
   labeled query set with source-specific intent where the current
   pipeline (and a plain `added_by` filter) under-performs. The
   multipass-SME corpus is the natural home for these (#180 notes
   speaker/source disambiguation reduces noise from irrelevant agent
   conversations).
3. **#178 (novelty scoring) and #179 (cross-encoder rerank) are settled** —
   the issue explicitly ranks this below both. Speaker weighting on top of an
   unsettled rerank stack would confound the A/B.

**Cheaper alternative that must be ruled out first:** an exact `added_by` /
`source_file` *filter* (or a small bounded boost keyed on an explicit
`speaker=` search parameter) may capture the entire win without style
vectors. The trigger for the *full* style-profile design is specifically a
demonstrated gap that the exact-match filter does **not** close — i.e.,
queries where the user wants "Claude-*style* content" rather than "content
literally tagged Claude." Until that gap is shown, the design degrades to the
filter (see Proposed design §0).

## Proposed design

Staged from cheapest to most speculative, so the trigger can stop at the
first stage that satisfies the measured need.

### §0 — Exact-match scoping (the floor; may be all that's needed)

Add an explicit, optional retrieval parameter that filters/boosts on the
already-present `added_by` (and `source_file`) metadata. This is *not* a
style profile — it is exact-match scoping, and it composes with the existing
`wing`/`room`/`tags` filters the backends already index:

- `mempalace_search(query=..., speaker="claude")` — scope to (or bounded-boost)
  hits whose `added_by` matches.

If the measured gap closes here, the style-vector machinery (§1–§2) is never
built. This stage is the honest baseline every later stage must beat.

### §1 — Per-source style vectors (the True Memory shape)

For each distinct `added_by` (and optionally `source_file` cluster), build a
lightweight **style profile** offline:

- **Char-n-gram or token-distribution vector** over a sample of that source's
  drawers. Char-n-grams (True Memory's choice) capture *style*
  (phrasing, formatting, verbosity) independent of *topic* — the point is to
  separate "how Claude writes" from "what the drawer is about". Cheap, no
  model: a hashed char-3..5-gram TF vector, L2-normalized.
- Stored as a small per-source artifact, recomputed incrementally as new
  drawers land (the same offline-batch posture as the closet index and the
  proposed KG triple worker — never on the write hot path).

At query time, when the query carries source intent (explicit `speaker=` or
an inferred source from the query text), the source's style vector
contributes a **bounded** distance nudge toward matching-source hits —
following the `ratings.py` / `recency.py` capped-adjustment idiom so it
reorders neighbors but never drops a hit (recall preserved).

### §2 — Inferred source intent (most speculative)

Infer which source a query is "about" without an explicit `speaker=`
parameter — e.g. classify "what did Claude say…" → source intent `claude`.
This is the highest-risk stage (mis-inference silently down-weights correct
hits) and should only follow a clear §1 win. Local-first classification only
(lexical rules or a local LLM), never an external API by default.

### Bounded reweight (recall-safe, shared with #181)

Whatever the source signal, the *application* is the fork's standard capped
distance adjustment, not a hard filter (except the explicit §0 filter, which
the user opts into knowingly):

```python
# mempalace/speaker_profiles.py (NEW), mirroring ratings.py / recency.py
SPEAKER_DISTANCE_CAP = 0.10  # tuned by A/B on our corpus

def speaker_distance_adjustment(hit_source: str, query_source: str | None,
                                style_sim: float) -> float:
    """Small, bounded nudge toward query-relevant sources.

    Clamped to ±SPEAKER_DISTANCE_CAP so it reorders neighbors but never
    displaces a relevant drawer out of the result set — recall is preserved.
    Returns 0.0 when there is no source intent (default behavior unchanged).
    """
```

No source intent ⇒ 0.0 adjustment ⇒ today's behavior, bit-for-bit.

### Gating

Off by default, env-wins-over-config, identical to the other optional stages:

- `MEMPALACE_SPEAKER_PROFILES=1` / `"speaker_profiles": true` — enable.
- `MEMPALACE_SPEAKER_CAP=0.10` — the cap, for A/B tuning.
- `speaker=` — explicit per-query source scope (works even with the feature
  otherwise off, since §0 is just a metadata filter).

## Integration points in current code (cite real files/functions)

- **`mempalace/convo_miner.py:96`, `mempalace/miner.py:1262`,
  `mempalace/format_miner.py:570` / `:651`, `mempalace/mcp_server.py:1790`**
  — every write path already sets `added_by`. §0 needs zero new write-side
  work; the signal is present today.
- **`mempalace/searcher.py:2079`** — the hit `entry` dict does **not**
  currently surface `added_by` (it carries `source_file`, `wing`, `room`,
  `topic`, `matched_via`). §0/§1 must thread `meta.get("added_by")` into the
  entry so the reweight stage and `speaker=` filter can read it. This is the
  primary data-flow change (see Data/schema impact).
- **`mempalace/searcher.py:2065`** — `effective_dist = ... + rating_adj +
  recency_adj`. The speaker adjustment becomes another bounded addend here,
  or a post-fusion reorder stage near `searcher.py:2182` (alongside the
  cross-encoder / modality stages). The query-dependent intent argues for a
  stage; the exact-match `speaker=` filter can apply during construction.
- **`mempalace/ratings.py:76` (`RATING_DISTANCE_CAP=0.12`) /
  `mempalace/recency.py:79`** — the exact recall-safe idiom
  `mempalace/speaker_profiles.py` mirrors (cap, clamp both directions,
  recall-preserving comment).
- **`mempalace/config.py:400-437`** — the optional-stage config-property
  pattern (`cross_encoder_rerank` family); add `speaker_profiles` /
  `speaker_cap` in the same env-wins, lazy-import, off-by-default style.
- **Backend metadata filtering** — `added_by` lives in the same metadata
  payload the postgres backend indexes for `wing`/`room`/`tags`
  (`backends/postgres.py`); the §0 exact-match filter reuses that path
  rather than adding a new index, provided `added_by` is filter-exposed.
- **Offline profile build** — model the per-source style-vector job on the
  existing offline-batch jobs (closet index build; the KG triple worker
  proposed in `docs/specs/kg-triple-extraction.md`), never the write hot
  path. A `mempalace speaker-profiles build` CLI + optional daemon endpoint,
  parallel to the rest of the offline tooling.

## Data/schema impact

- **No verbatim re-write, no new write-side field.** `added_by` and
  `source_file` already exist on every drawer. Principle 1 (verbatim always)
  and Incremental-only both hold untouched.
- **One read-path change (§0/§1):** surface `added_by` in the hit `entry`
  dict at `searcher.py:2079`. Purely additive; existing callers ignore the
  new key.
- **New artifact (§1 only):** per-source style vectors, a small derivative
  index analogous to the closet collection — recomputed offline,
  incrementally. It is a *derivative* index over verbatim data, never a
  rewrite of it. (If/when it becomes search-visible rather than a weighting
  input, it would itself be a derivative collection subject to the
  modality-reranking spec, #181 — noted as a cross-link, not built here.)
- **Optional config keys only:** `speaker_profiles`, `speaker_cap`. Absent ⇒
  today's behavior, bit-for-bit.
- **Filter exposure:** `added_by` must be added to the set of
  metadata fields the search filter accepts (currently `wing`/`room`/`tags`).
  Backend-level additive change; no migration.

## Open questions

1. **Are the styles actually separable?** The decisive empirical question
   (#180 / the comparison doc both flag it): does wing/room scoping +
   `added_by` filter *already* capture the win, leaving style vectors with no
   measurable headroom? Resolve with §0 first; only build §1 if §0 leaves a
   gap. This is the make-or-break question — the feature may correctly never
   ship past §0.
2. **Style vs. topic confound.** Char-n-gram vectors aim to capture style
   independent of topic, but agents writing about different topics will also
   differ in tokens. Does the "style" signal survive topic-matching, or is it
   just a topic proxy? Needs an ablation holding topic constant.
3. **Source granularity.** Profile by `added_by` (agent identity) or by
   `source_file` cluster (session/project) or both? `added_by` is coarse
   (few distinct agents); `source_file` is fine but sparse per source. Lean:
   start at `added_by`.
4. **Inferred vs. explicit intent.** Is §2 (inferring source intent from
   query text) ever worth the mis-inference risk, or should source scoping
   stay explicit (`speaker=`) forever? Lean explicit until a strong §1 result
   justifies the risk.
5. **Cap value.** `0.10` placeholder; A/B on the fork's own corpus per "test
   retrieval against our corpus" discipline — speaker weighting must beat
   both the no-op baseline *and* the §0 exact-match filter to earn its keep.
6. **Interaction with cross-encoder / modality.** With #179 and (eventually)
   #181 also reordering the head, a third reweight risks over-fitting the top
   few results. Needs a combined ablation, not isolated tuning.

## References

- Issue: [techempower-org/mempalace#180](https://github.com/techempower-org/mempalace/issues/180)
  — Add speaker/source style profiles for retrieval weighting.
- True Memory comparison: `docs/research/2026-05-24-true-memory-comparison.md`
  (see "What MemPalace could learn → Speaker profiles (L0)").
- True Memory paper: Adler & Zehavi, "Storage Is Not Memory" (May 2026),
  [arXiv:2605.04897](https://arxiv.org/abs/2605.04897), Section 3.1
  (Speaker Engram).
- Related, higher-priority retrieval work: novelty scoring (#178),
  cross-encoder rerank (#179, shipped — `mempalace/cross_encoder_rerank.py`).
- Bounded recall-safe adjustment idiom: `mempalace/ratings.py` (#159),
  `mempalace/recency.py` (#158).
- Cross-link — if style profiles ever become search-visible, they fall under
  modality-aware reranking: `docs/specs/modality-aware-reranking.md` (#181).
