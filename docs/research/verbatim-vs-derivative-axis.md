# The Verbatim-vs-Derivative Axis

*A standalone reference for the foundational architectural call behind MemPalace's
storage layer: store verbatim, derive lazily, treat every derivative as
replaceable. Companion to the README's four-layer model and operational thesis.*

Last revised: 2026-05-22.

## TL;DR

- Memory systems split cleanly into two camps along a single axis: those that store the *user's words* (**verbatim**) and those that store a *model's interpretation of the user's words* (**derivative**).
- The choice is load-bearing. Derive-on-write systems lock in the assumption that their derivation algorithm is correct enough to stake the ground-truth layer on. Every later improvement happens through that assumption; every later failure is invisible underneath it.
- MemPalace's bet — drawer = literal utterance, every other artifact built next to it — conserves the original signal. Derivatives (KG triples, AAAK indices, agent journals, Auto-Dream indices) are welcome as siblings, never as replacements.
- Empirical evidence on the canonical 300K+ drawer palace: when the corpus shape was wrong, *no* retrieval-side tuning closed the gap; a structural change at the write layer closed a 210× token gap in one day. Fix corpus shape at write time, not query time.
- Vendor-API validation: Anthropic's Managed Agents Dreams API (beta header `dreaming-2026-04-21`) treats input memory stores as read-only and produces a *separate* output store — the verbatim-vs-derivative axis at the level of a vendor API, arrived at independently.
- The axis has limits. Section 6 names five exceptions (explicit shelving, per-domain redaction, regulated-data classes, adversarial inputs, high-cardinality machine output).

## 1. The architectural choice

MemPalace's storage layer makes one decision and pivots everything else around it: *the unit of memory is the verbatim utterance.* A drawer holds the exact text the user produced or witnessed — a chat turn, a tool result, a mined file, a snippet of code. Nothing in the indexing path summarizes, paraphrases, classifies, or normalizes that text. The drawer's literal bytes are the system of record.

Derivative work lives *next to* the verbatim record, not on top of it. Knowledge-graph triples live in a sibling store keyed by `subject/predicate/object`. AAAK-encoded room indices live in their own collection, queried for fast scan. Agent journals and dream-style consolidated indices live one step further out again. None are required for the verbatim layer to function; each is independently improvable; each is replaceable, because the verbatim text underneath is untouched and can be re-derived from.

The decision creates an asymmetric contract. **Verbatim writes must always succeed.** Derivative writes are best-effort; an entity-extraction failure does not block the verbatim drawer. The reverse — verbatim writes blocked by derivative failures — is a failure mode the design specifically rules out. Earlier versions of MemPalace had bugs in exactly this shape (`room=None` crashes, write-time classifier misfires, an entity detector that papered over stopword false positives by growing the stopword list to 285 entries). The current architecture inverts the relationship: derivatives derive from drawers, never gate them.

"Verbatim canonical, derivative-replaceable" is three claims taken as one:

1. **Verbatim is canonical.** The drawer is the source of truth; nothing else claims that role.
2. **Lazy derivation.** Derivative artifacts are produced when needed (background processes, on-demand re-derivation, opt-in enrichment), not synchronously inside the write path. The write path is the drawer plus minimum unambiguous metadata (wing/room from cwd or transcript path, timestamp, content hash).
3. **Replaceable.** Any derivative store can be dropped, rebuilt under a different algorithm, re-embedded, or re-keyed — because the verbatim record underneath is intact.

The README's thesis section names this as Principle 1 ("verbatim vs. derivative is the canonical axis"). This essay is the longer argument for why the call is load-bearing, what alternatives fail at, and where the line actually sits.

## 2. Why derive-on-write accumulates failure modes

Most production-shaped AI memory systems in 2026 do not store verbatim. They store the *output* of a write-time transformation: extracted "memories" (Mem0), tiered summaries (Letta), a knowledge graph from an ECL pipeline (Cognee), retain/recall/reflect facts (Hindsight). The verbatim original is either dropped entirely or kept only as a retrieval-anchored side reference. The ground-truth layer is the derived artifact.

Derive-on-write pays for storage compression, retrieval shape control, and immediate readability. For workloads where the user's utterance is itself low-signal (high-throughput email triage, log distillation, alerting), the math may work out. For general-purpose agent memory, the costs compound in four places.

**Lost nuance.** Extraction is lossy by construction. What gets thrown away is determined by the extractor's training, not by the user's later needs. An LLM-extracted "the user fixed a USB issue with `xhci_hcd`" loses both the exact command and the kernel-module specifics; six months later, when the question is "what was the workaround for the xhci issue on Razer keyboards," the original utterance has the workaround and the summary does not. No re-derivation is possible — the verbatim was discarded.

**Classifier ceiling.** Write-time classification has a hard ceiling at the accuracy of the classifier. A 92% accurate entity tagger places 8% of mentions wrong; a topic router with three-decimal-place F1 still misroutes one drawer in ten. Those misfilings are inert *as misfilings* until a downstream query comes up empty. The only signal is absence of a result. A verbatim-first system with a wrong scoping signal still returns the drawer when a vector search matches the text — worst case a re-rank away, not a structural miss.

**Irreversibility.** Once the verbatim is gone, every later improvement in extraction, embedding, or chunking operates on the residual derivative. Better classifiers don't help; you can't reclassify what you didn't keep. Better embedding models don't help; you can only re-embed what you have. The investment in a derivative store ratchets one direction.

**Locked-in derivation assumptions.** Every derivative store encodes assumptions about what's worth keeping. Mem0's "memory extraction" assumes memory is a fact-shaped sentence. Cognee's KG assumes the world decomposes into entities and relations. Letta's tiered summaries assume sensible compression at the recent / mid-term / long-term boundary. Each may be right for its target workload; each is structurally betting that the assumption holds for the application's lifetime. A verbatim layer makes no such bet.

The honest framing: derive-on-write trades preservation for compression. The wrong trade for general-purpose agent memory at the substrate layer, because the system doesn't yet know what its consumer will need in a year. The verbatim layer keeps the option open; the derivative layer forecloses it. Mixing the two in one corpus is a separate failure mode — the recovery-collection episode below is the empirical case for why derivative and verbatim drawers don't belong in the same vector index, because their embeddings, typical lengths, and access patterns are all different.

## 3. Empirical signal — the recovery-collection split (2026-04-25 → 2026-05-05)

The fork has one well-documented case of the verbatim-vs-derivative axis showing up in retrieval quality as a 210× token-budget gap. The lesson generalizes; worth walking through.

**Setup.** Through April 2026, the Stop hook in Claude Code wrote two kinds of records on every fire: a verbatim transcript chunk via the auto-miner, and a 1-KB classifier-style "checkpoint" summary via `tool_diary_write` — short, importance-ranked, room-tagged. Both landed in the same ChromaDB collection (`mempalace_drawers`). At 300K+ drawer scale, the corpus contained roughly 640 checkpoint summaries — 0.4% of the corpus.

**Symptom.** End-to-end QA had collapsed. The engram-2 benchmark publication ("17% E2E QA against 0.984 R@5") landed on a real failure surface. The diagnostic version inside MemPalace: `mempalace_search` over `kind=content` returned an average of 3 tokens per question. Recall@5 was 0.984; the retriever was finding *something*, but what it was finding was useless. A week of filter tuning, BM25 fallback, over-fetch parameters, and `kind=` predicate filtering produced no measurable improvement.

**Diagnosis.** The 640 checkpoint summaries were short (1 KB), query-term-saturated (designed to be retrievable), and embedded close to recent prompts (generated *from* recent conversation context). They dominated the vector top-N for ~80% of queries. The verbatim drawers — which contained the actual answers — were never reached. Recall@5 was still 0.984 because *the retriever was succeeding at finding what the corpus shape said was relevant.* The corpus shape was wrong.

**Fix, attempt 1 (Apr 25 → 26).** Move the checkpoint summaries to a dedicated collection (`mempalace_session_recovery`). `kind=content` retrieval against `mempalace_drawers` jumped from 3 tokens to ~1,267 tokens per question — a 210× delta from a single structural change, separating verbatim from derivative at the write layer.

**Fix, attempt 2 (May 5).** The split had a follow-on problem: agents searched `mempalace_drawers` by default, and the dedicated recovery collection never got its own semantic-search MCP surface. Checkpoints became structurally invisible to retrieval. JP's call on 2026-05-05 was the deeper one: stop writing the checkpoint summaries entirely. The Stop hook now writes only verbatim transcript chunks; the recovery collection and its read tool retired. One collection, one search path, no derivative half to balance against.

**The shape of the lesson.** When the corpus is wrong-shaped, retrieval-side tuning has no leverage. Filter cleverness, over-fetch hacks, BM25 fallback, hybrid rerank — none produced a measurable gain on the 3-tokens-per-question pathology. The structural change at the write layer produced a step-function gain in one day. Generalizing: *retrieval algorithms have less leverage over end-to-end quality than the shape of what you ingest.*

Two related notes. First, the corpus-shape problem is *not* a tagging or filtering problem in disguise. We tried both. The `kind=` filter on `mempalace_search` was inert against the 3-tokens-per-question gap, because the wrong rows were still scoring highly enough to crowd out the right ones before the predicate ran. Second, the structural fix preserved every piece of information: the verbatim transcript already contained what the checkpoint summary would have surfaced. The derivative half was, in retrospect, lossless to drop. Derivatives that don't add information aren't worth their corpus footprint.

## 4. Vendor-API validation — Anthropic's Dreams API

Independent ratification of the verbatim-vs-derivative axis arrived in late April 2026 from a vendor: Anthropic's Managed Agents Dreams API ([platform.claude.com/docs/en/managed-agents/dreams](https://platform.claude.com/docs/en/managed-agents/dreams), beta header `dreaming-2026-04-21`, research preview).

The Dreams API takes a memory store plus up to 100 session transcripts and runs an asynchronous pipeline that produces — and this is the architectural call — a *new* memory store. Duplicates merged, contradictions resolved, stale entries replaced, new insights surfaced. **The input is never modified.** The output is a separate store you can attach to future sessions, archive, or discard. Deleting an input store mid-run causes the pipeline to fail with `input_memory_store_unavailable` rather than silently dropping the constraint.

That is verbatim-canonical / derivative-replaceable at the level of a vendor API. Three properties worth pointing out:

**Input read-only is the design.** Not a deployment posture, not a configuration option — the API surface itself enforces it. The pipeline's only legal interaction with the input is *read*. Same property MemPalace's drawer table has by construction.

**Output as a separate store with a review gate.** The dream's output is not auto-attached. The caller decides whether to attach, archive, or delete. This maps to MemPalace's "derivative stays next to verbatim, never replaces it" — a derived index is something you can diff against the original, evaluate, and choose to use.

**Idempotent on the inputs.** You can re-run the pipeline against the same input store under a different model, different `instructions`, or different session selections, and compare outputs. Re-derivability under changing algorithms — exactly what MemPalace's verbatim layer preserves for KG extraction, AAAK encoding, and embedding-model pipelines.

The timelines overlap. The recovery-collection split landed April 25; the verbatim-only follow-up landed May 5; the Dreams API beta header dates to April 21. We did not write the Dreams API and were not consulted on it. Two independent designs — a small open-source fork running a 300K+ drawer palace on commodity hardware, and a vendor research-preview API targeting agent-scale workloads — landed on the same architectural call from different starting points. When that happens, the call is probably load-bearing.

One corollary. The pre-2026-04-21 framing — *"neither memory layer has automatic consolidation; MemPalace's investment in derivative work is unblocked by Anthropic's absence"* — is dead. Auto-memory has consolidation now via Claude Code's `/dream` and the Dreams API. MemPalace's verbatim layer **deliberately** still doesn't. That is now a thesis claim, not a gap. Consolidating the verbatim layer would forfeit the property that justifies storing it verbatim in the first place — the ability to audit, re-derive, and recover the original utterance under a changed downstream pipeline.

The Dreams API and MemPalace are complementary, not competitive. Dreams curates a small high-signal index for every-turn reading; MemPalace stores the corpus verbatim for post-hoc retrieval. They consume the same Claude Code JSONL transcripts. Same substrate, different roles.

## 5. Operational implications

The verbatim-vs-derivative axis changes how operational decisions around the storage layer fall out. Four worth naming.

**Backup is a single concern.** When the verbatim layer is canonical and derivatives are replaceable, the backup question collapses to "is the verbatim layer durable?" KG triples, AAAK indices, embedding stores, diary entries, graph cache — none need their own backup tier, because the source they derive from is what's being backed up. In practice: backup the drawer table, period. Anything else can be regenerated.

**Audit and re-derivation are routine.** Because derivatives are replaceable, auditing one is a matter of running it again against the verbatim layer and diffing. "Did the entity extractor get this triple right?" Re-run on the drawer; the verbatim is right there. "Did the embedding model upgrade drift hit retrieval?" Re-embed and run side-by-side recall comparisons. None of these workflows are possible against a derive-on-write store. The fork's substrate migration from ChromaDB to Postgres + pgvector + Apache AGE (shipped 2026-05-15) was a wholesale re-derivation of the vector layer against the verbatim drawers — reversible end-to-end, because the source was untouched throughout.

**Model-upgrade resilience.** Embedding models, classifiers, summarizers, entity extractors all have a shelf life of months, not years. Each upgrade is, on a verbatim-first system, a re-derivation pass: run the new model against the existing drawers, write to a new derivative store, A/B against the old. On a derive-on-write system, an embedding-model upgrade either requires re-mining source data (often unavailable months later) or freezes the system at the model in place at write time.

**Privacy and shelving are architectural.** A verbatim-canonical system has a clean answer for "delete the X transcripts": find drawers by metadata, delete them, then re-derive every derivative store from the remaining drawers. The deletion is *complete* — there is no residual extracted-fact store carrying the information in derived form. On a derive-on-write system, "delete" requires tracking down every derivative that may have absorbed information from the source. The architectural property cuts cleanly for both `forget this` (Section 6) and routine retention.

The reverse implication is worth saying explicitly. **Derivatives must always be re-derivable.** The moment a derivation pipeline becomes undocumented, depends on a closed-source classifier with non-reproducible behavior, or relies on source data pruned out from under it, the verbatim-canonical contract is broken from the derivative side. The fork's working answer is to make derivative pipelines explicit, scripted, and tested (`backfill_age`, `kg_writethrough`, the AAAK encoding pass) rather than allowing them to drift into "the way the index ended up." Tooling debt on the derivative side compounds against the architectural property, even when the verbatim layer is intact.

## 6. Limits — when verbatim DOES need explicit shelving

The axis is structural, not absolute. The verbatim-canonical position is defensible because it's a default with named exceptions, not an unconditional rule. Five exceptions worth naming.

**`forget this` is a first-class action.** Users will, and should, mark records for removal. The pattern is not decay (decay weights the *score*, not the *existence*, and is a retrieval optimization, not a privacy mechanism). The pattern is explicit, deliberate, and propagates: drop the drawer, re-derive every derivative store, confirm the residue. The verbatim layer's property here is not "preservation against the user's wishes" — it is "deletion is complete and observable, because the source was canonical."

**Per-domain redaction.** Passwords, API keys, payment-card numbers, government identifiers, and other secret-material strings are not memory; they are operational hazards. The right policy is *write-time redaction with verbatim sentinels* — replace the bytes with a redaction marker (`[REDACTED:API_KEY]`), keep surrounding context, never store the original. Redaction is the one place where the verbatim contract is deliberately broken at the write layer, and the contract becomes "we never had the original," not "we had it and then removed it." The choice of what to store is upstream of the axis.

**Regulated-data classes.** HIPAA, PCI, FERPA, GDPR — these come with statutory retention, residency, and right-of-erasure requirements that may conflict with default-verbatim storage. The architectural property the verbatim layer needs for these classes is *legibility* — knowing what's in the corpus and how to comply with deletion requests — not necessarily preservation. A regulated-data palace runs with shorter retention, stricter scoping, redaction-at-mine, and a dedicated audit log; the verbatim contract still applies *within* what's stored, but the scope is narrower. The fork has not built this tier; flagging as a known limit.

**Adversarial inputs.** Prompt-injection patterns and content designed to corrupt derivatives are write-layer concerns. Current posture is *flag, don't block*: if a write looks suspicious, it lands with `sanitized: true` metadata, not a refusal. The sanitizer attaches metadata; it doesn't rewrite content. Works for local-first single-principal deployments; less clearly right for shared or multi-tenant palaces (which the fork doesn't target).

**High-cardinality machine output.** Log streams, telemetry, raw tool output at 100MB+ scale don't fail the verbatim contract but may fail the *retrieval-quality* contract by saturating the embedding space with low-entropy content. The right response is structural: rate-limit the mine path, exclude known noise sources, or partition into a sibling collection (the multi-collection-by-purpose pattern). Not "summarize the logs" — that's the derive-on-write trap.

The honest summary: verbatim-canonical is the right default for general-purpose agent memory. It is not the right policy for every byte the system encounters. The named exceptions are explicit-shelving, per-domain redaction, regulated-data classes, adversarial inputs, and high-cardinality machine output. The axis holds; the scope is bounded.

## 7. Conclusion

The case for the verbatim-vs-derivative axis comes in three pieces. **Structural:** derive-on-write systems lock in an algorithmic assumption as the ground-truth layer; verbatim-first systems defer it, so the algorithm can change while the storage doesn't. **Empirical:** the April-May 2026 recovery-collection episode demonstrated on a 300K+ drawer production palace that retrieval-side tuning has no leverage when the corpus shape is wrong, and that a structural change at the write layer closes a 210× token-budget gap in one day. **Convergence:** Anthropic's Dreams API and the April-2026 verbatim cohort (MemPalace, Longhand, Celiums, mcp-memory-service) converged within eight days of each other on the same architectural call from different starting points.

Within bounded scope, the call is clear: store verbatim, derive lazily, treat every derivative as replaceable. The verbatim layer's job is to preserve the source. The derivative layer's job is to make the source usable on whatever terms today's consumer needs, without prejudicing tomorrow's.

The fork's working slogan is "derivatives next to, never on top of." This essay is the longer argument for why it's worth remembering.

## Further reading

Background on the four-layer model the axis sits inside:

- [README — the four layers](../../README.md#the-four-layers) — storage / encoder / retrieval / consumption.
- [README — the thesis](../../README.md#the-thesis-operational-principles) — Principle 1 is the README's compressed statement of the verbatim-vs-derivative axis.

Adjacent fork research:

- [`docs/research/three-patterns-for-agent-memory.md`](three-patterns-for-agent-memory.md) — Familiar / RLM / parallel hybrid on the jp-realm-v0.1 corpus; invocation as the bottleneck.
- [`docs/research/three-mempalace-consumers.md`](three-mempalace-consumers.md) — Familiar / CampaignGenerator / Kent triangulation; convergent design decisions among peer builds on the MemPalace substrate.
- [`docs/research/adaptmem-orthogonal-layers.md`](adaptmem-orthogonal-layers.md) — encoder fine-tuning as an orthogonal layer; independent reproduction of MemPalace's 0.966 R@5 plus FT-300 lift.
- [`docs/research/convergent-findings-kostadis-comparison.md`](convergent-findings-kostadis-comparison.md) — why deterministic intermediate compression is a precision discipline.

Architectural specs that depend on the axis:

- [`docs/superpowers/specs/2026-05-05-verbatim-only-design.md`](../superpowers/specs/2026-05-05-verbatim-only-design.md) — the verbatim-only Stop hook spec, the architectural move the recovery-collection episode taught.
- [Upstream RFC 001](https://github.com/MemPalace/mempalace/pull/743) — multi-collection-by-purpose, the pattern that lets sibling derivative stores coexist with the verbatim layer without polluting it.

External references cited in the body:

- [Anthropic — Dreams (Managed Agents API)](https://platform.claude.com/docs/en/managed-agents/dreams) — input read-only, output a separate store; the vendor-API ratification.
- [Claude Code Auto Dream guide](https://claudefa.st/blog/guide/mechanics/auto-dream) — research-preview consolidator inside Claude Code; the lightweight-layer half of the two-memory-layer model.
- [engram-2 benchmark note](https://github.com/199-biotechnologies/engram-2) — the "17% E2E QA against 0.984 R@5" claim that landed on the corpus-shape pathology described in Section 3.
