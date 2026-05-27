#!/usr/bin/env python3
"""
MemPalace — Give your AI a memory. No API key required.

Three ways to ingest:
  Projects:      mempalace mine ~/projects/my_app                  (code, docs, notes)
  Conversations: mempalace mine <convo-dir> --mode convos          (Claude Code, Claude.ai, ChatGPT, Slack exports)
  Documents:     mempalace mine <docs-dir> --mode extract          (PDF, DOCX, PPTX, XLSX, RTF, EPUB — requires mempalace[extract])

Same palace. Same search. Different ingest strategies.

Commands:
    mempalace init <dir>                  Detect rooms from folder structure
    mempalace split <dir>                 Split concatenated mega-files into per-session files
    mempalace mine <dir>                  Mine project files (default)
    mempalace mine <dir> --mode convos    Mine conversation exports
    mempalace mine <dir> --mode extract   Mine binary office documents (PDF/DOCX/etc.)
    mempalace search "query"              Find anything, exact words
    mempalace mcp                         Show MCP setup command
    mempalace wake-up                     Show L0 + L1 wake-up context
    mempalace wake-up --wing my_app       Wake-up for a specific project
    mempalace status                      Show what's been filed
    mempalace mined                       List mined source files grouped by wing
    mempalace purge --source-file <path>  Remove drawers mined from a specific file

Examples:
    mempalace init ~/projects/my_app
    mempalace mine ~/projects/my_app
    mempalace mine ~/.claude/projects/-Users-you-Projects-my_app --mode convos --wing my_app
    mempalace search "why did we switch to GraphQL"
    mempalace search "pricing discussion" --wing my_app --room costs
"""

from __future__ import annotations

import os
import sys
import json
import shlex
import argparse
from pathlib import Path

from .config import MempalaceConfig
from .corpus_origin import detect_origin_heuristic, detect_origin_llm
from .llm_client import LLMError, get_provider
from .version import __version__


# ==================== AGENT-SHAPED OUTPUT (issue #44) ====================
# ``--json`` / ``-j`` flips command output from prose to a stable JSON
# document on stdout, intended for shell pipelines and non-MCP agents.
# ``--quiet`` / ``-q`` suppresses decorative chrome (headers, divider
# lines, the daemon-routing announcement on stderr). When stdout is not
# a TTY we default to quiet mode so piped output stays clean — explicit
# ``--quiet`` / ``--json`` still override (see ``_resolve_quiet``).
#
# Exit codes (per issue #44):
#   0  success
#   1  no results / search returned empty
#   2  palace unavailable (daemon unreachable, palace missing, etc.)
#   64 bad args (argparse default for parse errors)


def _resolve_quiet(args) -> bool:
    """True when chrome should be suppressed.

    Quiet is on whenever any of these are true:
      * ``--quiet`` / ``-q`` was passed
      * ``--json`` / ``-j`` was passed (JSON output is always machine
        consumption — chrome would corrupt the document)
      * ``sys.stdout`` is not a TTY (piped or redirected)
    """
    if getattr(args, "json", False):
        return True
    if getattr(args, "quiet", False):
        return True
    try:
        return not sys.stdout.isatty()
    except (AttributeError, ValueError):
        # ``sys.stdout`` may be replaced by a non-stream in some test
        # harnesses; treat that as "no TTY" so we err toward clean output.
        return True


def _emit_json(payload: dict) -> None:
    """Write a JSON document to stdout with a trailing newline.

    Centralised so every JSON-emitting command uses the same formatting
    (sort_keys=False to preserve insertion order, indent=2 for human
    readability when the agent prints what it just received).
    """
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# ==================== DAEMON ROUTING ====================
# When ``PALACE_DAEMON_URL`` is set, palace-daemon is the single writer
# for the canonical palace and high-traffic CLI subcommands route there
# instead of opening a local chromadb client. Mirrors the gate already
# in ``mempalace.hooks_cli`` (mining side) and
# ``mempalace.mcp_server`` (MCP dispatch). Currently routes ``status``,
# ``search``, and ``mine``; the remaining subcommands (``repair``,
# ``export``, ``sweep``, ``init``) still need on-host filesystem access
# and stay local. When the daemon URL is unset, all paths run locally
# unchanged.

_DAEMON_TIMEOUT_DEFAULT = 120  # seconds; tune via PALACE_MCP_TIMEOUT


class DaemonError(RuntimeError):
    """Raised when a daemon HTTP call fails or returns a JSON-RPC error."""


def _print_retired_local_palace_or_default(palace_path: str) -> None:
    """If the user's default palace is missing AND a RETIRED marker
    exists, print the marker's content as the not-found message — so
    agents see "set PALACE_DAEMON_URL" instead of "Run: mempalace init".

    Falls through to the legacy "Run: mempalace init" message in every
    other case (palace literally absent on a fresh install, etc.).
    """
    palace_root = os.path.expanduser("~/.mempalace")
    marker = os.path.join(palace_root, "RETIRED")
    default_path = os.path.join(palace_root, "palace")
    is_default = os.path.abspath(palace_path) == os.path.abspath(default_path)
    if is_default and os.path.exists(marker):
        try:
            with open(marker) as f:
                note = f.read().rstrip()
        except OSError:
            note = "(marker unreadable)"
        print(f"\n  Local palace at {default_path} is RETIRED.\n")
        for line in note.splitlines():
            print(f"  {line}")
        return
    print(f"\n  No palace found at {palace_path}")
    print("  Run: mempalace init <dir> then mempalace mine <dir>")


def _daemon_strict() -> bool:
    """True when daemon routing is on and strict mode is enabled.

    Resolution: ``MempalaceConfig.daemon_strict`` — env var
    ``PALACE_DAEMON_URL`` wins as the source of the URL, with
    ``~/.mempalace/config.json`` key ``"daemon_url"`` as fallback (see
    issue #49). Set ``PALACE_DAEMON_STRICT=0`` or
    ``"daemon_strict": false`` in config to force the local path.
    """
    return MempalaceConfig().daemon_strict


def _daemon_url() -> str:
    return MempalaceConfig().daemon_url or ""


def _daemon_timeout() -> int:
    raw = os.environ.get("PALACE_MCP_TIMEOUT", str(_DAEMON_TIMEOUT_DEFAULT))
    try:
        return int(raw)
    except ValueError:
        return _DAEMON_TIMEOUT_DEFAULT


def _call_daemon_tool(name: str, arguments: dict) -> dict:
    """JSON-RPC ``tools/call`` against the daemon's ``/mcp`` endpoint.

    Returns the inner tool result already parsed from the JSON text
    payload (the ``content[0].text`` envelope MCP wraps every tool
    response in). Raises :class:`DaemonError` on network failure or a
    JSON-RPC error response — the CLI must surface failures to the
    caller, never silently fall back to local (that would re-introduce
    the split-brain that daemon-strict was created to prevent).
    """
    import urllib.error
    import urllib.request

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    headers = {"content-type": "application/json"}
    api_key = os.environ.get("PALACE_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        f"{_daemon_url()}/mcp",
        data=json.dumps(request).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_daemon_timeout()) as resp:
            body = resp.read()
        envelope = json.loads(body.decode("utf-8", errors="replace"))
    except (urllib.error.URLError, ConnectionError, OSError, json.JSONDecodeError) as e:
        raise DaemonError(f"daemon unreachable at {_daemon_url()}: {e}") from e
    if "error" in envelope:
        err = envelope["error"]
        raise DaemonError(f"daemon error {err.get('code')}: {err.get('message')}")
    content = (envelope.get("result") or {}).get("content") or []
    if not content:
        return {}
    text = content[0].get("text") or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Non-JSON tool output (rare). Return as-is for the caller to
        # decide what to do.
        return {"_raw": text}


def _call_daemon_rest(path: str, params: dict | None = None) -> dict:
    """GET a daemon REST endpoint directly — no MCP envelope, no AGE locks.

    Falls back to _call_daemon_tool on non-2xx (endpoint might not exist
    on older daemons). Raises DaemonError on network failure.
    """
    import urllib.error
    import urllib.request
    import urllib.parse

    url = f"{_daemon_url()}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {}
    api_key = os.environ.get("PALACE_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 401, 403):
            return None  # endpoint missing or auth mismatch — caller falls back to MCP
        raise DaemonError(f"daemon REST {path} failed ({e.code}): {e.reason}") from e
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise DaemonError(f"daemon unreachable at {_daemon_url()}: {e}") from e


def _post_daemon_rest(path: str, body: dict) -> dict:
    """POST to a daemon REST endpoint — for hybrid/keyword/age-fused search.

    Returns the parsed JSON response. Returns None on 404 (endpoint not
    available on older daemons). Raises DaemonError on network failure.
    """
    import urllib.error
    import urllib.request

    url = f"{_daemon_url()}{path}"
    headers = {"content-type": "application/json"}
    api_key = os.environ.get("PALACE_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_daemon_timeout()) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise DaemonError(f"daemon REST {path} failed ({e.code}): {e.reason}") from e
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise DaemonError(f"daemon unreachable at {_daemon_url()}: {e}") from e


def _post_daemon_mine_cli(directory: str, wing: str, mode: str = "convos") -> bool:
    """POST a mine request to the daemon's ``/mine`` endpoint.

    CLI-shaped variant of :func:`mempalace.hooks_cli._post_daemon_mine`:
    on failure, prints to stderr and returns ``False`` so the caller can
    ``sys.exit(1)``. Hooks_cli's version logs to a file and swallows
    silently because a missed-mine isn't worth crashing a hook over;
    here, the user invoked `mempalace mine` and expects to see errors.
    """
    import urllib.error
    import urllib.request

    headers = {"content-type": "application/json"}
    api_key = os.environ.get("PALACE_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        f"{_daemon_url()}/mine",
        data=json.dumps({"dir": directory, "wing": wing, "mode": mode}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_daemon_timeout()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        print(f"  Daemon mine accepted: {body[:200]}")
        return True
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        print(f"  ERROR: daemon mine failed: {e}", file=sys.stderr)
        return False


def _print_daemon_status(data: dict) -> None:
    """Format ``mempalace_status`` JSON for human reading.

    The daemon's status tool returns a flat ``wings: {name: count}``
    dict (not the wing×room nested shape that ``miner.status`` builds
    from raw metadata). We print the daemon's shape rather than
    over-fetching to reconstruct the local layout — the daemon URL is
    visible in the header so the reader knows which view they're
    looking at.
    """
    import json as _json

    total = data.get("total_drawers", 0)
    wings = data.get("wings") or {}
    print(f"\n{'=' * 55}")
    print(f"  MemPalace Status — {total} drawers")
    print(f"  via palace-daemon @ {_daemon_url()}")
    print(f"{'=' * 55}\n")
    if isinstance(wings, dict) and wings:
        for wing, count in sorted(wings.items(), key=lambda kv: kv[1], reverse=True):
            if isinstance(count, dict):
                # Defensive: if a future daemon returns wing×room nested,
                # still render something useful.
                inner = count.get("total", sum((count.get("rooms") or {}).values()))
                print(f"  WING: {wing:30} {inner:>6} drawers")
            else:
                print(f"  WING: {wing:30} {count:>6} drawers")
    elif "error" in data:
        # Render daemon errors with the same shape as _print_daemon_search:
        # error → message → hint. Important for "palace.backend_unreachable"
        # so the user sees "start the postgres container" instead of the
        # legacy "Run: mempalace init <dir>" hint that was actively misleading
        # during the 2026-05-17 power-event diagnosis.
        print(f"  daemon reported error: {data['error']}")
        if "message" in data:
            print(f"  {data['message']}")
        if "hint" in data:
            print(f"  {data['hint']}")
    else:
        # Surface unexpected shapes verbatim.
        print(_json.dumps(data, indent=2))
    print(f"\n{'=' * 55}\n")


# ── enhanced search output (#191) ──────────────────────────────────────
# Three human-facing formats share a single hit shape from the daemon
# (or from ``searcher.search`` on local fallback): ``wing``, ``room``,
# ``source_file``, ``similarity``, ``bm25_score``, ``matched_via``,
# ``text``, ``created_at``, optional ``tags``. ``table`` is the default
# enhanced view (multi-line per hit with metadata + a relevance bar
# proportional to cosine similarity). ``compact`` collapses each hit to
# a single line for fast scanning. ``full`` is identical to ``table``
# but skips content truncation so long drawers render in full.
#
# ANSI colour is opt-in via ``_search_use_color``: only when stdout is a
# TTY, ``--quiet`` / ``--json`` is off, and the ``NO_COLOR`` env var is
# unset (per https://no-color.org). Honouring NO_COLOR means CI logs
# and accessibility users get plain text without extra flags.

_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_BOLD = "\033[1m"
_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_MAGENTA = "\033[35m"

# Default content truncation budget for ``table`` format. Long drawers
# get the first N lines with a ``... +M more lines`` marker so the
# terminal stays readable on a 5-result page. ``full`` skips truncation.
_SEARCH_TABLE_MAX_LINES = 12


def _search_use_color(quiet: bool) -> bool:
    """True when ANSI colour codes are safe to emit.

    Suppressed for ``--quiet`` / ``--json`` callers (machine consumption),
    when stdout is not a TTY (piped/redirected output), and when the
    ``NO_COLOR`` env var is set (https://no-color.org convention).
    """
    if quiet:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _color(text: str, code: str, use_color: bool) -> str:
    """Wrap ``text`` in an ANSI colour code only when ``use_color`` is True."""
    if not use_color or not text:
        return text
    return f"{code}{text}{_ANSI_RESET}"


def _relevance_bar(similarity, width: int = 16) -> str:
    """Render a horizontal bar proportional to cosine ``similarity``.

    Mirrors ``_stats_bar``'s Unicode block fill so search and stats
    look like siblings. Returns an empty string when similarity is
    unavailable (e.g. BM25-only hit) so the caller can fall back to
    a numeric BM25 line instead of an empty bar.
    """
    if similarity is None:
        return ""
    try:
        ratio = max(0.0, min(1.0, float(similarity)))
    except (TypeError, ValueError):
        return ""
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def _format_hit_metadata(hit: dict) -> str:
    """Inline metadata line — source file + creation timestamp.

    Both fields are best-effort. ``created_at`` is the ``filed_at``
    timestamp the daemon attaches at write time (see #184 fork-changes
    entry on ``filed_at``); missing values render as ``unknown`` rather
    than an empty placeholder so the column alignment stays readable.
    """
    source = hit.get("source_file") or "?"
    created = hit.get("created_at") or "unknown"
    return f"{source}  ·  {created}"


def _truncate_content(text: str, max_lines: int) -> tuple:
    """Return (shown_lines, truncated_count) for ``table`` format.

    Preserves newlines (we never collapse multi-line drawers) and
    appends a ``+N more lines`` marker when truncated so the reader
    knows there's more behind the ellipsis. ``--format full`` skips
    this helper entirely.
    """
    if not text:
        return [], 0
    lines = text.strip().split("\n")
    if max_lines <= 0 or len(lines) <= max_lines:
        return lines, 0
    return lines[:max_lines], len(lines) - max_lines


def _print_search_header(
    query: str, data: dict, wing, room, warnings, quiet: bool, use_color: bool
) -> None:
    """Header chrome for ``table`` / ``full`` formats. Suppressed under --quiet."""
    if quiet:
        return
    print(f"\n{'=' * 60}")
    print(f'  Results for: "{_color(query, _ANSI_BOLD, use_color)}"')
    if wing:
        print(f"  Wing: {_color(wing, _ANSI_CYAN, use_color)}")
    if room:
        print(f"  Room: {_color(room, _ANSI_MAGENTA, use_color)}")
    if data.get("available_in_scope") is not None:
        print(f"  Scope has: {data['available_in_scope']} drawers matching filter")
    for w in warnings:
        print(f"  ! {w}")
    print(f"  via palace-daemon @ {_daemon_url()}")
    print(f"{'=' * 60}\n")


def _print_hit_table(index: int, hit: dict, *, full: bool, use_color: bool) -> None:
    """Render a single hit in ``table`` (default) or ``full`` format."""
    wing = _color(hit.get("wing", "?"), _ANSI_CYAN, use_color)
    room = _color(hit.get("room", "?"), _ANSI_MAGENTA, use_color)
    sim = hit.get("similarity")
    bm25 = hit.get("bm25_score")

    print(f"  [{index}] {wing} / {room}")

    bar = _relevance_bar(sim)
    if bar:
        # Cosine bar is the primary signal; show BM25 inline when both
        # exist so hybrid hits don't lose their second score.
        sim_str = f"{sim:.3f}" if isinstance(sim, (int, float)) else str(sim)
        bm25_suffix = f"  bm25={bm25}" if bm25 is not None else ""
        print(f"      {_color(bar, _ANSI_GREEN, use_color)}  cosine={sim_str}{bm25_suffix}")
    elif bm25 is not None:
        # BM25-only hit (rare; vector store missing or filter forced it).
        matched_via = hit.get("matched_via", "drawer")
        print(f"      BM25: {bm25}  (matched_via: {matched_via})")

    meta = _format_hit_metadata(hit)
    print(f"      {_color(meta, _ANSI_DIM, use_color)}")

    tags = hit.get("tags")
    if tags:
        tag_str = ", ".join(str(t) for t in tags)
        print(f"      {_color('tags:', _ANSI_DIM, use_color)} {tag_str}")

    print()
    max_lines = 0 if full else _SEARCH_TABLE_MAX_LINES
    shown, hidden = _truncate_content(hit.get("text") or "", max_lines)
    for line in shown:
        print(f"      {line}")
    if hidden:
        marker = f"... +{hidden} more lines (use --format full to see all)"
        print(f"      {_color(marker, _ANSI_DIM, use_color)}")
    print()
    print(f"  {'─' * 56}")


def _print_hit_compact(index: int, hit: dict, *, use_color: bool) -> None:
    """One-line-per-hit rendering for ``--format compact``.

    Format: ``[N] wing/room  ▰▰▰▰░░░░░░░░  0.812  source  preview``.
    Preview is the first non-empty line of the drawer, truncated to
    fit a reasonable terminal width (~110 cols). Newlines collapse
    into a single space so the preview never wraps.
    """
    wing = _color(hit.get("wing", "?"), _ANSI_CYAN, use_color)
    room = _color(hit.get("room", "?"), _ANSI_MAGENTA, use_color)
    sim = hit.get("similarity")
    bar = _relevance_bar(sim, width=12)
    sim_str = f"{sim:.3f}" if isinstance(sim, (int, float)) else "  -  "
    bar_part = _color(bar, _ANSI_GREEN, use_color) if bar else "  -  "
    text = (hit.get("text") or "").strip()
    first_line = next((ln for ln in text.split("\n") if ln.strip()), "")
    if len(first_line) > 70:
        first_line = first_line[:67] + "…"
    source = hit.get("source_file") or "?"
    if len(source) > 28:
        source = "…" + source[-27:]
    print(
        f"  [{index}] {wing}/{room}  {bar_part}  {sim_str}  "
        f"{_color(source, _ANSI_DIM, use_color)}  {first_line}"
    )


def _print_daemon_search(
    query: str,
    data: dict,
    wing: str = None,
    room: str = None,
    *,
    fmt: str = "table",
    quiet: bool = False,
) -> None:
    """Format ``mempalace_search`` JSON; mirrors ``searcher.search`` output.

    ``fmt`` selects ``table`` (default — multi-line, metadata + relevance
    bar + truncated content), ``compact`` (one line per hit), or ``full``
    (table layout with no content truncation). ``quiet`` suppresses the
    header chrome and ANSI colour, intended for piped output and
    ``--quiet``/``--json`` callers.
    """
    if "error" in data and not data.get("results"):
        print(f"\n  {data['error']}")
        if "hint" in data:
            print(f"  {data['hint']}")
        return

    hits = data.get("results") or []
    warnings = data.get("warnings") or []

    if not hits:
        print(f'\n  No results found for: "{query}"')
        for w in warnings:
            print(f"  ! {w}")
        return

    use_color = _search_use_color(quiet)
    _print_search_header(query, data, wing, room, warnings, quiet, use_color)

    if fmt == "compact":
        for i, hit in enumerate(hits, 1):
            _print_hit_compact(i, hit, use_color=use_color)
        if not quiet:
            print()
        return

    full = fmt == "full"
    for i, hit in enumerate(hits, 1):
        _print_hit_table(i, hit, full=full, use_color=use_color)
    if not quiet:
        print()


_MEMPALACE_PROJECT_FILES = ("mempalace.yaml", "entities.json")

# Pass 0 corpus-origin sampling caps. Tier 1 reads FULL file content (no
# front-bias sampling) but bounds total memory on enormous corpora. Tier 2
# trims to a smaller view because LLM context windows are finite.
_PASS_ZERO_MAX_FILES = 30
_PASS_ZERO_PER_FILE_CAP = 100_000  # 100KB per file is generous for prose
_PASS_ZERO_TOTAL_CAP = 5_000_000  # 5MB total ceiling — bounds memory
_PASS_ZERO_LLM_PER_SAMPLE = 2_000  # for Tier 2 LLM call only
_PASS_ZERO_LLM_MAX_SAMPLES = 20  # caps the LLM-tier sample count


def _gather_origin_samples(project_dir) -> list:
    """Collect Tier-1 samples for corpus-origin detection.

    Reads FULL file content (capped at ``_PASS_ZERO_PER_FILE_CAP`` per file
    and ``_PASS_ZERO_TOTAL_CAP`` overall). No front-bias sampling — AI
    signal that lives past the first N chars of a file must still trip
    detection, so we read the whole file up to the cap.

    Skips mempalace's own per-project artifacts (``entities.json``,
    ``mempalace.yaml``) so a re-run of ``mempalace init`` produces the
    same classification result it did on the first run. Without this
    filter, the first run writes entities.json into the corpus, the
    second run picks it up as a sample, and the Tier-1 density math
    drifts (different total_chars). That makes init non-idempotent.

    Returns a list of strings (one per readable file). Empty list when
    the project has no readable text.
    """
    from .entity_detector import scan_for_detection

    files = scan_for_detection(project_dir, max_files=_PASS_ZERO_MAX_FILES)
    samples: list = []
    total_chars = 0
    for filepath in files:
        if filepath.name in _MEMPALACE_PROJECT_FILES:
            continue
        if total_chars >= _PASS_ZERO_TOTAL_CAP:
            break
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read(_PASS_ZERO_PER_FILE_CAP)
        except OSError:
            continue
        if not content:
            continue
        samples.append(content)
        total_chars += len(content)
    return samples


def _trim_samples_for_llm(samples: list) -> list:
    """Reduce Tier-1 full-content samples to LLM-friendly size.

    Tier 2 hits an LLM with a finite context window — we trim each sample
    to ``_PASS_ZERO_LLM_PER_SAMPLE`` chars and cap the overall sample
    count at ``_PASS_ZERO_LLM_MAX_SAMPLES``.
    """
    return [s[:_PASS_ZERO_LLM_PER_SAMPLE] for s in samples[:_PASS_ZERO_LLM_MAX_SAMPLES]]


def _run_pass_zero(project_dir, palace_dir, llm_provider) -> dict:
    """Pass 0: detect whether the corpus is AI-dialogue and persist the
    result to ``<palace>/.mempalace/origin.json``.

    Returns the wrapped result dict (same shape as origin.json) on success,
    or ``None`` when there are no readable samples to detect from. The
    return value is what cmd_init forwards to ``discover_entities`` via
    the ``corpus_origin`` kwarg.

    File-write failures (e.g. read-only palace) are caught and reported on
    stderr; init never blocks on them.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    samples = _gather_origin_samples(project_dir)
    if not samples:
        print("  Skipping corpus-origin detection — no readable samples.")
        return None

    # Tier 1 — always runs. Cheap regex grep, no API.
    result = detect_origin_heuristic(samples)

    # Tier 2 — runs only when an LLM provider is available. The provider
    # contract is best-effort: corpus_origin internally falls back to a
    # conservative default on transport/parse failure, so we don't need a
    # try/except here, but we still keep one for any unforeseen exception.
    #
    # MERGE-FIELDS, NOT REPLACE: Tier 2's persona/user/platform extraction
    # is the whole reason to run it, but a weak local model (e.g. Ollama
    # gemma4:e4b) can return a wrong likely_ai_dialogue/confidence call
    # that overrides a confident heuristic answer. Per @igorls's review of
    # PR #1211: keep the heuristic's likely_ai_dialogue + confidence
    # (don't let a weak LLM flip a confident regex answer), and merge in
    # LLM's persona-related fields + combined evidence.
    if llm_provider is not None:
        try:
            llm_result = detect_origin_llm(_trim_samples_for_llm(samples), llm_provider)
            # Heuristic owns: likely_ai_dialogue, confidence (do NOT touch).
            # LLM contributes: primary_platform, user_name, agent_persona_names
            # (heuristic doesn't extract any of these).
            if llm_result.primary_platform:
                result.primary_platform = llm_result.primary_platform
            if llm_result.user_name:
                result.user_name = llm_result.user_name
            if llm_result.agent_persona_names:
                result.agent_persona_names = list(llm_result.agent_persona_names)
            # Combine evidence — keep both signal trails for the audit record,
            # prefixed so the on-disk origin.json says which tier produced
            # each entry. Idempotent: re-prefixing an already-tagged entry
            # is a no-op.
            tier1_prefix = "Tier-1 heuristic: "
            tier2_prefix = "Tier-2 LLM: "
            heuristic_evidence = [
                s if s.startswith(tier1_prefix) else f"{tier1_prefix}{s}"
                for s in (str(e) for e in result.evidence)
            ]
            llm_evidence = [
                s if s.startswith(tier2_prefix) else f"{tier2_prefix}{s}"
                for s in (str(e) for e in llm_result.evidence)
            ]
            result.evidence = heuristic_evidence + llm_evidence
        except Exception as exc:  # noqa: BLE001 — never block init on LLM failure
            print(f"  LLM corpus-origin tier failed ({exc}); using heuristic only.")

    wrapped = {
        "schema_version": 1,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "result": result.to_dict(),
    }

    origin_path = Path(palace_dir).expanduser() / ".mempalace" / "origin.json"
    try:
        origin_path.parent.mkdir(parents=True, exist_ok=True)
        with open(origin_path, "w", encoding="utf-8") as f:
            json.dump(wrapped, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"  Could not write {origin_path}: {exc}", file=sys.stderr)
        # Return the wrapped dict anyway so the in-memory pipeline still
        # benefits from the detection result this run.
        return wrapped

    # Banner — one line, two-space indent matching existing init style.
    res = result
    if res.likely_ai_dialogue:
        platform = res.primary_platform or "AI dialogue (platform unidentified)"
        user = res.user_name or "—"
        agents = ", ".join(res.agent_persona_names) if res.agent_persona_names else "—"
        print(f"  Detected: {platform} (user: {user}, agents: {agents})")
    else:
        print(f"  Corpus origin: not AI-dialogue (confidence: {res.confidence:.2f})")

    return wrapped


def _ensure_mempalace_files_gitignored(project_dir) -> bool:
    """If project_dir is a git repo, ensure MemPalace's per-project files
    are listed in .gitignore so they don't get committed by accident.

    Returns True if .gitignore was updated, False otherwise. Issue #185:
    `mempalace init` writes mempalace.yaml + entities.json into the
    project root, where they previously had no protection against being
    staged into git.
    """
    from pathlib import Path

    project_path = Path(project_dir).expanduser().resolve()
    if not (project_path / ".git").exists():
        return False
    gitignore = project_path / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [p for p in _MEMPALACE_PROJECT_FILES if p not in existing_lines]
    if not missing:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    block = prefix + "\n# MemPalace per-project files (issue #185)\n" + "\n".join(missing) + "\n"
    with open(gitignore, "a") as f:
        f.write(block)
    print(f"  Added {', '.join(missing)} to {gitignore.name}")
    return True


def cmd_init(args):
    import json
    from pathlib import Path
    from .entity_detector import confirm_entities
    from .project_scanner import discover_entities
    from .room_detector_local import detect_rooms_local

    # Honor --palace (issue #1313): without this, init silently ignored the
    # flag and always used ~/.mempalace. Mirror the env-var pattern used by
    # mcp_server.py so every downstream read of ``cfg.palace_path`` (Pass 0,
    # cfg.init(), the post-init mine) routes to the user-specified location.
    if getattr(args, "palace", None):
        os.environ["MEMPALACE_PALACE_PATH"] = os.path.abspath(os.path.expanduser(args.palace))

    cfg = MempalaceConfig()

    # Resolve entity-detection languages: --lang overrides config.
    lang_arg = getattr(args, "lang", None)
    if lang_arg:
        languages = [s.strip() for s in lang_arg.split(",") if s.strip()] or ["en"]
        cfg.set_entity_languages(languages)
    else:
        languages = cfg.entity_languages
    languages_tuple = tuple(languages)

    # --llm is ON by default. --no-llm is the explicit opt-out. Provider
    # precedence is unchanged (Ollama localhost first, then openai-compat,
    # then anthropic). Never block init on a missing LLM: when no provider
    # responds, print a one-line message pointing at --no-llm and fall
    # through to heuristics-only.
    llm_provider = None
    if not getattr(args, "no_llm", False):
        provider_name = getattr(args, "llm_provider", "ollama") or "ollama"
        provider_model = getattr(args, "llm_model", "gemma4:e4b") or "gemma4:e4b"
        try:
            candidate = get_provider(
                name=provider_name,
                model=provider_model,
                endpoint=getattr(args, "llm_endpoint", None),
                api_key=getattr(args, "llm_api_key", None),
            )
            ok, msg = candidate.check_available()
            if ok:
                llm_provider = candidate
                print(f"  LLM enabled: {provider_name}/{provider_model}")
                # Privacy warning (issue #24): if the configured endpoint
                # sends data off the user's machine/network, surface that
                # before init proceeds. URL-based — Ollama on localhost,
                # LM Studio on LAN, etc. won't trigger; Anthropic /
                # cloud OpenAI-compat / any non-local endpoint will.
                if candidate.is_external_service:
                    print(
                        f"  ⚠ {provider_name} is an EXTERNAL API. Your folder "
                        f"content will be sent to the provider during init. "
                        f"MemPalace does not control how the provider logs, "
                        f"retains, or uses your data. Pass --no-llm to keep "
                        f"init fully local."
                    )
                    # Consent gate (issue #26): block init when the api_key
                    # was acquired via env-fallback (stray credential in
                    # shell env). Explicit --llm-api-key (api_key_source ==
                    # "flag") means the user already opted in.
                    # --accept-external-llm bypasses for CI / non-interactive.
                    api_key_source = getattr(candidate, "api_key_source", None)
                    accept_flag = getattr(args, "accept_external_llm", False)
                    if api_key_source == "env" and not accept_flag:
                        try:
                            answer = (
                                input(
                                    "  Your API key was loaded from the environment "
                                    "(not passed via --llm-api-key). Continue with "
                                    "external LLM? [y/N] "
                                )
                                .strip()
                                .lower()
                            )
                        except EOFError:
                            answer = ""
                        if answer != "y":
                            print(
                                "  Declined — falling back to heuristics-only. "
                                "Pass --llm-api-key explicitly or "
                                "--accept-external-llm to skip this prompt."
                            )
                            llm_provider = None
            else:
                print(
                    f"  No LLM provider reachable ({msg}). "
                    f"Running heuristics-only — pass --no-llm to silence this."
                )
        except LLMError as e:
            print(
                f"  LLM init failed ({e}). Running heuristics-only — pass --no-llm to silence this."
            )

    # Pass 0: detect whether the corpus is AI-dialogue. Writes
    # <palace>/.mempalace/origin.json and supplies corpus context to the
    # entity classifier so it can correctly handle agent persona names
    # (e.g. "Echo", "Sparrow") without misclassifying them as people.
    corpus_origin = _run_pass_zero(
        project_dir=args.dir,
        palace_dir=cfg.palace_path,
        llm_provider=llm_provider,
    )

    # Pass 1: discover entities — manifests + git authors first, prose detection
    # as supplement for names mentioned only in docs/notes. Optional phase-2
    # LLM refinement runs inside discover_entities when llm_provider is given.
    print(f"\n  Scanning for entities in: {args.dir}")
    if languages_tuple != ("en",):
        print(f"  Languages: {', '.join(languages_tuple)}")
    detected = discover_entities(
        args.dir,
        languages=languages_tuple,
        llm_provider=llm_provider,
        corpus_origin=corpus_origin,
    )
    total = (
        len(detected["people"])
        + len(detected["projects"])
        + len(detected.get("topics", []))
        + len(detected["uncertain"])
    )
    if total > 0:
        confirmed = confirm_entities(detected, yes=getattr(args, "yes", False))
        # Save confirmed entities to <project>/entities.json (per-project
        # audit trail — user can inspect or hand-edit) AND merge into the
        # global registry the miner reads at mine time. Topics are kept
        # separately so the miner can later compute cross-wing tunnels
        # from shared topics (see palace_graph.compute_topic_tunnels).
        if confirmed["people"] or confirmed["projects"] or confirmed.get("topics"):
            project_path = Path(args.dir).expanduser().resolve()
            entities_path = project_path / "entities.json"
            with open(entities_path, "w", encoding="utf-8") as f:
                json.dump(confirmed, f, indent=2, ensure_ascii=False)
            print(f"  Entities saved: {entities_path}")

            from .config import normalize_wing_name
            from .miner import add_to_known_entities

            # Match the slug ``room_detector_local`` writes into
            # ``mempalace.yaml`` so the miner's tunnel lookup hits the
            # same key in ``topics_by_wing`` at mine time (issue #1194 —
            # without this, hyphenated dirnames silently lose tunnels).
            wing = normalize_wing_name(project_path.name)
            registry_path = add_to_known_entities(confirmed, wing=wing)
            print(f"  Registry updated: {registry_path}")
    else:
        print("  No entities detected — proceeding with directory-based rooms.")

    # Pass 2: detect rooms from folder structure
    detect_rooms_local(project_dir=args.dir, yes=getattr(args, "yes", False))
    cfg.init()

    # Pass 3: protect git repos from accidentally committing per-project files
    _ensure_mempalace_files_gitignored(args.dir)

    # Pass 4: offer to run mine immediately. The directory just had its
    # rooms + entities set up, so 99% of users will mine next anyway —
    # asking here removes the "remember to type the next command" friction.
    # `--auto-mine` skips the prompt and mines automatically; `--yes` is
    # SCOPED to entity auto-accept and does NOT imply mining.
    _maybe_run_mine_after_init(args, cfg)


def _format_size_mb(num_bytes: int) -> str:
    """Render a byte count as a human-readable size for the mine estimate.

    < 1 MB rounds up to ``<1 MB`` so users never see a misleading ``0 MB``
    on small projects. Otherwise reports an integer megabyte count.
    """
    if num_bytes <= 0:
        return "<1 MB"
    mb = num_bytes / (1024 * 1024)
    if mb < 1:
        return "<1 MB"
    return f"{mb:.0f} MB"


def _maybe_run_mine_after_init(args, cfg) -> None:
    """Prompt the user to mine the directory just initialised, or auto-mine
    when ``--auto-mine`` was passed. Extracted so the prompt path is
    unit-testable.

    Behaviour matrix:

    - default (no flags) — prompt, default Yes, mine in-process if accepted
    - ``--yes`` — entity auto-accept only; STILL prompts for the mine step
    - ``--auto-mine`` — skip the mine prompt and mine directly
    - ``--yes --auto-mine`` — fully non-interactive

    Mine errors are surfaced (not swallowed): a failing mine exits with a
    non-zero status via :func:`sys.exit` so downstream scripts can see it.
    The pre-scan that produces the file-count estimate is reused as the
    mine input so we never walk the corpus twice.
    """
    from .miner import mine, scan_project

    project_dir = args.dir
    auto_mine = bool(getattr(args, "auto_mine", False))

    # Single corpus walk: this scan feeds BOTH the "what would be mined"
    # estimate the user sees in the prompt AND the file list mine() will
    # process. We pass the result into mine() via the `files` kwarg so it
    # doesn't re-walk the tree.
    try:
        scanned_files = scan_project(project_dir)
        file_count = len(scanned_files)
        total_bytes = 0
        for fp in scanned_files:
            try:
                total_bytes += fp.stat().st_size
            except OSError:
                # Skip files that vanished between scan and stat — mine()
                # will skip them too.
                continue
        size_str = _format_size_mb(total_bytes)
    except Exception:
        scanned_files = None
        file_count = None
        size_str = None

    # Show the scope estimate BEFORE the prompt so the user knows what
    # they are agreeing to. On a real corpus mine takes minutes; hitting
    # Enter on a default-Y prompt with no size cue is a footgun.
    if isinstance(file_count, int):
        if size_str:
            print(f"  ~{file_count} files (~{size_str}) would be mined into this palace.\n")
        else:
            print(f"  ~{file_count} files would be mined into this palace.\n")

    if not auto_mine:
        try:
            answer = input("  Mine this directory now? [Y/n] ").strip().lower()
        except EOFError:
            # Non-interactive stdin (e.g. piped) — treat like decline so
            # we don't block. User can re-run with --auto-mine to opt in.
            answer = "n"
        if answer not in ("", "y", "yes"):
            print(f"\n  Skipped. Run `mempalace mine {shlex.quote(project_dir)}` when ready.")
            return

    palace_path = cfg.palace_path
    try:
        mine(
            project_dir=project_dir,
            palace_path=palace_path,
            files=scanned_files,
        )
    except KeyboardInterrupt:
        # mine() handles its own SIGINT summary + sys.exit(130); re-raise
        # any KeyboardInterrupt that escapes (shouldn't happen) so the
        # shell still sees a clean interrupt rather than a swallowed one.
        raise
    except Exception as e:
        print(f"\n  ERROR: mine failed: {e}", file=sys.stderr)
        sys.exit(1)


def _mine_via_adapter(args) -> None:
    """Route ``mempalace mine --source <adapter>`` through the adapter plugin contract.

    Constructs a :class:`PalaceContext`, calls :meth:`BaseSourceAdapter.ingest`,
    and upserts every yielded :class:`DrawerRecord` into the palace. This is
    the CLI-side glue between the source-adapter subsystem and the existing
    mine command — ``cmd_mine`` delegates here when ``--source`` is present.
    """
    from .config import normalize_wing_name
    from .sources import (
        DrawerRecord,
        PalaceContext,
        SourceItemMetadata,
        SourceRef,
        available_adapters,
        get_adapter,
    )

    adapter_name = args.source

    # ``--source list`` is a convenience alias: show installed adapters and exit.
    if adapter_name == "list":
        names = available_adapters()
        if names:
            print("Installed source adapters:")
            for name in names:
                print(f"  {name}")
        else:
            print("No source adapters installed.")
        return

    try:
        adapter = get_adapter(adapter_name)
    except KeyError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    directory = os.path.abspath(os.path.expanduser(args.dir))
    wing = args.wing or normalize_wing_name(Path(directory).name)
    agent = getattr(args, "agent", "mempalace")
    dry_run = getattr(args, "dry_run", False)
    limit = getattr(args, "limit", 0)

    source = SourceRef(
        local_path=directory,
        options={
            "wing": wing,
            "agent": agent,
            "limit": limit,
        },
    )

    if dry_run:
        print(f"\n  DRY RUN: would mine {directory} via adapter '{adapter_name}'")
        print(f"  Wing: {wing}")
        summary = adapter.source_summary(source=source)
        if summary.item_count is not None:
            print(f"  Items: {summary.item_count}")
        print(f"  Description: {summary.description}")
        print()
        return

    # Build palace context — open the drawer collection and knowledge graph.
    from .knowledge_graph import KnowledgeGraph
    from .palace import get_collection

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    try:
        collection = get_collection(palace_path, create=True)
    except Exception as e:
        print(f"  ERROR: cannot open palace at {palace_path}: {e}", file=sys.stderr)
        sys.exit(1)

    kg_path = os.path.join(palace_path, ".mempalace", "knowledge_graph.sqlite3")
    os.makedirs(os.path.dirname(kg_path), exist_ok=True)
    kg = KnowledgeGraph(db_path=kg_path)

    palace_ctx = PalaceContext(
        drawer_collection=collection,
        knowledge_graph=kg,
        palace_path=palace_path,
        adapter_name=adapter_name,
        adapter_version=adapter.adapter_version,
    )

    print(f"\n  Mining {directory} via adapter '{adapter_name}' (v{adapter.adapter_version})")
    print(f"  Wing: {wing}")

    drawer_count = 0
    item_count = 0

    try:
        for result in adapter.ingest(source=source, palace=palace_ctx):
            if isinstance(result, SourceItemMetadata):
                item_count += 1
                # Check incremental: skip if palace already has current version
                if adapter.is_current(item=result, existing_metadata=None):
                    palace_ctx.skip_current_item()
                continue

            if isinstance(result, DrawerRecord):
                if palace_ctx.is_skip_requested():
                    continue

                # Apply route hint: prefer adapter-supplied wing/room, fall
                # back to CLI-specified wing and a default room.
                meta = dict(result.metadata)
                hint = result.route_hint
                meta["wing"] = hint.wing if hint and hint.wing else wing
                meta["room"] = hint.room if hint and hint.room else "general"
                meta["agent"] = agent
                meta["source_file"] = result.source_file

                enriched = DrawerRecord(
                    content=result.content,
                    source_file=result.source_file,
                    chunk_index=result.chunk_index,
                    metadata=meta,
                    route_hint=result.route_hint,
                )
                palace_ctx.upsert_drawer(enriched)
                drawer_count += 1
    except KeyboardInterrupt:
        print(f"\n  Interrupted. Filed {drawer_count} drawers from {item_count} items.")
        sys.exit(130)
    finally:
        try:
            kg.close()
        except Exception:
            pass

    print(f"  Filed {drawer_count} drawers from {item_count} items.\n")


def cmd_mine(args):
    # --source flag: route through the adapter plugin contract
    source_adapter = getattr(args, "source", None)
    if source_adapter:
        _mine_via_adapter(args)
        return

    if _daemon_strict() and not args.palace:
        # Daemon-strict: route to /mine. The daemon owns the canonical
        # palace and its filesystem layout, and translates client-side
        # paths via PALACE_DAEMON_PATH_MAP. Flags that only make sense
        # on the local FS (--redetect-origin, --include-ignored,
        # --no-gitignore, --dry-run) are warned about but not passed —
        # the daemon's /mine endpoint does not expose them.
        ignored_flags = []
        if getattr(args, "redetect_origin", False):
            ignored_flags.append("--redetect-origin")
        if getattr(args, "dry_run", False):
            ignored_flags.append("--dry-run")
        if getattr(args, "no_gitignore", False):
            ignored_flags.append("--no-gitignore")
        if getattr(args, "include_ignored", None):
            ignored_flags.append("--include-ignored")
        if ignored_flags:
            print(
                f"  WARN: daemon-strict mode ignores these local-only flags: {', '.join(ignored_flags)}",
                file=sys.stderr,
            )

        directory = os.path.abspath(os.path.expanduser(args.dir))
        wing = args.wing
        if not wing:
            # Match local-mine semantics: derive wing from directory name
            # the same way miner / convo_miner do when --wing is omitted.
            from .config import normalize_wing_name

            wing = normalize_wing_name(Path(directory).name)
        ok = _post_daemon_mine_cli(directory, wing=wing, mode=args.mode)
        sys.exit(0 if ok else 1)

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    include_ignored = []
    for raw in args.include_ignored or []:
        include_ignored.extend(part.strip() for part in raw.split(",") if part.strip())

    # --redetect-origin re-runs corpus_origin on the current corpus state
    # and overwrites <palace>/.mempalace/origin.json before mining proceeds.
    # Heuristic-only by design — full LLM detection lives on `mempalace init`.
    if getattr(args, "redetect_origin", False):
        _run_pass_zero(
            project_dir=args.dir,
            palace_dir=palace_path,
            llm_provider=None,
        )

    from .palace import MineAlreadyRunning, MineValidationError

    try:
        if args.mode == "session":
            # hybrid-search-taxonomy follow-up: per-session manifest mode.
            # One addressable drawer per session file (vs convos mode's
            # N chunked drawers). Use when you want a single navigable
            # anchor for "did session X exist? what did it cover?" —
            # complements convos mode, doesn't replace it.
            from .convo_miner import mine_sessions

            mine_sessions(
                convo_dir=args.dir,
                palace_path=palace_path,
                wing=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        elif args.mode == "convos":
            from .convo_miner import mine_convos

            mine_convos(
                convo_dir=args.dir,
                palace_path=palace_path,
                wing=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
                extract_mode=args.extract,
            )
        elif args.mode == "extract":
            from .format_miner import mine_formats

            mine_formats(
                format_dir=args.dir,
                palace_path=palace_path,
                wing=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        else:
            from .miner import mine

            mine(
                project_dir=args.dir,
                palace_path=palace_path,
                wing_override=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
                respect_gitignore=not args.no_gitignore,
                include_ignored=include_ignored,
                max_chunks_per_file=getattr(args, "max_chunks_per_file", None),
            )
    except MineAlreadyRunning as exc:
        # A live MCP server or another mine is already writing to this
        # palace. Surface the holder identity so the operator knows what
        # to wait for (or stop), and exit non-zero so wrappers like
        # nohup / scripts can detect the contention.
        print(f"mempalace: {exc}", file=sys.stderr)
        sys.exit(1)
    except MineValidationError as exc:
        # PRAGMA quick_check on chroma.sqlite3 returned errors at end of mine.
        # The corruption may pre-date the mine; we surface it here so automation
        # cannot proceed against a half-broken palace. Reuse cmd_repair's
        # recovery banner so the operator sees one consistent message regardless
        # of which command surfaces it.
        from .repair import print_sqlite_integrity_abort

        print_sqlite_integrity_abort(exc.palace_path, exc.errors)
        print(
            "\n  PRAGMA quick_check after this mine reported errors (the corruption\n"
            "  may pre-date the mine itself). Drawers may still be intact for direct\n"
            "  lookup; wing-filtered or full-text search will fail until the FTS5\n"
            "  index is rebuilt. `mempalace repair --yes` rebuilds the FTS5 virtual\n"
            "  table automatically (step 6 of the recovery above).",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_sweep(args):
    """Sweep a transcript file or directory.

    The sweeper deduplicates against its own prior writes via
    deterministic drawer IDs + a timestamp cursor. It does NOT currently
    coordinate with the file-level miners (miner.py / convo_miner.py) —
    those produce char-chunked drawers without compatible message
    metadata, so running both miners may store overlapping content under
    different IDs.
    """
    from .sweeper import sweep, sweep_directory

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    target = os.path.expanduser(args.target)

    if os.path.isfile(target):
        result = sweep(target, palace_path)
        print(
            f"  Swept {target}: +{result['drawers_added']} new, "
            f"{result['drawers_already_present']} already present, "
            f"{result['drawers_skipped']} skipped (< cursor)."
        )
    elif os.path.isdir(target):
        result = sweep_directory(target, palace_path)
        print(
            f"  Swept {result['files_succeeded']}/{result['files_attempted']} "
            f"files from {target}: +{result['drawers_added']} new, "
            f"{result['drawers_already_present']} already present, "
            f"{result['drawers_skipped']} skipped (< cursor)."
        )
        failures = result.get("failures") or []
        if failures:
            print(
                f"  WARNING: {len(failures)} file(s) failed to sweep - see stderr / logs for details.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        print(f"  ERROR: Not a file or directory: {target}", file=sys.stderr)
        sys.exit(1)


def cmd_sync(args):
    """Prune drawers whose source files are gitignored, deleted, or moved (#1252)."""
    from .mcp_server import _wal_log
    from .palace import MineAlreadyRunning
    from .sync import sync_palace

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path

    if not os.path.isdir(palace_path):
        _print_retired_local_palace_or_default(palace_path)
        return
    if not os.path.isfile(os.path.join(palace_path, "chroma.sqlite3")):
        print(f"\n  Palace dir at {palace_path} exists but has no chroma.sqlite3 yet.")
        print("  Run: mempalace mine <dir>")
        return

    project_dirs = []
    if args.dir:
        project_dirs.append(os.path.expanduser(args.dir))
    project_dirs.extend(os.path.expanduser(r) for r in args.root)
    project_dirs = project_dirs or None

    print(f"\n{'=' * 55}")
    print("  MemPalace Sync — Gitignore-aware drawer prune")
    print(f"{'=' * 55}")
    print(f"  Palace:   {palace_path}")
    if args.wing:
        print(f"  Wing:     {args.wing}")
    if project_dirs:
        for p in project_dirs:
            print(f"  Project:  {p}")
    if args.dry_run:
        print("  Mode:     DRY RUN (no deletions)")
    else:
        print("  Mode:     APPLY (deleting drawers)")
    print(f"{'-' * 55}\n")

    try:
        report = sync_palace(
            palace_path=palace_path,
            project_dirs=project_dirs,
            wing=args.wing,
            dry_run=args.dry_run,
            wal_log=_wal_log,
        )
    except MineAlreadyRunning as exc:
        print(f"mempalace: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"mempalace: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"mempalace: sync failed: {exc}", file=sys.stderr)
        sys.exit(1)

    removed_suffix = "(would remove)" if args.dry_run else "(removed)"
    print(f"  Scanned:        {report['scanned']}")
    print(f"  Kept:           {report['kept']}")
    print(f"  Gitignored:     {report['gitignored']}  {removed_suffix}")
    print(f"  Missing:        {report['missing']}  {removed_suffix}")
    print(f"  No source:      {report['no_source']}  (kept)")
    print(f"  Out of scope:   {report['out_of_scope']}  (kept)")

    by_source = report.get("by_source") or {}
    if by_source:
        top = sorted(by_source.items(), key=lambda kv: -kv[1])[:5]
        label = "Top sources to remove" if args.dry_run else "Top sources removed"
        print(f"\n  {label}:")
        for src, n in top:
            print(f"    {src}  ({n})")

    if args.dry_run:
        if report["gitignored"] + report["missing"] > 0:
            print("\n  Re-run with --apply to commit these deletions.")
    else:
        print(
            f"\n  Removed {report['removed_drawers']} drawers, {report['removed_closets']} closets."
        )

    print(f"\n{'=' * 55}\n")


def _resolve_search_format(args) -> str:
    """Pick the output format for ``mempalace search``.

    ``--format`` always wins. ``--json`` (legacy flag) maps to ``json``.
    Defaults to ``table`` — the enhanced multi-line view. ``getattr``
    fallbacks keep older test fixtures (which build ``argparse.Namespace``
    without the new attrs) working unchanged.
    """
    fmt = getattr(args, "format", None)
    if fmt:
        return fmt
    if getattr(args, "json", False):
        return "json"
    return "table"


def _resolve_search_limit(args) -> int:
    """Pick the result limit. ``--limit`` overrides legacy ``--results``."""
    limit = getattr(args, "limit", None)
    if limit is not None:
        return limit
    return getattr(args, "results", 5)


def _daemon_search_fast(query: str, n_results: int, wing: str = None) -> dict | None:
    """BM25 fast path via GET /search/fast. Returns normalised data dict or None."""
    rest_params = {"q": query, "limit": n_results}
    if wing:
        rest_params["wing"] = wing
    raw = _call_daemon_rest("/search/fast", rest_params)
    if raw is None:
        return None
    if isinstance(raw, dict):
        hits = raw.get("results")
    elif isinstance(raw, list):
        hits = raw
    else:
        hits = None
    if not isinstance(hits, list):
        return None
    for hit in hits:
        if "snippet" in hit:
            hit["text"] = hit.pop("snippet")
        elif "text" not in hit:
            hit["text"] = ""
        if "rank" in hit:
            hit["bm25_score"] = round(hit.pop("rank"), 3)
        if hit.get("source_file"):
            hit["source"] = hit["source_file"]
    return {"results": hits, "query": query, "source": "bm25-fast"}


def _daemon_search_hybrid(
    query: str, n_results: int, wing: str = None, room: str = None
) -> dict | None:
    """Hybrid search via POST /search/hybrid (vector + BM25 + AGE graph)."""
    body = {"query": query, "limit": n_results}
    if wing:
        body["wing"] = wing
    if room:
        body["room"] = room
    data = _post_daemon_rest("/search/hybrid", body)
    if data is None:
        return None
    data.setdefault("source", "hybrid")
    return data


def cmd_search(args):
    fmt = _resolve_search_format(args)
    want_json = fmt == "json"
    n_results = _resolve_search_limit(args)
    tags = list(args.tags) if getattr(args, "tags", None) else None
    search_mode = getattr(args, "mode", None) or "auto"
    quiet = bool(getattr(args, "quiet", False)) or want_json
    if _daemon_strict() and not args.palace:
        arguments = {"query": args.query, "limit": n_results}
        if args.wing:
            arguments["wing"] = args.wing
        if args.room:
            arguments["room"] = args.room
        if tags:
            arguments["tags"] = tags
        try:
            data = None

            if search_mode == "hybrid":
                data = _daemon_search_hybrid(args.query, n_results, wing=args.wing, room=args.room)
            elif search_mode == "fast":
                data = _daemon_search_fast(args.query, n_results, wing=args.wing)
            elif search_mode == "auto":
                if not args.room and not tags:
                    data = _daemon_search_fast(args.query, n_results, wing=args.wing)

            if data is None:
                data = _call_daemon_tool("mempalace_search", arguments)
        except DaemonError as e:
            if want_json:
                _emit_json({"error": str(e), "source": "daemon", "query": args.query})
            else:
                print(f"\n  ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        if want_json:
            data.setdefault("query", args.query)
            _emit_json(data)
            sys.exit(0 if (data.get("results") or []) else 1)
        if not quiet:
            source = data.get("source", "mcp")
            print(f"  [{source}]", file=sys.stderr)
        _print_daemon_search(args.query, data, wing=args.wing, room=args.room, fmt=fmt, quiet=quiet)
        if "error" in data and not data.get("results"):
            sys.exit(2)
        return

    from .searcher import SearchError, search, search_memories

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path

    if want_json:
        _emit_local_search_json(
            query=args.query,
            palace_path=palace_path,
            wing=args.wing,
            room=args.room,
            tags=tags,
            n_results=n_results,
            search_memories=search_memories,
        )
        return

    # Local-path fallback for ``compact`` / ``full`` reuses the daemon
    # renderer by calling ``search_memories`` directly. Plain ``table``
    # stays on the legacy ``searcher.search`` printer so existing local
    # callers keep their familiar output.
    if fmt in ("compact", "full"):
        result = search_memories(
            args.query,
            palace_path,
            wing=args.wing,
            room=args.room,
            tags=tags,
            n_results=n_results,
        )
        _print_daemon_search(
            args.query, result, wing=args.wing, room=args.room, fmt=fmt, quiet=quiet
        )
        if "error" in result and not result.get("results"):
            sys.exit(2)
        sys.exit(0 if (result.get("results") or []) else 1)

    try:
        search(
            query=args.query,
            palace_path=palace_path,
            wing=args.wing,
            room=args.room,
            tags=tags,
            n_results=n_results,
        )
    except SearchError:
        sys.exit(1)


def _emit_local_search_json(
    *,
    query: str,
    palace_path: str,
    wing: str,
    room: str,
    n_results: int,
    search_memories,
    tags: list = None,
) -> None:
    """JSON search against a local palace — mirrors the MCP
    ``tool_search`` response shape. ``search_memories`` is injected so
    tests can substitute a fake without monkey-patching the module.
    """
    # Mirror the filesystem-first probes from ``searcher.search`` so we
    # return a clear ``palace_unavailable`` error before the backend
    # would silently create a chroma.sqlite3 on first open.
    if not os.path.isdir(palace_path):
        _emit_json(
            {
                "error": "palace_unavailable",
                "hint": f"No palace found at {palace_path}. Run: mempalace init <dir>",
                "palace_path": palace_path,
                "query": query,
            }
        )
        sys.exit(2)
    if not os.path.isfile(os.path.join(palace_path, "chroma.sqlite3")):
        _emit_json(
            {
                "error": "palace_unavailable",
                "hint": f"Palace dir at {palace_path} has no chroma.sqlite3 yet. Run: mempalace mine <dir>",
                "palace_path": palace_path,
                "query": query,
            }
        )
        sys.exit(2)

    result = search_memories(
        query, palace_path, wing=wing, room=room, tags=tags, n_results=n_results
    )
    result.setdefault("query", query)
    _emit_json(result)
    if "error" in result and not result.get("results"):
        sys.exit(2)
    sys.exit(0 if (result.get("results") or []) else 1)


# ── mempalace list — fast direct-to-daemon drawer browser (#191) ────────
#
# Pure metadata browse: no ranking, no exclusion, no embedding. Wraps
# the daemon's GET /list endpoint (which itself wraps the
# ``mempalace_list_drawers`` MCP tool). Read-only, safe to run during
# backfill — it just paginates the metadata table.
#
# Recall-preserving by design: every drawer matching the wing/room
# filter is reachable via offset, and no drawer is dropped. This is the
# human/script counterpart to the existing ``mempalace_list_drawers``
# MCP tool (which serves the AI path).


_LIST_LIMIT_MAX = 1000  # sanity ceiling; the daemon clamps to 100 anyway


def _print_list_table(data: dict) -> None:
    """Human-readable multi-line render — drawer_id (short) + wing/room + preview."""
    drawers = data.get("drawers") or []
    total = data.get("total", len(drawers))
    offset = data.get("offset", 0)
    limit = data.get("limit", len(drawers))
    if not drawers:
        print("\n  No drawers found.")
        return
    end = offset + len(drawers)
    print(f"\n  Drawers {offset + 1}–{end} of {total} (limit {limit})\n")
    for d in drawers:
        did = d.get("drawer_id", "")
        short = did[:12] if did else "(no-id)"
        wing = d.get("wing") or "(no-wing)"
        room = d.get("room") or "(no-room)"
        preview = (d.get("content_preview") or "").replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"  {short}  {wing}/{room}")
        if preview:
            print(f"    {preview}")
    print()


def _print_list_compact(data: dict) -> None:
    """One line per drawer: ``<id12> <wing>/<room>: <preview[:120]>``."""
    for d in data.get("drawers") or []:
        did = (d.get("drawer_id") or "")[:12]
        wing = d.get("wing") or "-"
        room = d.get("room") or "-"
        preview = (d.get("content_preview") or "").replace("\n", " ").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."
        print(f"{did} {wing}/{room}: {preview}")


def _print_list_full(data: dict) -> None:
    """Labelled sections, no truncation; separators between drawers."""
    drawers = data.get("drawers") or []
    total = data.get("total", len(drawers))
    offset = data.get("offset", 0)
    if not drawers:
        print("\n  No drawers found.")
        return
    print(f"\n  Drawers {offset + 1}–{offset + len(drawers)} of {total}\n")
    sep = "  " + "─" * 70
    for d in drawers:
        print(sep)
        print(f"  drawer_id: {d.get('drawer_id', '')}")
        print(f"  wing:      {d.get('wing') or ''}")
        print(f"  room:      {d.get('room') or ''}")
        tags = d.get("tags") or []
        if tags:
            print(f"  tags:      {', '.join(tags)}")
        print("  content:")
        for line in (d.get("content_preview") or "").splitlines() or [""]:
            print(f"    {line}")
    print(sep)
    print()


def _resolve_list_format(args) -> str:
    """Pick ``mempalace list`` output format. ``--format`` wins, then ``--json``."""
    fmt = getattr(args, "format", None)
    if fmt:
        return fmt
    if getattr(args, "json", False):
        return "json"
    return "table"


def cmd_list(args):
    """Fast direct-to-daemon drawer browser (issue #191).

    Pure metadata listing — wraps ``GET /list?wing=&room=&limit=&offset=``
    on the palace daemon. Output formats: ``table`` (default), ``compact``,
    ``full``, ``json``. Daemon unreachable → stderr error + exit 1.
    """
    fmt = _resolve_list_format(args)
    want_json = fmt == "json"

    limit = max(1, min(int(getattr(args, "limit", 20) or 20), _LIST_LIMIT_MAX))
    offset = max(0, int(getattr(args, "offset", 0) or 0))

    params: dict = {"limit": limit, "offset": offset}
    if getattr(args, "wing", None):
        params["wing"] = args.wing
    if getattr(args, "room", None):
        params["room"] = args.room

    try:
        data = _call_daemon_rest("/list", params)
    except DaemonError as e:
        # Match cmd_status's daemon-down fallback (line 2230) and the
        # graceful 401/403 + unreachable handling added in 850e08c. On
        # JSON output, emit a structured error so machine callers see
        # the same shape as other failure paths.
        if want_json:
            _emit_json({"error": str(e), "source": "daemon"})
        else:
            print(
                f"palace daemon unreachable at {_daemon_url()} — "
                f"see mempalace status for diagnostics ({e})",
                file=sys.stderr,
            )
        sys.exit(1)

    if data is None:
        # _call_daemon_rest returns None on 404/401/403 — endpoint
        # missing on an older daemon, or auth mismatch. Same exit code
        # as the unreachable case so scripts can treat "no daemon list"
        # uniformly without parsing the message.
        if want_json:
            _emit_json({"error": "daemon /list unavailable", "source": "daemon"})
        else:
            print(
                f"palace daemon unreachable at {_daemon_url()} — "
                "see mempalace status for diagnostics",
                file=sys.stderr,
            )
        sys.exit(1)

    # Daemon /list mirrors mempalace_list_drawers' shape: error key when
    # the underlying palace is unreachable from inside the daemon.
    if "error" in data and not data.get("drawers"):
        if want_json:
            _emit_json(data)
        else:
            print(f"\n  {data['error']}", file=sys.stderr)
        sys.exit(2)

    if want_json:
        # Stable top-level shape — drawers/total/count/offset/limit pass
        # through unchanged so scripts can rely on the keys.
        out = {
            "drawers": data.get("drawers") or [],
            "total": data.get("total", 0),
            "count": data.get("count", len(data.get("drawers") or [])),
            "offset": data.get("offset", offset),
            "limit": data.get("limit", limit),
        }
        _emit_json(out)
        return

    if fmt == "compact":
        _print_list_compact(data)
    elif fmt == "full":
        _print_list_full(data)
    else:
        _print_list_table(data)


def cmd_wakeup(args):
    """Show L0 (identity) + L1 (essential story) — the wake-up context."""
    from .layers import MemoryStack

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    stack = MemoryStack(palace_path=palace_path)

    text = stack.wake_up(wing=args.wing)
    tokens = len(text) // 4
    print(f"Wake-up text (~{tokens} tokens):")
    print("=" * 50)
    print(text)


def cmd_split(args):
    """Split concatenated transcript mega-files into per-session files."""
    from .split_mega_files import main as split_main
    import sys

    # Rebuild argv for split_mega_files argparse
    # Expand ~ and resolve to absolute path so split_mega_files sees a real path
    argv = ["--source", str(Path(args.dir).expanduser().resolve())]
    if args.output_dir:
        argv += ["--output-dir", args.output_dir]
    if args.dry_run:
        argv.append("--dry-run")
    if args.min_sessions != 2:
        argv += ["--min-sessions", str(args.min_sessions)]

    old_argv = sys.argv
    sys.argv = ["mempalace split"] + argv
    try:
        split_main()
    finally:
        sys.argv = old_argv


def cmd_export(args):
    from .exporter import export_palace

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    output_dir = os.path.expanduser(args.output)

    print(f"\n{'=' * 55}")
    print("  MemPalace Export")
    print(f"{'=' * 55}\n")
    print(f"  Palace: {palace_path}")
    print(f"  Output: {output_dir}\n")

    export_palace(palace_path=palace_path, output_dir=output_dir)

    print(f"\n{'=' * 55}\n")


def cmd_migrate(args):
    """Migrate palace from a different ChromaDB version."""
    from .migrate import migrate

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    migrate(
        palace_path=palace_path,
        dry_run=args.dry_run,
        confirm=getattr(args, "yes", False),
    )


def cmd_migrate_to_postgres(args):
    """Migrate a ChromaDB palace to Postgres (pgvector + AGE).

    Different from `cmd_migrate` (which handles intra-ChromaDB version
    upgrades). This one moves the entire substrate. See
    `mempalace/migrate_to_postgres.py` for the 7-phase pipeline.
    """
    from .migrate_to_postgres import run_migration

    chroma_path = os.path.expanduser(args.from_palace)
    run_migration(
        chroma_path=chroma_path,
        postgres_dsn=args.to_dsn,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )


def cmd_rooms(args):
    """Manage the canonical room set (mempalace_canonical_rooms table).

    hybrid-search-taxonomy follow-up. The FK constraint on mempalace_drawers.room
    means this CLI is the supported way to add/rename/remove canonical
    rooms without breaking the DB. ON UPDATE CASCADE on the FK makes
    renames safe (all drawers auto-update); removes fail if any drawer
    still in the target room.

    Requires postgres backend + MEMPALACE_POSTGRES_DSN env var.
    """
    try:
        import psycopg as psycopg2  # noqa: F401
    except ImportError:
        print(
            "error: rooms CLI requires the psycopg driver. Install with: pip install mempalace[postgres]"
        )
        sys.exit(1)

    import psycopg as psycopg2

    dsn = os.environ.get("MEMPALACE_POSTGRES_DSN")
    if not dsn:
        print("error: MEMPALACE_POSTGRES_DSN env var is not set", file=sys.stderr)
        sys.exit(1)

    cmd = getattr(args, "rooms_cmd", None)
    try:
        with psycopg2.connect(dsn) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                if cmd == "list":
                    cur.execute(
                        "SELECT name, COALESCE(description, '') AS description, added_at FROM mempalace_canonical_rooms ORDER BY name"
                    )
                    rows = cur.fetchall()
                    if not rows:
                        print("(no canonical rooms registered)")
                        return
                    print(f"{'name':14}  {'added_at':25}  description")
                    print("-" * 80)
                    for name, desc, added_at in rows:
                        ts = added_at.strftime("%Y-%m-%d") if added_at else ""
                        print(f"{name:14}  {ts:25}  {desc}")

                elif cmd == "add":
                    name = args.name.strip().lower()
                    if not name or not name.replace("_", "").isalnum():
                        print(
                            f"error: room name must be lowercase snake_case alphanumeric, got {args.name!r}"
                        )
                        sys.exit(1)
                    cur.execute(
                        "INSERT INTO mempalace_canonical_rooms (name, description) VALUES (%s, %s) "
                        "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description "
                        "RETURNING (xmax = 0) AS inserted",
                        (name, args.description or None),
                    )
                    inserted = cur.fetchone()[0]
                    action = "added" if inserted else "updated description for"
                    print(f"{action} canonical room {name!r}")
                    print("hint: POST /admin/refresh-rooms on the daemon to invalidate its cache")

                elif cmd == "rename":
                    old = args.old.strip().lower()
                    new = args.new.strip().lower()
                    if not new.replace("_", "").isalnum():
                        print(
                            f"error: new room name must be lowercase snake_case alphanumeric, got {args.new!r}"
                        )
                        sys.exit(1)
                    # UPDATE PK triggers ON UPDATE CASCADE on the drawers FK,
                    # renaming the room across all drawers atomically.
                    cur.execute(
                        "UPDATE mempalace_canonical_rooms SET name = %s WHERE name = %s",
                        (new, old),
                    )
                    if cur.rowcount == 0:
                        print(f"error: no canonical room named {old!r}")
                        sys.exit(1)
                    cur.execute("SELECT count(*) FROM mempalace_drawers WHERE room = %s", (new,))
                    affected = cur.fetchone()[0]
                    print(
                        f"renamed canonical room {old!r} → {new!r} ({affected:,} drawers cascade-renamed)"
                    )
                    print("hint: POST /admin/refresh-rooms on the daemon to invalidate its cache")

                elif cmd == "remove":
                    name = args.name.strip().lower()
                    cur.execute("SELECT count(*) FROM mempalace_drawers WHERE room = %s", (name,))
                    n_drawers = cur.fetchone()[0]
                    if n_drawers > 0:
                        print(
                            f"error: cannot remove {name!r} — {n_drawers:,} drawers still in this room.\n"
                            f"  Move them first: UPDATE mempalace_drawers SET room = 'discoveries' WHERE room = '{name}';\n"
                            f"  Or via mempalace purge --room {name}"
                        )
                        sys.exit(1)
                    cur.execute("DELETE FROM mempalace_canonical_rooms WHERE name = %s", (name,))
                    if cur.rowcount == 0:
                        print(f"error: no canonical room named {name!r}")
                        sys.exit(1)
                    print(f"removed canonical room {name!r}")
                    print("hint: POST /admin/refresh-rooms on the daemon to invalidate its cache")

                else:
                    print(f"error: unknown rooms subcommand {cmd!r}")
                    sys.exit(1)
    except psycopg2.errors.UndefinedTable:
        print(
            "error: mempalace_canonical_rooms table doesn't exist yet.\n"
            "  Run the hybrid-search-taxonomy migration first (see hybrid-search-taxonomy spec).",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_purge(args):
    """Delete drawers by wing and/or room.

    Uses ``collection.delete(where=...)`` — chromadb's filter-delete path
    doesn't go through ``updatePoint`` / ``repairConnectionsForUpdate``,
    which is the upsert-only race from #521 that an earlier draft of this
    command tried to side-step with a nuke-and-rebuild. The simpler path
    works without losing drawers if the process is interrupted, without
    re-embedding the survivors under a default model, and without
    bypassing the backend abstraction.

    ``--room`` without ``--wing`` purges that room across ALL wings.
    Not idempotent — running purge twice on the same criteria prints
    "No drawers found" the second time.
    """
    from .backends.chroma import ChromaBackend
    from .migrate import confirm_destructive_action, contains_palace_database

    palace_path = os.path.abspath(
        os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    )

    if not os.path.isdir(palace_path) or not contains_palace_database(palace_path):
        _print_retired_local_palace_or_default(palace_path)
        return

    source_file = getattr(args, "source_file", None)
    clauses = []
    if args.wing:
        clauses.append({"wing": args.wing})
    if args.room:
        clauses.append({"room": args.room})
    if source_file:
        clauses.append({"source_file": source_file})

    if not clauses:
        print("  Error: specify at least one of --wing, --room, --source-file")
        return
    where = clauses[0] if len(clauses) == 1 else {"$and": clauses}

    backend = ChromaBackend()
    from .backends.base import PalaceRef

    try:
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
        )
    except Exception as e:
        print(f"\n  Error reading palace: {e}")
        return

    # Probe match count via a `where=`-filtered get with no payload.
    # ChromaCollection.get returns the typed result; we only need ids.
    try:
        matched = col.get(where=where, include=[])
    except Exception as e:
        print(f"\n  Error querying drawers: {e}")
        return

    match_ids = matched.get("ids") if isinstance(matched, dict) else getattr(matched, "ids", [])
    match_ids = match_ids or []
    match_count = len(match_ids)

    label_parts = []
    if args.wing:
        label_parts.append(f"wing={args.wing}")
    if args.room:
        label_parts.append(f"room={args.room}")
    if source_file:
        label_parts.append(f"source-file={source_file}")
    label = " ".join(label_parts)

    if match_count == 0:
        print(f"\n  No drawers found matching {label}\n")
        return

    print(f"\n  Found {match_count:,} drawers matching {label}")

    if not args.yes:
        if not confirm_destructive_action(f"Purge of {match_count:,} drawers", palace_path):
            return

    print("  Deleting matching drawers...")
    try:
        col.delete(where=where)
    except Exception as e:
        print(f"\n  Delete failed: {e}\n")
        return

    remaining = col.count()
    print(f"\n  Purged {match_count:,} drawers. Remaining: {remaining:,}\n")


def cmd_prune(args):
    """Delete drawers older than ``--stale-days N`` (dry-run by default).

    Age is the span between a drawer's ``filed_at`` timestamp and now. Unlike
    ``purge``'s metadata-equality filter, the staleness predicate is a string
    timestamp that chromadb ``where=`` can't range-compare reliably, so we
    fetch candidate metadata and decide age in Python (``mempalace.recency``),
    then delete by explicit id list.

    Safety: this is the only command that destroys data on a *time* predicate
    rather than an explicit selection, so it is **dry-run by default**. Nothing
    is deleted unless ``--confirm`` is passed. A drawer with no parseable
    ``filed_at`` is treated as ageless and is **never** pruned — we never
    delete a drawer we can't date.
    """
    from datetime import datetime, timezone

    from .backends.base import PalaceRef
    from .backends.chroma import ChromaBackend
    from .migrate import contains_palace_database
    from .recency import age_days

    want_json = getattr(args, "json", False)
    stale_days = args.stale_days
    confirm = getattr(args, "confirm", False)

    if stale_days is None or stale_days <= 0:
        msg = "--stale-days must be a positive integer"
        print(json.dumps({"error": msg}) if want_json else f"  Error: {msg}")
        return

    palace_path = os.path.abspath(
        os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    )

    if not os.path.isdir(palace_path) or not contains_palace_database(palace_path):
        if want_json:
            print(json.dumps({"error": "no palace database", "palace": palace_path}))
        else:
            _print_retired_local_palace_or_default(palace_path)
        return

    # Optional wing/room scope — without it, prune spans the whole palace.
    clauses = []
    if args.wing:
        clauses.append({"wing": args.wing})
    if args.room:
        clauses.append({"room": args.room})
    where = None
    if clauses:
        where = clauses[0] if len(clauses) == 1 else {"$and": clauses}

    backend = ChromaBackend()
    try:
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}) if want_json else f"\n  Error reading palace: {e}")
        return

    # Pull ids + metadata for the scope; age is decided in Python.
    try:
        got = (
            col.get(where=where, include=["metadatas"]) if where else col.get(include=["metadatas"])
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}) if want_json else f"\n  Error querying drawers: {e}")
        return

    if isinstance(got, dict):
        all_ids = got.get("ids") or []
        all_metas = got.get("metadatas") or []
    else:
        all_ids = getattr(got, "ids", []) or []
        all_metas = getattr(got, "metadatas", []) or []

    now = datetime.now(timezone.utc)
    stale_ids = []
    undated = 0
    for did, meta in zip(all_ids, all_metas):
        age = age_days(meta or {}, now=now)
        if age is None:
            undated += 1
            continue
        if age >= stale_days:
            stale_ids.append(did)

    scope_parts = []
    if args.wing:
        scope_parts.append(f"wing={args.wing}")
    if args.room:
        scope_parts.append(f"room={args.room}")
    scope = " ".join(scope_parts) if scope_parts else "entire palace"

    if want_json:
        print(
            json.dumps(
                {
                    "stale_days": stale_days,
                    "scope": scope,
                    "scanned": len(all_ids),
                    "stale": len(stale_ids),
                    "undated_skipped": undated,
                    "confirmed": bool(confirm),
                    "deleted": 0,
                }
            )
        )
    else:
        print(f"\n  Scanned {len(all_ids):,} drawers in {scope}")
        print(f"  {len(stale_ids):,} older than {stale_days} days; {undated:,} undated (kept)")

    if not stale_ids:
        if not want_json:
            print("  Nothing to prune.\n")
        return

    if not confirm:
        if not want_json:
            print(
                f"\n  DRY RUN — nothing deleted. Re-run with --confirm to delete "
                f"{len(stale_ids):,} drawers.\n"
            )
        return

    try:
        col.delete(ids=stale_ids)
    except Exception as e:
        print(json.dumps({"error": str(e)}) if want_json else f"\n  Delete failed: {e}\n")
        return

    remaining = col.count()
    if want_json:
        print(
            json.dumps(
                {
                    "stale_days": stale_days,
                    "scope": scope,
                    "deleted": len(stale_ids),
                    "remaining": remaining,
                }
            )
        )
    else:
        print(f"\n  Pruned {len(stale_ids):,} drawers. Remaining: {remaining:,}\n")


def cmd_rename_wing(args):
    want_json = getattr(args, "json", False)
    from_wing = args.from_wing
    to_wing = args.to_wing
    dry_run = getattr(args, "dry_run", False)
    batch_size = getattr(args, "batch_size", 500)

    if _daemon_strict():
        if dry_run:
            try:
                data = _call_daemon_tool(
                    "mempalace_list_drawers",
                    {
                        "wing": from_wing,
                        "limit": 1,
                    },
                )
            except DaemonError as e:
                if want_json:
                    _emit_json({"error": str(e)})
                else:
                    print(f"\n  ERROR: {e}", file=sys.stderr)
                sys.exit(2)
            total = data.get("total", 0)
            if want_json:
                _emit_json(
                    {"dry_run": True, "from_wing": from_wing, "to_wing": to_wing, "count": total}
                )
            else:
                print(
                    f"\n  Dry run: {total:,} drawers would be renamed from '{from_wing}' to '{to_wing}'\n"
                )
            return

        try:
            data = _call_daemon_tool(
                "mempalace_rename_wing",
                {
                    "from_wing": from_wing,
                    "to_wing": to_wing,
                    "batch_size": batch_size,
                },
            )
        except DaemonError as e:
            if want_json:
                _emit_json({"error": str(e)})
            else:
                print(f"\n  ERROR: {e}", file=sys.stderr)
            sys.exit(2)

        if want_json:
            _emit_json(data)
        else:
            renamed = data.get("renamed", 0)
            errors = data.get("errors", 0)
            print(f"\n  Renamed {renamed:,} drawers: '{from_wing}' -> '{to_wing}'")
            if errors:
                print(f"  Errors: {errors:,}")
            print()
        return

    from .backends.chroma import ChromaBackend
    from .backends.base import PalaceRef

    palace_path = os.path.abspath(
        os.path.expanduser(args.palace)
        if getattr(args, "palace", None)
        else MempalaceConfig().palace_path
    )
    backend = ChromaBackend()
    try:
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
        )
    except Exception as e:
        print(f"\n  Error reading palace: {e}")
        sys.exit(1)

    if dry_run:
        matched = col.get(where={"wing": from_wing}, include=[])
        count = len(matched.ids) if hasattr(matched, "ids") else len(matched.get("ids", []))
        if want_json:
            _emit_json(
                {"dry_run": True, "from_wing": from_wing, "to_wing": to_wing, "count": count}
            )
        else:
            print(
                f"\n  Dry run: {count:,} drawers would be renamed from '{from_wing}' to '{to_wing}'\n"
            )
        return

    result = col.rename_wing(from_wing=from_wing, to_wing=to_wing, batch_size=batch_size)
    if want_json:
        _emit_json({"success": True, "from_wing": from_wing, "to_wing": to_wing, **result})
    else:
        print(f"\n  Renamed {result['renamed']:,} drawers: '{from_wing}' -> '{to_wing}'")
        if result["errors"]:
            print(f"  Errors: {result['errors']:,}")
        print()


def cmd_replay(args):
    """Drain ``~/.mempalace/pending/*.jsonl`` by re-issuing each request to the daemon.

    Pending requests accumulate when the Stop / PreCompact hooks fire while
    the daemon (or its backend) is unreachable — see the 2026-05-21
    power-resilience design. Drain semantics:

    * Each line is one ``{"dir", "wing", "mode", "ts"}`` mine request.
    * On 2xx daemon response the line is consumed; on failure the line
      stays in the file for the next attempt.
    * Duplicate ``(dir, wing, mode)`` tuples are deduped before transmit
      so a long outage doesn't replay the same target N times.
    """
    if not _daemon_strict():
        print(
            "mempalace replay: nothing to do (PALACE_DAEMON_URL not set or strict mode off).",
            file=sys.stderr,
        )
        return 0

    try:
        from . import pending_queue
    except Exception as e:
        print(f"  ERROR: could not import pending_queue: {e}", file=sys.stderr)
        return 1

    def post(request: dict) -> bool:
        # _post_daemon_mine_cli doesn't share the hook's pending-queue
        # re-enqueue path, so skip_queue isn't applicable here; the
        # CLI variant prints to stderr and returns bool unconditionally.
        return _post_daemon_mine_cli(request["dir"], request["wing"], request.get("mode", "convos"))

    report = pending_queue.replay(post)
    if report.is_empty:
        print("mempalace replay: pending queue is empty.")
        return 0

    print(
        f"mempalace replay: attempted={report.attempted} "
        f"succeeded={report.succeeded} failed={report.failed} "
        f"files_drained={report.files_drained}"
    )
    return 0 if report.failed == 0 else 1


def cmd_status(args):
    want_json = getattr(args, "json", False)
    if _daemon_strict() and not args.palace:
        # --palace overrides routing: an explicit local-path argument
        # means the user wants to inspect THAT palace, not the daemon.
        try:
            data = _call_daemon_rest("/status/fast")
            if data is None:
                data = _call_daemon_tool("mempalace_status", {})
        except DaemonError as e:
            if want_json:
                _emit_json({"error": str(e), "source": "daemon"})
            else:
                print(f"\n  ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        if want_json:
            _emit_json(data)
            return
        _print_daemon_status(data)
        return

    from .miner import status

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    if want_json:
        _emit_local_status_json(palace_path)
        return
    status(palace_path=palace_path)


def _emit_local_status_json(palace_path: str) -> None:
    """JSON status from a local palace — mirrors the MCP ``tool_status``
    response shape: ``{total_drawers, wings, rooms}`` plus an ``error``
    key when the palace is unreachable. Used by ``cmd_status --json``
    when daemon routing is off (or ``--palace`` was passed).
    """
    from collections import defaultdict

    from .miner import _open_collection_or_explain

    # ``_open_collection_or_explain`` prints a human-readable hint to
    # stdout on failure. Capture it so JSON output stays clean.
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        col = _open_collection_or_explain(palace_path)
    if col is None:
        _emit_json(
            {
                "error": "palace_unavailable",
                "hint": buf.getvalue().strip() or f"No palace found at {palace_path}",
                "palace_path": palace_path,
            }
        )
        sys.exit(2)

    total = col.count()
    wings: dict = defaultdict(int)
    rooms: dict = defaultdict(int)
    batch_size = 5000
    offset = 0
    while offset < total:
        r = col.get(limit=batch_size, offset=offset, include=["metadatas"])
        batch = r.get("metadatas") or []
        if not batch:
            break
        for m in batch:
            m = m or {}
            wings[m.get("wing", "unknown")] += 1
            rooms[m.get("room", "unknown")] += 1
        offset += len(batch)

    _emit_json(
        {
            "total_drawers": total,
            "wings": dict(wings),
            "rooms": dict(rooms),
            "palace_path": palace_path,
        }
    )


def cmd_mined(args):
    """List mined source files grouped by wing.

    Companion to ``status`` (which groups by wing × room) — answers "which
    files have I mined into this wing?" so an operator can pick targets
    for ``mempalace purge --source-file <path>``.

    Skips drawers without a ``source_file`` metadata key (typically
    diary entries, kg drawers, manually-added entries).
    """
    from collections import defaultdict

    from .backends.chroma import ChromaBackend
    from .migrate import contains_palace_database

    palace_path = os.path.abspath(
        os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    )

    want_json_early = getattr(args, "json", False)

    if not os.path.isdir(palace_path) or not contains_palace_database(palace_path):
        if want_json_early:
            _emit_json(
                {
                    "error": "palace_unavailable",
                    "hint": f"No palace database at {palace_path}",
                    "palace_path": palace_path,
                }
            )
            sys.exit(2)
        _print_retired_local_palace_or_default(palace_path)
        return

    backend = ChromaBackend()
    from .backends.base import PalaceRef

    try:
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
        )
    except Exception as e:
        if want_json_early:
            _emit_json(
                {
                    "error": "palace_unavailable",
                    "hint": f"Error reading palace: {e}",
                    "palace_path": palace_path,
                }
            )
            sys.exit(2)
        print(f"\n  Error reading palace: {e}")
        return

    # Wing-by-source aggregation. Pagination mirrors miner.status so
    # palaces with hundreds of thousands of drawers don't trip SQLite's
    # max-variable limit on a single col.get(limit=total).
    #
    # When --wing is given, push the filter into each batch's where=
    # so we never scan unrelated wings. The previous version called
    # col.get(where=..., include=[]) without pagination to size the loop;
    # ChromaDB returns at most ~10K ids on a single get() call, silently
    # truncating beyond that — so the inner loop's `offset < total` would
    # have undercounted on a wing > 10K drawers, missing late entries.
    # Drop the upfront sizing entirely; the inner loop reads until it
    # gets back an empty batch (Copilot finding on jphein/mempalace#7).
    where = {"wing": args.wing} if args.wing else None
    wing_sources: dict = defaultdict(lambda: defaultdict(int))
    batch_size = 5000
    offset = 0
    while True:
        kwargs = {"limit": batch_size, "offset": offset, "include": ["metadatas"]}
        if where is not None:
            kwargs["where"] = where
        r = col.get(**kwargs)
        batch = r.get("metadatas") if isinstance(r, dict) else getattr(r, "metadatas", [])
        if not batch:
            break
        for m in batch:
            m = m or {}
            src = m.get("source_file")
            if not src:
                continue
            wing = m.get("wing", "?")
            wing_sources[wing][src] += 1
        offset += len(batch)
        # Defensive: if the backend returned fewer than batch_size, we're
        # past the last page. Saves one trailing empty col.get on palaces
        # whose wing-count is an exact multiple of batch_size (rare but
        # cheap to handle).
        if len(batch) < batch_size:
            break

    want_json = getattr(args, "json", False)

    if not wing_sources:
        if want_json:
            _emit_json(
                {
                    "sources_by_wing": {},
                    "wing_filter": args.wing,
                    "total_wings": 0,
                    "total_sources": 0,
                }
            )
            sys.exit(1)
        scope = f" in wing={args.wing}" if args.wing else ""
        print(f"\n  No mined source files found{scope}.\n")
        return

    if want_json:
        sources_by_wing: dict = {}
        total_sources = 0
        for wing in sorted(wing_sources):
            sources = sorted(wing_sources[wing].items(), key=lambda x: x[1], reverse=True)
            shown = sources if args.limit == 0 else sources[: args.limit]
            sources_by_wing[wing] = {
                "sources": [{"source_file": src, "drawer_count": count} for src, count in shown],
                "total_sources": len(sources),
                "total_drawers": sum(c for _, c in sources),
                "truncated": bool(args.limit) and len(sources) > args.limit,
            }
            total_sources += len(sources)
        _emit_json(
            {
                "sources_by_wing": sources_by_wing,
                "wing_filter": args.wing,
                "limit": args.limit,
                "total_wings": len(wing_sources),
                "total_sources": total_sources,
            }
        )
        return

    print(f"\n{'=' * 55}")
    print("  MemPalace Mined — sources by wing")
    print(f"{'=' * 55}\n")
    for wing in sorted(wing_sources):
        sources = sorted(wing_sources[wing].items(), key=lambda x: x[1], reverse=True)
        print(f"  WING: {wing}  ({len(sources)} sources, {sum(c for _, c in sources)} drawers)")
        shown = sources if args.limit == 0 else sources[: args.limit]
        for src, count in shown:
            print(f"    {count:5}  {src}")
        if args.limit and len(sources) > args.limit:
            print(f"    ... {len(sources) - args.limit} more (use --limit 0 to show all)")
        print()
    print(f"{'=' * 55}\n")


def _count_of(value) -> int:
    """Coerce a wing/room count from ``/status/fast`` into an int.

    The daemon normally returns ``{name: int}``, but a future or
    misbehaving daemon could nest ``{name: {"total": int}}`` or hand back
    a non-numeric value entirely. Accept the int and the ``{"total": ...}``
    shapes; anything else (string, list, None) counts as 0 rather than
    crashing the whole dashboard with an AttributeError.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        total = value.get("total", 0)
        return total if isinstance(total, int) else 0
    return 0


def _stats_bar(count: int, total: int, width: int = 24) -> str:
    """Render a horizontal bar proportional to ``count`` against ``total``.

    Uses Unicode block-element fills so a row at half the max draws to
    roughly half the bar width. Empty when ``total`` is zero so we never
    divide by zero on a freshly-initialised palace.
    """
    if total <= 0:
        return ""
    ratio = max(0.0, min(1.0, count / total))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def _gather_daemon_stats(want_tags: bool) -> dict:
    """Fan out daemon calls for the ``stats`` dashboard.

    Each section is independent — KG/graph failures don't blank the whole
    dashboard. We catch :class:`DaemonError` per section and inline a
    ``"_error"`` key so the renderer can surface "(KG unavailable: ...)"
    next to that block instead of bailing out entirely. The daemon's
    overall reachability is owned by the caller via ``mempalace_status``;
    if that one fails the whole command is aborted upstream.
    """
    bundle: dict = {}

    status_data = _call_daemon_rest("/status/fast")
    if status_data is None:
        status_data = _call_daemon_tool("mempalace_status", {})
    bundle["status"] = status_data

    try:
        bundle["kg"] = _call_daemon_tool("mempalace_kg_stats", {})
    except DaemonError as e:
        bundle["kg"] = {"_error": str(e)}

    try:
        bundle["graph"] = _call_daemon_tool("mempalace_graph_stats", {})
    except DaemonError as e:
        bundle["graph"] = {"_error": str(e)}

    if want_tags:
        try:
            bundle["tags"] = _call_daemon_tool("mempalace_list_tags", {"min_count": 1})
        except DaemonError as e:
            bundle["tags"] = {"_error": str(e)}

    return bundle


def _print_stats_dashboard(bundle: dict, top: int) -> None:
    """Render the stats bundle as a human-friendly dashboard.

    Mirrors ``_print_daemon_status``'s 55-char rules + two-space indent
    style so ``status`` and ``stats`` look like siblings to a human
    skimming the terminal.
    """
    status = bundle.get("status") or {}
    total = status.get("total_drawers", 0)
    wings = status.get("wings") or {}

    print(f"\n{'=' * 60}")
    print(f"  MemPalace Stats — {total} drawers")
    print(f"  via palace-daemon @ {_daemon_url()}")
    print(f"{'=' * 60}\n")

    print("  WINGS")
    print(f"  {'-' * 56}")
    if isinstance(wings, dict) and wings:
        items = sorted(
            ((w, _count_of(c)) for w, c in wings.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )
        max_count = items[0][1] if items else 0
        shown = items[:top] if top else items
        for wing, count in shown:
            bar = _stats_bar(count, max_count)
            print(f"    {wing:<28} {count:>7}  {bar}")
        remaining = len(items) - len(shown)
        if remaining > 0:
            tail = sum(c for _, c in items[len(shown) :])
            print(f"    ... {remaining} more wings ({tail} drawers; --top 0 shows all)")
    elif "error" in status:
        print(f"    (status error: {status.get('error')})")
    else:
        print("    (no wings)")
    print()

    # Rooms ride along in the same /status/fast payload as wings, so the
    # breakdown is free — no extra daemon call. The issue (#191) asks for
    # "drawer count by wing/room"; wings answer "which domains", rooms
    # answer "which kinds of memory" (the canonical 7-room taxonomy).
    rooms = status.get("rooms") or {}
    print("  ROOMS")
    print(f"  {'-' * 56}")
    if isinstance(rooms, dict) and rooms:
        room_items = sorted(
            ((r, _count_of(c)) for r, c in rooms.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )
        room_max = room_items[0][1] if room_items else 0
        room_shown = room_items[:top] if top else room_items
        for room, count in room_shown:
            bar = _stats_bar(count, room_max)
            print(f"    {room:<28} {count:>7}  {bar}")
        room_remaining = len(room_items) - len(room_shown)
        if room_remaining > 0:
            tail = sum(c for _, c in room_items[len(room_shown) :])
            print(f"    ... {room_remaining} more rooms ({tail} drawers; --top 0 shows all)")
    elif "error" in status:
        print(f"    (status error: {status.get('error')})")
    else:
        print("    (no rooms)")
    print()

    kg = bundle.get("kg") or {}
    print("  KNOWLEDGE GRAPH")
    print(f"  {'-' * 56}")
    if "_error" in kg:
        print(f"    (unavailable: {kg['_error']})")
    elif "error" in kg:
        print(f"    (error: {kg.get('error')})")
    else:
        print(f"    entities         : {kg.get('entities', 0):>7}")
        print(f"    triples          : {kg.get('triples', 0):>7}")
        print(f"    current facts    : {kg.get('current_facts', 0):>7}")
        print(f"    expired facts    : {kg.get('expired_facts', 0):>7}")
        rels = kg.get("relationship_types") or []
        if rels:
            preview = ", ".join(rels[:8])
            suffix = f" (+{len(rels) - 8} more)" if len(rels) > 8 else ""
            print(f"    relations ({len(rels):>2})  : {preview}{suffix}")
    print()

    graph = bundle.get("graph") or {}
    print("  GRAPH")
    print(f"  {'-' * 56}")
    if "_error" in graph:
        print(f"    (unavailable: {graph['_error']})")
    elif "error" in graph:
        print(f"    (error: {graph.get('error')})")
    else:
        print(f"    total rooms      : {graph.get('total_rooms', 0):>7}")
        print(
            f"    tunnel rooms     : {graph.get('tunnel_rooms', 0):>7}  (rooms shared by 2+ wings)"
        )
        print(f"    edges            : {graph.get('total_edges', 0):>7}")
        top_tunnels = graph.get("top_tunnels") or []
        if top_tunnels:
            print("    top tunnels      :")
            for t in top_tunnels[: min(5, top) if top else 5]:
                wing_list = ", ".join(t.get("wings") or [])
                print(f"      - {t.get('room', '?'):<22} [{wing_list}]")
    print()

    if "tags" in bundle:
        tags = bundle["tags"] or {}
        print("  TAGS")
        print(f"  {'-' * 56}")
        if "_error" in tags:
            print(f"    (unavailable: {tags['_error']})")
        elif "error" in tags:
            print(f"    (error: {tags.get('error')})")
        else:
            items = tags.get("tags") or []
            if not items:
                print("    (no tags)")
            else:
                max_count = items[0].get("count", 0) if items else 0
                shown = items[:top] if top else items
                for entry in shown:
                    tag = entry.get("tag", "?")
                    count = entry.get("count", 0)
                    bar = _stats_bar(count, max_count, width=18)
                    print(f"    {tag:<28} {count:>5}  {bar}")
                remaining = len(items) - len(shown)
                if remaining > 0:
                    print(f"    ... {remaining} more tags (--top 0 shows all)")
        print()

    print(f"{'=' * 60}\n")


def cmd_stats(args):
    """Palace analytics dashboard (#191).

    Composes ``mempalace_status`` + ``mempalace_kg_stats`` +
    ``mempalace_graph_stats`` (and optionally ``mempalace_list_tags``)
    into a single read-only view of corpus health. Daemon-only — there is
    no local fallback today because the KG/graph data lives in the
    daemon's postgres + AGE store; surfacing a misleading partial view
    from a stale local chromadb would re-introduce the split-brain
    ``status`` already warns against. When the daemon URL is unset, we
    abort with the same "set PALACE_DAEMON_URL" hint as the rest of the
    CLI's daemon-strict surfaces.
    """
    want_json = getattr(args, "json", False)
    want_tags = getattr(args, "tags", False)
    top = max(0, getattr(args, "top", 10) or 0)

    if not _daemon_url():
        msg = (
            "stats requires the palace-daemon. Set PALACE_DAEMON_URL "
            "(or daemon_url in ~/.mempalace/config.json) and retry."
        )
        if want_json:
            _emit_json({"error": "daemon_required", "hint": msg})
        else:
            print(f"\n  ERROR: {msg}", file=sys.stderr)
        sys.exit(2)

    try:
        bundle = _gather_daemon_stats(want_tags=want_tags)
    except DaemonError as e:
        if want_json:
            _emit_json({"error": str(e), "source": "daemon"})
        else:
            print(f"\n  ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if want_json:
        payload = {
            "total_drawers": (bundle.get("status") or {}).get("total_drawers", 0),
            "wings": (bundle.get("status") or {}).get("wings") or {},
            "rooms": (bundle.get("status") or {}).get("rooms") or {},
            "kg": bundle.get("kg") or {},
            "graph": bundle.get("graph") or {},
        }
        if want_tags:
            payload["tags"] = bundle.get("tags") or {}
        _emit_json(payload)
        return

    _print_stats_dashboard(bundle, top=top)


def cmd_repair_status(args):
    """Read-only HNSW capacity health check (#1222)."""
    from .repair import status as repair_status

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path
    repair_status(palace_path=palace_path)


def cmd_repair(args):
    """Repair palace state.

    Default mode is full HNSW rebuild via extract + re-upsert
    (``--mode rebuild`` / ``--mode legacy``, synonyms). Also handles
    ``--mode max-seq-id`` for un-poisoning ``max_seq_id`` rows
    corrupted by the legacy 0.6.x → 1.5.x chromadb migration shim
    (#1208 / #1288 family). The earlier ``reorganize`` mode was
    retired alongside the recovery collection (PR #8 / row 32).

    Closes Copilot finding on jphein/mempalace#8: docstring claimed
    only "rebuild" while the function continued to dispatch
    ``max-seq-id`` based on ``args.mode``.
    """
    import shutil
    from .backends.chroma import ChromaBackend
    from .migrate import confirm_destructive_action, contains_palace_database
    from .repair import (
        RebuildCollectionError,
        TruncationDetected,
        _close_chroma_handles,
        _extract_drawers,
        _rebuild_collection_via_temp,
        check_extraction_safety,
        maybe_repair_poisoned_max_seq_id_before_rebuild,
        print_sqlite_integrity_abort,
        sqlite_integrity_errors,
    )

    config = MempalaceConfig()
    collection_name = config.collection_name
    palace_path = os.path.abspath(
        os.path.expanduser(args.palace) if args.palace else config.palace_path
    )

    if getattr(args, "mode", "legacy") == "max-seq-id":
        from .repair import repair_max_seq_id

        repair_max_seq_id(
            palace_path,
            segment=getattr(args, "segment", None),
            from_sidecar=getattr(args, "from_sidecar", None),
            backup=getattr(args, "backup", True),
            dry_run=getattr(args, "dry_run", False),
            assume_yes=getattr(args, "yes", False),
        )
        return

    if getattr(args, "mode", "legacy") == "from-sqlite":
        from .migrate import confirm_destructive_action
        from .repair import RebuildPartialError, rebuild_from_sqlite

        source_path = getattr(args, "source", None)
        source_path = (
            os.path.abspath(os.path.expanduser(source_path)) if source_path else palace_path
        )
        archive_existing = getattr(args, "archive_existing", False)

        # Gate any path that touches the user's existing palace dir
        # behind confirm_destructive_action. The legacy mode already
        # gates; from-sqlite needs the same protection because:
        # (a) --archive-existing renames the existing palace,
        # (b) --source PATH writes into --palace dir which the user
        #     may not realize is also a palace.
        # No prompt when source != dest AND dest does not exist (pure
        # extract-into-fresh-dir case is non-destructive to existing
        # palaces).
        is_destructive_to_dest = source_path == palace_path or os.path.exists(palace_path)
        if is_destructive_to_dest and not confirm_destructive_action(
            "Rebuild from SQLite", palace_path, assume_yes=getattr(args, "yes", False)
        ):
            return

        try:
            counts = rebuild_from_sqlite(
                source_palace=source_path,
                dest_palace=palace_path,
                archive_existing_dest=archive_existing,
            )
        except RebuildPartialError as exc:
            # The error itself was already printed by rebuild_from_sqlite
            # with recovery instructions; surface a non-zero exit so
            # scripts and CI gates see the failure.
            print(
                "\n  Rebuild partial — see message above. "
                f"Failed in collection: {exc.failed_collection}"
            )
            sys.exit(1)
        # An empty counts dict is rebuild_from_sqlite's documented signal
        # for a validation refusal (missing source, existing dest,
        # in-place without --archive-existing). The library already
        # printed an actionable message; exit non-zero so unattended
        # scripts/CI distinguish "invalid inputs" from a successful
        # rebuild that legitimately found zero rows (which still returns
        # a populated dict with 0-valued counts).
        if not counts:
            sys.exit(1)
        return

    db_path = os.path.join(palace_path, "chroma.sqlite3")

    if not os.path.isdir(palace_path):
        _print_retired_local_palace_or_default(palace_path)
        return
    if not contains_palace_database(palace_path):
        print(f"\n No palace database found at {db_path}")
        return

    # Run the SQLite integrity preflight before any chromadb client open.
    # ChromaDB's rust binding raises pyo3_runtime.PanicException on a
    # malformed page, which is not a regular Exception subclass and
    # propagates past the try/except below — the user gets a 30-line
    # stack trace instead of the friendly abort message. Run quick_check
    # here so we can surface the clear recovery instructions and exit
    # cleanly before chromadb's compactor touches the disk.
    sqlite_errors = sqlite_integrity_errors(palace_path)
    if sqlite_errors:
        print_sqlite_integrity_abort(palace_path, sqlite_errors)
        sys.exit(1)

    preflight = maybe_repair_poisoned_max_seq_id_before_rebuild(
        palace_path,
        backup=getattr(args, "backup", True),
        dry_run=getattr(args, "dry_run", False),
        assume_yes=getattr(args, "yes", False),
    )
    if preflight is not None:
        return

    print(f"\n{'=' * 55}")
    print(" MemPalace Repair")
    print(f"{'=' * 55}\n")
    print(f"  Palace: {palace_path}")

    backend = ChromaBackend()
    from .backends.base import PalaceRef

    # Try to read existing drawers
    try:
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name=collection_name,
        )
        total = col.count()
        print(f"  Drawers found: {total}")
    except Exception as e:
        print(f"  Error reading palace: {e}")
        print("  Cannot recover — palace may need to be re-mined from source files.")
        return

    if total == 0:
        print("  Nothing to repair.")
        return

    if not confirm_destructive_action(
        "Repair", palace_path, assume_yes=getattr(args, "yes", False)
    ):
        return

    # Extract all drawers in batches
    print("\n  Extracting drawers...")
    batch_size = 5000
    all_ids, all_docs, all_metas, all_embeddings = _extract_drawers(col, total, batch_size)
    emb_status = (
        "with stored embeddings"
        if all_embeddings is not None
        else "without embeddings (will recompute)"
    )
    print(f"  Extracted {len(all_ids)} drawers ({emb_status})")

    # ── #1208 guard ──────────────────────────────────────────────────
    # Cross-check against the SQLite ground truth before doing anything
    # destructive. Catches the user-reported case where chromadb's
    # collection-layer get() silently caps at 10,000 rows even on much
    # larger palaces (e.g. after manual HNSW quarantine). Override with
    # --confirm-truncation-ok only after independently verifying the
    # extraction count is real.
    try:
        check_extraction_safety(
            palace_path,
            len(all_ids),
            confirm_truncation_ok=getattr(args, "confirm_truncation_ok", False),
            collection_name=collection_name,
        )
    except TruncationDetected as e:
        print(e.message)
        return

    palace_path = os.path.normpath(palace_path)
    backup_path = palace_path + ".backup"
    if os.path.exists(backup_path):
        if not contains_palace_database(backup_path):
            print(
                "  Backup validation failed: backup path exists but does not contain chroma.sqlite3. "
                f"Please remove or rename: {backup_path}"
            )
            return
        shutil.rmtree(backup_path)
    print(f"  Backing up to {backup_path}...")
    shutil.copytree(palace_path, backup_path)

    try:
        filed = _rebuild_collection_via_temp(
            backend,
            palace_path,
            all_ids,
            all_docs,
            all_metas,
            batch_size,
            collection_name=collection_name,
            progress=print,
            all_embeddings=all_embeddings,
        )
    except RebuildCollectionError as e:
        print(f"  Repair failed: {e}")
        if getattr(e, "live_replaced", False):
            print("  Live collection was already replaced; restoring from backup...")
            try:
                _close_chroma_handles(palace_path, backend=backend)
                if os.path.exists(palace_path):
                    shutil.rmtree(palace_path)
                shutil.copytree(backup_path, palace_path)
                print(f"  Restore complete from backup: {backup_path}")
            except Exception as restore_error:
                print(f"  Automatic restore failed: {restore_error}")
                print("  Manual recovery required:")
                print(f"    1. Remove or rename the broken directory: {palace_path}")
                print(f"    2. Restore the backup directory to: {palace_path}")
                print(f"       Backup location: {backup_path}")
        sys.exit(1)

    print(f"\n  Repair complete. {filed} drawers rebuilt.")
    print(f"  Backup saved at {backup_path}")
    print(f"\n{'=' * 55}\n")


def cmd_hook(args):
    """Run hook logic: reads JSON from stdin, outputs JSON to stdout."""
    from .hooks_cli import run_hook

    run_hook(hook_name=args.hook, harness=args.harness)


def cmd_instructions(args):
    """Output skill instructions to stdout."""
    from .instructions_cli import run_instructions

    run_instructions(name=args.name)


def cmd_mcp(args):
    """Show how to wire MemPalace into MCP-capable hosts."""
    base_server_cmd = "mempalace-mcp"

    if args.palace:
        resolved_palace = str(Path(args.palace).expanduser())
        server_cmd = f"{base_server_cmd} --palace {shlex.quote(resolved_palace)}"
    else:
        server_cmd = base_server_cmd

    print("MemPalace MCP quick setup:")
    print(f"  claude mcp add mempalace -- {server_cmd}")
    print(f"  codex mcp add mempalace -- {server_cmd}")
    print("\nRun the server directly:")
    print(f"  {server_cmd}")

    if not args.palace:
        print("\nOptional custom palace:")
        print(f"  claude mcp add mempalace -- {base_server_cmd} --palace /path/to/palace")
        print(f"  codex mcp add mempalace -- {base_server_cmd} --palace /path/to/palace")
        print(f"  {base_server_cmd} --palace /path/to/palace")


def cmd_compress(args):
    """Compress drawers in a wing using AAAK Dialect."""
    from .dialect import Dialect
    from .palace import get_closets_collection

    palace_path = os.path.expanduser(args.palace) if args.palace else MempalaceConfig().palace_path

    # Load dialect (with optional entity config)
    config_path = args.config
    if not config_path:
        for candidate in ["entities.json", os.path.join(palace_path, "entities.json")]:
            if os.path.exists(candidate):
                config_path = candidate
                break

    if config_path and os.path.exists(config_path):
        dialect = Dialect.from_config(config_path)
        print(f"  Loaded entity config: {config_path}")
    else:
        dialect = Dialect()

    # State-aware open: distinguish "no palace" from "initialized but empty"
    # from "corrupt" via the shared helper (#1498). MCP and library callers
    # catch the backend exceptions directly; CLI gets the friendly print.
    from .palace import _open_collection_or_explain

    col = _open_collection_or_explain(palace_path, collection_name="mempalace_drawers")
    if col is None:
        sys.exit(1)

    # Query drawers in batches to avoid SQLite variable limit (~999)
    where = {"wing": args.wing} if args.wing else None
    _BATCH = 500
    docs, metas, ids = [], [], []
    offset = 0
    while True:
        try:
            kwargs = {
                "include": ["documents", "metadatas"],
                "limit": _BATCH,
                "offset": offset,
            }
            if where:
                kwargs["where"] = where
            batch = col.get(**kwargs)
        except Exception as e:
            if not docs:
                print(f"\n  Error reading drawers: {e}")
                sys.exit(1)
            break
        batch_docs = batch.get("documents", [])
        if not batch_docs:
            break
        docs.extend(batch_docs)
        metas.extend(batch.get("metadatas", []))
        ids.extend(batch.get("ids", []))
        offset += len(batch_docs)
        if len(batch_docs) < _BATCH:
            break

    if not docs:
        wing_label = f" in wing '{args.wing}'" if args.wing else ""
        print(f"\n  No drawers found{wing_label}.")
        return

    print(
        f"\n  Compressing {len(docs)} drawers"
        + (f" in wing '{args.wing}'" if args.wing else "")
        + "..."
    )
    print()

    total_original = 0
    total_compressed = 0
    compressed_entries = []

    for doc, meta, doc_id in zip(docs, metas, ids):
        compressed = dialect.compress(doc, metadata=meta)
        stats = dialect.compression_stats(doc, compressed)

        total_original += stats["original_chars"]
        total_compressed += stats["summary_chars"]

        compressed_entries.append((doc_id, compressed, meta, stats))

        if args.dry_run:
            wing_name = meta.get("wing", "?")
            room_name = meta.get("room", "?")
            source = Path(meta.get("source_file", "?")).name
            print(f"  [{wing_name}/{room_name}] {source}")
            print(
                f"    {stats['original_tokens_est']}t -> {stats['summary_tokens_est']}t ({stats['size_ratio']:.1f}x)"
            )
            print(f"    {compressed}")
            print()

    # Store compressed versions (unless dry-run)
    if not args.dry_run:
        try:
            # Route through palace.get_closets_collection so the shared
            # chroma backend (via get_backend("chroma")) is reused — avoids
            # a redundant ChromaBackend instance and its potential WAL-lock
            # contention on Windows.
            comp_col = get_closets_collection(palace_path, create=True)
            for doc_id, compressed, meta, stats in compressed_entries:
                comp_meta = dict(meta)
                comp_meta["compression_ratio"] = round(stats["size_ratio"], 1)
                comp_meta["original_tokens"] = stats["original_tokens_est"]
                comp_col.upsert(
                    ids=[doc_id],
                    documents=[compressed],
                    metadatas=[comp_meta],
                )
            print(
                f"  Stored {len(compressed_entries)} compressed drawers in 'mempalace_closets' collection."
            )
        except Exception as e:
            print(f"  Error storing compressed drawers: {e}")
            sys.exit(1)

    # Summary
    ratio = total_original / max(total_compressed, 1)
    # Estimate tokens from char count (~3.8 chars/token for English text)
    orig_tokens = max(1, int(total_original / 3.8))
    comp_tokens = max(1, int(total_compressed / 3.8))
    print(f"  Total: {orig_tokens:,}t -> {comp_tokens:,}t ({ratio:.1f}x compression)")
    if args.dry_run:
        print("  (dry run -- nothing stored)")


def _reconfigure_stdio_utf8_on_windows():
    """Decode stdio as UTF-8 on Windows for the primary `mempalace` CLI.

    Thin wrapper around the shared helper in ``mempalace._stdio``. The CLI
    overrides stdout/stderr to ``replace`` because ``mempalace search``
    prints verbatim drawer text that may carry surrogate halves
    round-tripped from filenames -- ``strict`` would crash mid-print and
    lose the rest of the search result block. stdin keeps the default
    ``surrogateescape`` so a redirected non-UTF-8 file does not kill the
    read on the first bad byte.
    """
    from ._stdio import reconfigure_stdio_utf8_on_windows

    reconfigure_stdio_utf8_on_windows(stdout_errors="replace", stderr_errors="replace")


def main():
    """CLI entry point for the ``mempalace`` console script.

    Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
    any subprocess this CLI spawns inherits a clean env. Host applications
    that call ``main()`` programmatically should be aware that the parent
    process loses ``PYTHONPATH`` as well. Library imports
    (``import mempalace.searcher`` from a host app) do NOT trigger this
    side effect; only the CLI/MCP entry points pop the env var.
    """
    # Drop leaked PYTHONPATH so any subprocess the CLI spawns (mine workers,
    # repair tooling) starts with a clean env. The sys.path filter in
    # mempalace/__init__.py already protects this process from the same
    # ABI mismatch; here we extend the protection to children.
    os.environ.pop("PYTHONPATH", None)

    _reconfigure_stdio_utf8_on_windows()

    version_label = f"MemPalace {__version__}"
    parser = argparse.ArgumentParser(
        description="MemPalace — Give your AI a memory. No API key required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"{version_label}\n\n{__doc__}",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_label,
        help="Show version and exit",
    )
    parser.add_argument(
        "--palace",
        default=None,
        help="Where the palace lives (default: from ~/.mempalace/config.json or ~/.mempalace/palace)",
    )
    # ── Agent-shaped output (issue #44) ──────────────────────────────
    # Both flags are global so any subcommand can opt in. They're also
    # registered on each subparser below so users can write either
    # ``mempalace --json status`` or the more natural
    # ``mempalace status --json``.
    parser.add_argument(
        "--json",
        "-j",
        dest="json",
        action="store_true",
        default=False,
        help="Emit JSON to stdout (implies --quiet; suitable for shell pipelines)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        dest="quiet",
        action="store_true",
        default=False,
        help="Suppress decorative output (headers, progress, routing announcement)",
    )

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Detect rooms from your folder structure")
    p_init.add_argument("dir", help="Project directory to set up")
    p_init.add_argument(
        "--yes",
        action="store_true",
        help="Auto-accept all detected entities (non-interactive)",
    )
    p_init.add_argument(
        "--auto-mine",
        action="store_true",
        help=(
            "Skip the post-init mine prompt and run mine automatically. "
            "Combine with --yes for a fully non-interactive setup."
        ),
    )
    p_init.add_argument(
        "--lang",
        default=None,
        help=(
            "Comma-separated language codes for entity detection "
            "(e.g. 'en' or 'en,pt-br'). Defaults to value from config "
            "(MEMPALACE_ENTITY_LANGUAGES env var or config.json), or 'en'. "
            "When given, the value is also persisted to config.json."
        ),
    )
    p_init.add_argument(
        "--llm",
        action="store_true",
        help=(
            "DEPRECATED — LLM-assisted entity refinement is now ON by default. "
            "This flag is preserved for backward compatibility; pass --no-llm "
            "to opt out instead."
        ),
    )
    p_init.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Disable LLM-assisted entity refinement. Run init in heuristics-only "
            "mode (no provider acquisition, no LLM calls). Use when running "
            "without a local LLM and you don't want the graceful-fallback message."
        ),
    )
    p_init.add_argument(
        "--llm-provider",
        default="ollama",
        choices=["ollama", "openai-compat", "anthropic"],
        help="LLM provider (default: ollama). Pass --no-llm to disable LLM-assisted refinement entirely.",
    )
    p_init.add_argument(
        "--llm-model",
        default="gemma4:e4b",
        help="Model name for the chosen provider (default: gemma4:e4b for Ollama).",
    )
    p_init.add_argument(
        "--llm-endpoint",
        default=None,
        help=(
            "Provider endpoint URL. Default for Ollama: http://localhost:11434. "
            "Required for openai-compat."
        ),
    )
    p_init.add_argument(
        "--llm-api-key",
        default=None,
        help=(
            "API key for the provider. For anthropic, defaults to $ANTHROPIC_API_KEY; "
            "for openai-compat, defaults to $OPENAI_API_KEY."
        ),
    )
    p_init.add_argument(
        "--accept-external-llm",
        action="store_true",
        help=(
            "Bypass the interactive consent prompt that fires when an external "
            "LLM is configured via an environment-variable API key (issue #26). "
            "Use this in CI / non-interactive runs where you've already decided "
            "the external send is acceptable."
        ),
    )

    # mine
    p_mine = sub.add_parser("mine", help="Mine files into the palace")
    p_mine.add_argument("dir", help="Directory to mine")
    p_mine.add_argument(
        "--mode",
        choices=["projects", "convos", "session", "extract"],
        default="projects",
        help=(
            "Ingest mode: 'projects' for code/docs (default), 'convos' for chat "
            "exports (one drawer per exchange), 'session' for one addressable "
            "manifest drawer per session file (fork-only; anchor for 'did session X "
            "exist?' queries), 'extract' for office documents (PDF/DOCX/RTF/etc., "
            "requires mempalace[extract])"
        ),
    )
    p_mine.add_argument("--wing", default=None, help="Wing name (default: directory name)")
    p_mine.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Don't respect .gitignore files when scanning project files",
    )
    p_mine.add_argument(
        "--include-ignored",
        action="append",
        default=[],
        help="Always scan these project-relative paths even if ignored; repeat or pass comma-separated paths",
    )
    p_mine.add_argument(
        "--agent",
        default="mempalace",
        help="Your name — recorded on every drawer (default: mempalace)",
    )
    p_mine.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    p_mine.add_argument(
        "--redetect-origin",
        action="store_true",
        help=(
            "Re-run corpus_origin detection on this directory and overwrite "
            "<palace>/.mempalace/origin.json. Useful when the corpus has grown "
            "since `mempalace init` and the stored origin may be stale. "
            "Heuristic-only (no LLM call) — re-run `mempalace init --llm` for "
            "Tier 2 refinement."
        ),
    )
    p_mine.add_argument(
        "--dry-run", action="store_true", help="Show what would be filed without filing"
    )
    p_mine.add_argument(
        "--source",
        default=None,
        metavar="ADAPTER",
        help=(
            "Route through a registered source adapter instead of the built-in "
            "mine pipeline. Available adapters are discovered from the "
            "'mempalace.sources' entry-point group (e.g. filesystem, conversations, "
            "opencode, codex, gemini, aider). Use 'mempalace mine --source list' "
            "to see installed adapters."
        ),
    )
    p_mine.add_argument(
        "--extract",
        choices=["exchange", "general"],
        default="exchange",
        help="Extraction strategy for convos mode: 'exchange' (default) or 'general' (5 memory types)",
    )
    p_mine.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel workers for file processing (default: min(8, cpu_count); 1 = sequential)",
    )
    from . import miner as _miner_for_default

    p_mine.add_argument(
        "--max-chunks-per-file",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Per-file chunk cap; files producing more chunks are skipped with a "
            f"summary counter. Default {_miner_for_default.MAX_CHUNKS_PER_FILE} "
            f"(or MEMPALACE_MAX_CHUNKS_PER_FILE). Set 0 to disable. Lower this on "
            f"Windows if you hit ONNX bad_alloc (#1455)."
        ),
    )

    # sweep
    p_sweep = sub.add_parser(
        "sweep",
        help="Tandem miner: catch anything the primary miner missed "
        "(message-level, timestamp-coordinated, idempotent)",
    )
    p_sweep.add_argument(
        "target",
        help="A .jsonl transcript file, or a directory to scan recursively",
    )

    # sync
    p_sync = sub.add_parser(
        "sync",
        help="Prune drawers whose source files are gitignored, deleted, or moved (#1252)",
    )
    p_sync.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="Project root to sync (optional; auto-detects from drawer metadata)",
    )
    p_sync.add_argument("--wing", default=None, help="Limit to one wing")
    p_sync.add_argument(
        "--root",
        action="append",
        default=[],
        help="Additional project root (repeatable)",
    )
    p_sync.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preview only (default)",
    )
    p_sync.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually delete drawers (overrides --dry-run; requires --wing or a project root)",
    )

    # search
    p_search = sub.add_parser("search", help="Find anything, exact words")
    p_search.add_argument("query", help="What to search for")
    p_search.add_argument("--wing", default=None, help="Limit to one project")
    p_search.add_argument("--room", default=None, help="Limit to one room")
    p_search.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=None,
        metavar="TAG",
        help=(
            "Only return drawers carrying this tag. May be repeated; "
            "multiple --tag flags AND together (drawer must have ALL of them)."
        ),
    )
    p_search.add_argument("--results", type=int, default=5, help="Number of results")
    p_search.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Alias of --results (overrides --results when both given)",
    )
    p_search.add_argument(
        "--mode",
        choices=("auto", "fast", "hybrid"),
        default=None,
        help=(
            "Search mode: auto (default — BM25 when possible, MCP fallback), "
            "fast (BM25 only, ~100ms), hybrid (vector + BM25 + AGE graph, ~500ms)"
        ),
    )
    p_search.add_argument(
        "--format",
        choices=("table", "compact", "full", "json"),
        default=None,
        help=(
            "Output format: table (default, multi-line + relevance bar), "
            "compact (one line per hit), full (table layout, no content "
            "truncation), json (machine-readable; same as --json)"
        ),
    )

    # list — fast direct-to-daemon drawer browser (#191)
    p_list = sub.add_parser(
        "list",
        help="Browse drawers by wing/room metadata (no ranking, no embedding)",
    )
    p_list.add_argument("--wing", default=None, help="Limit to one wing")
    p_list.add_argument("--room", default=None, help="Limit to one room")
    p_list.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max drawers to return (default: 20, max: 1000)",
    )
    p_list.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Pagination offset (default: 0)",
    )
    p_list.add_argument(
        "--format",
        choices=("table", "compact", "full", "json"),
        default=None,
        help=(
            "Output format: table (default, multi-line preview), "
            "compact (one line per drawer), full (labelled sections, "
            "no truncation), json (machine-readable; same as --json)"
        ),
    )

    # compress
    p_compress = sub.add_parser(
        "compress", help="Compress drawers using AAAK Dialect (~30x reduction)"
    )
    p_compress.add_argument("--wing", default=None, help="Wing to compress (default: all wings)")
    p_compress.add_argument(
        "--dry-run", action="store_true", help="Preview compression without storing"
    )
    p_compress.add_argument(
        "--config", default=None, help="Entity config JSON (e.g. entities.json)"
    )

    # export
    p_export = sub.add_parser("export", help="Export palace as browsable markdown files")
    p_export.add_argument(
        "--output",
        "-o",
        default="./palace-export",
        help="Output directory (default: ./palace-export)",
    )

    # wake-up
    p_wakeup = sub.add_parser("wake-up", help="Show L0 + L1 wake-up context (~600-900 tokens)")
    p_wakeup.add_argument("--wing", default=None, help="Wake-up for a specific project/wing")

    # split
    p_split = sub.add_parser(
        "split",
        help="Split concatenated transcript mega-files into per-session files (run before mine)",
    )
    p_split.add_argument("dir", help="Directory containing transcript files")
    p_split.add_argument(
        "--output-dir",
        default=None,
        help="Write split files here (default: same directory as source files)",
    )
    p_split.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be split without writing files",
    )
    p_split.add_argument(
        "--min-sessions",
        type=int,
        default=2,
        help="Only split files containing at least N sessions (default: 2)",
    )

    # hook
    p_hook = sub.add_parser(
        "hook",
        help="Run hook logic (reads JSON from stdin, outputs JSON to stdout)",
    )
    hook_sub = p_hook.add_subparsers(dest="hook_action")
    p_hook_run = hook_sub.add_parser("run", help="Execute a hook")
    p_hook_run.add_argument(
        "--hook",
        required=True,
        choices=["session-start", "stop", "precompact"],
        help="Hook name to run",
    )
    p_hook_run.add_argument(
        "--harness",
        required=True,
        choices=["claude-code", "codex"],
        help="Harness type (determines stdin JSON format)",
    )

    # instructions
    p_instructions = sub.add_parser(
        "instructions",
        help="Output skill instructions to stdout",
    )
    instructions_sub = p_instructions.add_subparsers(dest="instructions_name")
    for instr_name in ["init", "search", "mine", "help", "status"]:
        instructions_sub.add_parser(instr_name, help=f"Output {instr_name} instructions")

    # repair
    p_repair = sub.add_parser(
        "repair",
        help=(
            "Rebuild palace vector index (legacy mode) or un-poison max_seq_id rows "
            "(--mode max-seq-id)"
        ),
    )
    p_repair.add_argument(
        "--yes", action="store_true", help="Skip confirmation for destructive changes"
    )
    p_repair.add_argument(
        "--mode",
        choices=["rebuild", "legacy", "max-seq-id", "from-sqlite"],
        default="legacy",
        help=(
            "rebuild/legacy: full-palace HNSW rebuild via extract + re-upsert (default; "
            "rebuild and legacy are synonyms). "
            "max-seq-id: un-poison max_seq_id rows corrupted by the legacy 0.6.x shim. "
            "from-sqlite: rebuild by reading rows directly from chroma.sqlite3, "
            "bypassing the chromadb client. Use when legacy mode bails because the "
            "chromadb client cannot open the collection."
        ),
    )
    p_repair.add_argument(
        "--confirm-truncation-ok",
        action="store_true",
        help=(
            "Override the #1208 safety guard. Required when chromadb's collection-layer "
            "extraction returns exactly 10,000 drawers and the SQLite ground-truth check "
            "either matches or can't be read. Use only after independently confirming "
            "the palace really contains that count."
        ),
    )
    p_repair.add_argument(
        "--source",
        default=None,
        help=(
            "Source palace path for --mode from-sqlite (defaults to --palace). "
            "Use when extracting from an archived corrupt palace into a new location."
        ),
    )
    p_repair.add_argument(
        "--archive-existing",
        action="store_true",
        help=(
            "For --mode from-sqlite when --source equals --palace: rename the "
            "existing palace to <palace>.pre-rebuild-<timestamp> before "
            "rebuilding so the corrupt copy is preserved."
        ),
    )
    p_repair.add_argument(
        "--segment",
        default=None,
        help="Segment UUID filter for --mode max-seq-id (repairs only that segment).",
    )
    p_repair.add_argument(
        "--from-sidecar",
        default=None,
        help=(
            "Path to a pre-corruption chroma.sqlite3 sidecar (for --mode max-seq-id); "
            "clean values are copied from its max_seq_id table verbatim."
        ),
    )
    p_repair.add_argument(
        "--backup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Back up SQLite before mutation (default: on)",
    )
    p_repair.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected poisoned rows and exit without mutation (--mode max-seq-id only)",
    )

    # repair-status — read-only HNSW capacity health check (#1222)
    sub.add_parser(
        "repair-status",
        help="Compare sqlite vs HNSW element counts (read-only; never opens a chromadb client)",
    )

    # mcp
    sub.add_parser(
        "mcp",
        help="Show MCP setup command for connecting MemPalace to your AI client",
    )

    # status
    # migrate
    p_migrate = sub.add_parser(
        "migrate",
        help="Migrate palace from a different ChromaDB version (fixes 3.0.0 → 3.1.0 upgrade)",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without changing anything",
    )
    p_migrate.add_argument(
        "--yes", action="store_true", help="Skip confirmation for destructive changes"
    )

    p_mig_pg = sub.add_parser(
        "migrate-to-postgres",
        help="Migrate a ChromaDB palace to Postgres (pgvector + AGE)",
    )
    p_mig_pg.add_argument(
        "--from",
        dest="from_palace",
        required=True,
        help="Path to source ChromaDB palace directory",
    )
    p_mig_pg.add_argument(
        "--to",
        dest="to_dsn",
        required=True,
        help="Postgres DSN of target (e.g. postgresql://user:pass@host/db)",
    )
    p_mig_pg.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Drawer batch size for the drawer-copy step (default 1000)",
    )
    p_mig_pg.add_argument(
        "--dry-run",
        action="store_true",
        help="Run preflight only; no writes to the target",
    )

    p_purge = sub.add_parser(
        "purge",
        help="Delete drawers by wing, room, and/or source-file (filtered delete via chromadb)",
    )
    p_purge.add_argument("--wing", help="Wing to purge")
    p_purge.add_argument("--room", help="Room to purge (without --wing, purges across ALL wings)")
    p_purge.add_argument(
        "--source-file",
        help="Source-file path to purge (matches metadata.source_file exactly)",
    )
    p_purge.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    p_prune = sub.add_parser(
        "prune",
        help="Delete drawers older than --stale-days N (dry-run unless --confirm)",
    )
    p_prune.add_argument(
        "--stale-days",
        type=int,
        required=True,
        help="Prune drawers whose filed_at is older than this many days",
    )
    p_prune.add_argument("--wing", help="Limit prune to this wing")
    p_prune.add_argument("--room", help="Limit prune to this room")
    p_prune.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete (without this flag, prune only reports a dry-run count)",
    )

    p_rename_wing = sub.add_parser(
        "rename-wing",
        help="Rename all drawers from one wing to another (atomic on postgres)",
    )
    p_rename_wing.add_argument("--from", dest="from_wing", required=True, help="Source wing name")
    p_rename_wing.add_argument("--to", dest="to_wing", required=True, help="Target wing name")
    p_rename_wing.add_argument(
        "--dry-run", action="store_true", help="Count matching drawers without renaming"
    )
    p_rename_wing.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for non-postgres backends (default: 500)",
    )

    # ── rooms — manage the canonical room set (hybrid-search-taxonomy follow-up) ────
    p_rooms = sub.add_parser(
        "rooms",
        help="Manage the canonical room set (mempalace_canonical_rooms postgres table)",
    )
    rooms_sub = p_rooms.add_subparsers(dest="rooms_cmd", required=True)
    rooms_sub.add_parser("list", help="List all canonical rooms with descriptions")
    p_rooms_add = rooms_sub.add_parser("add", help="Add a new canonical room")
    p_rooms_add.add_argument("name", help="Room slug (lowercase snake_case)")
    p_rooms_add.add_argument(
        "--description",
        default="",
        help="Human-readable description",
    )
    p_rooms_rename = rooms_sub.add_parser(
        "rename", help="Rename a canonical room (cascades to all drawers via ON UPDATE CASCADE)"
    )
    p_rooms_rename.add_argument("old", help="Current room name")
    p_rooms_rename.add_argument("new", help="New room name")
    p_rooms_remove = rooms_sub.add_parser(
        "remove", help="Remove a canonical room (fails if any drawers still in it)"
    )
    p_rooms_remove.add_argument("name", help="Room slug to remove")

    sub.add_parser("status", help="Show what's been filed")

    sub.add_parser(
        "replay",
        help="Drain ~/.mempalace/pending/ by re-issuing queued mine requests to the daemon",
    )

    p_mined = sub.add_parser(
        "mined",
        help="List mined source files grouped by wing (companion to status, which groups by room)",
    )
    p_mined.add_argument("--wing", help="Show only this wing")

    def _nonneg_int(value: str) -> int:
        # Reject negative --limit values; argparse's bare type=int would
        # silently accept e.g. -1 and produce nonsensical "... -2 more"
        # output (Copilot finding on jphein/mempalace#4).
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"expected non-negative integer, got {value!r}")
        if n < 0:
            raise argparse.ArgumentTypeError(f"--limit must be >= 0 (got {n})")
        return n

    p_mined.add_argument(
        "--limit",
        type=_nonneg_int,
        default=50,
        help="Show at most this many sources per wing (default 50; 0 means show all)",
    )

    # stats — palace analytics dashboard (#191)
    p_stats = sub.add_parser(
        "stats",
        help="Palace analytics dashboard (wings, rooms, knowledge graph, tunnels, tags)",
    )
    p_stats.add_argument(
        "--top",
        type=_nonneg_int,
        default=10,
        help="Show at most this many rows per section (default 10; 0 means show all)",
    )
    p_stats.add_argument(
        "--tags",
        action="store_true",
        help="Include the tag-count breakdown (extra daemon call)",
    )

    # ── Propagate --json/--quiet to every subparser (issue #44) ─────
    # argparse parses pre-subcommand flags into ``args.json`` /
    # ``args.quiet`` only if they appear BEFORE the subcommand. To let
    # users write the natural ``mempalace status --json``, attach the
    # same flags to each subparser so post-subcommand usage parses too.
    # Help is suppressed on the per-subparser copies to keep ``--help``
    # output uncluttered — the canonical docs live on the top-level parser.
    for _sp in sub.choices.values():
        # Skip the two-level parents (hook, instructions, rooms) — their
        # actions are owned by nested sub-parsers and the leaf parsers
        # don't need JSON output (hook/instructions write their own JSON;
        # rooms is operator-only). Detect them by the presence of a
        # registered subparsers action.
        has_nested = any(isinstance(a, argparse._SubParsersAction) for a in _sp._actions)
        if has_nested:
            continue
        _sp.add_argument(
            "--json",
            "-j",
            dest="json",
            action="store_true",
            default=False,
            help=argparse.SUPPRESS,
        )
        _sp.add_argument(
            "--quiet",
            "-q",
            dest="quiet",
            action="store_true",
            default=False,
            help=argparse.SUPPRESS,
        )

    args = parser.parse_args()

    # When ``--json`` / ``--quiet`` was passed at top level, the
    # subparser-side default would clobber it (argparse stores per-action
    # defaults). Restore the top-level value if the subparser-side flag
    # wasn't explicitly set. Cheapest way: check ``sys.argv`` for the
    # token — explicit flag in argv means the user asked for it.
    _argv_after_command = sys.argv[1:]
    if any(t in ("--json", "-j") for t in _argv_after_command):
        args.json = True
    if any(t in ("--quiet", "-q") for t in _argv_after_command):
        args.quiet = True

    if not args.command:
        parser.print_help()
        return

    # Handle two-level subcommands
    if args.command == "hook":
        if not getattr(args, "hook_action", None):
            p_hook.print_help()
            return
        cmd_hook(args)
        return

    if args.command == "instructions":
        name = getattr(args, "instructions_name", None)
        if not name:
            p_instructions.print_help()
            return
        args.name = name
        cmd_instructions(args)
        return

    dispatch = {
        "init": cmd_init,
        "mine": cmd_mine,
        "split": cmd_split,
        "search": cmd_search,
        "list": cmd_list,
        "export": cmd_export,
        "sweep": cmd_sweep,
        "sync": cmd_sync,
        "mcp": cmd_mcp,
        "compress": cmd_compress,
        "wake-up": cmd_wakeup,
        "repair": cmd_repair,
        "repair-status": cmd_repair_status,
        "migrate": cmd_migrate,
        "migrate-to-postgres": cmd_migrate_to_postgres,
        "purge": cmd_purge,
        "prune": cmd_prune,
        "rename-wing": cmd_rename_wing,
        "rooms": cmd_rooms,
        "status": cmd_status,
        "stats": cmd_stats,
        "mined": cmd_mined,
        "replay": cmd_replay,
    }

    # Issue #49: announce the routing decision to stderr when daemon_url is
    # set, regardless of strict mode. Silent on the pure-local default (no
    # URL configured anywhere) since that's upstream's expected behavior
    # and announcing it on every CLI invocation would be noise. Surfaces:
    #   - daemon-strict on   → "routing → daemon @ URL (source: env|config)"
    #   - daemon-strict off  → "routing → local (PALACE_DAEMON_STRICT=0 overrides
    #                            daemon_url=URL)"
    # Diagnoses the silent split-brain failure mode the issue documents.
    try:
        _cfg = MempalaceConfig()
        # Suppress the routing chrome when --json / --quiet is on, or
        # when stdout isn't a TTY (piped). The announcement is for
        # interactive humans; agents capturing both streams expect a
        # clean surface (issue #44).
        _suppress_routing = (
            getattr(args, "json", False) or getattr(args, "quiet", False) or _resolve_quiet(args)
        )
        if _cfg.daemon_url and args.command not in (None, "--help") and not _suppress_routing:
            _src = "env" if os.environ.get("PALACE_DAEMON_URL", "").strip() else "config"
            if _cfg.daemon_strict:
                print(
                    f"mempalace: routing → daemon @ {_cfg.daemon_url} (source: {_src})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"mempalace: routing → local (PALACE_DAEMON_STRICT=0 overrides "
                    f"daemon_url={_cfg.daemon_url} from {_src})",
                    file=sys.stderr,
                )
    except Exception:
        # Never let routing-announce crash a CLI invocation.
        pass

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
