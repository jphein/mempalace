#!/usr/bin/env python3
"""maintain-fork-changes.py — mechanical cleanup passes on docs/fork-changes.yaml.

Two passes:

1. **Resolve `commit: HEAD` placeholders** by looking up the closing commit
   for each entry. Authors write `commit: HEAD` on the PR branch because
   the squash-merge SHA isn't known yet; nothing in our flow rewrites it
   post-merge. This pass scans the entry's text for `#NN` references and
   matches them to the most recent commit on the configured branch using
   priority order:

     2 — explicit `closes #NN` / `fixes #NN` (or repo-prefixed) in commit
         subject or body
     1 — trailing `(#NN)` in commit subject (the squash-merge marker)
     0 — any other `#NN` mention

   Highest priority wins. Entries without any resolvable `#NN` are left
   on `commit: HEAD`.

2. **De-duplicate entries by `id:`**. Long rebase chains can re-insert
   the same entry into a yaml block. This pass keeps the first occurrence
   of each id slug and drops the rest, reporting what was removed.

Both passes are idempotent — running on a clean tree produces no diff.

Filed as #316. See #317 for the manual fix this script makes mechanical.

Usage::

    scripts/maintain-fork-changes.py            # apply both passes
    scripts/maintain-fork-changes.py --check    # exit 1 if either pass
                                                # would change anything
    scripts/maintain-fork-changes.py --no-dedup
    scripts/maintain-fork-changes.py --no-resolve-head
    scripts/maintain-fork-changes.py --branch=origin/main --depth=200
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


YAML_PATH = Path("docs/fork-changes.yaml")
ENTRY_ID_RE = re.compile(r"^  - id: (\S+)$")
HEAD_LINE_RE = re.compile(r"^(\s*commit:\s*)HEAD\s*$")
ISSUE_REF_RE = re.compile(r"#(\d+)")
CLOSES_RE = re.compile(r"(?:closes?|fix(?:es)?)\s+(?:[\w-]+/[\w-]+)?#(\d+)", re.IGNORECASE)
SUBJECT_PR_RE = re.compile(r"\(#(\d+)\)\s*$")


def build_issue_to_sha(branch: str, depth: int) -> dict[str, str]:
    """Walk recent commits, build {issue_number: short_sha} with priority resolution."""
    out = subprocess.check_output(
        ["git", "log", "--format=%H%x00%s%x00%b%x01", f"-{depth}", branch],
        text=True,
    )
    issue_to_sha: dict[str, tuple[int, str]] = {}

    def offer(n: str, priority: int, sha: str) -> None:
        cur = issue_to_sha.get(n)
        if cur is None or priority > cur[0]:
            issue_to_sha[n] = (priority, sha)

    for chunk in out.split("\x01"):
        if not chunk.strip():
            continue
        parts = chunk.strip().split("\x00")
        if len(parts) < 3:
            continue
        sha = parts[0][:7]
        subject = parts[1]
        body = parts[2]
        combined = subject + "\n" + body

        # Priority 2: closes/fixes #N (with optional repo prefix)
        for m in CLOSES_RE.finditer(combined):
            offer(m.group(1), 2, sha)
        # Priority 1: trailing "(#N)" in subject
        m = SUBJECT_PR_RE.search(subject)
        if m:
            offer(m.group(1), 1, sha)
        # Priority 0: any other #N mention
        for m in ISSUE_REF_RE.finditer(combined):
            offer(m.group(1), 0, sha)

    return {k: v[1] for k, v in issue_to_sha.items()}


def resolve_head_placeholders(
    lines: list[str], issue_to_sha: dict[str, str], dry_run: bool = False
) -> tuple[list[str], list[tuple[str, str]]]:
    """Replace `commit: HEAD` lines with the resolved short SHA.

    Returns (new_lines, resolved_pairs).
    """
    out: list[str] = []
    resolved: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        m = HEAD_LINE_RE.match(line.rstrip("\n"))
        if not m:
            out.append(line)
            continue
        # Look at the next 12 lines for a #NN reference
        sha = None
        ref = None
        for j in range(i + 1, min(i + 13, len(lines))):
            for issue_match in ISSUE_REF_RE.finditer(lines[j]):
                n = issue_match.group(1)
                if n in issue_to_sha:
                    sha = issue_to_sha[n]
                    ref = n
                    break
            if sha:
                break
        if sha and not dry_run:
            prefix = m.group(1)
            out.append(f"{prefix}{sha}\n")
            resolved.append((sha, ref or "?"))
        elif sha and dry_run:
            out.append(line)
            resolved.append((sha, ref or "?"))
        else:
            out.append(line)
    return out, resolved


def dedup_by_id(lines: list[str]) -> tuple[list[str], list[str]]:
    """Drop subsequent entries sharing an id with an earlier one.

    Returns (new_lines, dropped_ids).
    """
    seen: set[str] = set()
    dropped: list[str] = []
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = ENTRY_ID_RE.match(lines[i].rstrip("\n"))
        if m:
            slug = m.group(1)
            if slug in seen:
                # Find end of this entry block: next "  - id:" or EOF
                j = i + 1
                while j < len(lines):
                    if ENTRY_ID_RE.match(lines[j].rstrip("\n")):
                        break
                    j += 1
                # Strip any trailing blank lines that belonged to this block
                while j > i + 1 and lines[j - 1].strip() == "":
                    j -= 1
                dropped.append(slug)
                i = j
                continue
            else:
                seen.add(slug)
        out.append(lines[i])
        i += 1

    # Normalize triple-blank-line runs
    text = "".join(out)
    text = re.sub(r"\n\n\n+", "\n\n", text)
    return text.splitlines(keepends=True), dropped


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--no-resolve-head", action="store_true", help="skip the HEAD-resolution pass")
    ap.add_argument("--no-dedup", action="store_true", help="skip the de-duplication pass")
    ap.add_argument(
        "--branch",
        default="origin/main",
        help="branch to walk for commit lookup (default: origin/main)",
    )
    ap.add_argument(
        "--depth", type=int, default=200, help="commit history depth to scan (default: 200)"
    )
    args = ap.parse_args()

    repo_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    yaml_path = Path(repo_root) / YAML_PATH
    if not yaml_path.exists():
        print(f"✗ {yaml_path} not found", file=sys.stderr)
        return 2

    original = yaml_path.read_text()
    lines = original.splitlines(keepends=True)
    changed = False

    if not args.no_dedup:
        lines, dropped = dedup_by_id(lines)
        if dropped:
            changed = True
            for slug in dropped:
                print(f"  dedup: removed duplicate entry id={slug!r}")

    if not args.no_resolve_head:
        issue_to_sha = build_issue_to_sha(args.branch, args.depth)
        lines, resolved = resolve_head_placeholders(lines, issue_to_sha, dry_run=args.check)
        if resolved:
            changed = True
            for sha, issue in resolved:
                print(f"  resolved: commit:HEAD → {sha} (matched #{issue})")

    new_text = "".join(lines)
    if args.check:
        if new_text != original:
            print("✗ docs/fork-changes.yaml would change — run without --check to apply")
            return 1
        print("✦ docs/fork-changes.yaml is clean")
        return 0

    if changed:
        yaml_path.write_text(new_text)
        print("✦ docs/fork-changes.yaml updated")
    else:
        print("✦ docs/fork-changes.yaml is clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
