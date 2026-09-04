"""#408: session_id is threaded from tool_checkpoint's diary into tool_diary_write
and declared in both tools' input schemas (it was a write-only dead slot)."""

from unittest.mock import patch

from mempalace import mcp_server


def _tool(name):
    return (
        next(t for t in mcp_server.TOOLS_LIST if t["name"] == name)
        if hasattr(mcp_server, "TOOLS_LIST")
        else mcp_server.TOOLS[name]
    )


def test_schemas_declare_session_id():
    for name in ("mempalace_checkpoint", "mempalace_diary_write"):
        tool = _tool(name)
        props = tool["input_schema"]["properties"]
        if name == "mempalace_checkpoint":
            assert "session_id" in props["diary"]["properties"]
        else:
            assert "session_id" in props


def test_checkpoint_passes_session_id_to_diary_write():
    captured = {}

    def fake_diary_write(**kw):
        captured.update(kw)
        return {"success": True}

    with (
        patch.object(mcp_server, "tool_diary_write", side_effect=fake_diary_write),
        patch.object(
            mcp_server, "tool_check_duplicate", return_value={"is_duplicate": False}, create=True
        ),
        patch.object(
            mcp_server,
            "tool_add_drawer",
            return_value={"success": True, "drawer_id": "d"},
            create=True,
        ),
    ):
        mcp_server.tool_checkpoint(
            items=[],
            diary={"agent_name": "claude-code", "entry": "SESSION:x", "session_id": "sess-123"},
        )
    assert captured.get("session_id") == "sess-123"
