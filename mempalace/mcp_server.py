#!/usr/bin/env python3
"""
MemPalace MCP Server — read/write palace access for Claude Code
================================================================
Install: claude mcp add mempalace -- mempalace-mcp [--palace /path/to/palace]

Tools (read):
  mempalace_status          — total drawers, wing/room breakdown
  mempalace_list_wings      — all wings with drawer counts
  mempalace_list_rooms      — rooms within a wing
  mempalace_get_taxonomy    — full wing → room → count tree
  mempalace_search          — semantic search, optional wing/room filter
  mempalace_check_duplicate — check if content already exists before filing

Tools (write):
  mempalace_add_drawer      — file verbatim content into a wing/room
  mempalace_delete_drawer   — remove a drawer by ID

Tools (maintenance):
  mempalace_reconnect       — force cache invalidation and reconnect after external writes
"""

import os
import sys

# --- MCP stdio protection (issue #225) -----------------------------------
# The MCP protocol multiplexes JSON-RPC over stdio: stdout MUST carry only
# valid JSON-RPC messages, stderr is for human-readable logs. Some
# transitive dependencies (chromadb → onnxruntime, posthog telemetry) print
# banners and error messages directly to stdout — sometimes at C level —
# which breaks Claude Desktop's JSON parser. Redirect stdout → stderr at
# both the Python and file-descriptor level before heavy imports, then
# restore the real stdout in main() before entering the protocol loop.
_REAL_STDOUT = sys.stdout
_REAL_STDOUT_FD = None
try:
    _REAL_STDOUT_FD = os.dup(1)
    os.dup2(2, 1)
except (OSError, AttributeError):
    # Environments without fd-level stdio (embedded interpreters, some test
    # harnesses). The Python-level redirect below still applies.
    pass
sys.stdout = sys.stderr

import argparse  # noqa: E402  (deferred until after stdio protection above)
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import hashlib  # noqa: E402
import sqlite3  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

from .config import (  # noqa: E402
    MempalaceConfig,
    sanitize_kg_value,
    sanitize_name,
    sanitize_content,
    sanitize_iso_temporal,
    strip_lone_surrogates,
)
from .version import __version__  # noqa: E402
from chromadb.errors import NotFoundError as _ChromaNotFoundError  # noqa: E402

from .backends.chroma import (  # noqa: E402
    ChromaBackend,
    ChromaCollection,
    _HNSW_BLOAT_GUARD,
    _pin_hnsw_threads,
    hnsw_capacity_status,
)
from .query_sanitizer import sanitize_query  # noqa: E402
from .write_sanitizer import sanitize_write_content, sanitize_write_name  # noqa: E402
from .searcher import search_memories  # noqa: E402
from .palace_graph import (  # noqa: E402
    traverse,
    find_tunnels,
    graph_stats,
    create_tunnel,
    list_tunnels,
    delete_tunnel,
    follow_tunnels,
)

from .knowledge_graph import KnowledgeGraph, DEFAULT_KG_PATH  # noqa: E402


def _init_logging() -> None:
    """Root-logger init: always stderr, optionally append to ``MEMPALACE_LOG_FILE``.

    Stderr-only is the default. When ``MEMPALACE_LOG_FILE`` is set, a
    ``FileHandler`` is attached so MCP-client failures that the client
    does not surface (e.g. the ``-32000`` cold-load timeout in #1495)
    remain diagnosable from the file.

    Failure modes:

    * Invalid path (missing directory, no perms, Windows NUL byte) →
      stderr-only with a warning. The env var must not become a new
      server-start failure surface — that would defeat the diagnostic
      goal. ``ValueError`` is included in the catch because Windows
      raises it for paths with embedded NUL bytes, not ``OSError``.
    * Root logger already configured (host app embedding the server,
      transitive imports touching ``logging``) → ``force=True`` resets
      the handlers so MEMPALACE_LOG_FILE's contract holds regardless
      of what touched root logging first. Without ``force=True``,
      ``basicConfig`` is a no-op when handlers exist and the env var
      silently does nothing — exactly the diagnostic black hole #1495
      exists to close.
    * Concurrent writers (multiple ``mempalace-mcp`` processes pointing
      at the same path) interleave at the line level. The handler uses
      append mode so nothing is overwritten, but operators running
      Claude Code + Claude Desktop simultaneously should give each
      process its own log path.

    ``delay=True`` is intentionally NOT set: deferring the open means an
    invalid path raises at ``emit()`` time (unhandled), defeating the
    fail-soft contract. With eager open the same error surfaces inside
    ``FileHandler.__init__`` and lands in our ``except`` below.

    Module-level invocation: this function runs at import time, preserving
    the side effect of the previous module-level ``logging.basicConfig``
    call. Callers that import ``mempalace.mcp_server`` for introspection
    (``TOOLS`` dict, handler functions) inherit the reset; this matches
    pre-PR behaviour and is intentional for an MCP entry-point module.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    # MEMPALACE_LOG_FILE is operator-supplied and opt-in; this is a
    # local-first server (CLAUDE.md design principle), so no path
    # sanitization — the operator's process UID is the trust boundary.
    log_file = os.environ.get("MEMPALACE_LOG_FILE", "").strip()
    file_handler_error: Exception | None = None
    if log_file:
        try:
            handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Fail-soft: see "Invalid path" failure mode above. Broad on
            # (OSError, ValueError) because Windows raises ValueError for
            # NUL-byte paths while POSIX uses OSError for missing-dir / EPERM.
            file_handler_error = exc
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=handlers, force=True)
    if file_handler_error is not None:
        logging.getLogger("mempalace_mcp").warning(
            "MEMPALACE_LOG_FILE=%r could not be opened (%s); using stderr only",
            log_file,
            file_handler_error,
        )


_init_logging()
logger = logging.getLogger("mempalace_mcp")


def _parse_args():
    parser = argparse.ArgumentParser(description="MemPalace MCP Server")
    parser.add_argument(
        "--palace",
        metavar="PATH",
        help="Path to the palace directory (overrides config file and env var)",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        logger.debug("Ignoring unknown args: %s", unknown)
    return args


_args = _parse_args()

if _args.palace:
    os.environ["MEMPALACE_PALACE_PATH"] = os.path.abspath(_args.palace)

_config = MempalaceConfig()

_kg_by_path: dict = {}  # KG instance cache; KnowledgeGraph or KnowledgeGraphAGE
_kg_cache_lock = threading.Lock()
_palace_flag_given: bool = bool(_args.palace)

# MCP server idle auto-exit (#1552).  Stale MCP servers from ended Claude
# Code sessions do not self-terminate, accumulating ChromaDB/HNSW file
# handles on Windows.  When MEMPALACE_MCP_IDLE_HOURS is set (or defaults
# to 8 h), a background daemon thread exits the process once no request
# has been handled for that long.  Set to 0 to disable.
_MCP_IDLE_HOURS_ENV = "MEMPALACE_MCP_IDLE_HOURS"
_MCP_IDLE_HOURS_DEFAULT = 8.0
_last_request_time: float = time.monotonic()


def _mcp_idle_timeout_secs() -> float:
    """Return the configured MCP idle timeout in seconds (0 = disabled)."""
    raw = os.environ.get(_MCP_IDLE_HOURS_ENV, "")
    if raw:
        try:
            hours = float(raw)
            return max(0.0, hours) * 3600
        except ValueError:
            return 0.0
    return _MCP_IDLE_HOURS_DEFAULT * 3600


def _resolve_kg_path() -> str:
    if _palace_flag_given:
        return os.path.join(_config.palace_path, "knowledge_graph.sqlite3")
    return DEFAULT_KG_PATH


def _canonicalize_kg_path(path: str) -> str:
    """Canonicalize a KG cache key so aliases collapse onto one entry.

    ``realpath`` resolves symlinks: two tenants pointing at the same
    SQLite file via different layouts (``/srv/A`` and
    ``/srv/link-to-A``) hit a single cached ``KnowledgeGraph`` rather
    than opening duplicate connections. ``normcase`` normalizes Windows
    drive-letter casing (``C:\\palace`` vs ``c:\\palace``) and
    path-separator style; on POSIX it returns the input unchanged.

    Upstream-introduced helper (PR series 4dc3ccf/51eb279/3ee158e/6f9fe80).
    """
    return os.path.normcase(os.path.realpath(path))


def _get_kg(canonical_path=None) -> KnowledgeGraph:
    """Return the cached KG instance, constructing one lazily.

    Backend selection (fork-only, kg_backend routing per the
    pgvector-age migration plan): ``MEMPALACE_KG_BACKEND=age`` or
    ``config.json {"kg_backend": "age"}`` routes construction to
    ``KnowledgeGraphAGE`` against ``MempalaceConfig.postgres_dsn``.
    Default ``"sqlite"`` keeps the classic file-based ``KnowledgeGraph``.

    Cache key is the canonicalized path for the sqlite case (upstream
    PR series collapsing symlink/case aliases) and the DSN for the AGE
    case — keeps each backing store one cached instance.

    When ``canonical_path`` is ``None`` (default), the sqlite path is
    resolved from module state and canonicalized. Callers like
    :func:`_call_kg` that have already captured a canonical key before
    entering a retry loop should pass it through here so the dict
    insertion uses the same key the caller will later use for eviction.
    Recomputing the key inside this function would let
    ``MEMPALACE_PALACE_PATH`` rotation, a symlink remap, or a mount
    remap between the captured value and this call drift the insert and
    evict keys apart, stranding a closed handle under one key while the
    lookup probes another.
    """
    # Fork-only AGE branch — canonical_path is sqlite-specific so the
    # AGE path ignores it and keys on DSN instead.
    kg_backend = _config.kg_backend
    if kg_backend == "age":
        from .knowledge_graph_age import KnowledgeGraphAGE

        dsn = _config.postgres_dsn
        if not dsn:
            raise RuntimeError(
                "MEMPALACE_KG_BACKEND=age requires MEMPALACE_POSTGRES_DSN "
                "(or postgres_dsn in config.json)"
            )
        cache_key = f"age::{dsn}"
        kg = _kg_by_path.get(cache_key)
        if kg is not None:
            return kg
        with _kg_cache_lock:
            kg = _kg_by_path.get(cache_key)
            if kg is None:
                kg = KnowledgeGraphAGE(dsn=dsn)
                _kg_by_path[cache_key] = kg
        return kg

    # Default sqlite path — canonicalized via upstream helper.
    path = (
        canonical_path if canonical_path is not None else _canonicalize_kg_path(_resolve_kg_path())
    )
    kg = _kg_by_path.get(path)
    if kg is not None:
        return kg
    with _kg_cache_lock:
        kg = _kg_by_path.get(path)
        if kg is None:
            kg = KnowledgeGraph(db_path=path)
            _kg_by_path[path] = kg
    return kg


def _call_kg(op):
    """Run ``op(kg)`` against the cached KG with one-shot retry on close.

    Race we're guarding against: a handler grabs ``kg = _get_kg()`` and is
    about to call ``kg.add_triple(...)`` when ``tool_reconnect`` fires on
    another thread, drains ``_kg_by_path``, and closes the underlying
    sqlite3.Connection. The handler's call then raises
    ``sqlite3.ProgrammingError: Cannot operate on a closed database`` and
    bubbles up as a -32000 to the MCP client even though the user just
    asked for a reconnect.

    Catch that single class of error, evict the stale entry from the
    cache (only if it still points at the closed instance — another
    thread may have already replaced it), and try once more with a fresh
    KG. Beyond one retry give up: a second close means we're losing a
    sustained race we won't win in this loop, and a hung loop is worse
    than a clear failure surface.

    The canonical path is captured once at the top and threaded through
    every ``_get_kg`` call plus the eviction lookup. Doing canonicalize
    only here means an ``OSError`` from ``realpath`` (transient Windows
    junction loss, broken mount) surfaces cleanly before any handler
    runs instead of masking a ``sqlite3.ProgrammingError`` mid-retry.
    Passing the captured key through to ``_get_kg`` also locks the
    insert key to the evict key even if FS or env state mutates between
    attempts, preventing a closed handle from leaking under a stale
    key the lookup no longer matches.
    """
    path = _canonicalize_kg_path(_resolve_kg_path())
    for attempt in range(2):
        kg = _get_kg(path)
        try:
            return op(kg)
        except sqlite3.ProgrammingError:
            if attempt == 0:
                with _kg_cache_lock:
                    if _kg_by_path.get(path) is kg:
                        _kg_by_path.pop(path, None)
                continue
            raise


_client_cache = None
_collection_cache = None
_postgres_backend_cache = None  # set when _config.backend == "postgres"
# Last backend connection failure (set in _get_collection_postgres on except).
# Read by _no_palace() to distinguish "backend unreachable" from "no palace
# configured", so the CLI can render an actionable hint instead of the
# misleading "Run: mempalace init <dir>". Cleared on next successful open.
# Typed as Optional[dict] (not dict | None) for Python 3.9 compatibility —
# pyproject targets py39, and PEP 604 union syntax in runtime annotations
# is 3.10+. Wrapping in Optional avoids needing `from __future__ import
# annotations` at the top of the (large) module.
_last_backend_error: Optional[dict] = None
_palace_db_inode = 0  # inode of chroma.sqlite3 at cache time
_palace_db_mtime = 0.0  # mtime of chroma.sqlite3 at cache time


def _is_transient_index_error(result) -> bool:
    # Chroma can return "Internal error: Error finding id" during the
    # HNSW flush window after a bulk CLI mine — SQLite rows are
    # committed but the binary segment metadata isn't flushed yet.
    # Self-heals once the flush completes (~30-60s). See issue #1315.
    if not isinstance(result, dict):
        return False
    err = result.get("error", "")
    return isinstance(err, str) and ("Error finding id" in err or "Internal error" in err)


def _force_chroma_cache_reset() -> None:
    # Drop both the MCP-local client cache and the shared backend's
    # per-palace cache so the next call rebuilds against the post-flush
    # state. Without clearing the chroma backend's ._clients cache the
    # retry would just hit the same stale handle, since tool_search routes
    # via search_memories -> palace.get_collection -> backend cache.
    global \
        _client_cache, \
        _collection_cache, \
        _palace_db_inode, \
        _palace_db_mtime, \
        _metadata_cache, \
        _metadata_cache_time
    _client_cache = None
    _collection_cache = None
    _palace_db_inode = 0
    _palace_db_mtime = 0.0
    _metadata_cache = None
    _metadata_cache_time = 0
    try:
        from .palace import get_backend

        chroma_backend = get_backend("chroma")
        # Route eviction through close_palace() so chromadb's rust-side
        # SQLite file lock is released deterministically via
        # PersistentClient.close(); bare _clients.pop() leaves the lock
        # held until GC reaps the orphaned client (#262).
        chroma_backend.close_palace(_config.palace_path)
    except Exception:
        pass


# ── Vector-search disabled flag (#1222) ──────────────────────────────────
# Set when ``hnsw_capacity_status`` reports a divergence between sqlite
# and the HNSW segment large enough that chromadb would segfault on
# segment load. While this is set, vector-shaped tools (``search``,
# ``check_duplicate``) route to the sqlite-only BM25 fallback in
# :func:`mempalace.searcher._bm25_only_via_sqlite`. Cleared after a
# successful repair via :func:`tool_reconnect` (which re-runs the probe).
_vector_disabled = False
_vector_disabled_reason = ""
# Optional[dict] (not ``dict | None``) keeps Python 3.9 import-time
# parsing happy — PEP 604 unions in annotations only became unconditional
# at module-eval time in 3.10.
_vector_capacity_status: Optional[dict] = None


def _refresh_vector_disabled_flag() -> None:
    """Re-run the HNSW capacity probe and update the module-level flag.

    Called from :func:`_get_client` whenever the client cache is rebuilt
    (first open or palace replacement). Cheap — pure sqlite + pickle
    read, no chromadb interaction. Never raises: a probe that crashes
    would defeat the point.
    """
    global _vector_disabled, _vector_disabled_reason, _vector_capacity_status
    try:
        info = hnsw_capacity_status(_config.palace_path, _config.collection_name)
    except Exception:
        logger.debug("HNSW capacity probe raised", exc_info=True)
        return
    _vector_capacity_status = info
    if info.get("diverged"):
        if not _vector_disabled:
            logger.warning(
                "HNSW capacity divergence detected (%s) — routing search to "
                "BM25-only sqlite fallback. Run `mempalace repair` to restore "
                "vector search.",
                info.get("message", "unknown"),
            )
        _vector_disabled = True
        _vector_disabled_reason = info.get("message", "")
    else:
        if _vector_disabled:
            logger.info(
                "HNSW capacity within tolerance (%s) — vector search re-enabled",
                info.get("message", ""),
            )
        _vector_disabled = False
        _vector_disabled_reason = ""


# ==================== DAEMON ROUTING ====================
# When ``PALACE_DAEMON_URL`` is set, palace-daemon is the single writer
# for the canonical palace and we route every JSON-RPC envelope to its
# ``/mcp`` proxy instead of opening a local chromadb client. Mirrors the
# gate in :mod:`mempalace.hooks_cli` (the mining side). The 2026-04-24
# HNSW-drift incident traced to dual writers (hook subprocesses +
# Syncthing replication) writing the same palace from two hosts; the
# fix is "daemon is the single writer", and that fix is incomplete as
# long as ``mcp_server`` can still open chromadb directly. Folding the
# routing in here makes the stand-alone bridge at
# ``palace-daemon/clients/mempalace-mcp.py`` optional — anyone running
# ``python -m mempalace.mcp_server`` with the env var set gets the same
# behavior natively.

_DAEMON_FORWARD_TIMEOUT_DEFAULT = 120  # seconds


def _daemon_strict() -> bool:
    """True when daemon routing is on and strict mode is enabled.

    Resolution: ``MempalaceConfig.daemon_strict`` — env var
    ``PALACE_DAEMON_URL`` wins as the source of the URL, with
    ``~/.mempalace/config.json`` key ``"daemon_url"`` as fallback (see
    issue #49). Set ``PALACE_DAEMON_STRICT=0`` or
    ``"daemon_strict": false`` in config to force the local path
    (useful when running the test suite or doing offline development).
    """
    return MempalaceConfig().daemon_strict


def _forward_to_daemon(request: dict) -> dict:
    """POST a JSON-RPC envelope to palace-daemon's ``/mcp`` proxy.

    Returns the parsed response. On any failure (network, non-JSON body),
    returns a JSON-RPC error envelope — strict mode never silently falls
    back to local, since that would re-introduce the split-brain that
    daemon-strict was created to prevent.
    """
    # Resolve via MempalaceConfig so config.json fallback applies — issue #49.
    daemon_url = MempalaceConfig().daemon_url or ""
    req_id = request.get("id")
    try:
        import urllib.request

        headers = {"content-type": "application/json"}
        api_key = os.environ.get("PALACE_API_KEY", "").strip()
        if api_key:
            headers["x-api-key"] = api_key
        req = urllib.request.Request(
            f"{daemon_url}/mcp",
            data=json.dumps(request).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        raw_timeout = os.environ.get("PALACE_MCP_TIMEOUT", str(_DAEMON_FORWARD_TIMEOUT_DEFAULT))
        try:
            timeout = int(raw_timeout)
        except ValueError:
            timeout = _DAEMON_FORWARD_TIMEOUT_DEFAULT
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("palace-daemon /mcp forward failed: %s", e)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": f"Daemon unreachable: {e}"},
        }


# ==================== WRITE-AHEAD LOG ====================
# Every write operation is logged to a JSONL file before execution.
# This provides an audit trail for detecting memory poisoning and
# enables review/rollback of writes from external or untrusted sources.

_WAL_DIR = Path(os.path.expanduser("~/.mempalace/wal"))
_WAL_DIR.mkdir(parents=True, exist_ok=True)
try:
    _WAL_DIR.chmod(0o700)
except (OSError, NotImplementedError):
    pass
_WAL_FILE = _WAL_DIR / "write_log.jsonl"
# Atomically create WAL file with restricted permissions (no TOCTOU race).
# os.open with O_CREAT|O_WRONLY and mode 0o600 creates the file if absent
# or opens it if present, both in a single syscall.
try:
    _fd = os.open(str(_WAL_FILE), os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(_fd)
except (OSError, NotImplementedError):
    pass

# Keys whose values should be redacted in WAL entries to avoid logging sensitive content
_WAL_REDACT_KEYS = frozenset(
    {"content", "content_preview", "document", "entry", "entry_preview", "query", "text"}
)


def _wal_log(operation: str, params: dict, result: dict = None):
    """Append a write operation to the write-ahead log."""
    # Redact sensitive content from params before logging
    safe_params = {}
    for k, v in params.items():
        if k in _WAL_REDACT_KEYS:
            safe_params[k] = f"[REDACTED {len(v)} chars]" if isinstance(v, str) else "[REDACTED]"
        else:
            safe_params[k] = v
    entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "params": safe_params,
        "result": result,
    }
    try:
        fd = os.open(str(_WAL_FILE), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.error(f"WAL write failed: {e}")


def _get_client():
    """Return a ChromaDB PersistentClient, reconnecting if the database changed on disk.

    Detects palace rebuilds (repair/nuke/purge) by checking the inode of
    chroma.sqlite3.  A full rebuild replaces the file, changing the inode.
    Also detects external writes (scripts, CLI) via mtime changes — the
    inode check alone misses in-place modifications that invalidate the
    in-memory HNSW index.

    Note: FAT/exFAT may return 0 for st_ino — the ``current_inode != 0``
    guard skips reconnect detection on those filesystems (safe fallback).
    """
    global \
        _client_cache, \
        _collection_cache, \
        _palace_db_inode, \
        _palace_db_mtime, \
        _metadata_cache, \
        _metadata_cache_time
    db_path = os.path.join(_config.palace_path, "chroma.sqlite3")
    try:
        st = os.stat(db_path)
        current_inode = st.st_ino
        current_mtime = st.st_mtime
    except OSError:
        current_inode = 0
        current_mtime = 0.0

    # If the DB file disappeared (e.g. during rebuild) but we have a cached
    # collection, invalidate so we don't serve stale data.  Without this,
    # both stored and current values are 0 on the first call after deletion,
    # making inode_changed and mtime_changed both False.
    if not os.path.isfile(db_path) and _collection_cache is not None:
        _client_cache = None
        _collection_cache = None
        _palace_db_inode = 0
        _palace_db_mtime = 0.0
        # Fall through to normal reconnect which will handle missing DB

    inode_changed = current_inode != 0 and current_inode != _palace_db_inode
    mtime_changed = current_mtime != 0.0 and abs(current_mtime - _palace_db_mtime) > 0.01

    if _client_cache is None or inode_changed or mtime_changed:
        # Run the HNSW capacity probe BEFORE chromadb opens the segment —
        # if the index is severely undersized, segment load can segfault
        # the whole MCP server (#1222). The probe is pure sqlite +
        # metadata-pickle read; never touches the HNSW binary files.
        _refresh_vector_disabled_flag()
        _client_cache = ChromaBackend.make_client(_config.palace_path)
        _collection_cache = None
        _metadata_cache = None
        _metadata_cache_time = 0
        _palace_db_inode = current_inode
        _palace_db_mtime = current_mtime
    return _client_cache


def _get_collection(create=False):
    """Return the configured backend's collection, caching between calls.

    Backend routing (mirrors ``_get_kg``):

    - ``_config.backend == "postgres"`` → ``_get_collection_postgres``
      (uses ``mempalace.backends.postgres.PostgresBackend``). Requires
      ``MEMPALACE_POSTGRES_DSN`` or equivalent config.
    - ``_config.backend == "chroma"`` (default) → existing ChromaDB
      path below. Preserved unchanged for legacy palaces still on
      ChromaDB — the SIGSEGV recovery logic, HNSW preflight, stale-
      handle retry, etc. all stay relevant for that backend and only
      that backend.

    Both paths return a ``BaseCollection``-conforming object; callers
    interact with it via the same API surface (``count``, ``get``,
    ``query``, ``add``, ``upsert``, ``delete``) and don't need to
    branch.
    """
    if _config.backend == "postgres":
        return _get_collection_postgres(create=create)
    return _get_collection_chroma(create=create)


def _get_collection_postgres(create=False):
    """Postgres-backed branch of ``_get_collection``.

    Caches a single PostgresBackend instance (lazily constructed on first
    call) plus the resolved ``PostgresCollection`` in ``_collection_cache``.
    The chromadb-specific ``_client_cache`` / ``_pin_hnsw_threads`` /
    ``_quarantine_*`` machinery is bypassed entirely — none of it
    applies to a postgres backend.

    Failure semantics differ from the chroma branch's stale-handle
    retry: postgres connection errors are surfaced clearly (no HNSW
    staleness class to heal), so we log + return None rather than retry.

    On failure, the exception type+message is captured in the module-level
    ``_last_backend_error`` slot so ``_no_palace()`` can distinguish a
    truly-uninitialised palace from a reachable-but-down backend (power-
    resilience design 2026-05-21).
    """
    global _collection_cache, _postgres_backend_cache, _metadata_cache, _metadata_cache_time
    global _last_backend_error
    try:
        if _postgres_backend_cache is None:
            from .backends.postgres import PostgresBackend

            _postgres_backend_cache = PostgresBackend(dsn=_config.postgres_dsn)
            # When backend changes underneath us, drop the chroma-shaped cache.
            _collection_cache = None

        if _collection_cache is None or create:
            from .backends.base import PalaceRef

            palace_ref = PalaceRef(
                id=_config.palace_path,
                local_path=_config.palace_path,
            )
            _collection_cache = _postgres_backend_cache.get_collection(
                palace=palace_ref,
                collection_name=_config.collection_name,
                create=create,
            )
            _metadata_cache = None
            _metadata_cache_time = 0

            if hasattr(_collection_cache, "set_kg_writethrough"):
                from .palace import _maybe_attach_writethrough

                _maybe_attach_writethrough(_collection_cache, _config.postgres_dsn)

        _last_backend_error = None
        return _collection_cache
    except Exception as e:
        logger.exception(
            "_get_collection_postgres failed (palace=%s, create=%s, dsn-set=%s)",
            _config.palace_path,
            create,
            bool(_config.postgres_dsn),
        )
        _last_backend_error = {
            "type": type(e).__name__,
            "message": str(e),
            "ts": time.time(),
        }
        # Drop the cached backend so a re-imported PostgresBackend re-resolves
        # the DSN cleanly when postgres comes back. Without this, the failed
        # connection in PostgresBackend's pool keeps being reused.
        _postgres_backend_cache = None
        return None


def _check_local_palace_retired(palace_path: str) -> None:
    """Refuse to open the default local palace when a RETIRED marker exists.

    Recurring confusion source: an agent / CLI invocation that doesn't set
    ``PALACE_DAEMON_URL`` silently falls through to the default chroma
    palace at ``~/.mempalace/palace`` and reports a smaller, stale drawer
    count instead of erroring. When a user has retired their local
    palace in favor of a daemon-routed setup, they can drop a
    ``~/.mempalace/RETIRED`` text file. This check refuses to open ANY
    palace whose path resolves to the default location while that
    marker is present, and surfaces the marker's content as the error
    message.

    Escape hatch: ``MEMPALACE_ALLOW_RETIRED_PALACE=1`` lets forensic
    reads of the archived palace proceed. Useful when explicitly
    passing ``--palace <archived-dir>``.
    """
    if os.environ.get("MEMPALACE_ALLOW_RETIRED_PALACE", "").strip():
        return
    palace_root = os.path.expanduser("~/.mempalace")
    marker_path = os.path.join(palace_root, "RETIRED")
    if not os.path.exists(marker_path):
        return
    default_palace = os.path.join(palace_root, "palace")
    if os.path.abspath(palace_path) != os.path.abspath(default_palace):
        return  # explicit non-default path; let the caller through
    try:
        with open(marker_path) as f:
            note = f.read().strip()
    except OSError:
        note = ""
    raise RuntimeError(
        f"local palace at {default_palace} is retired (see "
        f"~/.mempalace/RETIRED).\n\n{note}\n\n"
        "Set MEMPALACE_ALLOW_RETIRED_PALACE=1 to override (forensic only)."
    )


def _get_collection_chroma(create=False):
    """ChromaDB-backed branch of ``_get_collection``.

    Preserved unchanged from the pre-routing implementation. Retry on
    failure + cache reset is the right behavior for ChromaDB's stale-
    handle and HNSW-corruption failure classes — see the original
    inline comments below.
    """
    global _client_cache, _collection_cache, _metadata_cache, _metadata_cache_time
    global _last_backend_error
    # Honor the ~/.mempalace/RETIRED marker — refuse to silently open a
    # default-path palace when the user has retired it. Surfaces a clear
    # error via _no_palace() instead of returning a stale drawer count.
    try:
        _check_local_palace_retired(_config.palace_path)
    except RuntimeError as e:
        _last_backend_error = {
            "type": "LocalPalaceRetired",
            "message": str(e),
            "ts": time.time(),
        }
        return None
    for attempt in range(2):
        try:
            client = _get_client()
            # ChromaDB 1.x persists the EF *identity* (its ``name()``) with the
            # collection but not the EF *instance/configuration*. So a reader or
            # writer that omits ``embedding_function=`` silently gets chromadb's
            # built-in ``DefaultEmbeddingFunction`` — its ``name()`` matches the
            # one we spoof in ``mempalace.embedding`` (both report ``"default"``,
            # the identity check passes), but the *provider list* is chromadb's
            # default rather than the user's resolved device. On bleeding-edge
            # interpreters (#1299: python 3.14 + chromadb 1.5.x on Apple Silicon)
            # that default provider selection can SIGSEGV the host process on
            # first ``col.add()``. The miner / Stop hook ingest path avoids this
            # because it routes through ``ChromaBackend.get_collection``, which
            # resolves the EF via ``ChromaBackend._resolve_embedding_function``;
            # the MCP server bypassed that abstraction. Resolve the EF inside the
            # branches that actually open a collection so warm-cache reads stay
            # zero-cost. Reuse the backend helper so the two call sites can't
            # drift on logging or fallback semantics.
            if create:
                ef = ChromaBackend._resolve_embedding_function()
                ef_kwargs = {"embedding_function": ef} if ef is not None else {}
                # hnsw:num_threads=1 disables ChromaDB's multi-threaded ParallelFor
                # HNSW insert path, which has a race in repairConnectionsForUpdate /
                # addPoint (see issues #974, #965). Set via metadata on fresh
                # collections and re-applied via _pin_hnsw_threads() for legacy
                # palaces whose collections were created before this fix (the
                # runtime config does not persist cross-process in chromadb 1.5.x,
                # so the retrofit runs every time _get_collection opens a cache).
                #
                # ChromaDB 1.5.x's Rust binding SIGSEGVs when get_or_create_collection
                # is called with metadata that differs from what's stored. The split
                # below skips the metadata-comparison codepath for existing
                # collections, mirroring the backend-layer fix from #1262.
                try:
                    raw = client.get_collection(_config.collection_name, **ef_kwargs)
                except _ChromaNotFoundError:
                    raw = client.create_collection(
                        _config.collection_name,
                        metadata={
                            "hnsw:space": "cosine",
                            "hnsw:num_threads": 1,
                            **_HNSW_BLOAT_GUARD,
                        },
                        **ef_kwargs,
                    )
                _pin_hnsw_threads(raw)
                _collection_cache = ChromaCollection(raw, palace_path=_config.palace_path)
                _metadata_cache = None
                _metadata_cache_time = 0
            elif _collection_cache is None:
                ef = ChromaBackend._resolve_embedding_function()
                ef_kwargs = {"embedding_function": ef} if ef is not None else {}
                raw = client.get_collection(_config.collection_name, **ef_kwargs)
                _pin_hnsw_threads(raw)
                _collection_cache = ChromaCollection(raw, palace_path=_config.palace_path)
                _metadata_cache = None
                _metadata_cache_time = 0
            return _collection_cache
        except Exception:
            logger.exception(
                "_get_collection attempt %d/2 failed (palace=%s, create=%s)",
                attempt + 1,
                _config.palace_path,
                create,
            )
            if attempt == 0:
                # Reset all caches so the next attempt forces _get_client()
                # to rebuild the chromadb client from scratch — that path
                # re-runs quarantine_stale_hnsw (#1322) and reopens the
                # collection cleanly, healing the common stale-handle case.
                _client_cache = None
                _collection_cache = None
                _metadata_cache = None
                _metadata_cache_time = 0
    return None


def _no_palace():
    """Build a "no palace" response, distinguishing two failure modes.

    * **Backend unreachable** — the configured postgres DSN refused the
      connection (most commonly because the ``mempalace-db`` container is
      stopped). The fix is to start the backend, not to re-init the
      palace. This is the failure mode that bit JP for 3 days during the
      2026-05-17 power event.
    * **No palace configured** — the daemon is wired up and the backend
      responded, but the collection is empty / palace was never built.
      The fix is to ``mempalace init`` / ``mempalace mine``.

    The module-level ``_last_backend_error`` slot is set by
    ``_get_collection_postgres`` on connection failure; we inspect it
    here so the response carries the actual cause.
    """
    err = _last_backend_error
    if err and err.get("type") == "LocalPalaceRetired":
        # User has explicitly retired the local palace (RETIRED marker
        # under ~/.mempalace/). Tell them exactly what to do — usually
        # set PALACE_DAEMON_URL — rather than the legacy "init" hint.
        return {
            "error": "palace.local_retired",
            "message": err.get("message", "local palace retired"),
            "hint": "Set PALACE_DAEMON_URL to use the daemon, or MEMPALACE_ALLOW_RETIRED_PALACE=1 for forensic local reads.",
        }
    if err and err.get("type") == "OperationalError":
        return {
            "error": "palace.backend_unreachable",
            "message": f"daemon up, but its postgres backend is unreachable: {err.get('message', 'unknown')}",
            "hint": "Check: docker ps mempalace-db (and `cd /opt/mediaserver && docker compose start mempalace-db`)",
        }
    if err:
        # Other backend errors — surface the type+message so the user
        # sees something actionable instead of the misleading init hint.
        return {
            "error": "palace.backend_error",
            "message": f"{err.get('type', 'Error')}: {err.get('message', 'unknown')}",
            "hint": "Check daemon logs (sudo journalctl -u palace-daemon -n 50)",
        }
    return {
        "error": "No palace found",
        "hint": "Run: mempalace init <dir> && mempalace mine <dir>",
    }


# ==================== HELPERS ====================


def _safe_meta(meta):
    """Coerce a Chroma metadata value to a dict.

    ChromaDB's ``col.get()`` / ``col.query()`` can return ``None`` for the
    metadata cell of a partially-flushed row (or any row written without
    metadata in older formats). Indexing the result then yields ``None``,
    and downstream ``.get(...)`` calls raise::

        AttributeError: 'NoneType' object has no attribute 'get'

    This bug bricked the embeddings_queue cleanup path in issue #1426 —
    the handler crashed before reaching the ``DELETE FROM embeddings_queue``
    step, so the queue grew without bound while writes kept appearing
    successful.

    Centralizing the coercion through this helper makes the contract
    explicit and keeps the fix self-documenting at every call site:
    *metadata is always a dict by the time it leaves the boundary*.
    """
    return meta if isinstance(meta, dict) else {}


def _fetch_all_metadata(col, where=None):
    """Paginate col.get() to avoid the 10K silent truncation limit."""
    total = col.count()
    all_meta = []
    offset = 0
    while offset < total:
        kwargs = {"include": ["metadatas"], "limit": 1000, "offset": offset}
        if where:
            kwargs["where"] = where
        batch = col.get(**kwargs)
        if not batch["metadatas"]:
            break
        all_meta.extend(batch["metadatas"])
        offset += len(batch["metadatas"])
    return all_meta


_metadata_cache = None
_metadata_cache_time = 0
_METADATA_CACHE_TTL = 5.0  # seconds
_MAX_RESULTS = 100  # upper bound for search/list limit params

# IDF table cache for auto-tag extraction (#201). One entry per (wing, room).
# Built lazily on first auto-tagged write; refreshed when the TTL expires so a
# write-heavy session eventually picks up new vocabulary.
_idf_cache = None  # mempalace.tag_extraction.IdfCache, lazy init
_IDF_CORPUS_CAP = 2000  # snapshot at most this many docs when (re)building


def _get_idf_table(col, wing: str, room: str) -> dict:
    """Return the IDF weights for ``(wing, room)`` via the process-local cache.

    The corpus builder caps the snapshot at ``_IDF_CORPUS_CAP`` to keep
    rebuild latency bounded on large palaces — the IDF is a ranking aid,
    not a corpus inventory, and the top tokens stabilise long before
    the full corpus is read.
    """
    global _idf_cache
    if _idf_cache is None:
        from .tag_extraction import IdfCache

        _idf_cache = IdfCache()

    def _corpus():
        try:
            batch = col.get(
                where={"$and": [{"wing": wing}, {"room": room}]},
                limit=_IDF_CORPUS_CAP,
                include=["documents"],
            )
        except Exception:
            logger.debug("IDF corpus fetch failed", exc_info=True)
            return []
        docs = (
            batch.get("documents") if isinstance(batch, dict) else getattr(batch, "documents", None)
        )
        return [d for d in (docs or []) if isinstance(d, str)]

    return _idf_cache.get(wing, room, _corpus)


def _get_cached_metadata(col, where=None):
    """Return cached metadata if fresh, else fetch and cache."""
    global _metadata_cache, _metadata_cache_time
    now = time.time()
    if (
        where is None
        and _metadata_cache is not None
        and (now - _metadata_cache_time) < _METADATA_CACHE_TTL
    ):
        return _metadata_cache
    result = _fetch_all_metadata(col, where=where)
    if where is None:
        _metadata_cache = result
        _metadata_cache_time = now
    return result


def _sanitize_optional_name(value: str = None, field_name: str = "name") -> str:
    """Validate optional wing/room-style filters."""
    if value is None or not value.strip():
        return None
    return sanitize_name(value, field_name)


def _resolve_room_alias(value: str = None) -> str:
    """Sanitize and resolve room aliases via config."""
    sanitized = _sanitize_optional_name(value, "room")
    if sanitized is None:
        return None
    return _config.resolve_room(sanitized)


# ==================== READ TOOLS ====================


def _tool_status_via_sqlite() -> dict:
    """Pure-sqlite status reader for the #1222 fallback path.

    When the HNSW capacity probe detects divergence, opening the chromadb
    persistent client can segfault. This reader pulls the same wing/room
    breakdown directly from ``embedding_metadata`` so the operator still
    gets a working status response — and crucially the
    ``vector_disabled`` flag — without us touching the vector segment.
    """
    import sqlite3 as _sqlite3

    db_path = os.path.join(_config.palace_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return _no_palace()
    collection_name = _config.collection_name

    wings: dict = {}
    rooms: dict = {}
    total = 0
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM embeddings e
                JOIN segments s ON e.segment_id = s.id
                JOIN collections c ON s.collection = c.id
                WHERE c.name = ?
                """,
                (collection_name,),
            ).fetchone()
            total = int(row[0]) if row and row[0] is not None else 0
            for key, target in (("wing", wings), ("room", rooms)):
                for value, count in conn.execute(
                    """
                    SELECT em.string_value, COUNT(*)
                    FROM embedding_metadata em
                    JOIN embeddings e ON em.id = e.id
                    JOIN segments s ON e.segment_id = s.id
                    JOIN collections c ON s.collection = c.id
                    WHERE c.name = ?
                      AND em.key = ?
                      AND em.string_value IS NOT NULL
                    GROUP BY em.string_value
                    """,
                    (collection_name, key),
                ):
                    target[value] = count
        finally:
            conn.close()
    except _sqlite3.Error:
        logger.exception("tool_status sqlite fallback read failed")

    result = {
        "total_drawers": total,
        "wings": wings,
        "rooms": rooms,
        "protocol": PALACE_PROTOCOL,
        "aaak_dialect": AAAK_SPEC,
        "vector_disabled": True,
        "vector_disabled_reason": _vector_disabled_reason,
    }
    if _vector_capacity_status:
        result["hnsw_capacity"] = {
            "sqlite_count": _vector_capacity_status.get("sqlite_count"),
            "hnsw_count": _vector_capacity_status.get("hnsw_count"),
            "divergence": _vector_capacity_status.get("divergence"),
        }
    return result


def _tool_status_via_postgres() -> Optional[dict]:
    """Postgres fast-path for tool_status: SQL group-by, sub-100ms on 370k rows.

    Mirrors palace-daemon's ``/status/fast`` (main.py ``status_fast``):
    three queries against ``mempalace_drawers`` wrapped in a 3s
    ``statement_timeout`` so a stuck query can't replace one slow path
    with another. Returns ``None`` when the postgres backend isn't
    actually reachable (no DSN configured, driver missing, connection
    refused) so the caller can fall back to the legacy metadata sweep
    rather than surfacing a hard failure. Issue #267.
    """
    dsn = _config.postgres_dsn
    if not dsn:
        return None
    table = _config.collection_name or "mempalace_drawers"
    try:
        import psycopg
        from psycopg import sql as _sql
    except ImportError:
        return None

    table_id = _sql.Identifier(table)
    wings: dict = {}
    rooms: dict = {}
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3s'")
                cur.execute(_sql.SQL("SELECT count(*) FROM {}").format(table_id))
                total = cur.fetchone()[0]
                cur.execute(
                    _sql.SQL("SELECT wing, count(*) FROM {} GROUP BY wing").format(table_id)
                )
                for w, c in cur.fetchall():
                    wings[w if w is not None else "unknown"] = c
                cur.execute(
                    _sql.SQL(
                        "SELECT room, count(*) FROM {} WHERE room IS NOT NULL GROUP BY room"
                    ).format(table_id)
                )
                for r, c in cur.fetchall():
                    rooms[r] = c
    except Exception:
        logger.exception("tool_status postgres fast-path failed")
        return None
    return {
        "total_drawers": int(total or 0),
        "wings": wings,
        "rooms": rooms,
        "protocol": PALACE_PROTOCOL,
        "aaak_dialect": AAAK_SPEC,
    }


def tool_status():
    # Run the safe sqlite/pickle probe before we touch chromadb. In the
    # #1222 failure mode, opening the persistent client to call .count()
    # can segfault — short-circuit to a pure-sqlite path when divergence
    # is detected so status stays reachable.
    db_exists = os.path.isfile(os.path.join(_config.palace_path, "chroma.sqlite3"))
    _refresh_vector_disabled_flag()

    if _vector_disabled:
        return _tool_status_via_sqlite()

    # Postgres backend: pull wing/room counts from indexed SQL rather than
    # sweeping every drawer's metadata in Python (~29s → <100ms on a 370k-
    # row palace; #267).
    if _config.backend == "postgres":
        fast = _tool_status_via_postgres()
        if fast is not None:
            return fast
        # Fast path unavailable (no DSN, driver missing, query failed) —
        # fall through to the legacy metadata sweep so the caller still
        # gets a response.

    # Use create=True only when a palace DB already exists on disk -- this
    # bootstraps the ChromaDB collection on a valid-but-empty palace without
    # accidentally creating a palace in a non-existent directory (#830).
    col = _get_collection(create=db_exists)
    if not col:
        return _no_palace()
    count = col.count()
    wings = {}
    rooms = {}
    result = {
        "total_drawers": count,
        "wings": wings,
        "rooms": rooms,
        "protocol": PALACE_PROTOCOL,
        "aaak_dialect": AAAK_SPEC,
    }
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            wings[w] = wings.get(w, 0) + 1
            rooms[r] = rooms.get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_status metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


# ── AAAK Dialect Spec ─────────────────────────────────────────────────────────
# Included in status response so the AI learns it on first wake-up call.
# Also available via mempalace_get_aaak_spec tool.

PALACE_PROTOCOL = """IMPORTANT — MemPalace Memory Protocol:
1. ON WAKE-UP: Call mempalace_status to load palace overview + AAAK spec.
2. BEFORE RESPONDING about any person, project, or past event: call mempalace_kg_query or mempalace_search FIRST. Never guess — verify.
3. IF UNSURE about a fact (name, gender, age, relationship): say "let me check" and query the palace. Wrong is worse than slow.
4. AFTER EACH SESSION: call mempalace_diary_write to record what happened, what you learned, what matters.
5. WHEN FACTS CHANGE: call mempalace_kg_invalidate on the old fact, mempalace_kg_add for the new one.

This protocol ensures the AI KNOWS before it speaks. Storage is not memory — but storage + this protocol = memory."""

AAAK_SPEC = """AAAK is a compressed memory dialect that MemPalace uses for efficient storage.
It is designed to be readable by both humans and LLMs without decoding.

FORMAT:
  ENTITIES: 3-letter uppercase codes. ALC=Alice, JOR=Jordan, RIL=Riley, MAX=Max, BEN=Ben.
  EMOTIONS: *action markers* before/during text. *warm*=joy, *fierce*=determined, *raw*=vulnerable, *bloom*=tenderness.
  STRUCTURE: Pipe-separated fields. FAM: family | PROJ: projects | ⚠: warnings/reminders.
  DATES: ISO format (2026-03-31). COUNTS: Nx = N mentions (e.g., 570x).
  IMPORTANCE: ★ to ★★★★★ (1-5 scale).
  HALLS: hall_facts, hall_events, hall_discoveries, hall_preferences, hall_advice.
  WINGS: wing_user, wing_agent, wing_team, wing_code, wing_myproject, wing_hardware, wing_ue5, wing_ai_research.
  ROOMS: Hyphenated slugs representing named ideas (e.g., chromadb-setup, gpu-pricing).

EXAMPLE:
  FAM: ALC→♡JOR | 2D(kids): RIL(18,sports) MAX(11,chess+swimming) | BEN(contributor)

Read AAAK naturally — expand codes mentally, treat *markers* as emotional context.
When WRITING AAAK: use entity codes, mark emotions, keep structure tight."""


def tool_list_wings():
    col = _get_collection()
    if not col:
        return _no_palace()
    wings = {}
    result = {"wings": wings}
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            wings[w] = wings.get(w, 0) + 1
    except Exception as e:
        logger.exception("tool_list_wings metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_list_rooms(wing: str = None):
    try:
        wing = _sanitize_optional_name(wing, "wing")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    if not col:
        return _no_palace()
    rooms = {}
    result = {"wing": wing or "all", "rooms": rooms}
    try:
        where = {"wing": wing} if wing else None
        all_meta = _fetch_all_metadata(col, where=where)
        for m in all_meta:
            m = m or {}
            r = m.get("room", "unknown")
            rooms[r] = rooms.get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_list_rooms metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_get_taxonomy():
    col = _get_collection()
    if not col:
        return _no_palace()
    taxonomy = {}
    result = {"taxonomy": taxonomy}
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            if w not in taxonomy:
                taxonomy[w] = {}
            taxonomy[w][r] = taxonomy[w].get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_get_taxonomy metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_search(
    query: str,
    limit: int = 5,
    wing: str = None,
    room: str = None,
    tags: list = None,
    max_distance: float = 1.5,
    min_similarity: float = None,
    context: str = None,
    candidate_strategy: str = "hybrid",
    fusion_mode: str = "convex",
    include_trace: bool = False,
):
    limit = max(1, min(limit, _MAX_RESULTS))
    try:
        wing = _sanitize_optional_name(wing, "wing")
        room = _resolve_room_alias(room)
    except ValueError as e:
        return {"error": str(e)}
    from .tags import normalise_tags

    normalised_tags = normalise_tags(tags)
    # Backwards compat: convert old similarity scale (higher=stricter) to
    # distance scale (lower=stricter). Similarity 0.8 → distance 0.2.
    dist = (1.0 - min_similarity) if min_similarity is not None else max_distance
    # Mitigate system prompt contamination (Issue #333)
    sanitized = sanitize_query(query)
    # Ensure the vector-disabled probe has been run via the safe
    # sqlite/pickle path before we touch chromadb. Calling _get_client()
    # here would defeat the fallback — it constructs a PersistentClient
    # which can segfault on segment load in the #1222 failure mode.
    _refresh_vector_disabled_flag()
    result = search_memories(
        sanitized["clean_query"],
        palace_path=_config.palace_path,
        wing=wing,
        room=room,
        tags=normalised_tags or None,
        n_results=limit,
        max_distance=dist,
        vector_disabled=_vector_disabled,
        collection_name=_config.collection_name,
        candidate_strategy=candidate_strategy,
        fusion_mode=fusion_mode,
    )
    # Attach per-source trace if requested (Phase 4 hybrid retrieval).
    # Surfaces matched_via counts so callers can debug which channel
    # contributed which drawer.
    if include_trace and isinstance(result, dict) and "results" in result:
        from collections import Counter as _Counter

        sources = _Counter(r.get("matched_via", "vector") for r in result["results"])
        result["trace"] = {
            "candidate_strategy": candidate_strategy,
            "fusion_mode": fusion_mode,
            "sources": dict(sources),
        }
    if _is_transient_index_error(result):
        # Post-bulk-write HNSW flush window (#1315): drop caches, give
        # the segment a moment to settle, retry once. Caller never sees
        # the transient unless the second attempt also fails.
        _force_chroma_cache_reset()
        time.sleep(2)
        _refresh_vector_disabled_flag()
        result = search_memories(
            sanitized["clean_query"],
            palace_path=_config.palace_path,
            wing=wing,
            room=room,
            tags=normalised_tags or None,
            n_results=limit,
            max_distance=dist,
            vector_disabled=_vector_disabled,
        )
        if not _is_transient_index_error(result):
            result["index_recovered"] = True
    if _vector_disabled:
        result["vector_disabled"] = True
        result["vector_disabled_reason"] = _vector_disabled_reason
    # Attach sanitizer metadata for transparency
    if sanitized["was_sanitized"]:
        result["query_sanitized"] = True
        result["sanitizer"] = {
            "method": sanitized["method"],
            "original_length": sanitized["original_length"],
            "clean_length": sanitized["clean_length"],
            "clean_query": sanitized["clean_query"],
        }
    if context:
        result["context_received"] = True
    return result


def tool_check_duplicate(content: str, threshold: float = 0.9):
    _refresh_vector_disabled_flag()
    if _vector_disabled:
        # Without a usable HNSW we can't compute cosine similarity for
        # near-duplicate detection. Report the limitation rather than
        # silently returning "not a duplicate" — false negatives here
        # would let the AI re-file content the palace already holds.
        return {
            "is_duplicate": False,
            "matches": [],
            "vector_disabled": True,
            "vector_disabled_reason": _vector_disabled_reason,
            "hint": (
                "duplicate detection requires vector search; run `mempalace repair` to restore"
            ),
        }
    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        content = strip_lone_surrogates(content)
        results = col.query(
            query_texts=[content],
            n_results=5,
            include=["metadatas", "documents", "distances"],
        )
        duplicates = []
        if results["ids"] and results["ids"][0]:
            for i, drawer_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                similarity = round(max(0.0, 1 - dist), 3)
                if similarity >= threshold:
                    # Chroma 1.5.x can return None for partially-flushed rows;
                    # coerce to empty sentinels so downstream .get() is safe.
                    meta = _safe_meta(results["metadatas"][0][i])
                    doc = results["documents"][0][i] or ""
                    duplicates.append(
                        {
                            "id": drawer_id,
                            "wing": meta.get("wing", "?"),
                            "room": meta.get("room", "?"),
                            "similarity": similarity,
                            "content": doc[:200] + "..." if len(doc) > 200 else doc,
                        }
                    )
        return {
            "is_duplicate": len(duplicates) > 0,
            "matches": duplicates,
        }
    except Exception:
        logger.exception("check_duplicate failed")
        return {"error": "Duplicate check failed"}


def tool_get_aaak_spec():
    """Return the AAAK dialect specification."""
    return {"aaak_spec": AAAK_SPEC}


def tool_traverse_graph(start_room: str, max_hops: int = 2):
    """Walk the palace graph from a room. Find connected ideas across wings."""
    max_hops = max(1, min(max_hops, 10))
    col = _get_collection()
    if not col:
        return _no_palace()
    return traverse(start_room, col=col, max_hops=max_hops)


def tool_walk_palace(
    start_wing: str = None,
    start_room: str = None,
    start_entity: str = None,
    depth: int = 2,
    limit: int = 50,
):
    """Agent-facing palace walk via AGE Cypher traversal.

    Phase 6 of the AGE-integration goal. Exposes the "agent walks into the
    palace" metaphor as a single tool: pass a starting node (wing OR room
    OR entity) and a depth, get back the navigable subgraph it touches.

    Three traversal modes by starting node:

    - **start_wing="memorypalace"**: enumerate rooms in this wing
      (depth=1), plus drawers in those rooms (depth=2), plus mentioned
      entities (depth=3). The "walking into a wing" pattern.
    - **start_room="problems"**: enumerate drawers in this room across
      all wings (depth=1), plus their mentioned entities (depth=2). The
      "walking into a specific room" pattern.
    - **start_entity="pgvector"**: enumerate drawers mentioning this
      entity (depth=1), plus the rooms+wings containing them (depth=2).
      The "find where in the palace X is discussed" pattern (inverse
      walk — entity → drawer → room → wing).

    Exactly one of (start_wing, start_room, start_entity) must be given.

    Returns a structured walk result with:
      - ``start``: the input anchor
      - ``walk``: list of {wing, room, drawer, entity} rows, one per
        leaf reached
      - ``stats``: {wings_touched, rooms_touched, drawers_touched,
        entities_touched}

    Requires MEMPALACE_BACKEND=postgres and the AGE graph populated via
    kg_writethrough or backfill_age.
    """
    # Validate inputs
    anchors_set = sum(bool(x) for x in (start_wing, start_room, start_entity))
    if anchors_set != 1:
        return {
            "error": "exactly one of start_wing, start_room, start_entity must be provided",
            "got": {
                "start_wing": start_wing,
                "start_room": start_room,
                "start_entity": start_entity,
            },
        }
    depth = max(1, min(depth, 5))
    limit = max(1, min(limit, 500))

    # Require postgres + AGE
    if _config.backend != "postgres":
        return {"error": "tool_walk_palace requires MEMPALACE_BACKEND=postgres"}
    if _config.kg_backend != "age":
        return {"error": "tool_walk_palace requires MEMPALACE_KG_BACKEND=age"}
    dsn = _config.postgres_dsn
    if not dsn:
        return {"error": "MEMPALACE_POSTGRES_DSN not set"}

    try:
        # _get_kg() returns the cached KnowledgeGraphAGE instance when
        # kg_backend == "age". Direct ``KnowledgeGraphAGE(dsn)`` would
        # open a fresh postgres connection on every call — across
        # repeated tool_walk_palace invocations that's both expensive
        # and unnecessary. (Gemini PR #101 review.)
        kg = _get_kg()
    except Exception as e:
        return {"error": f"could not connect to AGE: {e}"}

    walk_rows: list[dict] = []
    # Map result-column-name → stats-key-name so 'entity' goes to 'entities_touched'
    # (not 'entitys_touched' if we naively pluralized).
    col_to_stat = {
        "wing": "wings_touched",
        "room": "rooms_touched",
        "drawer": "drawers_touched",
        "entity": "entities_touched",
    }
    stats = {v: set() for v in col_to_stat.values()}

    try:
        if start_wing:
            # Wing → Room → Drawer → MENTIONS → Entity
            cypher_by_depth = {
                1: """
                    MATCH (w:Wing {name: $anchor})-[:CONTAINS]->(r:Room)
                    RETURN w.name AS wing, r.name AS room LIMIT $limit
                """,
                2: """
                    MATCH (w:Wing {name: $anchor})-[:CONTAINS]->(r:Room)-[:CONTAINS]->(d:Drawer)
                    RETURN w.name AS wing, r.name AS room, d.id AS drawer LIMIT $limit
                """,
                3: """
                    MATCH (w:Wing {name: $anchor})-[:CONTAINS]->(r:Room)-[:CONTAINS]->(d:Drawer)
                    OPTIONAL MATCH (d)-[m:MENTIONS]->(e:Entity)
                    RETURN w.name AS wing, r.name AS room, d.id AS drawer, e.name AS entity LIMIT $limit
                """,
            }
            anchor = start_wing
        elif start_room:
            # Room (across wings) → Drawer → MENTIONS → Entity
            cypher_by_depth = {
                1: """
                    MATCH (w:Wing)-[:CONTAINS]->(r:Room {name: $anchor})-[:CONTAINS]->(d:Drawer)
                    RETURN w.name AS wing, r.name AS room, d.id AS drawer LIMIT $limit
                """,
                2: """
                    MATCH (w:Wing)-[:CONTAINS]->(r:Room {name: $anchor})-[:CONTAINS]->(d:Drawer)
                    OPTIONAL MATCH (d)-[m:MENTIONS]->(e:Entity)
                    RETURN w.name AS wing, r.name AS room, d.id AS drawer, e.name AS entity LIMIT $limit
                """,
            }
            anchor = start_room
        else:  # start_entity
            # Inverse walk: Entity → Drawer → Room → Wing
            cypher_by_depth = {
                1: """
                    MATCH (e:Entity {name: $anchor})<-[m:MENTIONS]-(d:Drawer)
                    RETURN d.id AS drawer, e.name AS entity LIMIT $limit
                """,
                2: """
                    MATCH (e:Entity {name: $anchor})<-[m:MENTIONS]-(d:Drawer)
                    OPTIONAL MATCH (w:Wing)-[:CONTAINS]->(r:Room)-[:CONTAINS]->(d)
                    RETURN w.name AS wing, r.name AS room, d.id AS drawer, e.name AS entity LIMIT $limit
                """,
            }
            anchor = start_entity

        effective_depth = min(depth, max(cypher_by_depth.keys()))
        cypher = cypher_by_depth[effective_depth]

        rows = kg._run_cypher(cypher, {"anchor": anchor, "limit": limit}, fetch=True)
        for r in rows:
            entry: dict = {}
            # Map columns based on what the chosen cypher returned.
            # Use aliases from the RETURN clause: wing, room, drawer, entity.
            # The order matches: wing first, then room, then drawer, then entity
            # (skipping any not present in the result).
            for i, key in enumerate(("wing", "room", "drawer", "entity")):
                if i >= len(r):
                    break
                v = kg._unwrap_agtype(r[i])
                if v is not None:
                    entry[key] = v
                    stat_key = col_to_stat.get(key)
                    if stat_key:
                        stats[stat_key].add(v)
            walk_rows.append(entry)
    finally:
        kg.close()

    return {
        "start": {"wing": start_wing, "room": start_room, "entity": start_entity},
        "depth": depth,
        "walk": walk_rows,
        "stats": {k: len(v) for k, v in stats.items()},
    }


def tool_find_tunnels(wing_a: str = None, wing_b: str = None):
    """Find rooms that bridge two wings — the hallways connecting domains."""
    try:
        wing_a = _sanitize_optional_name(wing_a, "wing_a")
        wing_b = _sanitize_optional_name(wing_b, "wing_b")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    if not col:
        return _no_palace()
    return find_tunnels(wing_a, wing_b, col=col)


def tool_graph_stats():
    """Palace graph overview: nodes, tunnels, edges, connectivity."""
    col = _get_collection()
    if not col:
        return _no_palace()
    return graph_stats(col=col)


def tool_create_tunnel(
    source_wing: str,
    source_room: str,
    target_wing: str,
    target_room: str,
    label: str = "",
    source_drawer_id: str = None,
    target_drawer_id: str = None,
):
    """Create an explicit cross-wing tunnel between two palace locations.

    Use when you notice content in one project relates to another project.
    Example: an API design discussion in project_api connects to the
    database schema in project_database.
    """
    # sanitize_name and create_tunnel both raise ValueError for invalid or
    # missing endpoints (empty/non-string names, and create_tunnel's
    # room-existence checks). Catch both so the real reason is surfaced
    # instead of escaping and being wrapped as the opaque "Internal tool
    # error" (#1473), mirroring sibling tools.
    try:
        source_wing = sanitize_name(source_wing, "source_wing")
        source_room = sanitize_name(source_room, "source_room")
        target_wing = sanitize_name(target_wing, "target_wing")
        target_room = sanitize_name(target_room, "target_room")
        return create_tunnel(
            source_wing,
            source_room,
            target_wing,
            target_room,
            label=label,
            source_drawer_id=source_drawer_id,
            target_drawer_id=target_drawer_id,
        )
    except ValueError as e:
        return {"error": str(e)}


def tool_list_tunnels(wing: str = None, include_passive: bool = False):
    """List cross-wing tunnels, optionally filtered by wing.

    Default returns only explicit (agent-created) tunnels stored at
    ``~/.mempalace/tunnels.json``. Pass ``include_passive=True`` to also
    include passive tunnels (rooms appearing in 2+ wings, computed from
    ``graph_stats``). Each result is tagged with ``kind: 'explicit'|'passive'``.
    See techempower-org/mempalace#75 for the asymmetry that motivated the
    merged-result form.
    """
    try:
        wing = _sanitize_optional_name(wing, "wing")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection() if include_passive else None
    return list_tunnels(wing, include_passive=include_passive, col=col)


def tool_delete_tunnel(tunnel_id: str):
    """Delete an explicit tunnel by its ID."""
    if not tunnel_id or not isinstance(tunnel_id, str):
        return {"error": "tunnel_id is required"}
    return delete_tunnel(tunnel_id)


def tool_follow_tunnels(wing: str, room: str):
    """Follow explicit tunnels from a room to see connected drawers in other wings."""
    try:
        wing = sanitize_name(wing, "wing")
        room = sanitize_name(_config.resolve_room(room), "room")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    return follow_tunnels(wing, room, col=col)


# ==================== WRITE TOOLS ====================


def tool_add_drawer(
    wing: str,
    room: str,
    content: str,
    source_file: str = None,
    added_by: str = "mcp",
    tags: list = None,
):
    """File verbatim content into a wing/room. Checks for duplicates first.

    Content above ``chunk_size`` is split into bounded per-chunk drawers
    via a single batched upsert. Each chunk carries ``parent_drawer_id``
    linkage and ``chunk_index`` metadata so search can rejoin them. The
    returned ``drawer_id`` is the LOGICAL group handle on the chunked
    path; physical drawer ids are in ``chunk_ids`` (#1539). To delete
    or fetch the underlying drawers, iterate ``chunk_ids`` or query by
    ``parent_drawer_id`` — ``tool_get_drawer(drawer_id)`` and
    ``tool_delete_drawer(drawer_id)`` report "not found" on the chunked
    path because no row is stored under the logical group id.

    ``tags`` is an optional list of cross-cutting labels (multi-label
    additive layer over the strict wing/room hierarchy). See
    ``mempalace.tags`` for normalisation rules.
    """
    global _metadata_cache
    # Observation-grade write sanitizer (#40) runs first: strips control
    # chars, normalizes whitespace, caps at 1 MiB. The strict layer below
    # then enforces the existing wing/room shape and the 100K hard limit.
    wing_pass = sanitize_write_name(wing, "wing")
    if wing_pass["error"]:
        return {"success": False, "error": wing_pass["error"]}
    room = _config.resolve_room(room) if room else room
    room_pass = sanitize_write_name(room, "room")
    if room_pass["error"]:
        return {"success": False, "error": room_pass["error"]}
    content_pass = sanitize_write_content(content)
    if content_pass["error"]:
        return {"success": False, "error": content_pass["error"]}
    sanitize_flags = sorted(
        set(wing_pass["flags"]) | set(room_pass["flags"]) | set(content_pass["flags"])
    )
    try:
        wing = sanitize_name(wing_pass["cleaned"], "wing")
        room = sanitize_name(room_pass["cleaned"], "room")
        content = sanitize_content(content_pass["cleaned"])
        if source_file:
            source_file = strip_lone_surrogates(source_file)
        added_by = strip_lone_surrogates(added_by)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    from .tags import apply_tags_to_metadata, normalise_tags

    auto_tagged = tags is None
    normalised_tags = normalise_tags(tags)

    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    if auto_tagged:
        try:
            from .tag_extraction import extract_tags as _extract_tags

            idf = _get_idf_table(col, wing, room)
            normalised_tags = _extract_tags(content, idf)
        except Exception:
            logger.debug("auto tag extraction failed", exc_info=True)
            normalised_tags = []

    drawer_id = (
        f"drawer_{wing}_{room}_{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
    )

    _wal_log(
        "add_drawer",
        {
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "added_by": added_by,
            "content_length": len(content),
            "content_preview": content[:200],
            "tags": normalised_tags,
        },
    )

    # Per #86 — non-canonical rooms are accepted with a soft warning
    # rather than rejected by an FK. Compute warnings up-front so they
    # ride out on both the new-insert and already-exists paths.
    from .room_taxonomy import validate_room

    warnings = validate_room(room)

    chunk_size = _config.chunk_size
    base_meta = {
        "wing": wing,
        "room": room,
        "source_file": source_file or "",
        "added_by": added_by,
        "filed_at": datetime.now().isoformat(),
    }
    apply_tags_to_metadata(base_meta, normalised_tags)

    from .novelty_wiring import compute_novelty_tag

    novelty_tag = compute_novelty_tag(col, wing, room, content, config=_config)
    if novelty_tag is not None:
        base_meta["novelty_tag"] = novelty_tag

    # Idempotency. Three cases to detect a prior committed write:
    # (a) Single-doc path: drawer_id row exists (the only id used).
    # (b) Chunked path: probe the LAST chunk id — its presence implies
    #     every earlier chunk also landed, since the batched upsert
    #     is all-or-nothing.
    # (c) Legacy pre-#1539 single-row write of oversized content under
    #     drawer_id: probe drawer_id alongside the last chunk id so a
    #     re-call with identical oversized content does not duplicate
    #     the legacy row by adding fresh chunks under different ids.
    if len(content) <= chunk_size:
        idempotency_probe_ids = [drawer_id]
    else:
        last_chunk_idx = (len(content) - 1) // chunk_size
        idempotency_probe_ids = [drawer_id, f"{drawer_id}_chunk_{last_chunk_idx:06d}"]
    try:
        existing = col.get(ids=idempotency_probe_ids, include=[])
        if existing.ids:
            return {
                "success": True,
                "reason": "already_exists",
                "drawer_id": drawer_id,
                "warnings": warnings,
                "sanitize_flags": sanitize_flags,
            }
    except Exception:
        logger.debug("Idempotency pre-check failed for %s", idempotency_probe_ids, exc_info=True)

    try:
        if len(content) <= chunk_size:
            col.upsert(
                ids=[drawer_id],
                documents=[content],
                metadatas=[{**base_meta, "chunk_index": 0}],
            )
            inserted = col.get(ids=[drawer_id], include=[])
            if not inserted.ids:
                raise RuntimeError(
                    "Drawer write was acknowledged but the new ID is not readable. "
                    "The palace index may be stale; run reconnect or repair."
                )
            _metadata_cache = None
            logger.info(f"Filed drawer: {drawer_id} → {wing}/{room}")
            for _w in warnings:
                logger.warning("room taxonomy: %s (drawer=%s)", _w, drawer_id)
            return {
                "success": True,
                "drawer_id": drawer_id,
                "wing": wing,
                "room": room,
                "tags": normalised_tags,
                "warnings": warnings,
                "sanitize_flags": sanitize_flags,
                "chunks": 1,
            }

        # Oversized content: split into bounded per-chunk drawers so the
        # embedding model never sees a document above ``chunk_size``.
        # Single batched ``upsert`` so the embedding pass either commits
        # every chunk or none — no half-written palace if the embedding
        # model fails mid-loop (#1539).
        chunk_ids: list[str] = []
        chunk_docs: list[str] = []
        chunk_metas: list[dict] = []
        for i in range(0, len(content), chunk_size):
            chunk_idx = i // chunk_size
            chunk_ids.append(f"{drawer_id}_chunk_{chunk_idx:06d}")
            chunk_docs.append(content[i : i + chunk_size])
            chunk_metas.append(
                {**base_meta, "chunk_index": chunk_idx, "parent_drawer_id": drawer_id}
            )
        col.upsert(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)
        # Probe the LAST chunk id, not the first — its presence confirms
        # the whole batch landed, not just the leading row.
        inserted = col.get(ids=[chunk_ids[-1]], include=[])
        if not inserted.ids:
            raise RuntimeError(
                "Drawer write was acknowledged but the new ID is not readable. "
                "The palace index may be stale; run reconnect or repair."
            )
        _metadata_cache = None
        logger.info(f"Filed drawer: {drawer_id} → {wing}/{room} ({len(chunk_ids)} chunks)")
        for _w in warnings:
            logger.warning("room taxonomy: %s (drawer=%s)", _w, drawer_id)
        return {
            "success": True,
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "tags": normalised_tags,
            "warnings": warnings,
            "sanitize_flags": sanitize_flags,
            "chunks": len(chunk_ids),
            "chunk_ids": chunk_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "warnings": warnings}


def tool_delete_drawer(drawer_id: str):
    """Delete a single drawer by ID."""
    global _metadata_cache
    col = _get_collection()
    if not col:
        return _no_palace()
    existing = col.get(ids=[drawer_id])
    if not existing["ids"]:
        return {"success": False, "error": f"Drawer not found: {drawer_id}"}

    # Log the deletion with the content being removed for audit trail
    deleted_content = existing.get("documents", [""])[0] if existing.get("documents") else ""
    deleted_meta = _safe_meta(
        existing.get("metadatas", [{}])[0] if existing.get("metadatas") else {}
    )
    _wal_log(
        "delete_drawer",
        {
            "drawer_id": drawer_id,
            "deleted_meta": deleted_meta,
            "content_preview": deleted_content[:200],
        },
    )

    try:
        col.delete(ids=[drawer_id])
        _metadata_cache = None
        logger.info(f"Deleted drawer: {drawer_id}")
        return {"success": True, "drawer_id": drawer_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_sync(project_dir: str = None, wing: str = None, apply: bool = False):
    """Prune drawers whose source files are gitignored, missing, or moved (#1252)."""
    global _metadata_cache
    from .palace import MineAlreadyRunning
    from .sync import sync_palace

    if not _config.palace_path:
        np = _no_palace()
        return {"success": False, "error": np.get("error", "no palace"), "hint": np.get("hint")}
    project_dirs = [project_dir] if project_dir else None
    try:
        try:
            report = sync_palace(
                palace_path=_config.palace_path,
                project_dirs=project_dirs,
                wing=wing,
                dry_run=not apply,
                wal_log=_wal_log,
            )
            return {"success": True, **report}
        # Order matters: typed handlers must precede the bare Exception
        # below, otherwise MineAlreadyRunning and ValueError fall into the
        # generic "sync failed" branch and break the structured-error tests.
        except MineAlreadyRunning as exc:
            return {
                "success": False,
                "error": f"another mine is in progress: {exc}",
                "error_class": "LockHeldByOtherProcess",
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"sync failed: {exc}"}
    finally:
        if apply:
            _metadata_cache = None


def tool_get_drawer(drawer_id: str):
    """Fetch a single drawer by ID. Returns full content and metadata."""
    from .tags import extract_tags_from_metadata

    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        result = col.get(ids=[drawer_id], include=["documents", "metadatas"])
        if not result["ids"]:
            return {"error": f"Drawer not found: {drawer_id}"}
        meta = _safe_meta(result["metadatas"][0])
        doc = result["documents"][0]
        # source_file is the absolute filesystem path written by the
        # miners. Reduce to its basename before handing it to the MCP
        # client — same threat model as the palace_path leak fix:
        # nested-agent / multi-server topologies treat the client as a
        # separate trust domain. Basename preserves citation utility.
        # Mirrors the searcher.search_memories() return shape.
        safe_meta = dict(meta) if meta else {}
        if safe_meta.get("source_file"):
            safe_meta["source_file"] = Path(safe_meta["source_file"]).name
        return {
            "drawer_id": drawer_id,
            "content": doc,
            "wing": safe_meta.get("wing", ""),
            "room": safe_meta.get("room", ""),
            "tags": extract_tags_from_metadata(safe_meta),
            "metadata": safe_meta,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_list_drawers(
    wing: str = None,
    room: str = None,
    tags: list = None,
    limit: int = 20,
    offset: int = 0,
):
    """List drawers with pagination. Optional wing/room/tag filter."""
    from .tags import extract_tags_from_metadata, normalise_tags

    limit = max(1, min(limit, _MAX_RESULTS))
    offset = max(0, offset)
    try:
        wing = _sanitize_optional_name(wing, "wing")
        room = _resolve_room_alias(room)
    except ValueError as e:
        return {"error": str(e)}
    normalised_tags = normalise_tags(tags) if tags else []
    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        where = None
        conditions = []
        if wing:
            conditions.append({"wing": wing})
        if room:
            conditions.append({"room": room})
        if normalised_tags:
            conditions.append({"tags": {"$contains_all": normalised_tags}})
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        kwargs = {"include": ["documents", "metadatas"], "limit": limit, "offset": offset}
        if where:
            kwargs["where"] = where
        result = col.get(**kwargs)

        # Compute total matching drawers for pagination.
        if where:
            total_result = col.get(where=where, include=[])
            total = len(total_result["ids"])
        else:
            total = col.count()

        drawers = []
        for i, did in enumerate(result["ids"]):
            meta = _safe_meta(result["metadatas"][i])
            doc = result["documents"][i]
            drawers.append(
                {
                    "drawer_id": did,
                    "wing": meta.get("wing", ""),
                    "room": meta.get("room", ""),
                    "tags": extract_tags_from_metadata(meta),
                    "content_preview": doc[:200] + "..." if len(doc) > 200 else doc,
                }
            )
        return {
            "drawers": drawers,
            "total": total,
            "count": len(drawers),
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_update_drawer(
    drawer_id: str,
    content: str = None,
    wing: str = None,
    room: str = None,
    tags: list = None,
):
    """Update an existing drawer's content and/or metadata.

    ``tags`` semantics:
        * ``None`` — leave the existing tag list untouched.
        * ``[]``   — clear all tags.
        * non-empty list — replace the existing tag list with the
          normalised input.
    """
    global _metadata_cache

    if content is None and wing is None and room is None and tags is None:
        return {"success": True, "drawer_id": drawer_id, "noop": True}

    from .tags import apply_tags_to_metadata, extract_tags_from_metadata, normalise_tags

    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        existing = col.get(ids=[drawer_id], include=["documents", "metadatas"])
        if not existing["ids"]:
            return {"success": False, "error": f"Drawer not found: {drawer_id}"}

        old_meta = _safe_meta(existing["metadatas"][0])
        old_doc = existing["documents"][0]

        # Observation-grade write sanitizer (#40) — see tool_add_drawer for rationale.
        update_sanitize_flags: list[str] = []

        new_doc = old_doc
        if content is not None:
            content_pass = sanitize_write_content(content)
            if content_pass["error"]:
                return {"success": False, "error": content_pass["error"]}
            update_sanitize_flags.extend(content_pass["flags"])
            try:
                new_doc = sanitize_content(content_pass["cleaned"])
            except ValueError as e:
                return {"success": False, "error": str(e)}

        new_meta = dict(old_meta)
        if wing is not None:
            wing_pass = sanitize_write_name(wing, "wing")
            if wing_pass["error"]:
                return {"success": False, "error": wing_pass["error"]}
            update_sanitize_flags.extend(wing_pass["flags"])
            try:
                new_meta["wing"] = sanitize_name(wing_pass["cleaned"], "wing")
            except ValueError as e:
                return {"success": False, "error": str(e)}
        if room is not None:
            room = _config.resolve_room(room)
            room_pass = sanitize_write_name(room, "room")
            if room_pass["error"]:
                return {"success": False, "error": room_pass["error"]}
            update_sanitize_flags.extend(room_pass["flags"])
            try:
                new_meta["room"] = sanitize_name(room_pass["cleaned"], "room")
            except ValueError as e:
                return {"success": False, "error": str(e)}

        update_sanitize_flags = sorted(set(update_sanitize_flags))

        if tags is not None:
            apply_tags_to_metadata(new_meta, normalise_tags(tags))

        _wal_log(
            "update_drawer",
            {
                "drawer_id": drawer_id,
                "old_wing": old_meta.get("wing", ""),
                "old_room": old_meta.get("room", ""),
                "new_wing": new_meta.get("wing", ""),
                "new_room": new_meta.get("room", ""),
                "old_tags": extract_tags_from_metadata(old_meta),
                "new_tags": extract_tags_from_metadata(new_meta),
                "content_changed": content is not None,
                "content_preview": new_doc[:200] if content is not None else None,
            },
        )

        update_kwargs = {"ids": [drawer_id]}
        if content is not None:
            update_kwargs["documents"] = [new_doc]
        update_kwargs["metadatas"] = [new_meta]
        col.update(**update_kwargs)

        _metadata_cache = None

        # Per #86 — if the caller reassigned the room to a non-canonical
        # value, surface the warning. When room wasn't touched we don't
        # warn (the drawer keeps its existing room; this update isn't
        # responsible for whatever taxonomy it was filed under).
        from .room_taxonomy import validate_room

        warnings = validate_room(new_meta.get("room", "")) if room is not None else []

        logger.info(f"Updated drawer: {drawer_id}")
        for _w in warnings:
            logger.warning("room taxonomy: %s (drawer=%s)", _w, drawer_id)
        return {
            "success": True,
            "drawer_id": drawer_id,
            "wing": new_meta.get("wing", ""),
            "room": new_meta.get("room", ""),
            "tags": extract_tags_from_metadata(new_meta),
            "warnings": warnings,
            "sanitize_flags": update_sanitize_flags,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_rate_memory(drawer_id: str, useful: bool):
    """Record feedback on whether a search result was helpful (#159).

    Stores the rating as drawer *metadata* — the verbatim content is never
    touched. Each call increments one of two counters (``rating_useful`` /
    ``rating_not_useful``); the net of the two becomes a bounded, capped
    ranking signal in ``search_memories`` that can reorder neighbours but
    never excludes a drawer (recall is preserved).
    """
    global _metadata_cache

    from .ratings import apply_rating_to_metadata, extract_rating_from_metadata, net_rating

    if not isinstance(useful, bool):
        return {"success": False, "error": "`useful` must be a boolean"}

    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        existing = col.get(ids=[drawer_id], include=["metadatas"])
        if not existing["ids"]:
            return {"success": False, "error": f"Drawer not found: {drawer_id}"}

        new_meta = dict(_safe_meta(existing["metadatas"][0]))
        apply_rating_to_metadata(new_meta, useful)
        useful_count, not_useful_count = extract_rating_from_metadata(new_meta)

        _wal_log(
            "rate_memory",
            {
                "drawer_id": drawer_id,
                "useful": useful,
                "rating_useful": useful_count,
                "rating_not_useful": not_useful_count,
            },
        )

        # Metadata-only update: never pass `documents`, so the stored
        # verbatim text is left exactly as written.
        col.update(ids=[drawer_id], metadatas=[new_meta])
        _metadata_cache = None

        logger.info(
            "Rated drawer %s useful=%s (useful=%d not_useful=%d)",
            drawer_id,
            useful,
            useful_count,
            not_useful_count,
        )
        return {
            "success": True,
            "drawer_id": drawer_id,
            "useful": useful,
            "rating_useful": useful_count,
            "rating_not_useful": not_useful_count,
            "net_rating": net_rating(new_meta),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_rename_wing(from_wing: str, to_wing: str, batch_size: int = 500):
    """Rename all drawers in one wing to another, server-side.

    Iterates through the source wing in batches and updates each
    drawer's metadata. Much faster than individual update_drawer calls
    over HTTP since it operates directly on the collection.
    """
    global _metadata_cache

    try:
        from_wing = sanitize_name(from_wing, "from_wing")
        to_wing = sanitize_name(to_wing, "to_wing")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if from_wing == to_wing:
        return {"success": True, "renamed": 0, "message": "source and target are the same"}

    col = _get_collection()
    if not col:
        return _no_palace()

    try:
        result = col.rename_wing(
            from_wing=from_wing,
            to_wing=to_wing,
            batch_size=batch_size,
        )
        _metadata_cache = None

        _wal_log(
            "rename_wing",
            {
                "from_wing": from_wing,
                "to_wing": to_wing,
                **result,
            },
        )

        logger.info(f"Renamed wing: {from_wing} -> {to_wing} ({result['renamed']} drawers)")
        return {
            "success": True,
            "from_wing": from_wing,
            "to_wing": to_wing,
            **result,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_list_tags(wing: str = None, room: str = None, min_count: int = 1):
    """Return every unique tag in the palace with the number of drawers carrying it.

    Results are sorted by count (descending). ``wing`` and ``room`` scope
    the count to a subset of the palace. ``min_count`` drops tags below
    the threshold from the result; default 1 keeps any tag with at least
    one drawer.
    """
    from collections import Counter

    from .tags import extract_tags_from_metadata

    try:
        wing = _sanitize_optional_name(wing, "wing")
        room = _resolve_room_alias(room)
    except ValueError as e:
        return {"error": str(e)}
    min_count = max(1, int(min_count))

    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        where = None
        if wing and room:
            where = {"$and": [{"wing": wing}, {"room": room}]}
        elif wing:
            where = {"wing": wing}
        elif room:
            where = {"room": room}

        counter: Counter = Counter()
        page_size = 1000
        offset = 0
        kwargs = {"include": ["metadatas"], "limit": page_size}
        if where:
            kwargs["where"] = where
        while True:
            kwargs["offset"] = offset
            batch = col.get(**kwargs)
            ids = batch["ids"] or []
            metas = batch["metadatas"] or []
            for meta in metas:
                for tag in extract_tags_from_metadata(meta):
                    counter[tag] += 1
            if not ids or len(ids) < page_size:
                break
            offset += len(ids)

        items = [
            {"tag": tag, "count": count}
            for tag, count in counter.most_common()
            if count >= min_count
        ]
        return {
            "tags": items,
            "total_unique_tags": len(items),
            "filters": {"wing": wing, "room": room, "min_count": min_count},
        }
    except Exception as e:
        return {"error": str(e)}


# ==================== KNOWLEDGE GRAPH ====================


def tool_kg_query(entity: str, as_of: str = None, direction: str = "both"):
    """Query the knowledge graph for an entity's relationships."""
    try:
        entity = sanitize_kg_value(entity, "entity")
        as_of = sanitize_iso_temporal(as_of, "as_of")
    except ValueError as e:
        return {"error": str(e)}

    if direction not in ("outgoing", "incoming", "both"):
        return {"error": "direction must be 'outgoing', 'incoming', or 'both'"}

    results = _call_kg(lambda kg: kg.query_entity(entity, as_of=as_of, direction=direction))
    return {"entity": entity, "as_of": as_of, "facts": results, "count": len(results)}


def tool_kg_add(
    subject: str,
    predicate: str,
    object: str,
    valid_from: str = None,
    valid_to: str = None,
    source_closet: str = None,
    source_file: str = None,
    source_drawer_id: str = None,
    context: str = None,
):
    """Add a relationship to the knowledge graph.

    All temporal and provenance fields are optional. ``valid_to`` lets callers
    backfill historical facts with a known end date/time in a single call
    instead of a separate ``kg_invalidate`` call.

    Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
    the form ``YYYY-MM-DDTHH:MM:SSZ``.

    ``context`` is the SPOC fourth-axis (techempower-org/mempalace#161):
    a free-form anchor naming where the fact was witnessed (e.g.
    ``drawer:abc123``, ``conversation:2026-05-28``). The AGE backend
    stores it on the RELATION edge and surfaces it through every read
    path; the SQLite backend silently accepts and ignores it (storage
    schema doesn't yet have a column for it) so callers don't need to
    branch on backend.
    """
    try:
        subject = sanitize_kg_value(subject, "subject")
        predicate = sanitize_name(predicate, "predicate")
        object = sanitize_kg_value(object, "object")
        valid_from = sanitize_iso_temporal(valid_from, "valid_from")
        valid_to = sanitize_iso_temporal(valid_to, "valid_to")
        if context is not None:
            context = sanitize_kg_value(context, "context")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    _wal_log(
        "kg_add",
        {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "source_closet": source_closet,
            "source_file": source_file,
            "source_drawer_id": source_drawer_id,
            "context": context,
        },
    )

    def _add(kg):
        # SQLite KG accepts source_* kwargs; AGE KG accepts ``context``
        # (and a ``source`` string). Build kwargs per backend so each
        # gets only what its signature understands. Backend detection is
        # cheap (a class check) and avoids the TypeError-on-kwarg drift
        # that ``MEMPALACE_KG_BACKEND=age`` would otherwise hit.
        from .knowledge_graph_age import KnowledgeGraphAGE

        if isinstance(kg, KnowledgeGraphAGE):
            return kg.add_triple(
                subject,
                predicate,
                object,
                valid_from=valid_from,
                valid_to=valid_to,
                source=source_drawer_id or source_file or source_closet,
                context=context,
            )
        return kg.add_triple(
            subject,
            predicate,
            object,
            valid_from=valid_from,
            valid_to=valid_to,
            source_closet=source_closet,
            source_file=source_file,
            source_drawer_id=source_drawer_id,
        )

    triple_id = _call_kg(_add)
    return {"success": True, "triple_id": triple_id, "fact": f"{subject} → {predicate} → {object}"}


def tool_kg_invalidate(subject: str, predicate: str, object: str, ended: str = None):
    """Mark a fact as no longer true.

    Returns the actual ``ended`` date/time that was stored. When the caller
    omits ``ended``, the underlying graph stamps ``date.today()`` and the
    response reflects that resolved value.

    Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
    the form ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    try:
        subject = sanitize_kg_value(subject, "subject")
        predicate = sanitize_name(predicate, "predicate")
        object = sanitize_kg_value(object, "object")
        ended = sanitize_iso_temporal(ended, "ended")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    resolved_ended = ended or date.today().isoformat()

    _wal_log(
        "kg_invalidate",
        {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "ended": resolved_ended,
        },
    )

    _call_kg(lambda kg: kg.invalidate(subject, predicate, object, ended=resolved_ended))
    return {
        "success": True,
        "fact": f"{subject} → {predicate} → {object}",
        "ended": resolved_ended,
    }


def tool_kg_timeline(entity: str = None, as_of: str = None):
    """Get chronological timeline of facts, optionally for one entity.

    ``as_of`` (techempower-org/mempalace#161) filters to facts whose
    temporal interval contains the given date/datetime; NULL ends are
    treated as open intervals (same semantics as ``mempalace_kg_query``).
    Omit ``as_of`` to see the full timeline including expired facts.
    """
    if entity is not None:
        try:
            entity = sanitize_kg_value(entity, "entity")
        except ValueError as e:
            return {"error": str(e)}
    if as_of is not None:
        try:
            as_of = sanitize_iso_temporal(as_of, "as_of")
        except ValueError as e:
            return {"error": str(e)}

    def _timeline(kg):
        # AGE backend grew an ``as_of`` parameter (#161); the SQLite
        # backend's ``timeline()`` accepts only ``entity_name``. Pass the
        # extra kwarg conditionally so SQLite callers don't TypeError.
        from .knowledge_graph_age import KnowledgeGraphAGE

        if isinstance(kg, KnowledgeGraphAGE):
            return kg.timeline(entity, as_of=as_of)
        return kg.timeline(entity)

    results = _call_kg(_timeline)
    return {
        "entity": entity or "all",
        "as_of": as_of,
        "timeline": results,
        "count": len(results),
    }


def tool_kg_stats():
    """Knowledge graph overview: entities, triples, relationship types.

    Returns a structured error envelope on transient postgres failures
    (the connection dropped between `_call_kg` opening the handle and
    `kg.stats()` finishing its query — typically caused by a postgres
    OOM-kill or restart under load). The caller sees
    ``{"error": "backend_unavailable", "retryable": True, ...}`` and can
    surface "try again in a moment" instead of an opaque -32000
    internal error. See techempower-org/mempalace#299.

    Non-transient errors (cypher syntax, value-validation, schema
    mismatch) still propagate — those need a real fix, not a retry.
    """
    try:
        return _call_kg(lambda kg: kg.stats())
    except Exception as e:  # noqa: BLE001
        if _is_transient_postgres_error(e):
            logger.warning("mempalace_kg_stats: backend transiently unavailable: %s", e)
            return {
                "error": "backend_unavailable",
                "detail": str(e),
                "retryable": True,
            }
        raise


def _is_transient_postgres_error(exc: BaseException) -> bool:
    """True when ``exc`` is a psycopg connection-dropped error.

    Matches the same two error families ``kg_triple_worker._execute_with_retry``
    catches (techempower-org/mempalace#298): ``OperationalError`` (server
    closed the connection, OOM-restart, network blip) and
    ``InterfaceError`` (the connection is closed, pool returned a dead
    handle). Other psycopg errors — ``DataError``, ``ProgrammingError``
    on the postgres side, syntax errors — are bugs, not transient
    backend state, and should propagate.

    Returns False if psycopg isn't importable (sqlite-only deployments
    can't have postgres-transient errors).
    """
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))


# ==================== AGENT DIARY ====================


def tool_diary_write(
    agent_name: str,
    entry: str,
    topic: str = "general",
    wing: str = "",
    session_id: str = "",
):
    """
    Write a diary entry for this agent. Entries are timestamped and
    accumulate over time in a diary room.

    This is the agent's personal journal — observations, thoughts,
    what it worked on, what it noticed, what it thinks matters.

    All entries land in the main ``mempalace_drawers`` collection — the
    earlier dedicated checkpoint collection has been retired (verbatim
    transcripts already cover the recovery use case).

    Note: ``agent_name`` is normalized to lowercase before storage so
    that diary reads are case-insensitive (see #1243). "Claude",
    "claude", and "CLAUDE" all resolve to the same agent.
    """
    # Observation-grade write sanitizer (#40) runs ahead of the strict
    # shape checks; see tool_add_drawer for rationale.
    agent_pass = sanitize_write_name(agent_name, "agent_name")
    if agent_pass["error"]:
        return {"success": False, "error": agent_pass["error"]}
    entry_pass = sanitize_write_content(entry)
    if entry_pass["error"]:
        return {"success": False, "error": entry_pass["error"]}
    topic_pass = sanitize_write_name(topic, "topic")
    if topic_pass["error"]:
        return {"success": False, "error": topic_pass["error"]}
    diary_sanitize_flags = sorted(
        set(agent_pass["flags"]) | set(entry_pass["flags"]) | set(topic_pass["flags"])
    )
    try:
        agent_name = sanitize_name(agent_pass["cleaned"], "agent_name").lower()
        entry = sanitize_content(entry_pass["cleaned"])
        topic = sanitize_name(topic_pass["cleaned"], "topic")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if wing:
        wing_pass = sanitize_write_name(wing, "wing")
        if wing_pass["error"]:
            return {"success": False, "error": wing_pass["error"]}
        if wing_pass["flags"]:
            diary_sanitize_flags = sorted(set(diary_sanitize_flags) | set(wing_pass["flags"]))
        wing = sanitize_name(wing_pass["cleaned"])
    else:
        wing = f"wing_{agent_name.replace(' ', '_')}"
    # NB: ``diary`` is not in the canonical 7-room taxonomy. Per #86 it
    # is accepted as-is and the soft-warn lands in the response's
    # ``warnings`` field. Pre-#86 this room name triggered an FK
    # rejection on the postgres backend (see 12a25d7); the FK has been
    # dropped. ``tool_diary_read`` filters on the same ``room="diary"``
    # so the round-trip stays consistent for historical entries.
    room = "diary"
    from .room_taxonomy import validate_room

    warnings = validate_room(room)
    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    now = datetime.now()
    entry_id = (
        f"diary_{wing}_{now.strftime('%Y%m%d_%H%M%S%f')}_"
        f"{hashlib.sha256(entry.encode()).hexdigest()[:12]}"
    )

    _wal_log(
        "diary_write",
        {
            "agent_name": agent_name,
            "topic": topic,
            "entry_id": entry_id,
            "entry_preview": entry[:200],
        },
    )

    try:
        # #300: AAAK is dense and structural; a frozen sentence-transformer
        # can't decode entity codes / pipe-prefixed fields / star markers,
        # so embedding raw AAAK loses signal. ``expand_aaak_for_embedding``
        # detects AAAK-shape input and sidecars a decoded-prose pass below
        # the verbatim line. Plain prose is returned untouched.
        from .dialect import expand_aaak_for_embedding, looks_like_aaak

        embed_text = expand_aaak_for_embedding(entry)
        entry_was_aaak = looks_like_aaak(entry) and embed_text != entry

        base_metadata = {
            "wing": wing,
            "room": room,
            "hall": "hall_diary",
            "topic": topic,
            "type": "diary_entry",
            "agent": agent_name,
            "filed_at": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
        }
        if session_id:
            base_metadata["session_id"] = session_id
        # Verbatim guarantee: preserve the raw AAAK source whenever the
        # embed text was augmented so ``diary_read`` and search hits can
        # surface the original line, not the decoded sidecar.
        if entry_was_aaak:
            base_metadata["aaak_source"] = entry

        chunk_size = _config.chunk_size
        if len(entry) <= chunk_size:
            col.add(
                ids=[entry_id],
                documents=[embed_text],
                metadatas=[{**base_metadata, "chunk_index": 0}],
            )
            logger.info(f"Diary entry: {entry_id} → {wing}/diary/{topic}")
            for _w in warnings:
                logger.warning("room taxonomy: %s (entry=%s)", _w, entry_id)
            return {
                "success": True,
                "entry_id": entry_id,
                "agent": agent_name,
                "topic": topic,
                "timestamp": now.isoformat(),
                "warnings": warnings,
                "sanitize_flags": diary_sanitize_flags,
                "chunks": 1,
            }

        # Oversized entry: split into bounded per-chunk drawers so the
        # embedding model never sees a document above ``chunk_size``.
        # Every chunk carries ``parent_entry_id`` so search can rejoin
        # them and ``chunk_index`` for ordered reconstruction (#1539).
        # Note on ``entry_id`` in the return value: for the chunked
        # path the returned ``entry_id`` is the LOGICAL group handle
        # (no drawer is stored under that exact id). The physical
        # drawer ids are in ``chunk_ids``. Callers wanting to fetch
        # by id should iterate ``chunk_ids``; callers wanting to
        # query by metadata can filter on ``parent_entry_id``.
        # Use a single batched ``add`` so the embedding pass either
        # commits all chunks or none — avoids a half-written palace
        # if the embedding model fails mid-loop. ``col.add`` (not
        # ``upsert``) is intentional here: ``entry_id`` is timestamp-
        # based with microsecond precision, so every call generates a
        # fresh id and a duplicate is by definition a same-microsecond
        # clash that should surface as an error rather than silently
        # overwrite the prior entry (cf. ``tool_add_drawer`` whose
        # content-hash ids are deliberately idempotent and use upsert).
        chunk_ids: list[str] = []
        chunk_docs: list[str] = []
        chunk_metas: list[dict] = []
        for i in range(0, len(entry), chunk_size):
            chunk_idx = i // chunk_size
            chunk_ids.append(f"{entry_id}_chunk_{chunk_idx:06d}")
            chunk_docs.append(entry[i : i + chunk_size])
            chunk_metas.append(
                {
                    **base_metadata,
                    "chunk_index": chunk_idx,
                    "parent_entry_id": entry_id,
                }
            )
        col.add(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)
        logger.info(f"Diary entry: {entry_id} → {wing}/diary/{topic} ({len(chunk_ids)} chunks)")
        for _w in warnings:
            logger.warning("room taxonomy: %s (entry=%s)", _w, entry_id)
        return {
            "success": True,
            "entry_id": entry_id,
            "agent": agent_name,
            "topic": topic,
            "timestamp": now.isoformat(),
            "warnings": warnings,
            "sanitize_flags": diary_sanitize_flags,
            "chunks": len(chunk_ids),
            "chunk_ids": chunk_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "warnings": warnings}


def tool_diary_read(agent_name: str, last_n: int = 10, wing: str = ""):
    """
    Read an agent's recent diary entries. Returns the last N entries
    in chronological order — the agent's personal journal.

    When ``wing`` is provided, reads only from that wing. When ``wing``
    is empty or omitted, returns entries from every wing this agent has
    written to. Diary writes from hooks land in project-derived wings
    (``wing_<project>``), so requiring a specific wing on read would
    silo those entries from agent-initiated reads.

    Note: ``agent_name`` is normalized to lowercase before filtering so
    that reads are case-insensitive (see #1243). Entries written under
    pre-fix mixed-case agent names will not match the lowercase filter;
    use ``mempalace repair`` to migrate legacy data if needed.
    """
    try:
        agent_name = sanitize_name(agent_name, "agent_name").lower()
        if wing:
            wing = sanitize_name(wing)
    except ValueError as e:
        return {"error": str(e)}
    last_n = max(1, min(last_n, 100))
    col = _get_collection()
    if not col:
        return _no_palace()

    # Build filter: always scope by agent + room=diary. PR #83 had moved
    # diary writes to room=sessions to satisfy the then-FK-enforced
    # canonical taxonomy, but PR #87 dropped the FK (soft-warn instead)
    # and restored diary writes to room=diary so the diary feature stayed
    # semantically distinct from generic session checkpoints. The read
    # side wasn't updated then — this filter still said "sessions" until
    # the test suite caught it. Round-trip is back in sync: write →
    # room=diary, read → room=diary. Wing is optional — when empty,
    # return entries across all wings this agent has written to
    # (matches the #1097 empty-string-as-no-filter convention).
    conditions = [{"room": "diary"}, {"agent": agent_name}]
    if wing:
        conditions.insert(0, {"wing": wing})

    try:
        results = col.get(
            where={"$and": conditions},
            include=["documents", "metadatas"],
            limit=10000,
        )

        if not results["ids"]:
            return {"agent": agent_name, "entries": [], "message": "No diary entries yet."}

        # Combine and sort by timestamp
        entries = []
        for drawer_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            meta = _safe_meta(meta)
            entries.append(
                {
                    "drawer_id": drawer_id,
                    "date": meta.get("date", ""),
                    "timestamp": meta.get("filed_at", ""),
                    "topic": meta.get("topic", ""),
                    "content": doc,
                }
            )

        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        entries = entries[:last_n]

        return {
            "agent": agent_name,
            "entries": entries,
            "total": len(results["ids"]),
            "showing": len(entries),
        }
    except Exception:
        logger.exception("diary_read failed")
        return {"error": "Failed to read diary entries"}


def tool_hook_settings(silent_save: bool = None, desktop_toast: bool = None):
    """
    Get or set hook behavior settings.

    - silent_save: True = stop hook saves directly (no MCP clutter),
      False = legacy blocking MCP calls. Default: True.
    - desktop_toast: True = show notify-send desktop toast on save,
      False = terminal-only notification. Default: False.

    Call with no arguments to see current settings.
    """
    from .config import MempalaceConfig

    try:
        config = MempalaceConfig()
    except Exception as e:
        return {"success": False, "error": str(e)}

    changed = []
    if silent_save is not None:
        config.set_hook_setting("silent_save", silent_save)
        changed.append(f"silent_save → {silent_save}")
    if desktop_toast is not None:
        config.set_hook_setting("desktop_toast", desktop_toast)
        changed.append(f"desktop_toast → {desktop_toast}")

    # Re-read to return current state
    try:
        config = MempalaceConfig()
    except Exception:
        logger.debug("Could not re-read config after update", exc_info=True)

    result = {
        "success": True,
        "settings": {
            "silent_save": config.hook_silent_save,
            "desktop_toast": config.hook_desktop_toast,
        },
    }
    if changed:
        result["updated"] = changed
    return result


def tool_memories_filed_away():
    """Acknowledge the latest silent checkpoint. Returns a short summary."""
    state_dir = Path.home() / ".mempalace" / "hook_state"
    ack_file = state_dir / "last_checkpoint"
    if not ack_file.is_file():
        return {
            "status": "quiet",
            "message": "No recent journal entry",
            "count": 0,
            "timestamp": None,
        }
    try:
        data = json.loads(ack_file.read_text(encoding="utf-8"))
        ack_file.unlink(missing_ok=True)
        msgs = data.get("msgs", 0)
        return {
            "status": "ok",
            "message": f"\u2726 {msgs} messages tucked into drawers",
            "count": msgs,
            "timestamp": data.get("ts", None),
        }
    except (json.JSONDecodeError, OSError):
        ack_file.unlink(missing_ok=True)
        return {
            "status": "error",
            "message": "\u2726 Journal entry filed in the palace",
            "count": 0,
            "timestamp": None,
        }


# ==================== SETTINGS TOOLS ====================


def tool_reconnect():
    """Force the MCP server to drop cached ChromaDB + KnowledgeGraph state.

    Use after external scripts or CLI commands modify the palace database
    or replace ``knowledge_graph.sqlite3`` directly, which can leave the
    in-memory HNSW index stale or pin a closed-on-disk SQLite connection.
    """
    global \
        _client_cache, \
        _collection_cache, \
        _palace_db_inode, \
        _palace_db_mtime, \
        _vector_disabled, \
        _vector_disabled_reason
    from .palace import get_backend

    close_errors = []
    try:
        get_backend("chroma").close_palace(_config.palace_path)
    except Exception as exc:
        logger.debug("Failed to close shared palace backend during reconnect", exc_info=True)
        close_errors.append(f"backend close_palace failed: {exc}")
    try:
        from chromadb.api.client import SharedSystemClient

        clear_system_cache = getattr(SharedSystemClient, "clear_system_cache", None)
        if callable(clear_system_cache):
            clear_system_cache()
        else:
            logger.debug(
                "SharedSystemClient.clear_system_cache is unavailable; skipping shared Chroma cache clear during reconnect"
            )
    except Exception as exc:
        logger.debug(
            "Failed to clear Chroma shared system cache during reconnect",
            exc_info=True,
        )
        close_errors.append(f"shared Chroma cache clear failed: {exc}")
    _client_cache = None
    _collection_cache = None
    _palace_db_inode = 0
    _palace_db_mtime = 0.0
    # Force probe re-run on next _get_client by clearing the flag now;
    # _refresh_vector_disabled_flag will re-set it if the divergence
    # still applies after the reconnect.
    _vector_disabled = False
    _vector_disabled_reason = ""
    # Drain the per-path KnowledgeGraph cache so a replaced sqlite file is
    # reopened on the next tool call rather than served from a stale handle.
    with _kg_cache_lock:
        for kg in _kg_by_path.values():
            try:
                kg.close()
            except Exception:
                pass
        _kg_by_path.clear()
    try:
        col = _get_collection()
        if col is None:
            result = {
                "success": False,
                "message": "No palace found after reconnect",
                "drawers": 0,
                "vector_disabled": _vector_disabled,
            }
            if close_errors:
                result["error"] = "; ".join(close_errors)
            return result
        if close_errors:
            return {
                "success": False,
                "message": "Reconnect reopened the palace but failed to fully reset cached handles",
                "drawers": col.count(),
                "vector_disabled": _vector_disabled,
                "vector_disabled_reason": _vector_disabled_reason,
                "error": "; ".join(close_errors),
            }
        return {
            "success": True,
            "message": "Reconnected to palace",
            "drawers": col.count(),
            "vector_disabled": _vector_disabled,
            "vector_disabled_reason": _vector_disabled_reason,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== MCP PROTOCOL ====================

TOOLS = {
    "mempalace_status": {
        "description": "Palace overview — total drawers, wing and room counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_status,
    },
    "mempalace_list_wings": {
        "description": "List all wings with drawer counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_list_wings,
    },
    "mempalace_list_rooms": {
        "description": "List rooms within a wing (or all rooms if no wing given)",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to list rooms for (optional)"},
            },
        },
        "handler": tool_list_rooms,
    },
    "mempalace_get_taxonomy": {
        "description": "Full taxonomy: wing → room → drawer count",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_get_taxonomy,
    },
    "mempalace_get_aaak_spec": {
        "description": "Get the AAAK dialect specification — the compressed memory format MemPalace uses. Call this if you need to read or write AAAK-compressed memories.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_get_aaak_spec,
    },
    "mempalace_kg_query": {
        "description": "Query the knowledge graph for an entity's relationships. Returns typed facts with temporal validity. E.g. 'Max' → child_of Alice, loves chess, does swimming. Filter by date with as_of to see what was true at a point in time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to query (e.g. 'Max', 'MyProject', 'Alice')",
                },
                "as_of": {
                    "type": "string",
                    "description": "Date/datetime filter — only facts valid at this time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional)",
                },
                "direction": {
                    "type": "string",
                    "description": "outgoing (entity→?), incoming (?→entity), or both (default: both)",
                },
            },
            "required": ["entity"],
        },
        "handler": tool_kg_query,
    },
    "mempalace_kg_add": {
        "description": "Add a fact to the knowledge graph. Subject → predicate → object with optional time window. E.g. ('Max', 'started_school', 'Year 7', valid_from='2026-09-01'). Pass valid_to to backfill an already-ended historical fact in a single call. Pass context to anchor the fact to a witnessing drawer/conversation (SPOC).",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity doing/being something"},
                "predicate": {
                    "type": "string",
                    "description": "The relationship type (e.g. 'loves', 'works_on', 'daughter_of')",
                },
                "object": {"type": "string", "description": "The entity being connected to"},
                "valid_from": {
                    "type": "string",
                    "description": "When this became true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional)",
                },
                "valid_to": {
                    "type": "string",
                    "description": "When this stopped being true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional). Use for backfilling already-ended historical facts.",
                },
                "source_closet": {
                    "type": "string",
                    "description": "Closet ID where this fact appears (optional)",
                },
                "source_file": {
                    "type": "string",
                    "description": "Source file path the fact was extracted from (optional)",
                },
                "source_drawer_id": {
                    "type": "string",
                    "description": "Drawer ID the fact was extracted from (optional, RFC 002 provenance)",
                },
                "context": {
                    "type": "string",
                    "description": "SPOC anchor — where this fact was witnessed (e.g. 'drawer:abc123', 'conversation:2026-05-28'). AGE backend only; SQLite ignores. Optional.",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_add,
    },
    "mempalace_kg_invalidate": {
        "description": "Mark a fact as no longer true. E.g. ankle injury resolved, job ended, moved house.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity"},
                "predicate": {"type": "string", "description": "Relationship"},
                "object": {"type": "string", "description": "Connected entity"},
                "ended": {
                    "type": "string",
                    "description": "When it stopped being true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, default: today)",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_invalidate,
    },
    "mempalace_kg_timeline": {
        "description": "Chronological timeline of facts. Shows the story of an entity (or everything) in order. Filter by date with as_of to see what was true at a point in time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to get timeline for (optional — omit for full timeline)",
                },
                "as_of": {
                    "type": "string",
                    "description": "Date/datetime filter — only facts valid at this time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional). AGE backend only.",
                },
            },
        },
        "handler": tool_kg_timeline,
    },
    "mempalace_kg_stats": {
        "description": "Knowledge graph overview: entities, triples, current vs expired facts, relationship types.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_kg_stats,
    },
    "mempalace_traverse": {
        "description": "Walk the palace graph from a room. Shows connected ideas across wings — the tunnels. Like following a thread through the palace: start at 'chromadb-setup' in wing_code, discover it connects to wing_myproject (planning) and wing_user (feelings about it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_room": {
                    "type": "string",
                    "description": "Room to start from (e.g. 'chromadb-setup', 'riley-school')",
                },
                "max_hops": {
                    "type": "integer",
                    "description": "How many connections to follow (default: 2)",
                },
            },
            "required": ["start_room"],
        },
        "handler": tool_traverse_graph,
    },
    "mempalace_walk_palace": {
        "description": (
            "Walk the palace via AGE Cypher traversal — the 'agent walks into the palace' "
            "primitive. Start at exactly one of {wing, room, entity} and enumerate the "
            "navigable subgraph at the given depth. "
            "start_wing='memorypalace' → rooms (d=1), drawers (d=2), entities (d=3). "
            "start_room='problems' → drawers across wings (d=1), entities (d=2). "
            "start_entity='pgvector' → drawers mentioning it (d=1), rooms+wings containing them (d=2 — inverse walk). "
            "Requires MEMPALACE_BACKEND=postgres + the AGE knowledge graph populated via kg_writethrough or backfill_age."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_wing": {
                    "type": "string",
                    "description": "Wing name to walk from (mutually exclusive with start_room/start_entity)",
                },
                "start_room": {"type": "string", "description": "Room name to walk from"},
                "start_entity": {
                    "type": "string",
                    "description": "Entity name to walk from (inverse walk)",
                },
                "depth": {"type": "integer", "description": "Walk depth (1..5, default 2)"},
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (1..500, default 50)",
                },
            },
        },
        "handler": tool_walk_palace,
    },
    "mempalace_find_tunnels": {
        "description": "Find rooms that bridge two wings — the hallways connecting different domains. E.g. what topics connect wing_code to wing_team?",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing (optional)"},
                "wing_b": {"type": "string", "description": "Second wing (optional)"},
            },
        },
        "handler": tool_find_tunnels,
    },
    "mempalace_graph_stats": {
        "description": "Palace graph overview: total rooms, tunnel connections, edges between wings.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_graph_stats,
    },
    "mempalace_create_tunnel": {
        "description": "Create a cross-wing tunnel linking two palace locations. Use when content in one project relates to another — e.g., an API design in project_api connects to a database schema in project_database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_wing": {"type": "string", "description": "Wing of the source"},
                "source_room": {"type": "string", "description": "Room in the source wing"},
                "target_wing": {"type": "string", "description": "Wing of the target"},
                "target_room": {"type": "string", "description": "Room in the target wing"},
                "label": {"type": "string", "description": "Description of the connection"},
                "source_drawer_id": {
                    "type": "string",
                    "description": "Optional specific drawer ID",
                },
                "target_drawer_id": {
                    "type": "string",
                    "description": "Optional specific drawer ID",
                },
            },
            "required": ["source_wing", "source_room", "target_wing", "target_room"],
        },
        "handler": tool_create_tunnel,
    },
    "mempalace_list_tunnels": {
        "description": (
            "List cross-wing tunnels. Default returns only explicit "
            "(agent-created) tunnels stored at ~/.mempalace/tunnels.json. "
            "Pass include_passive=true to also include passive tunnels "
            "(rooms appearing in 2+ wings, computed from graph_stats). "
            "Each result is tagged with kind: 'explicit'|'passive'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {
                    "type": "string",
                    "description": "Filter tunnels by wing (shows tunnels where wing is source or target)",
                },
                "include_passive": {
                    "type": "boolean",
                    "description": (
                        "When true, include passive tunnels — rooms appearing "
                        "in 2+ wings, discovered from the palace graph. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
        },
        "handler": tool_list_tunnels,
    },
    "mempalace_delete_tunnel": {
        "description": "Delete an explicit tunnel by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tunnel_id": {"type": "string", "description": "Tunnel ID to delete"},
            },
            "required": ["tunnel_id"],
        },
        "handler": tool_delete_tunnel,
    },
    "mempalace_follow_tunnels": {
        "description": "Follow tunnels from a room to see what it connects to in other wings. Returns connected rooms with drawer previews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to start from"},
                "room": {"type": "string", "description": "Room to follow tunnels from"},
            },
            "required": ["wing", "room"],
        },
        "handler": tool_follow_tunnels,
    },
    "mempalace_search": {
        "description": "Semantic search. Returns verbatim drawer content with similarity scores. IMPORTANT: 'query' must contain ONLY search keywords. Use 'context' for background. Results with cosine distance > max_distance are filtered out.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short search query ONLY — keywords or a question. Max 250 chars.",
                    "maxLength": 250,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                    "minimum": 1,
                    "maximum": 100,
                },
                "wing": {"type": "string", "description": "Filter by wing (optional)"},
                "room": {"type": "string", "description": "Filter by room (optional)"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only return drawers carrying ALL of these tags (AND logic). Tags are case-insensitive; spaces become hyphens.",
                },
                "max_distance": {
                    "type": "number",
                    "description": "Max cosine distance threshold (0=identical, 2=opposite). Results further than this are dropped. Lower = stricter. Default 1.5. Set to 0 to disable.",
                },
                "context": {
                    "type": "string",
                    "description": "Background context for the search (optional). NOT used for embedding — only for future re-ranking.",
                },
                "candidate_strategy": {
                    "type": "string",
                    "description": "Candidate selection: 'vector', 'union' (vector + BM25), or 'hybrid' (default, vector + BM25 + graph).",
                    "enum": ["vector", "union", "hybrid"],
                },
                "fusion_mode": {
                    "type": "string",
                    "description": "Fusion algorithm for the final ranking — 'convex' (default, weighted vec+BM25) or 'rrf' (Reciprocal Rank Fusion).",
                    "enum": ["convex", "rrf"],
                },
                "include_trace": {
                    "type": "boolean",
                    "description": "If true, attaches per-source matched_via counts to the response (debug/eval).",
                },
            },
            "required": ["query"],
        },
        "handler": tool_search,
    },
    "mempalace_check_duplicate": {
        "description": "Check if content already exists in the palace before filing",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to check"},
                "threshold": {
                    "type": "number",
                    "description": "Similarity threshold 0-1 (default 0.9)",
                },
            },
            "required": ["content"],
        },
        "handler": tool_check_duplicate,
    },
    "mempalace_add_drawer": {
        "description": "File verbatim content into the palace. Checks for duplicates first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project name)"},
                "room": {
                    "type": "string",
                    "description": "Room (aspect: backend, decisions, meetings...)",
                },
                "content": {
                    "type": "string",
                    "description": "Verbatim content to store — exact words, never summarized",
                },
                "source_file": {"type": "string", "description": "Where this came from (optional)"},
                "added_by": {"type": "string", "description": "Who is filing this (default: mcp)"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Cross-cutting labels for the drawer (optional). Normalised to lower-case with spaces → hyphens.",
                },
            },
            "required": ["wing", "room", "content"],
        },
        "handler": tool_add_drawer,
    },
    "mempalace_list_tags": {
        "description": "List all unique tags in the palace with the number of drawers carrying each. Sorted by count, descending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {
                    "type": "string",
                    "description": "Scope the count to a single wing (optional)",
                },
                "room": {
                    "type": "string",
                    "description": "Scope the count to a single room (optional)",
                },
                "min_count": {
                    "type": "integer",
                    "description": "Drop tags below this drawer-count threshold (default 1).",
                    "minimum": 1,
                },
            },
        },
        "handler": tool_list_tags,
    },
    "mempalace_delete_drawer": {
        "description": "Delete a drawer by ID. Irreversible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to delete"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_delete_drawer,
    },
    "mempalace_sync": {
        "description": "Prune drawers whose source files are gitignored, deleted, or moved. Returns dry-run report by default; pass apply=true to commit deletions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_dir": {
                    "type": "string",
                    "description": "Project root to scope the sync (optional; auto-detected from drawer metadata if omitted)",
                },
                "wing": {"type": "string", "description": "Limit to one wing (optional)"},
                "apply": {
                    "type": "boolean",
                    "description": "Actually delete drawers; default is dry-run preview",
                },
            },
        },
        "handler": tool_sync,
    },
    "mempalace_get_drawer": {
        "description": "Fetch a single drawer by ID — returns full content and metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to fetch"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_get_drawer,
    },
    "mempalace_list_drawers": {
        "description": "List drawers with pagination. Optional wing/room/tag filter. Returns IDs, wings, rooms, tags, content previews, and total matching count for pagination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Filter by wing (optional)"},
                "room": {"type": "string", "description": "Filter by room (optional)"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only list drawers carrying ALL of these tags (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results per page (default 20, max 100)",
                    "minimum": 1,
                    "maximum": 100,
                },
                "offset": {
                    "type": "integer",
                    "description": "Offset for pagination (default 0)",
                    "minimum": 0,
                },
            },
        },
        "handler": tool_list_drawers,
    },
    "mempalace_update_drawer": {
        "description": "Update an existing drawer's content and/or metadata (wing, room, tags). Fetches existing drawer first; returns error if not found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to update"},
                "content": {
                    "type": "string",
                    "description": "New content (optional — omit to keep existing)",
                },
                "wing": {
                    "type": "string",
                    "description": "New wing (optional — omit to keep existing)",
                },
                "room": {
                    "type": "string",
                    "description": "New room (optional — omit to keep existing)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace the drawer's tag list with this set (pass [] to clear; omit to leave untouched).",
                },
            },
            "required": ["drawer_id"],
        },
        "handler": tool_update_drawer,
    },
    "mempalace_rate_memory": {
        "description": "Record feedback on whether a search result was useful. Stored as drawer metadata (never mutates content); accumulated ratings become a bounded boost/penalty in search ranking — they reorder results but never exclude a drawer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer being rated"},
                "useful": {
                    "type": "boolean",
                    "description": "True if the result was helpful, False if not",
                },
            },
            "required": ["drawer_id", "useful"],
        },
        "handler": tool_rate_memory,
    },
    "mempalace_rename_wing": {
        "description": "Rename all drawers from one wing to another, server-side. Much faster than individual update_drawer calls. Use for wing name standardization.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_wing": {
                    "type": "string",
                    "description": "Source wing name to rename from",
                },
                "to_wing": {
                    "type": "string",
                    "description": "Target wing name to rename to",
                },
                "batch_size": {
                    "type": "integer",
                    "description": "Drawers per batch (default 500)",
                    "default": 500,
                    "minimum": 1,
                    "maximum": 5000,
                },
            },
            "required": ["from_wing", "to_wing"],
        },
        "handler": tool_rename_wing,
    },
    "mempalace_diary_write": {
        "description": "Write to your personal agent diary in AAAK format. Your observations, thoughts, what you worked on, what matters. Each agent has their own diary with full history. Write in AAAK for compression — e.g. 'SESSION:2026-04-04|built.palace.graph+diary.tools|ALC.req:agent.diaries.in.aaak|★★★'. Use entity codes from the AAAK spec.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name — each agent gets their own diary wing",
                },
                "entry": {
                    "type": "string",
                    "description": "Your diary entry in AAAK format — compressed, entity-coded, emotion-marked",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic tag (optional, default: general)",
                },
                "wing": {
                    "type": "string",
                    "description": "Target wing for this diary entry (optional). If omitted, uses wing_{agent_name}. Use this to write diary entries to a project wing instead of an agent-specific wing.",
                },
            },
            "required": ["agent_name", "entry"],
        },
        "handler": tool_diary_write,
    },
    "mempalace_diary_read": {
        "description": "Read your recent diary entries (in AAAK). See what past versions of yourself recorded — your journal across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name — each agent gets their own diary wing",
                },
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent entries to read (default: 10)",
                },
                "wing": {
                    "type": "string",
                    "description": "Wing to read diary entries from (optional). If omitted, reads from wing_{agent_name}.",
                },
            },
            "required": ["agent_name"],
        },
        "handler": tool_diary_read,
    },
    "mempalace_hook_settings": {
        "description": (
            "Get or set hook behavior. silent_save: True = save directly "
            "(no MCP clutter), False = legacy blocking. desktop_toast: "
            "True = show desktop notification. Call with no args to view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "silent_save": {
                    "type": "boolean",
                    "description": "True = silent direct save, False = blocking MCP calls",
                },
                "desktop_toast": {
                    "type": "boolean",
                    "description": "True = show desktop toast via notify-send",
                },
            },
        },
        "handler": tool_hook_settings,
    },
    "mempalace_memories_filed_away": {
        "description": "Check if a recent palace checkpoint was saved. Returns message count and timestamp.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_memories_filed_away,
    },
    "mempalace_reconnect": {
        "description": (
            "Force reconnect to the palace database. Use after external scripts or CLI commands"
            " modified the palace directly, which can leave the in-memory HNSW index stale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
        "handler": tool_reconnect,
    },
}


SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]


def _internal_tool_error(req_id, tool_name: str, exc: BaseException = None) -> dict:
    logger.exception(f"Tool error in {tool_name}")
    error: dict = {"code": -32000, "message": "Internal tool error"}
    if exc is not None:
        error["data"] = {
            "error_class": type(exc).__name__,
            "message": str(exc),
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": error,
    }


def handle_request(request):
    global _last_request_time
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    _last_request_time = time.monotonic()
    method = request.get("method") or ""
    params = request.get("params") or {}
    req_id = request.get("id")

    # Daemon-strict: forward the whole envelope to palace-daemon's /mcp
    # proxy. Single chokepoint here is functionally equivalent to per-
    # handler gates (initialize, tools/list, tools/call all flow
    # through) and avoids 30+ duplicated branches inside the TOOLS
    # dispatch below. Notifications skip both the daemon and local —
    # JSON-RPC spec says they never get a response.
    if _daemon_strict():
        if method.startswith("notifications/") or req_id is None:
            return None
        return _forward_to_daemon(request)

    if method == "initialize":
        client_version = params.get("protocolVersion", SUPPORTED_PROTOCOL_VERSIONS[-1])
        negotiated = (
            client_version
            if client_version in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mempalace", "version": __version__},
            },
        }
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method.startswith("notifications/"):
        # Notifications (no id) never get a response per JSON-RPC spec
        return None
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {"name": n, "description": t["description"], "inputSchema": t["input_schema"]}
                    for n, t in TOOLS.items()
                ]
            },
        }
    elif method == "resources/list":
        # MCP spec optional method. We don't expose any resources — the
        # palace surfaces drawers via the tools/call interface, not via
        # the resource model. Return an empty list rather than -32601 so
        # clients that probe on connect (e.g. OpenCode 1.15.x) don't log
        # an ERROR every session. (#-32601-noise / fork follow-up.)
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
    elif method == "prompts/list":
        # MCP spec optional method. We don't expose any prompts. Same
        # rationale as resources/list above — quiet client probes.
        return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}
    elif method == "tools/call":
        if not isinstance(params, dict) or "name" not in params:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Invalid params: 'name' is required for tools/call",
                },
            }
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        # Whitelist arguments to declared schema properties only.
        # Prevents callers from spoofing internal params like added_by/source_file.
        # Skip filtering if handler explicitly accepts **kwargs (pass-through).
        # Default to filtering on inspect failure (safe fallback).
        import inspect

        schema_props = TOOLS[tool_name]["input_schema"].get("properties", {})
        try:
            handler = TOOLS[tool_name]["handler"]
            sig = inspect.signature(handler)
            accepts_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except (ValueError, TypeError):
            accepts_var_keyword = False
        if not accepts_var_keyword:
            # An unknown kwarg here is almost always a wrong parameter *name*
            # (e.g. text= instead of content=). Silently dropping it makes the
            # cause surface only indirectly as a later "Missing required 'X'",
            # so name it explicitly — symmetric with the missing-required path
            # below. wait_for_previous is an internal transport kwarg in no
            # tool schema; it is popped before dispatch further down, so it
            # must not be reported as unknown here.
            unknown = [k for k in tool_args if k not in schema_props and k != "wait_for_previous"]
            if unknown:
                quoted = ", ".join(f"'{k}'" for k in unknown)
                word = "parameter" if len(unknown) == 1 else "parameters"
                logger.debug("Tool %s: unknown %s %s", tool_name, word, quoted)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Unknown {word} {quoted} for tool {tool_name}",
                    },
                }
            tool_args = {k: v for k, v in tool_args.items() if k in schema_props}
        # Coerce argument types based on input_schema.
        # MCP JSON transport may deliver integers as floats or strings;
        # ChromaDB and Python slicing require native int.
        for key, value in list(tool_args.items()):
            prop_schema = schema_props.get(key, {})
            declared_type = prop_schema.get("type")
            try:
                if declared_type == "integer" and not isinstance(value, int):
                    tool_args[key] = int(value)
                elif declared_type == "number" and not isinstance(value, (int, float)):
                    tool_args[key] = float(value)
            except (ValueError, TypeError):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Invalid value for parameter '{key}'"},
                }
        tool_args.pop("wait_for_previous", None)
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                    ]
                },
            }
        except TypeError as e:
            # Qualname match prevents leaking internal helper/param names raised
            # inside the handler body — see test_handler_internal_signature_shape_stays_generic.
            msg = str(e)
            handler = TOOLS[tool_name]["handler"]
            handler_qn = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", "")
            # Qualname can include "<locals>" for nested defs and "<lambda>"
            # for lambdas — accept Python's TypeError emit verbatim.
            m_missing = re.match(
                r"^([\w\.<>]+)\(\) missing \d+ required "
                r"(?:positional |keyword-only )?arguments?: (.+)$",
                msg,
            )
            if m_missing and m_missing.group(1) == handler_qn:
                names = re.findall(r"'(\w+)'", m_missing.group(2))
                if names:
                    quoted = ", ".join(f"'{n}'" for n in names)
                    word = "parameter" if len(names) == 1 else "parameters"
                    logger.debug("Tool %s: missing required %s %s", tool_name, word, quoted)
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Missing required {word} {quoted} for tool {tool_name}",
                        },
                    }
            return _internal_tool_error(req_id, tool_name, e)
        except Exception as exc:
            return _internal_tool_error(req_id, tool_name, exc)

    # Notifications (missing id) must never get a response
    if req_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _restore_stdout():
    """Restore real stdout for MCP JSON-RPC output (see issue #225)."""
    global _REAL_STDOUT, _REAL_STDOUT_FD
    if _REAL_STDOUT_FD is not None:
        try:
            os.dup2(_REAL_STDOUT_FD, 1)
            os.close(_REAL_STDOUT_FD)
        except OSError:
            pass
        _REAL_STDOUT_FD = None
    sys.stdout = _REAL_STDOUT


_WARMUP_TRUTHY = {"1", "true", "yes", "on"}
_WARMUP_FALSY = {"", "0", "false", "no", "off"}
# Sentinel text for the warmup query. Distinctive so it cannot semantically
# match real drawer content (e.g. a palace containing notes about "warmup"
# routines) and is greppable in chromadb debug logs if the team ever adds
# request instrumentation. Single non-empty string is enough to trigger
# ChromaDB's ONNXMiniLM_L6_V2.__call__ → _download_model_if_not_exists +
# InferenceSession.
_WARMUP_PROBE_TEXT = "__mempalace_warmup_probe__"


def _describe_device_safe() -> str:
    """Return ``embedding.describe_device()`` value or ``"unknown"`` on failure.

    Used only inside warmup-failure log lines; the import is deferred so
    that an embedding-stack import error cannot itself crash the warmup
    diagnostic path.
    """
    try:
        from .embedding import describe_device

        return describe_device()
    except Exception:  # fail-soft: see docstring — log-message helper must not crash
        return "unknown"


def _maybe_eager_warmup_embedder() -> None:
    """Pre-load embedder + HNSW segment at startup when ``MEMPALACE_EAGER_WARMUP`` is truthy.

    The first MCP tool call that touches chromadb (``diary_write``,
    ``add_drawer``, ``search``) otherwise pays two compounding cold-load
    costs that together can exceed the MCP client timeout and surface as
    ``-32000`` "Internal tool error" with no recoverable trace on the
    agent side (#1495):

    1. ONNX/CoreML embedder init in :func:`mempalace.embedding.get_embedding_function`
       (5–30s on first inference; ChromaDB's ``ONNXMiniLM_L6_V2.__call__``
       triggers ``_download_model_if_not_exists`` + ``InferenceSession``).
    2. HNSW segment cold-load (reading ``data_level0.bin`` into RAM on
       first collection operation; seconds on palaces of 50k+ drawers).

    Warming via :func:`_get_collection`'s collection-then-query path
    covers BOTH in a single startup-phase call — mirroring the reporter's
    proposal in #1495 — so users with large existing palaces see the
    same benefit as users on the embedder-only cost path.

    Truthy parsing accepts ``1/true/yes/on`` (case-insensitive); falsy
    set ``0/false/no/off`` and empty/whitespace are silently off; any
    other value logs a warning and stays off so typos like ``tru`` do
    not silently disable the feature.

    Fresh-install guard (pre-check, NOT a catch): ``_get_collection``'s
    retry layer absorbs ``_ChromaNotFoundError`` and returns ``None`` while
    also materialising ``chroma.sqlite3`` on disk via the chromadb client
    constructor. To preserve the documented "no palace yet → nothing to
    warm" contract WITHOUT writing palace scaffolding before
    ``mempalace init`` (which would violate CLAUDE.md "Incremental only"),
    we test for ``chroma.sqlite3`` ourselves before touching the chromadb
    client. Operators who set ``MEMPALACE_EAGER_WARMUP=1`` in their MCP
    config and launch the server before running ``mempalace init`` get a
    single INFO line and no on-disk side effect.

    Fail-soft beyond the fresh-install pre-check:

    * **Backend open failure** (palace path misconfigured, file locked,
      corrupted HNSW that ``quarantine_stale_hnsw`` cannot recover) →
      log exception with device + palace context and return. The next
      embedding-requiring call sees the same fail mode it would have
      without warmup.
    * **`_get_collection` retried and returned None** → palace exists
      but chromadb cannot open the collection (rare; usually a stale
      sqlite + segment-files mismatch surfaced by `_get_client` rebuild).
      A warning suffices because the retry layer already wrote two
      tracebacks with the underlying chromadb error class.
    * **Query failure** (network failure during ONNX model download,
      provider init crash, runtime decoder error) → log exception with
      device + palace context and return. Same fail-mode preservation.

    Note: on an existing palace with an empty collection (created via
    ``mempalace init`` but never written to), ``col.query`` succeeds but
    returns ``{'ids': [[]]}`` without reading any HNSW segment — the
    embedder warms but there is no HNSW segment to load. The success log
    still says ``embedder + HNSW ready`` because the no-HNSW-segment case
    has zero cold-load cost; nothing was skipped that the first real tool
    call would have paid.
    """
    raw = os.environ.get("MEMPALACE_EAGER_WARMUP", "").strip().lower()
    if raw in _WARMUP_FALSY:
        return
    if raw not in _WARMUP_TRUTHY:
        logger.warning(
            "MEMPALACE_EAGER_WARMUP=%r is not recognized (use one of %s); warmup disabled",
            raw,
            sorted(_WARMUP_TRUTHY | (_WARMUP_FALSY - {""})),
        )
        return
    palace_path = _config.palace_path
    db_path = os.path.join(palace_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        # Pre-check (NOT a try/except on _ChromaNotFoundError, which never
        # propagates out of _get_collection — see docstring). No palace
        # file means nothing to warm AND avoids the chromadb-client
        # side effect of materialising the palace dir.
        logger.info(
            "MEMPALACE_EAGER_WARMUP=%s: no palace at %s — nothing to warm",
            raw,
            palace_path,
        )
        return
    # Cache device once: _describe_device_safe re-imports embedding stack
    # each call, which is wasteful inside a function that already paid
    # that cost via the warmup query below.
    device = _describe_device_safe()
    try:
        col = _get_collection(create=False)
    except Exception as exc:  # fail-soft per docstring — broad on purpose
        logger.exception(
            "MEMPALACE_EAGER_WARMUP=%s: collection open failed (palace=%s, device=%s, error=%s)",
            raw,
            palace_path,
            device,
            type(exc).__name__,
        )
        return
    if col is None:
        logger.warning(
            "MEMPALACE_EAGER_WARMUP=%s: _get_collection returned None for palace=%s — see prior log lines",
            raw,
            palace_path,
        )
        return
    try:
        col.query(query_texts=[_WARMUP_PROBE_TEXT], n_results=1)
    except Exception as exc:  # fail-soft per docstring — broad on purpose
        logger.exception(
            "MEMPALACE_EAGER_WARMUP=%s: warmup query failed (palace=%s, device=%s, error=%s)",
            raw,
            palace_path,
            device,
            type(exc).__name__,
        )
    else:
        logger.info(
            "MEMPALACE_EAGER_WARMUP=%s: embedder + HNSW ready (palace=%s, device=%s)",
            raw,
            palace_path,
            device,
        )


def _start_idle_exit_watchdog() -> None:
    """Start a daemon thread that exits the process after an idle period.

    When no request has been handled for ``MEMPALACE_MCP_IDLE_HOURS``
    (default 8 h), the thread terminates the process so that stale MCP
    servers from ended Claude Code sessions do not accumulate ChromaDB /
    HNSW file handles on Windows (#1552).

    Set ``MEMPALACE_MCP_IDLE_HOURS=0`` to disable the watchdog.
    """
    timeout = _mcp_idle_timeout_secs()
    if timeout <= 0:
        return
    check_interval = min(60.0, timeout / 4)

    def _watchdog() -> None:
        while True:
            time.sleep(check_interval)
            idle = time.monotonic() - _last_request_time
            if idle >= timeout:
                logger.info(
                    "MCP server idle for %.1f h (limit %.1f h); exiting to release file handles.",
                    idle / 3600,
                    timeout / 3600,
                )
                os._exit(0)

    t = threading.Thread(target=_watchdog, name="mcp-idle-watchdog", daemon=True)
    t.start()


def main():
    """MCP server entry point for the ``mempalace-mcp`` console script.

    Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
    any subprocess this server spawns inherits a clean env. Host
    applications that call ``main()`` programmatically should be aware
    that the parent process loses ``PYTHONPATH`` as well. Library imports
    (``import mempalace.searcher`` from a host app) do NOT trigger this
    side effect; only the CLI/MCP entry points pop the env var.
    """
    # Drop leaked PYTHONPATH so any subprocess this server spawns starts
    # with a clean env. The sys.path filter in mempalace/__init__.py
    # already protects this process from the same ABI mismatch; here we
    # extend the protection to children.
    os.environ.pop("PYTHONPATH", None)
    _restore_stdout()
    # Force UTF-8 on stdio. MCP JSON-RPC is UTF-8, but Python on Windows
    # defaults stdin/stdout to the system codepage (e.g. cp1251), which
    # corrupts non-ASCII payloads and surfaces as generic -32000 errors on
    # Cyrillic/CJK content. See PEP 540.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
    logger.info("MemPalace MCP Server starting...")
    # Issue #49: always log the routing decision at startup. Previously only
    # the daemon-strict path logged, so an unset/unpropagated env var
    # silently fell back to local — the exact failure mode that wedged
    # familiar.realm.watch on 2026-05-10 (writes succeeded locally, daemon
    # view showed nothing). Now both paths announce themselves audibly.
    _cfg = MempalaceConfig()
    if _daemon_strict():
        _src = "env" if os.environ.get("PALACE_DAEMON_URL", "").strip() else "config"
        logger.info(
            "mempalace-mcp: routing → daemon @ %s (source: %s); "
            "skipping local-palace startup probes.",
            _cfg.daemon_url,
            _src,
        )
    else:
        logger.info("mempalace-mcp: routing → local palace @ %s", _cfg.palace_path)
        # Pre-flight: probe HNSW capacity before any tool call so the warning
        # is visible at startup rather than on first use (#1222). Pure
        # filesystem read; never opens a chromadb client. Skipped in
        # daemon-strict mode — the daemon owns its palace's capacity.
        _refresh_vector_disabled_flag()
        # Opt-in: pre-load the embedder so the first chromadb-write tool call
        # does not pay the ONNX/CoreML cold-load tax under the MCP client
        # timeout (upstream #1495). Default off — preserves current startup
        # latency. Skipped in daemon-strict mode for the same reason as
        # the capacity probe above.
        _maybe_eager_warmup_embedder()
        # Idle auto-exit: release ChromaDB file handles from stale servers
        # that outlived their Claude Code session (#1552). Local-mode only —
        # daemon-strict mode never opens a local Chroma client.
        _start_idle_exit_watchdog()
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
