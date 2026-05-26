# `mempalace-source-omoa` — design note

- **Date:** 2026-05-26
- **Tracker:** [techempower-org/mempalace#38](https://github.com/techempower-org/mempalace/issues/38)
- **Source RFC:** [RFC 002 — Source Adapter Plugin Specification](../rfcs/002-source-adapter-plugin-spec.md)
- **Companion note:** [opencode RLM gap analysis](2026-05-26-opencode-rlm-gap-analysis.md)
- **Status:** Planning. No code in this round.

## Summary

`code-yeongyu/oh-my-openagent` (omoa, formerly oh-my-opencode) is a TypeScript plugin layered on top of OpenCode. Its session storage is **identical** to bare OpenCode's — the SQLite database at `~/.local/share/opencode/opencode.db` — which means the existing [`OpenCodeSourceAdapter`](../../mempalace/sources/opencode.py) already covers omoa session ingestion with zero new code.

What omoa adds on top of OpenCode is a set of **project-tree artifacts** that hold curated context: `.omo/tasks/*.json`, hierarchical `AGENTS.md` files, learnings (location TBD per omoa's still-evolving #1397 spec), `.claude/skills/*/SKILL.md`, and `.claude/rules/*.md`. These are not session content; they are durable knowledge artifacts that a future omoa session reads to start with context. A second adapter, `mempalace-source-omoa`, mines those.

This note specifies the second adapter. Effort estimate: **M** (~1-2 days of work).

## Background — what omoa stores where

Confirmed against the omoa README, the `.opencode/opencode.json` plugin entry, and the [feature reference](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/reference/features.md):

| Artifact | Path (project-relative) | Format | Lifecycle |
|---|---|---|---|
| Session transcripts | `~/.local/share/opencode/opencode.db` (user-scope SQLite) | OpenCode SQLite schema (`session` / `message` / `part`) | Append-only on every assistant turn |
| Task state | `.omo/tasks/T-<uuid>.json` | JSON per task | Created/updated by omoa task tools |
| Hierarchical context | `AGENTS.md` (root + nested under `src/**/`) | Markdown | Updated by `/init-deep` and human edits |
| Skills | `.opencode/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md`, `.agents/skills/<name>/SKILL.md` | Markdown with frontmatter | Hand-authored or generated |
| Commands | `.opencode/command/*.md`, `.claude/commands/*.md` | Markdown | Hand-authored |
| Conditional rules | `.claude/rules/*.md(c)` | Markdown with frontmatter (`globs`, `alwaysApply`) | Hand-authored |
| Hook config | `.claude/settings.json`, `.claude/settings.local.json` | JSON | Hand-authored |
| Plugin config | `oh-my-openagent.jsonc` (or legacy `oh-my-opencode.jsonc`) | JSONC | Hand-authored |
| Learnings (proposed #1397) | `.sisyphus/learnings/{session}.jsonl` | JSONL | Append-only via `/capture-learning` |

**Sessions are out of scope for this adapter.** The existing `OpenCodeSourceAdapter` already maps `~/.local/share/opencode/opencode.db` → drawers. The omoa adapter focuses on the project-tree artifacts above.

## Why a separate adapter, not an `omo` mode on the existing one

The existing OpenCode adapter is keyed on a user-scope SQLite path; it has no concept of a project tree. The omoa artifacts are keyed on project root and live across many trees. They share no source format, no chunking strategy, and no `is_current` shape. Per RFC 002 §1 the natural split is "one adapter per source surface" — bundling them would force every omoa user to also enable bare-OpenCode mining and vice versa.

Both adapters compose: a user with omoa enabled mines twice — once via `opencode` (session content) and once via `omoa` (project context). The two adapters use distinct `source_file` URI shapes so drawers do not collide.

## Adapter contract

### Identity

```python
class OmoaSourceAdapter(BaseSourceAdapter):
    name = "omoa"
    adapter_version = "0.1.0"
    capabilities = frozenset({
        "supports_incremental",         # mtime-based, see §is_current
        "supports_structured_metadata",
        "adapter_owns_routing",
    })
    supported_modes = frozenset({"whole_record"})
    declared_transformations = frozenset({
        "newline_normalize",
        "whitespace_trim",
        # No content-altering transforms — markdown and JSON go through verbatim.
        # JSON is reformatted only if the source is whitespace-divergent;
        # the round-trip suite verifies `json.loads(content) == json.loads(source_bytes)`.
    })
    default_privacy_class = "pii_potential"
```

### `SourceRef` shape

```python
SourceRef(local_path="/home/user/project")  # project root containing .omo/, .claude/, etc.
```

Default behavior: discover artifacts beneath the project root using a fixed glob set (below). Operator MAY pass `options={"include_user_scope": True}` to also mine `~/.claude/`, `~/.config/opencode/`, `~/.agents/` — disabled by default because user-scope artifacts often belong in a different wing than the project they were used from.

### Discovery globs (v1)

```
{root}/.omo/tasks/T-*.json
{root}/AGENTS.md
{root}/**/AGENTS.md           # truncated at depth 6 to avoid build-output trees
{root}/.opencode/skills/*/SKILL.md
{root}/.opencode/command/*.md
{root}/.claude/skills/*/SKILL.md
{root}/.claude/commands/*.md
{root}/.claude/rules/*.md
{root}/.claude/rules/*.mdc
{root}/.agents/skills/*/SKILL.md
{root}/oh-my-openagent.jsonc       # plus the .json / oh-my-opencode legacy basenames
{root}/.claude/settings.json
{root}/.claude/settings.local.json
{root}/.sisyphus/learnings/*.jsonl  # if/when omoa #1397 lands; gated by file existence
```

The adapter walks each matched path and yields one `DrawerRecord` per file. JSONL files (`.sisyphus/learnings/*.jsonl` and `.omo/tasks/*.json` if it becomes JSONL) yield one drawer per line; the line is the drawer content, with line-number metadata.

`.gitignore` is respected: the adapter skips paths that match the project's gitignore so build outputs and ephemeral state are not mined.

### `source_file` URI shape

```
omoa://<project-root-absolute>/<relative-path-from-root>
omoa://<project-root-absolute>/<relative-path-from-root>#line=<n>   # for JSONL
```

Stable across re-ingests; used as the ChromaDB `where={"source_file": ...}` key for `is_current` lookup.

### Routing (§2.5)

Three-tier precedence per RFC 002:

1. Explicit `options["wing"]` from the `SourceRef`.
2. Project-root basename (sanitized via `normalize_wing_name`).
3. Adapter fallback: `"omoa_general"`.

Room detection per artifact kind:

| Artifact kind | Room |
|---|---|
| `AGENTS.md` and nested | `context` |
| `.omo/tasks/T-*.json` | `tasks` |
| Skills (any path) | `skills` |
| Commands | `commands` |
| Rules | `rules` |
| Config (jsonc, settings.json) | `config` |
| Learnings (when present) | `learnings` |

Hall: deferred to `convo_miner._detect_hall_cached` if room ends up generic; otherwise adapter sets hall explicitly to the artifact kind.

### Per-drawer metadata schema (§5.2)

```python
def describe_schema(self) -> AdapterSchema:
    return AdapterSchema(
        version="1.0",
        fields={
            "project_root": FieldSpec(
                type="string", required=True, indexed=True,
                description="Absolute path of the project root at ingest time",
            ),
            "artifact_kind": FieldSpec(
                type="string", required=True, indexed=True,
                description=(
                    "One of: agents_md, task, skill, command, rule, "
                    "config, learning"
                ),
            ),
            "relative_path": FieldSpec(
                type="string", required=True,
                description="Path relative to project_root (POSIX separators)",
            ),
            "source_mtime": FieldSpec(
                type="string", required=True,
                description="ISO-8601 UTC of source file mtime at ingest",
            ),
            "source_size_bytes": FieldSpec(
                type="int", required=True,
                description="Size of source file in bytes",
            ),
            "frontmatter_json": FieldSpec(
                type="json_string", required=False,
                description=(
                    "Parsed YAML frontmatter for skill/rule artifacts "
                    "(globs, alwaysApply, name, description)"
                ),
            ),
            "jsonl_line_no": FieldSpec(
                type="int", required=False,
                description="1-indexed line number for JSONL-derived drawers",
            ),
        },
    )
```

### Incremental ingest (`is_current`)

```python
def is_current(self, *, item: SourceItemMetadata, existing_metadata: Optional[dict]) -> bool:
    if not existing_metadata:
        return False
    stored = existing_metadata.get("source_mtime")
    return stored == item.version  # version == source_mtime ISO-8601
```

For JSONL artifacts, `version` includes the line count so an append at the tail invalidates the file cleanly without re-ingesting earlier lines (the lines that already exist are addressed by `source_file` with `#line=` and skipped individually).

## Verbatim guarantee (§1.4)

- Markdown files (`AGENTS.md`, skills, commands, rules) yield drawers whose `content` is the file's exact UTF-8 bytes after `newline_normalize` + `whitespace_trim`. The declared transformations are byte-checkable; the round-trip suite confirms `re_normalize(content) == re_normalize(original)`.
- JSON files (`.omo/tasks/*.json`, settings.json) yield drawers whose `content` is the file's exact text. The conformance check is `json.loads(content) == json.loads(original)` (structural equality — whitespace-only divergence is allowed because we want grep-friendly content, not byte-identical roundtrip).
- JSONL files yield one drawer per line. `content` is the line's exact text (no JSON re-encoding).
- No summarization, no LLM-assisted extraction, no `learning.type` re-classification. The omoa #1397 spec proposes classification *at capture time inside omoa*; MemPalace ingests whatever omoa wrote, as-is. If omoa later changes its learning schema, we mine the new shape verbatim and the schema lives in omoa's `LearningEntry` JSON, not ours.

This matches `CLAUDE.md` §"Verbatim always": the structured metadata an omoa user might want to filter on (`type=anti-pattern`, `target=AGENTS.md`) lives in the per-line JSON content — searchable via `mempalace_search` keyword match against the verbatim line.

## Privacy and gitignore handling

- Default `privacy_class`: `pii_potential`. Project-tree config files routinely contain user/host/repo names, API keys (in violation but real), absolute home paths. An operator MAY override via `options={"privacy_class": "secrets_possible"}` to gate against the upcoming [#389](https://github.com/MemPalace/mempalace/pull/389) sensitive-content scanner.
- The adapter respects `.gitignore`. `.claude/settings.local.json` is conventionally gitignored and SHOULD be mined only when the operator passes `options={"include_gitignored": True}` because secrets land there. Default: skip.
- `~/.config/opencode/mcp-oauth.json` (chmod 0600 in the omoa docs) is **never** mined regardless of options, because it holds OAuth tokens. The adapter hard-skips any file matching `*oauth*.json` or `*credentials*` under the OpenCode/Claude config paths.

## Composition with the existing OpenCode adapter

A user with both adapters enabled and a project at `/home/jp/Projects/example`:

```bash
mempalace mine --source opencode             # all sessions, user-scope
mempalace mine --source omoa /home/jp/Projects/example   # this project's context
```

Drawers from the two adapters never collide:

| Adapter | `source_file` URI prefix |
|---|---|
| `opencode` | `opencode://~/.local/share/opencode/opencode.db#session=ses_...` |
| `omoa` | `omoa:///home/jp/Projects/example/AGENTS.md` |

A `mempalace_search` over both yields verbatim hits ranked by semantic similarity: the agent gets *what was said* (sessions) and *what was decided* (project context) in one query.

## Open questions

1. **`AGENTS.md` chunking.** Long `AGENTS.md` files (the omoa example trees regularly hit 2-3K lines) may want section-level chunking, not whole-record. The first-party `convo_miner.chunk_exchanges` does exchange-pair chunking which doesn't fit. Proposed v1: whole-record (one drawer per file); revisit if drawers blow past Chroma's per-document soft ceiling or search precision suffers.
2. **`.omo/tasks/*.json` vs `~/.omo/`.** omoa's task system is project-local in `.omo/tasks/`, but the README hints at a user-scope `~/.omo/` for cross-project context. Verify against omoa source before v1 — if user-scope tasks exist, they belong behind the same `include_user_scope` flag.
3. **omoa #1397 lock-in.** The learning-capture spec is still draft / community-review. We should hold off mining `.sisyphus/learnings/*.jsonl` until omoa pins the schema. v1 ships without learnings support and adds it on follow-up PR.
4. **Rename transition.** omoa is dual-publishing as `oh-my-opencode` and `oh-my-openagent` during the rename. The adapter should recognize both `oh-my-opencode.jsonc` and `oh-my-openagent.jsonc` as the same artifact kind (`config`) — handled in the discovery glob list above.
5. **Should this live in-tree or as a separate `mempalace-source-omoa` package?** RFC 002 says third parties ship as `pip install mempalace-source-<name>`, and omoa is third-party. But the same RFC also recognizes "first-party adapters that ship in core" (filesystem, conversations, opencode, codex, gemini, aider, warp). Given that omoa-on-OpenCode is the closest analogue to Claude-Code-on-Anthropic and we already ship the bare OpenCode adapter in-tree, ship `omoa` in-tree too. A separate package adds release friction without buying isolation.

## Effort estimate

| Item | Effort | Notes |
|---|---|---|
| Adapter skeleton + discovery + verbatim emit | S (~3h) | Mirrors `mempalace/sources/filesystem.py` |
| `is_current` for files + JSONL append handling | S (~2h) | mtime check + JSONL line slicing |
| `describe_schema` + frontmatter extraction for skills/rules | S (~2h) | Reuse `python-frontmatter` if already a dep |
| Conformance suite (RFC 002 §7 round-trips) | M (~3h) | Markdown byte-roundtrip + JSON structural equality |
| Integration with `mempalace mine` CLI (`--source omoa`) | XS (~30 min) | Entry-point registration |
| Docs: `docs/integrations/omoa.md` modelled on the opencode doc | S (~2h) | |
| **Total** | **M (~12h, ~1.5 days)** | Single contributor |

## Next steps if approved

1. File a follow-up PR implementing `mempalace/sources/omoa.py` + tests + entry-point + docs.
2. Add a `docs/fork-changes.yaml` entry for fork-ahead inventory.
3. Hold the discussion #1277 reply until both opencode (already done) and omoa (this PR) are shippable — per [`feedback_upstream_comment_timing.md`](https://github.com/jphein/) (private memory).
4. Open an issue against omoa to confirm task-storage paths (`~/.omo/` vs `.omo/`) and learnings schema timing.

## References

- omoa repo: <https://github.com/code-yeongyu/oh-my-openagent> (default branch `dev`)
- omoa feature reference: <https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/reference/features.md>
- omoa learning capture proposal: <https://github.com/code-yeongyu/oh-my-openagent/issues/1397>
- The existing OpenCode adapter (which already covers omoa sessions): [`mempalace/sources/opencode.py`](../../mempalace/sources/opencode.py)
- RFC 002 spec text: [`docs/rfcs/002-source-adapter-plugin-spec.md`](../rfcs/002-source-adapter-plugin-spec.md)
- Companion note: [`2026-05-26-opencode-rlm-gap-analysis.md`](2026-05-26-opencode-rlm-gap-analysis.md)
