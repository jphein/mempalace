"""
palace.py — Shared palace operations.

Consolidates ChromaDB access patterns used by both miners and the MCP server.
"""

import logging
import os
import sqlite3

import chromadb

logger = logging.getLogger(__name__)

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    "coverage",
    ".mempalace",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".ipynb_checkpoints",
    ".eggs",
    "htmlcov",
    "target",
}


def _fix_blob_seq_ids(palace_path: str):
    """Fix ChromaDB 0.6.x → 1.5.x migration bug: BLOB seq_ids → INTEGER.

    ChromaDB 0.6.x stored seq_id as big-endian 8-byte BLOBs. ChromaDB 1.5.x
    expects INTEGER. The auto-migration doesn't convert existing rows, causing
    the Rust compactor to crash with "mismatched types; Rust type u64 (as SQL
    type INTEGER) is not compatible with SQL type BLOB".

    Must run BEFORE PersistentClient is created (the compactor fires on init).
    """
    db_path = os.path.join(palace_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        for table in ("embeddings", "max_seq_id"):
            try:
                rows = conn.execute(
                    f"SELECT rowid, seq_id FROM {table} WHERE typeof(seq_id) = 'blob'"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            if not rows:
                continue
            updates = [(int.from_bytes(blob, byteorder="big"), rowid) for rowid, blob in rows]
            conn.executemany(f"UPDATE {table} SET seq_id = ? WHERE rowid = ?", updates)
            logger.info("Fixed %d BLOB seq_ids in %s", len(updates), table)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Could not fix BLOB seq_ids: %s", e)


def get_collection(palace_path: str, collection_name: str = "mempalace_drawers"):
    """Get or create the palace ChromaDB collection."""
    os.makedirs(palace_path, exist_ok=True)
    try:
        os.chmod(palace_path, 0o700)
    except (OSError, NotImplementedError):
        pass
    _fix_blob_seq_ids(palace_path)
    client = chromadb.PersistentClient(path=palace_path)
    try:
        return client.get_collection(collection_name)
    except Exception:
        return client.create_collection(collection_name, metadata={"hnsw:space": "cosine"})


def file_already_mined(collection, source_file: str, check_mtime: bool = False) -> bool:
    """Check if a file has already been filed in the palace.

    When check_mtime=True (used by project miner), returns False if the file
    has been modified since it was last mined, so it gets re-mined.
    When check_mtime=False (used by convo miner), just checks existence.
    """
    try:
        results = collection.get(where={"source_file": source_file}, limit=1)
        if not results.get("ids"):
            return False
        if check_mtime:
            stored_meta = results.get("metadatas", [{}])[0]
            stored_mtime = stored_meta.get("source_mtime")
            if stored_mtime is None:
                return False
            current_mtime = os.path.getmtime(source_file)
            return abs(float(stored_mtime) - current_mtime) < 0.01
        return True
    except Exception:
        return False


def bulk_check_mined(collection) -> dict[str, float]:
    """Pre-fetch source_file/source_mtime pairs for all documents in the collection.

    Returns a dict mapping source_file -> source_mtime (as float) for every
    document that has both fields.  Callers can check membership and compare
    mtimes locally instead of issuing one ChromaDB query per file.

    Fetches the full collection in paginated batches (like palace_graph.py)
    since a WHERE-IN filter on thousands of paths is not supported by ChromaDB.
    """
    mined: dict[str, float] = {}
    try:
        total = collection.count()
        offset = 0
        while offset < total:
            batch = collection.get(limit=1000, offset=offset, include=["metadatas"])
            for meta in batch["metadatas"]:
                src = meta.get("source_file")
                mtime = meta.get("source_mtime")
                if src and mtime is not None:
                    mined[src] = float(mtime)
            if not batch["ids"]:
                break
            offset += len(batch["ids"])
    except Exception:
        logger.warning("bulk_check_mined: partial fetch, %d files loaded", len(mined))
    return mined
