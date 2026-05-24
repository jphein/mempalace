#!/usr/bin/env python3
"""One-way sync of repo docs to Outline.

Repo is the source of truth. For each file in DOCS_TO_SYNC, search Outline
for a document with the given title; create it if missing, update if the
text differs. Never reads anything back into the repo.

Env vars:
    OUTLINE_API_KEY     Required. Outline API token.
    OUTLINE_BASE_URL    Optional, defaults to https://outline.jphe.in.
    OUTLINE_COLLECTION  Optional, defaults to "MemPalace".
    DRY_RUN             Optional. If set, log actions without calling create/update.

Exit codes:
    0 — all syncs succeeded (or dry run completed).
    1 — at least one sync failed; details on stderr.
    2 — configuration error (missing key, missing collection).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Title -> repo-relative path. Title is what Outline displays; we look it up
# by exact match in documents.search results. Keep titles human-readable;
# they're the wiki's navigation surface.
DOCS_TO_SYNC: dict[str, str] = {
    "MemPalace README": "README.md",
    "MemPalace Architecture": "docs/ARCHITECTURE.md",
    "MemPalace Ecosystem": "docs/ECOSYSTEM.md",
    "MemPalace Bibliography": "docs/BIBLIOGRAPHY.md",
    "MemPalace Mission": "MISSION.md",
    "MemPalace Roadmap": "ROADMAP.md",
    "MemPalace Contributing": "CONTRIBUTING.md",
    "MemPalace Security": "SECURITY.md",
    "MemPalace Fork Changelog": "FORK_CHANGELOG.md",
    "Research: Chunking Strategy Ablation": "docs/research/2026-05-06-chunking-strategy-ablation.md",
    "Research: Multi-Encoder RRF": "docs/research/2026-05-15-multi-encoder-rrf.md",
    "Research: Docs Automation Survey": "docs/research/2026-05-24-docs-automation-survey.md",
    "Research: Memory System Benchmarks": "docs/research/2026-05-24-memory-system-benchmarks.md",
    "Research: True Memory Comparison": "docs/research/2026-05-24-true-memory-comparison.md",
    "Research: Verbatim vs Derivative": "docs/research/verbatim-vs-derivative-axis.md",
}


@dataclass
class Config:
    api_key: str
    base_url: str
    collection_name: str
    dry_run: bool


def load_config() -> Config:
    api_key = os.environ.get("OUTLINE_API_KEY", "").strip()
    if not api_key:
        print("ERROR: OUTLINE_API_KEY is not set", file=sys.stderr)
        sys.exit(2)
    base_url = os.environ.get("OUTLINE_BASE_URL", "https://outline.jphe.in").rstrip("/")
    collection_name = os.environ.get("OUTLINE_COLLECTION", "MemPalace")
    dry_run = bool(os.environ.get("DRY_RUN"))
    return Config(api_key=api_key, base_url=base_url, collection_name=collection_name, dry_run=dry_run)


def outline_post(cfg: Config, endpoint: str, body: dict) -> dict:
    """POST to Outline API with auth, retries on 429 and 5xx."""
    url = f"{cfg.base_url}/api/{endpoint}"
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            status = e.code
            body_text = e.read().decode("utf-8", errors="replace")
            if status == 429 or status >= 500:
                wait = 2 ** attempt
                print(f"  HTTP {status} on {endpoint}; retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                last_err = e
                continue
            raise RuntimeError(f"HTTP {status} on {endpoint}: {body_text}") from e
        except urllib.error.URLError as e:
            wait = 2 ** attempt
            print(f"  network error on {endpoint}: {e}; retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            last_err = e

    raise RuntimeError(f"giving up on {endpoint} after retries: {last_err}")


def find_collection_id(cfg: Config) -> str | None:
    resp = outline_post(cfg, "collections.list", {"limit": 100})
    for col in resp.get("data", []):
        if col.get("name") == cfg.collection_name:
            return col.get("id")
    return None


def find_doc(cfg: Config, title: str, collection_id: str) -> dict | None:
    """Find a doc by exact title within the collection. Returns the doc dict or None."""
    resp = outline_post(
        cfg,
        "documents.search",
        {"query": title, "collectionId": collection_id, "limit": 25},
    )
    for item in resp.get("data", []):
        doc = item.get("document", {})
        if doc.get("title") == title:
            return doc
    return None


def fetch_doc_text(cfg: Config, doc_id: str) -> str:
    resp = outline_post(cfg, "documents.info", {"id": doc_id})
    return resp.get("data", {}).get("text", "")


def create_doc(cfg: Config, title: str, text: str, collection_id: str) -> str:
    resp = outline_post(
        cfg,
        "documents.create",
        {"title": title, "text": text, "collectionId": collection_id, "publish": True},
    )
    return resp.get("data", {}).get("id", "")


def update_doc(cfg: Config, doc_id: str, text: str) -> None:
    outline_post(cfg, "documents.update", {"id": doc_id, "text": text, "append": False})


def read_doc(rel_path: str) -> str:
    full = REPO_ROOT / rel_path
    if not full.exists():
        raise FileNotFoundError(f"{full} does not exist")
    return full.read_text(encoding="utf-8")


def sync_one(cfg: Config, title: str, rel_path: str, collection_id: str) -> str:
    """Returns one of: 'created', 'updated', 'unchanged', 'skipped', 'error'."""
    try:
        text = read_doc(rel_path)
    except FileNotFoundError as e:
        print(f"  SKIP {title}: {e}", file=sys.stderr)
        return "skipped"

    existing = find_doc(cfg, title, collection_id)

    if existing is None:
        if cfg.dry_run:
            print(f"  DRY-RUN would CREATE: {title} ({len(text)} chars)")
            return "created"
        doc_id = create_doc(cfg, title, text, collection_id)
        print(f"  CREATED: {title} (id={doc_id})")
        return "created"

    doc_id = existing.get("id", "")
    current_text = fetch_doc_text(cfg, doc_id)
    if current_text == text:
        print(f"  unchanged: {title}")
        return "unchanged"

    if cfg.dry_run:
        print(f"  DRY-RUN would UPDATE: {title} (id={doc_id}, {len(text)} chars)")
        return "updated"
    update_doc(cfg, doc_id, text)
    print(f"  UPDATED: {title} (id={doc_id})")
    return "updated"


def main() -> int:
    cfg = load_config()
    print(f"Outline sync target: {cfg.base_url}")
    print(f"Collection: {cfg.collection_name}")
    if cfg.dry_run:
        print("DRY RUN — no writes will be made")

    collection_id = find_collection_id(cfg)
    if not collection_id:
        print(f"ERROR: collection '{cfg.collection_name}' not found", file=sys.stderr)
        return 2

    print(f"Collection ID: {collection_id}")
    print(f"Syncing {len(DOCS_TO_SYNC)} document(s)...")

    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "error": 0}
    failures: list[str] = []

    for title, rel_path in DOCS_TO_SYNC.items():
        try:
            result = sync_one(cfg, title, rel_path, collection_id)
            counts[result] += 1
        except Exception as e:
            counts["error"] += 1
            failures.append(f"{title}: {e}")
            print(f"  ERROR {title}: {e}", file=sys.stderr)

    print()
    print(
        "Summary: "
        f"{counts['created']} created, "
        f"{counts['updated']} updated, "
        f"{counts['unchanged']} unchanged, "
        f"{counts['skipped']} skipped, "
        f"{counts['error']} error(s)"
    )

    if failures:
        print("\nFailures:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
