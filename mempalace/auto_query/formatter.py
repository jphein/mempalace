"""Result formatter for the auto-query integration.

Takes an MCP tool call result and formats it for injection into the Claude Code
system message.  Output is verbatim snippets from palace drawers, wrapped in
sentinel tokens for reliable detection by feedback collectors.

The formatter handles four MCP result shapes:
  - mempalace_search  (semantic/hybrid search results)
  - mempalace_kg_query  (knowledge graph entity relationships)
  - mempalace_diary_read  (per-agent diary entries)
  - mempalace_traverse  (cross-wing graph traversal)
"""

from typing import List, Optional, Tuple

from mempalace.auto_query import MCPCall, SignalSet

SENTINEL_OPEN = "[mempalace:auto-query]"
SENTINEL_CLOSE = "[/mempalace:auto-query]"
MAX_INJECTION_CHARS = 6000  # ~1500 tokens
MAX_PREVIEW_CHARS = 200  # per-result content preview

# Overhead per injection block: sentinels + trigger + provenance + hint + newlines.
# Measured conservatively; the budget helpers use this to reserve space.
_FRAME_OVERHEAD = 300


def format_injection(
    tool_call: MCPCall,
    mcp_result: dict,
    signals: SignalSet,
    latency_ms: int,
) -> Optional[str]:
    """Format MCP results as an injection block with sentinel tokens.

    Returns the formatted injection string, or ``None`` if no results found.
    The output is verbatim -- no summarisation.  Each result is a truncated
    preview of the raw drawer/entry content.
    """
    tool = tool_call.tool

    # Dispatch by tool name to the appropriate formatter.
    if tool == "mempalace_search":
        results = mcp_result.get("results", [])
        if not results:
            return None
        budget = MAX_INJECTION_CHARS - _FRAME_OVERHEAD
        body_lines, count = _format_search_results(results, budget)
    elif tool == "mempalace_kg_query":
        outgoing = mcp_result.get("outgoing", [])
        incoming = mcp_result.get("incoming", [])
        if not outgoing and not incoming:
            return None
        budget = MAX_INJECTION_CHARS - _FRAME_OVERHEAD
        body_lines, count = _format_kg_results(outgoing, incoming, budget)
    elif tool == "mempalace_diary_read":
        entries = mcp_result.get("entries", [])
        if not entries:
            return None
        budget = MAX_INJECTION_CHARS - _FRAME_OVERHEAD
        body_lines, count = _format_diary_results(entries, budget)
    elif tool == "mempalace_traverse":
        nodes = mcp_result.get("nodes", [])
        edges = mcp_result.get("edges", [])
        if not nodes and not edges:
            return None
        budget = MAX_INJECTION_CHARS - _FRAME_OVERHEAD
        body_lines, count = _format_traverse_results(nodes, edges, budget)
    else:
        return None

    trigger = _format_trigger_line(signals, tool_call)
    provenance = _format_provenance(tool_call, latency_ms)

    lines = [
        SENTINEL_OPEN,
        trigger,
        "results (%d):" % count,
    ]
    lines.extend(body_lines)
    lines.append(provenance)
    lines.append(
        "hint to assistant: cite drawer_id if you use this; ignore the block if irrelevant."
    )
    lines.append(SENTINEL_CLOSE)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trigger line
# ---------------------------------------------------------------------------


def _format_trigger_line(signals: SignalSet, tool_call: MCPCall) -> str:
    """One-line summary of what triggered the auto-query."""
    summary = _signal_summary(signals)
    return "trigger: %s | score=%d | tool=%s" % (
        summary,
        signals.total_score,
        tool_call.tool,
    )


def _signal_summary(signals: SignalSet) -> str:
    """Build trigger summary from active signals."""
    parts = []  # type: List[str]
    for sig in signals.entity:
        parts.append("entity=%s" % sig.name)
    for sig in signals.temporal:
        phrase = sig.phrase or sig.name
        parts.append("temporal='%s'" % phrase)
    if signals.resumption:
        parts.append("resumption")
    if signals.explicit:
        parts.append("explicit")
    return ", ".join(parts) if parts else "unknown"


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------


def _format_search_results(results: list, budget: int) -> Tuple[List[str], int]:
    """Format search results.  Returns (formatted_lines, result_count)."""
    return _budget_reduce(results, budget, _render_search_item)


def _render_search_item(idx: int, item: dict, preview_cap: int) -> str:
    wing = item.get("wing", "?")
    room = item.get("room", "?")
    drawer_id = item.get("drawer_id", "?")
    created_at = item.get("created_at", "")
    date_part = "(%s)" % created_at if created_at else ""
    text = item.get("text", "")
    preview = _truncate(text, preview_cap)
    return "  %d. %s/%s  %s  %s\n     > %s" % (idx, wing, room, drawer_id, date_part, preview)


# ---------------------------------------------------------------------------
# Knowledge-graph results
# ---------------------------------------------------------------------------


def _format_kg_results(outgoing: list, incoming: list, budget: int) -> Tuple[List[str], int]:
    """Format knowledge graph query results."""
    combined = []  # type: list
    for item in outgoing:
        combined.append(("out", item))
    for item in incoming:
        combined.append(("in", item))
    return _budget_reduce(combined, budget, _render_kg_item)


def _render_kg_item(idx: int, pair: tuple, preview_cap: int) -> str:
    direction, item = pair
    if direction == "out":
        subject = item.get("subject", "?")
        predicate = item.get("predicate", "?")
        obj = item.get("object", "?")
        valid_from = item.get("valid_from", "")
        valid_to = item.get("valid_to", "")
        span = _format_time_span(valid_from, valid_to)
        return "  %d. %s -> %s -> %s%s" % (idx, subject, predicate, obj, span)
    else:
        subject = item.get("subject", "?")
        predicate = item.get("predicate", "?")
        obj = item.get("object", "?")
        valid_from = item.get("valid_from", "")
        valid_to = item.get("valid_to", "")
        span = _format_time_span(valid_from, valid_to)
        return "  %d. %s -> %s -> %s%s" % (idx, subject, predicate, obj, span)


def _format_time_span(valid_from: str, valid_to: str) -> str:
    """Render a '(from - to)' suffix if dates are present."""
    if valid_from and valid_to:
        return " (%s - %s)" % (valid_from, valid_to)
    if valid_from:
        return " (%s)" % valid_from
    if valid_to:
        return " (until %s)" % valid_to
    return ""


# ---------------------------------------------------------------------------
# Diary results
# ---------------------------------------------------------------------------


def _format_diary_results(entries: list, budget: int) -> Tuple[List[str], int]:
    """Format diary read results."""
    return _budget_reduce(entries, budget, _render_diary_item)


def _render_diary_item(idx: int, item: dict, preview_cap: int) -> str:
    topic = item.get("topic", "general")
    timestamp = item.get("timestamp", "")
    date_part = "(%s)" % timestamp if timestamp else ""
    entry_text = item.get("entry", "")
    preview = _truncate(entry_text, preview_cap)
    return "  %d. [%s] %s\n     > %s" % (idx, topic, date_part, preview)


# ---------------------------------------------------------------------------
# Traverse results
# ---------------------------------------------------------------------------


def _format_traverse_results(nodes: list, edges: list, budget: int) -> Tuple[List[str], int]:
    """Format graph traversal results."""
    # Build an edge lookup: room -> list of (to_room, via).
    edge_map = {}  # type: dict
    for edge in edges:
        from_room = edge.get("from_room", "")
        to_room = edge.get("to_room", "")
        via = edge.get("via", "")
        edge_map.setdefault(from_room, []).append((to_room, via))

    # Annotate nodes with their edges for rendering.
    annotated = []  # type: list
    for node in nodes:
        room = node.get("room", "?")
        connections = edge_map.get(room, [])
        annotated.append((node, connections))

    return _budget_reduce(annotated, budget, _render_traverse_item)


def _render_traverse_item(idx: int, pair: tuple, preview_cap: int) -> str:
    node, connections = pair
    wing = node.get("wing", "?")
    room = node.get("room", "?")
    hop = node.get("hop", 0)
    header = "  %d. %s/%s (hop %d)" % (idx, wing, room, hop)
    if connections:
        parts = []  # type: List[str]
        for to_room, via in connections:
            if via:
                parts.append("%s (via %s)" % (to_room, via))
            else:
                parts.append(to_room)
        conn_text = _truncate(", ".join(parts), preview_cap)
        return "%s\n     > connected via: %s" % (header, conn_text)
    return header


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _format_provenance(tool_call: MCPCall, latency_ms: int) -> str:
    """Provenance footer showing exact tool call."""
    arg_parts = []  # type: List[str]
    for key, value in sorted(tool_call.args.items()):
        if isinstance(value, str):
            arg_parts.append('%s="%s"' % (key, value))
        else:
            arg_parts.append("%s=%s" % (key, value))
    args_str = ", ".join(arg_parts)
    return "provenance: %s(%s) | latency=%dms" % (tool_call.tool, args_str, latency_ms)


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending '...' if truncated.

    The returned string is always a verbatim prefix of *text* (possibly with
    '...' appended).  Newlines are collapsed to spaces for single-line display.
    """
    # Collapse newlines to spaces for single-line preview.
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _budget_reduce(
    items: list,
    budget: int,
    render_fn,  # Callable[[int, Any, int], str]
) -> Tuple[List[str], int]:
    """Render items within a character budget.

    Strategy: first try all items with full preview cap.  If the total exceeds
    the budget, try reducing preview cap.  If that still exceeds, reduce item
    count through the sequence len(items) -> 5 -> 3 -> 1.
    """
    if not items:
        return ([], 0)

    # Step 1: try full set with full preview.
    lines = _render_all(items, render_fn, MAX_PREVIEW_CHARS)
    total = sum(len(line) for line in lines)
    if total <= budget:
        return (lines, len(items))

    # Step 2: reduce preview cap progressively.
    for preview_cap in (150, 100, 50):
        lines = _render_all(items, render_fn, preview_cap)
        total = sum(len(line) for line in lines)
        if total <= budget:
            return (lines, len(items))

    # Step 3: reduce item count.
    for max_items in (5, 3, 1):
        if max_items >= len(items):
            continue
        subset = items[:max_items]
        lines = _render_all(subset, render_fn, MAX_PREVIEW_CHARS)
        total = sum(len(line) for line in lines)
        if total <= budget:
            return (lines, max_items)
        # Also try with reduced preview on the subset.
        for preview_cap in (150, 100, 50):
            lines = _render_all(subset, render_fn, preview_cap)
            total = sum(len(line) for line in lines)
            if total <= budget:
                return (lines, max_items)

    # Last resort: single item with minimal preview.
    lines = _render_all(items[:1], render_fn, 50)
    return (lines, 1)


def _render_all(
    items: list,
    render_fn,
    preview_cap: int,
) -> List[str]:
    """Render all items using the given render function and preview cap."""
    lines = []  # type: List[str]
    for i, item in enumerate(items):
        lines.append(render_fn(i + 1, item, preview_cap))
    return lines
