#!/usr/bin/env python3
"""Render fork docs from the canonical YAML manifest.

Reads ``docs/fork-changes.yaml`` and regenerates the fork-ahead
narrative in:

  - ``FORK_CHANGELOG.md``                                         (today)
  - ``README.md`` (fork-change-queue table — between markers)     (planned)
  - ``CLAUDE.md`` (row-by-row inventory — between markers)        (planned)
  - scratch/promises.md (tracker entries — between markers)       (planned)

Today only the FORK_CHANGELOG render is implemented; the others are
stubbed and will land in follow-on commits.

Usage::

    scripts/render-docs.py              # write all targets
    scripts/render-docs.py --check      # exit 1 if any target would change
    scripts/render-docs.py --target changelog   # only render the changelog
"""

from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    print(f"PyYAML required (`pip install pyyaml`): {exc}", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = REPO_ROOT / "docs" / "fork-changes.yaml"

CHANGELOG_PATH = REPO_ROOT / "FORK_CHANGELOG.md"
CHANGELOG_HEADER = """\
# Fork Changelog (jphein/mempalace)

Fork-ahead changes that aren't yet in upstream `MemPalace/mempalace`.
Upstream's release history lives in [`CHANGELOG.md`](CHANGELOG.md);
this file is the supplement.

> **This file is generated.** Edit `docs/fork-changes.yaml` and run
> `scripts/render-docs.py` to regenerate. Hand-edits will be
> overwritten on the next render.

Date-based sections, not semver — the fork tracks `upstream/develop` and
doesn't cut its own release tags. When a fork-ahead row lands upstream,
move the entry to the **Merged into upstream** section at the bottom
(kept ~30 days, then trimmed).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---
"""

BUCKET_ORDER = ("Added", "Changed", "Fixed", "Performance")


def load_manifest(path: Path = YAML_PATH) -> dict:
    if not path.is_file():
        raise SystemExit(f"manifest not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "entries" not in data:
        raise SystemExit(f"manifest missing top-level 'entries' key: {path}")
    return data


def commit_link(sha: str) -> str:
    """Render a 7-char SHA as a markdown link to the fork commit."""
    return f"[`{sha}`](https://github.com/jphein/mempalace/commit/{sha})"


def render_entry(entry: dict[str, Any]) -> str:
    """Emit one bullet for an entry."""
    summary = entry.get("summary", "").strip()
    body = entry.get("body", "").strip()
    bullet = f"- **{summary}** ({commit_link(entry['commit'])})\n"
    # Reflow body: prepend each line with a 2-space indent so the
    # markdown bullet wraps the paragraph cleanly.
    if body:
        for line in body.splitlines():
            bullet += f"  {line}\n" if line.strip() else "\n"
    extras = []
    if entry.get("tests"):
        extras.append(f"  *Tests:* {entry['tests']}")
    if entry.get("pr"):
        pr = entry["pr"]
        state = entry.get("pr_state", "")
        state_str = f" ({state})" if state else ""
        extras.append(
            f"  *Upstream:* [PR #{pr}](https://github.com/MemPalace/mempalace/pull/{pr}){state_str}"
        )
    if entry.get("files"):
        extras.append("  *Files:* " + ", ".join(f"`{p}`" for p in entry["files"]))
    if extras:
        bullet += "\n" + "\n".join(extras) + "\n"
    return bullet


def render_changelog(manifest: dict) -> str:
    """Render FORK_CHANGELOG.md content from the manifest."""
    out = [CHANGELOG_HEADER]

    # Group by date (newest first) then by bucket.
    by_date: dict[str, dict[str, list[dict]]] = OrderedDict()
    for entry in manifest["entries"]:
        date = str(entry["date"])
        bucket = entry.get("bucket", "Changed")
        by_date.setdefault(date, {b: [] for b in BUCKET_ORDER}).setdefault(bucket, []).append(entry)

    # Sort dates descending — manifest is presentation order but a date
    # may straddle entries; sorting keeps headings stable.
    for date in sorted(by_date.keys(), reverse=True):
        out.append(f"\n## [{date}]\n")
        buckets = by_date[date]
        for bucket in BUCKET_ORDER:
            entries_in_bucket = buckets.get(bucket, [])
            if not entries_in_bucket:
                continue
            out.append(f"\n### {bucket}\n")
            for entry in entries_in_bucket:
                out.append("\n" + render_entry(entry))

    out.append("\n---\n\n## Merged into upstream (recent)\n")
    merged = manifest.get("merged_upstream", {})
    for note in merged.get("notes", []):
        out.append(f"\n*{note}*\n")
    out.append("")
    for m in merged.get("entries", []):
        pr = m.get("pr")
        title = m.get("title", "")
        merged_at = m.get("merged") or m.get("released_in") or ""
        link = (
            f"[PR #{pr}](https://github.com/MemPalace/mempalace/pull/{pr})"
            if pr
            else "(see upstream)"
        )
        when = f" — {merged_at}" if merged_at else ""
        out.append(f"- {link} — {title}{when}")
    out.append("")  # trailing newline

    return "\n".join(out)


def write_or_check(path: Path, content: str, check_only: bool) -> bool:
    """Write ``content`` to ``path`` (or compare in --check mode).

    Returns True if the file changed (or would change).
    """
    existing = path.read_text() if path.is_file() else ""
    if existing == content:
        return False
    if check_only:
        print(f"DRIFT: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return True
    path.write_text(content)
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="exit 1 if any target is stale")
    p.add_argument(
        "--target",
        choices=["changelog", "all"],
        default="all",
        help="which destination(s) to render",
    )
    args = p.parse_args()

    manifest = load_manifest()
    drift = False

    if args.target in ("changelog", "all"):
        rendered = render_changelog(manifest)
        if write_or_check(CHANGELOG_PATH, rendered, args.check):
            drift = True

    # README + CLAUDE + promises rendering planned for follow-on commits.

    if args.check and drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
