# `mempalace.convo_scanner`

Source: [`mempalace/convo_scanner.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/convo_scanner.py)

convo_scanner.py — Parse Claude Code conversation directories into ProjectInfo.

Claude Code stores sessions under ``~/.claude/projects/&lt;slug>/&lt;id>.jsonl``,
where the ``&lt;slug>`` is the original CWD with ``/`` replaced by ``-``. That
encoding is lossy: we can't tell whether ``foo-bar`` in a slug is the
literal project name ``foo-bar`` or two path segments ``foo/bar``.

Fortunately, every message record in the JSONL carries a ``cwd`` field with
the true path. This scanner reads one record per session to recover the
accurate project name, falling back to slug-decoding only if the JSONL
is malformed or empty.

Output is the same ``ProjectInfo`` shape used by ``project_scanner``, so the
``discover_entities`` orchestrator can mix-and-match sources.

Public:
    is_claude_projects_root(path) -> bool
    scan_claude_projects(path) -> list[ProjectInfo]

## Functions

### `is_claude_projects_root`

```python
def is_claude_projects_root(path: Path) -> bool
```

Return True if path looks like `.claude/projects/`.

Heuristic: at least one child dir whose name starts with ``-`` and which
contains at least one ``.jsonl`` file.

### `scan_claude_projects`

```python
def scan_claude_projects(path: str | Path) -> list[ProjectInfo]
```

Scan a ``.claude/projects/`` directory for Claude Code conversations.

One ProjectInfo per subdir. ``has_git`` is False (the directory isn't a
repo itself) but ``total_commits`` is repurposed here as session count so
the UX surfaces a density signal for ranking.
