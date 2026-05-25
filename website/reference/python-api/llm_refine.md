# `mempalace.llm_refine`

Source: [`mempalace/llm_refine.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/llm_refine.py)

llm_refine.py — Optional LLM refinement of regex-detected entities.

Takes the candidate set produced by phase-1 detection (manifests, git
authors, regex on prose) and asks an LLM to reclassify each candidate as
PERSON / PROJECT / TOPIC / COMMON_WORD / AMBIGUOUS.

Design constraints:
- Opt-in. Default init path never imports this module.
- Local-first by default (Ollama).
- Interactive UX: visible progress, clean cancellation (Ctrl-C returns
  whatever was classified before the interrupt).
- Don't feed the raw corpus to the LLM — feed candidates + a few sampled
  context lines each. Keeps total input to ~50-100K tokens even for huge
  prose corpora.

Public:
    refine_entities(detected, corpus_text, provider, ...) -> dict

## Classes

### `class RefineResult`

## Functions

### `refine_entities`

```python
def refine_entities(detected: dict, corpus_text: str, provider: LLMProvider, batch_size: int = BATCH_SIZE, show_progress: bool = True, allow_project_promotions: bool = True, corpus_origin: dict | None = None) -> RefineResult
```

Reclassify detected entities using the LLM provider.

Only regex-derived candidates are sent for refinement. Git authors and
manifest/git-backed projects are already source-backed and don't benefit
from LLM second-guessing.

Ctrl-C during refinement: cancels the remaining batches, returns a
RefineResult with ``cancelled=True`` and whatever was classified before
the interrupt. The partial result is safe to pass straight to
``confirm_entities``.

Transport or parse failures in individual batches are recorded in
``errors`` and do not abort the run.

``allow_project_promotions=False`` keeps LLM-only project guesses in the
uncertain bucket. This is useful when manifest/git signal already supplied
canonical projects and regex/LLM hits are likely tools, vendors, or topics.

### `collect_corpus_text`

```python
def collect_corpus_text(project_dir: str, max_files: int = 30, max_bytes_per_file: int = 20000) -> str
```

Gather prose text from ``project_dir`` for use as LLM context source.

Stratified: reads up to ``max_files`` prose files (``.md``, ``.txt``,
``.rst``), preferring recently-modified. Each file capped at
``max_bytes_per_file`` to bound total input.
