# Design: hierarchy derivation order (wing + room)

**Issue:** [techempower-org/mempalace#157](https://github.com/techempower-org/mempalace/issues/157)
**Author:** Vesper (team kg-backfill-stabilize)
**Date:** 2026-05-26
**Status:** Implemented (`mempalace/hooks_cli.py`)

---

## TL;DR

Wing and room assignment is **derived from unambiguous signals**, not
hand-classified. This ratifies Architectural Principle 2 in the README —
*"derived hierarchy from unambiguous signals outperforms hand-classified
hierarchy."* The contract is explicit and ordered:

```
cwd > transcript path > project directory hint > (optional) entity hint > unfiled
```

The entity detector is a **hint, never a gate**: it can only fill in when
every unambiguous signal above it is absent. A confident entity match can
never override a cwd / transcript-path / project-directory signal.

---

## The contract

### Wing — `derive_wing(transcript_path, project_dir=None, entity_hint=None)`

| # | Signal | Source | Why it's authoritative |
|---|--------|--------|------------------------|
| 1 | **cwd** | `cwd` field in the JSONL transcript | Claude Code records the absolute working directory on most message types. The leaf path segment *is* the project — no inference. |
| 2 | **transcript path** | encoded `.claude/projects/-…` folder, or a legacy `-Projects-<name>` segment | When cwd is absent, the on-disk transcript location still encodes the project directory. |
| 3 | **project directory hint** | explicit `project_dir` passed by the caller | The caller frequently knows which directory it is operating in even when the transcript path is unusable. Still unambiguous — it is a real filesystem path, not a guess. |
| 4 | **entity hint** | optional `entity_hint` (last resort) | A name surfaced by the entity detector. **Only consulted when 1–3 are all absent.** Demoted from gate to hint per #157. |
| 5 | **unfiled** | `wing_sessions` | Nothing resolved. |

Signals 1 and 2 are resolved together by `_wing_from_transcript_path()`
(cwd first, then the encoded path). `derive_wing()` wraps that resolver and
adds signals 3–5.

### Room — `derive_room(content="", room_hint=None, entity_hint=None)`

| # | Signal | Source | Why it's authoritative |
|---|--------|--------|------------------------|
| 1 | **explicit room hint** | caller-supplied canonical room | Unambiguous: the caller named the room directly. |
| 2 | **keyword-derived room** | content scored against the canonical room rules (`detect_convo_room`) | Deterministic scoring over the 7-room canonical taxonomy. |
| 3 | **entity hint** | optional `entity_hint` (last resort) | Only consulted when 1–2 yield nothing, and only accepted if it names a real canonical room. |
| 4 | **unfiled** | `DEFAULT_ROOM` (`discoveries`) | Nothing resolved. |

The result is always one of the canonical rooms (FK-safe on the postgres
backend), because both the keyword path and the default come from
`convo_miner`'s canonical rule set.

---

## Why demote the entity detector

The entity detector (`entity_detector.py`, `entity_registry.py`) is a
prose-scanning heuristic: it scores candidate names by frequency, casing,
and dictionary lookups. It is genuinely useful for *enriching* a drawer —
"this conversation mentions Alice and the realm-watch project" — but it is
the wrong tool to *decide* where a drawer is filed. A heuristic that scores
"Alice" highly should never override the fact that the conversation
literally happened in `~/Projects/customer-portal`.

Before this change there was no single place that stated the order, and the
entity detector's outputs risked being treated as co-equal with the
filesystem signals. The contract makes the precedence explicit and the code
enforces it: the entity hint is the **last** branch in both `derive_wing`
and `derive_room`, reached only when every unambiguous signal is empty.

---

## Tests

`tests/test_hooks_cli.py` verifies the priority order on synthetic inputs
(no live palace / daemon):

- `test_derive_wing_cwd_beats_entity_hint` — cwd wins over a confident entity hint
- `test_derive_wing_transcript_path_beats_entity_hint` — encoded path beats entity hint
- `test_derive_wing_project_dir_hint_beats_entity_hint` — project-dir hint beats entity hint
- `test_derive_wing_entity_hint_only_as_last_resort` — entity hint used only when no unambiguous signal
- `test_derive_wing_unfiled_when_no_signal` — falls through to `wing_sessions`
- `test_derive_wing_entity_hint_does_not_override_default_when_path_resolves`
- `test_derive_room_explicit_hint_wins`
- `test_derive_room_keyword_beats_entity_hint`
- `test_derive_room_entity_hint_only_when_no_keyword`
- `test_derive_room_entity_hint_must_be_canonical`
- `test_derive_room_unfiled_default`
