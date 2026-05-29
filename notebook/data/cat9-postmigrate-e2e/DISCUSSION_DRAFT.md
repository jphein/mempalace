<!-- DRAFT for a techempower-org/mempalace GitHub Discussion.
     JP posts this himself; Nebula only drafts it. Title suggestion below. -->

**Title:** First end-to-end QA number for the fork — 60.4% on LongMemEval (oracle), and what the retrieval→answer gap tells us

---

We've published retrieval recall before (0.984 R@5, inherited from upstream's
held-out hybrid), but never an **end-to-end QA accuracy** on a standard
benchmark. That gap is what let the "great recall, but can it actually
answer?" question linger — including an engram-2-attributed "0.984 R@5 but
~17% E2E QA" framing that, on inspection, was never substantiated in their
published materials (their ~17pp figure is engram-2's own LoCoMo-vs-SOTA gap,
not a MemPalace number).

So we measured it. Full write-up:
[`notebook/data/cat9-postmigrate-e2e/REPORT.md`](https://github.com/techempower-org/mempalace/blob/main/notebook/data/cat9-postmigrate-e2e/REPORT.md).
Raw numbers live in the eval repo at
[`docs/longmemeval_mempalace_results.md`](https://github.com/techempower-org/multipass-structural-memory-eval/blob/main/docs/longmemeval_mempalace_results.md).

## The numbers (LongMemEval oracle, n=500)

| Metric | Daemon `/search` (default) | `/search/age-fused` | Familiar |
|---|---:|---:|---:|
| R@5 (drawer_id matcher) | **97.0%** | 90.2% | 28.6% |
| **E2E QA accuracy** | **60.40%** | 17.60% | 29.20% |
| Retrieval→QA gap | +38.4pp | +72.8pp | −2.4pp |

Reader `o4-mini`, judge `gpt-5.3-chat` (both Azure Foundry).

## What it actually says

**Retrieval is near-ceiling; the reader is the bottleneck.** The default
path retrieves the gold session in the top-5 **97%** of the time, yet only
answers correctly 60.4% of the time — a **+38.4pp** gap. The post-migration
palace finds the right memory; the open work is in *consumption* (the Cat 9 /
"Handshake" problem), not retrieval. That's the inverse of the "can't
retrieve" critique.

**60.40% is the honest headline — not a SOTA claim.** Published systems
(OMEGA 95.4%, Hindsight 91.4%, True Memory 87.8%) ran a different
reader/judge stack (GPT-4 family) than ours. Cross-stack comparison is normal
for this benchmark, but please don't read 60.4% as "below OMEGA" without
also naming the model-stack delta. The substrate itself is parity-good — a
separate run measured the postgres-vector floor at R@5 = 0.966, byte-identical
to upstream.

**age-fused's 17.6% is a known-broken harness, not a verdict on graph
fusion.** Two confounds: the snippet handed to the reader was 5.5× narrower
than `/search` (~459 vs ~2,539 chars — palace-daemon#150), and the AGE
triples layer was effectively empty during the bench window. The real
age-fused A/B reruns once triples are populated and the snippet boundary is
fixed.

## Caveats worth repeating

- All numbers are the **oracle** split (small per-question haystack). S and M
  splits shift downward; hold the split constant when comparing.
- Familiar ran without per-question wing scoping, so its numbers are best-case
  and its stack is tuned for a different corpus shape.

## Where this goes next

The frontier this surfaces is the retrieval→answer handshake, not recall.
Feedback welcome on the consumption-layer direction (context width, reader
prompting, age-fused rerun once triples land). Numbers, methodology, and
reproduction steps are all in the report and the source results doc above.
