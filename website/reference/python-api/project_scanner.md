# `mempalace.project_scanner`

Source: [`mempalace/project_scanner.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/project_scanner.py)

project_scanner.py — Detect projects and people from real signal.

For a codebase with build manifests or git history, this beats regex-based
entity detection by a wide margin: the project's own name is already written
down in package.json / pyproject.toml / Cargo.toml / go.mod, and the people
who worked on it are in `git log`.

This module is used as the primary signal in `mempalace init`. The regex
detector in entity_detector.py stays as a fallback for prose-only folders
(notes, research, writing).

Public:
    scan(root) -> (projects, people)
    to_detected_dict(projects, people) -> &#123;people: [...], projects: [...], uncertain: []}

## Classes

### `class ProjectInfo`

#### `confidence`

```python
def confidence(self) -> float
```

#### `to_signal`

```python
def to_signal(self) -> str
```

### `class PersonInfo`

#### `confidence`

```python
def confidence(self) -> float
```

#### `to_signal`

```python
def to_signal(self) -> str
```

## Functions

### `find_git_repos`

```python
def find_git_repos(root: Path, max_depth: int = MAX_DEPTH) -> list[Path]
```

Return git repo roots under `root` (including root itself if it's a repo).

### `scan`

```python
def scan(root: str | os.PathLike) -> tuple[list[ProjectInfo], list[PersonInfo]]
```

Scan `root` for projects and people. Returns (projects, people) sorted.

### `to_detected_dict`

```python
def to_detected_dict(projects: list[ProjectInfo], people: list[PersonInfo], project_cap: int = 15, people_cap: int = 15) -> dict
```

Convert scan results into the dict shape produced by entity_detector.detect_entities.

### `discover_entities`

```python
def discover_entities(project_dir: str | os.PathLike, languages: tuple = ('en',), prose_file_cap: int = 10, project_cap: int = 15, people_cap: int = 15, llm_provider: object = None, show_progress: bool = True, corpus_origin: dict | None = None) -> dict
```

Top-level entity discovery: real signals first, prose detection second.

Returns the same dict shape as ``entity_detector.detect_entities`` so it
plugs into ``confirm_entities`` unchanged.

Order of signal preference:
  1. Package manifests (package.json, pyproject.toml, Cargo.toml, go.mod)
     → canonical project names
  2. Git commit authors → real people with real commit counts
  3. Claude Code conversation dirs (~/.claude/projects/) → per-session
     project names (pulled from each session's ``cwd`` metadata)
  4. Regex entity detection on prose files → supplementary names only
     mentioned in docs/notes (not code)
  5. Optional LLM refinement pass — reclassifies ambiguous candidates
     using the caller-supplied provider
  6. Optional corpus-origin persona filter — when the corpus is
     identified as AI-dialogue, candidates whose name matches an
     agent_persona_name are moved to an ``agent_personas`` bucket
     instead of being reported as people.

Passing ``llm_provider`` enables phase-2 refinement. The caller is
responsible for constructing the provider (``llm_client.get_provider``)
and confirming availability. Refinement is blocking-interactive:
progress prints to stderr; Ctrl-C returns partial results.

Passing ``corpus_origin`` enables corpus-origin persona reclassification.
The expected shape is the dict written by ``mempalace init`` to
``&lt;palace>/.mempalace/origin.json`` (see ``corpus_origin.py``).
