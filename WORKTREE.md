# WORKTREE.md — the once-and-for-all venv rule

This repo uses an editable install (`pip install -e .`). That means **every venv has a `.pth` file pointing at a checkout path**. If that path disappears, every import from that venv breaks.

Worktrees are checkouts that disappear. So:

## The rule

**One venv per worktree, *inside* that worktree, gitignored, disposable with the worktree.**

- A worktree owns its `.venv/` (already in `.gitignore`).
- A venv may only be `pip install -e .`'d from the worktree that contains it.
- When the worktree is removed, the venv goes with it — no orphaned `.pth` entries pointing at deleted paths.

That is the entire rule. Everything below is the mechanics.

## Creating a worktree

```bash
# From the canonical checkout root (e.g. /home/jp/Projects/kg-extract-katana)
git worktree add ../<worktree-name> -b <branch-name>
cd ../<worktree-name>

# Build its own venv inside the worktree
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev,postgres,kg-extract]"
```

Now `.venv/lib/python3.*/site-packages/mempalace.egg-link` (or the `.pth` file under newer pip) points at `../<worktree-name>`. When you remove the worktree, both die together.

## Removing a worktree

```bash
git worktree remove ../<worktree-name>     # refuses if you have uncommitted changes
# or, after pushing the branch:
git worktree remove ../<worktree-name> --force
```

The venv inside is removed with the worktree. No cleanup needed in any other venv.

## What you must NEVER do

1. **Never run `pip install -e .` from worktree A into the venv of worktree B (or into the canonical clone's venv).** That writes a `.pth` pointing at A. When A is removed, B's venv breaks.
2. **Never share a venv across worktrees by symlink, `VIRTUAL_ENV=...`, or activating the wrong one.** Same failure mode.
3. **Never `pip install` system-wide from inside a worktree.** Use the worktree's `.venv`.

## Activation

From the worktree root:

```bash
source .venv/bin/activate
# or invoke directly without activating:
.venv/bin/python -m pytest
.venv/bin/mempalace status
```

A quick sanity check before any `pip install -e .`:

```bash
# Should print the worktree path, not the canonical clone or sibling worktree
python -c "import sys; print(sys.prefix)"
```

## For agents working in this repo

If you are a Claude subagent spawned with `isolation: "worktree"`:

1. Your `pwd` IS the worktree. Stay there.
2. Before touching dependencies, run the **Creating a worktree** block from the section above — specifically the `python3 -m venv .venv && .venv/bin/pip install -e ".[...]"` lines. Each worktree needs its own venv.
3. If a test or build script reaches outside your worktree path, stop and report — that's a leak.
4. Commit + push + open the PR — but DO NOT run `git worktree remove` on your own worktree. The orchestrator does cleanup after consolidating.

## Why this matters

Incident 2026-04-XX (see `~/.claude/projects/-home-jp-Projects-memorypalace/memory/feedback_editable_install_worktree_trap.md`): a disposable worktree did `pip install -e .` into a shared venv. When the worktree was removed, every other venv that shared site-packages started raising `ModuleNotFoundError: mempalace` because the `.pth` entry pointed at a path that no longer existed. Recovery required hand-editing `.pth` files across multiple venvs.

One venv per worktree, inside the worktree. That's the rule.
