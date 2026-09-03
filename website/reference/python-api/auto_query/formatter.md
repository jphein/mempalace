# `mempalace.auto_query.formatter`

Source: [`mempalace/auto_query/formatter.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/auto_query/formatter.py)

Result formatter for the auto-query integration.

Takes an MCP tool call result and formats it for injection into the Claude Code
system message.  Output is verbatim snippets from palace drawers, wrapped in
sentinel tokens for reliable detection by feedback collectors.

The formatter handles four MCP result shapes:
  - mempalace_search  (semantic/hybrid search results)
  - mempalace_kg_query  (knowledge graph entity relationships)
  - mempalace_diary_read  (per-agent diary entries)
  - mempalace_traverse  (cross-wing graph traversal)

## Functions

### `format_injection`

```python
def format_injection(tool_call: MCPCall, mcp_result: dict, signals: SignalSet, latency_ms: int, header_lines: Optional[List[str]] = None) -> Optional[str]
```

Format MCP results as an injection block with sentinel tokens.

Returns the formatted injection string, or ``None`` if no results found.
The output is verbatim -- no summarisation.  Each result is a truncated
preview of the raw drawer/entry content.
