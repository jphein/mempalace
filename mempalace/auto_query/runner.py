"""Auto-query runner — chains signal extraction, routing, and formatting.

This module is the single entry point for the auto-query pipeline.
It is called from the UserPromptSubmit hook (via ``__main__.py``) or
directly from tests.

Pipeline::

    load config → check enabled/mode → extract signals → pick tool
    → execute MCP call → format injection → log decision

The MCP call is made via ``_call_mcp()``, which delegates to the
palace-daemon HTTP proxy (``/mcp`` endpoint).  When the daemon is not
reachable, the call is skipped and a dry-run decision is logged.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from mempalace.auto_query import Decision, SessionState
from mempalace.auto_query.decisions import append_decision, rotate_log
from mempalace.auto_query.formatter import format_injection
from mempalace.auto_query.router import THRESHOLDS, pick_tool
from mempalace.auto_query.signals import extract_signals
from mempalace.config import MempalaceConfig


class AutoQueryResult:
    """Result of a single auto-query pipeline run."""

    __slots__ = ("injection", "decision", "tool_call", "mcp_result")

    def __init__(
        self,
        injection=None,  # type: Optional[str]
        decision=None,  # type: Optional[Decision]
        tool_call=None,  # type: Optional[MCPCall]
        mcp_result=None,  # type: Optional[dict]
    ):
        self.injection = injection
        self.decision = decision
        self.tool_call = tool_call
        self.mcp_result = mcp_result


def run_auto_query(
    prompt,  # type: str
    session_id,  # type: str
    turn,  # type: int
    project_wing="",  # type: str
    known_wings=None,  # type: Optional[set]
    known_entities=None,  # type: Optional[set]
    has_recent_drawers=False,  # type: bool
    config=None,  # type: Optional[MempalaceConfig]
    queried_entities=None,  # type: Optional[set]
    log_dir=None,  # type: Optional[str]
):
    # type: (...) -> AutoQueryResult
    """Run the auto-query pipeline for a single user turn.

    Returns an ``AutoQueryResult`` with the injection text (or None),
    the decision record, and the raw MCP result.
    """
    if config is None:
        config = MempalaceConfig()

    if not config.auto_query_enabled:
        return AutoQueryResult()

    mode = config.auto_query_mode
    if mode == "off":
        return AutoQueryResult()

    if known_wings is None:
        known_wings = set()

    session_state = SessionState(
        turn_index=turn,
        queried_entities=queried_entities or set(),
        session_id=session_id,
    )

    signals = extract_signals(
        text=prompt,
        session_state=session_state,
        project_wing=project_wing,
        known_wings=known_wings,
        known_entities=known_entities,
        has_recent_drawers=has_recent_drawers,
    )

    tool_call = pick_tool(signals, mode, session_state)

    threshold = THRESHOLDS.get(mode, float("inf"))
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if tool_call is None:
        decision = Decision(
            ts=ts,
            session_id=session_id,
            turn=turn,
            signals=_serialize_signals(signals),
            score=signals.total_score,
            threshold=int(threshold) if threshold != float("inf") else 999,
            mode=mode,
            decision="skip",
            reason="below threshold or no signal pattern",
        )
        _safe_log(decision, log_dir)
        return AutoQueryResult(decision=decision)

    if mode == "dry-run":
        decision = Decision(
            ts=ts,
            session_id=session_id,
            turn=turn,
            signals=_serialize_signals(signals),
            score=signals.total_score,
            threshold=int(threshold),
            mode=mode,
            decision="dry-run-skip",
            reason="dry-run mode — would fire",
            tool=tool_call.tool,
            args=tool_call.args,
        )
        _safe_log(decision, log_dir)
        return AutoQueryResult(decision=decision, tool_call=tool_call)

    # Live mode: execute the MCP call.
    t0 = time.monotonic()
    mcp_result = _call_mcp(tool_call, config)
    latency_ms = int((time.monotonic() - t0) * 1000)

    if mcp_result is None:
        decision = Decision(
            ts=ts,
            session_id=session_id,
            turn=turn,
            signals=_serialize_signals(signals),
            score=signals.total_score,
            threshold=int(threshold),
            mode=mode,
            decision="skip",
            reason="daemon unreachable",
            tool=tool_call.tool,
            args=tool_call.args,
            latency_ms=latency_ms,
        )
        _safe_log(decision, log_dir)
        return AutoQueryResult(decision=decision, tool_call=tool_call)

    injection = format_injection(tool_call, mcp_result, signals, latency_ms)

    result_count = _count_results(mcp_result)
    injection_tokens = len(injection) // 4 if injection else 0

    decision_str = "fire" if injection else "skip"
    reason = "results formatted" if injection else "no results from MCP"

    decision = Decision(
        ts=ts,
        session_id=session_id,
        turn=turn,
        signals=_serialize_signals(signals),
        score=signals.total_score,
        threshold=int(threshold),
        mode=mode,
        decision=decision_str,
        reason=reason,
        tool=tool_call.tool,
        args=tool_call.args,
        latency_ms=latency_ms,
        result_drawers=result_count,
        injection_tokens=injection_tokens,
    )
    _safe_log(decision, log_dir)

    # Mark entities as queried so they don't fire again this session.
    for sig in signals.entity:
        session_state.queried_entities.add(sig.name)

    return AutoQueryResult(
        injection=injection,
        decision=decision,
        tool_call=tool_call,
        mcp_result=mcp_result,
    )


def _call_mcp(tool_call, config):
    # type: (MCPCall, MempalaceConfig) -> Optional[dict]
    """Call palace-daemon's /mcp HTTP proxy.

    Returns the parsed JSON result dict, or None if the daemon is
    unreachable or returns an error.
    """
    daemon_url = config.daemon_url
    if not daemon_url:
        return None

    url = "{}/mcp".format(daemon_url.rstrip("/"))
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_call.tool,
                "arguments": tool_call.args,
            },
            "id": 1,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("PALACE_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            rpc_response = json.loads(body)
            return _extract_mcp_result(rpc_response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def _extract_mcp_result(rpc_response):
    # type: (dict) -> Optional[dict]
    """Extract the tool result from a JSON-RPC MCP response.

    The daemon returns ``{"jsonrpc":"2.0","result":{"content":[{"type":"text",
    "text":"..."}]}}``.  The ``text`` field is itself a JSON string containing
    the actual result dict (search results, KG facts, diary entries, etc.).
    """
    if not isinstance(rpc_response, dict):
        return None
    result = rpc_response.get("result")
    if not isinstance(result, dict):
        return None
    content = result.get("content", [])
    if not content:
        return None
    text = content[0].get("text", "") if isinstance(content[0], dict) else ""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _serialize_signals(signals):
    # type: (SignalSet) -> dict
    """Serialize a SignalSet for the decision log."""
    return {
        "entity": [{"name": s.name, "score": s.score, "wing": s.wing} for s in signals.entity],
        "temporal": [{"name": s.name, "phrase": s.phrase} for s in signals.temporal],
        "resumption": signals.resumption,
        "explicit": signals.explicit,
        "depth_fire": signals.depth_fire,
        "total_score": signals.total_score,
        "project_wing": signals.project_wing,
    }


def _count_results(mcp_result):
    # type: (dict) -> int
    """Count result items from an MCP response."""
    for key in ("results", "entries", "nodes"):
        items = mcp_result.get(key)
        if isinstance(items, list):
            return len(items)
    outgoing = mcp_result.get("outgoing", [])
    incoming = mcp_result.get("incoming", [])
    return len(outgoing) + len(incoming)


def _safe_log(decision, log_dir):
    # type: (Decision, Optional[str]) -> None
    """Log a decision, swallowing any I/O errors."""
    try:
        append_decision(decision, log_dir=log_dir)
        rotate_log(log_dir=log_dir)
    except OSError:
        pass
