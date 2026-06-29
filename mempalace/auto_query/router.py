"""Tool router for auto-query integration.

Pure function: maps a SignalSet to an MCPCall (or None).  No I/O, no MCP
calls, no side effects.  The router is stateless; rate limiting and
deduplication are the caller's responsibility.

Priority order (from spec section 2):
  1. Task resumption  -> mempalace_diary_read  (highest precision)
  2. Explicit hint     -> mempalace_search      (user is asking directly)
  3. Entity + temporal -> mempalace_kg_query    (entity-scoped history)
  4. Entity only       -> mempalace_search      (entity-scoped)
  5. Temporal only     -> mempalace_search      (project-scoped recent)
"""

from typing import Optional

from mempalace.auto_query import MCPCall, SessionState, SignalSet

# Minimum total_score required for each mode before the router fires.
# "off" uses infinity so the threshold is never reached.
THRESHOLDS = {
    "off": float("inf"),
    "dry-run": 4,
    "conservative": 6,
    "balanced": 4,
    "aggressive": 2,
}

# Maximum length for search query strings passed to MCP tools.
_MAX_QUERY_LEN = 200


def pick_tool(
    signals: SignalSet,
    mode: str,
    session_state: SessionState,
) -> Optional[MCPCall]:
    """Select the MCP tool to invoke based on extracted signals.

    Returns None if the score is below the threshold for the given mode,
    or if no meaningful signal pattern is detected.

    The router does NOT call ``query_sanitizer.sanitize_query()`` itself --
    it performs lightweight truncation only.  The runner (harness shim)
    is responsible for full sanitization before calling the router.
    """
    threshold = THRESHOLDS.get(mode, float("inf"))
    if signals.total_score < threshold:
        return None

    return _select_tool(signals)


def _select_tool(signals: SignalSet) -> Optional[MCPCall]:
    """Route signals to the best MCP tool.

    Returns None if no signal pattern matches (edge case: score above
    threshold but no individual signal flags are set).
    """
    # Priority 1: Task resumption
    if signals.resumption:
        return MCPCall(
            tool="mempalace_diary_read",
            args={
                "agent_name": "claude-code",
                "wing": signals.project_wing,
                "last_n": 3,
            },
        )

    # Priority 1.5: Periodic depth refresh — a broad project-scoped pull to
    # re-anchor context mid-session. Sits just below task resumption and above
    # every content-derived signal (explicit/entity/temporal).
    if signals.depth_fire:
        query = "session context {}".format(signals.project_wing).strip()
        args = {"query": query, "limit": 3}
        if signals.project_wing:
            args["wing"] = signals.project_wing
        return MCPCall(tool="mempalace_search", args=args)

    # Priority 2: Explicit recall hint
    if signals.explicit:
        query = _sanitize_for_search(signals.query_text)
        return MCPCall(
            tool="mempalace_search",
            args={"query": query, "limit": 10},
        )

    # Priority 3: Entity + temporal compound
    if signals.entity and signals.temporal:
        best = max(signals.entity, key=lambda s: s.score)
        return MCPCall(
            tool="mempalace_kg_query",
            args={"entity": best.name, "direction": "both"},
        )

    # Priority 4: Entity only
    if signals.entity:
        best = max(signals.entity, key=lambda s: s.score)
        args = {"query": best.name, "limit": 5}
        if best.wing:
            args["wing"] = best.wing
        return MCPCall(tool="mempalace_search", args=args)

    # Priority 5: Temporal only
    if signals.temporal:
        query = _sanitize_for_search(signals.query_text)
        args = {"query": query, "limit": 5}
        if signals.project_wing:
            args["wing"] = signals.project_wing
        return MCPCall(tool="mempalace_search", args=args)

    # No signal pattern matched despite score >= threshold
    return None


def _sanitize_for_search(text: str) -> str:
    """Lightweight sanitization for search queries.

    Strips leading/trailing whitespace and truncates to ``_MAX_QUERY_LEN``
    characters.  For full sanitization, the runner should use
    ``query_sanitizer.sanitize_query()`` before calling the router.
    """
    return text.strip()[:_MAX_QUERY_LEN]
