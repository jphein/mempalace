# Tool Output Mining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture tool use and tool result blocks from Claude Code JSONL transcripts so Bash output, search results, and command context are mined into the palace instead of silently dropped.

**Architecture:** Enhance `_extract_content()` and `_try_claude_code_jsonl()` in `normalize.py` to recognize `tool_use` and `tool_result` content blocks. Tool-specific formatting strategies (head+tail for Bash, path-only for Read, etc.) are applied during normalization. The chunker and rest of the pipeline are untouched.

**Tech Stack:** Python, json, existing normalize.py module

**Spec:** `docs/superpowers/specs/2026-04-10-tool-output-mining-design.md`

---

### Task 1: Add tool_use formatting helper

**Files:**
- Modify: `mempalace/normalize.py` (add `_format_tool_use` after `_extract_content` at ~line 288)
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_normalize.py` after the existing `_extract_content` tests:

```python
from mempalace.normalize import _format_tool_use


def test_format_tool_use_bash():
    block = {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "lsusb | grep razer", "description": "Check USB"}}
    result = _format_tool_use(block)
    assert result == "[Bash] lsusb | grep razer"


def test_format_tool_use_bash_truncates_long_command():
    block = {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "x" * 300}}
    result = _format_tool_use(block)
    assert len(result) <= len("[Bash] ") + 200 + len("...")
    assert result.endswith("...")


def test_format_tool_use_read():
    block = {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/home/jp/file.py"}}
    result = _format_tool_use(block)
    assert result == "[Read /home/jp/file.py]"


def test_format_tool_use_read_with_range():
    block = {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/home/jp/file.py", "offset": 10, "limit": 50}}
    result = _format_tool_use(block)
    assert result == "[Read /home/jp/file.py:10-60]"


def test_format_tool_use_grep():
    block = {"type": "tool_use", "id": "t1", "name": "Grep",
             "input": {"pattern": "firmware", "path": "/home/jp/proj"}}
    result = _format_tool_use(block)
    assert result == "[Grep] firmware in /home/jp/proj"


def test_format_tool_use_grep_with_glob():
    block = {"type": "tool_use", "id": "t1", "name": "Grep",
             "input": {"pattern": "TODO", "glob": "*.py"}}
    result = _format_tool_use(block)
    assert result == "[Grep] TODO in *.py"


def test_format_tool_use_glob():
    block = {"type": "tool_use", "id": "t1", "name": "Glob",
             "input": {"pattern": "/home/jp/proj/**/*.py"}}
    result = _format_tool_use(block)
    assert result == "[Glob] /home/jp/proj/**/*.py"


def test_format_tool_use_edit():
    block = {"type": "tool_use", "id": "t1", "name": "Edit",
             "input": {"file_path": "/home/jp/file.py", "old_string": "x", "new_string": "y"}}
    result = _format_tool_use(block)
    assert result == "[Edit /home/jp/file.py]"


def test_format_tool_use_write():
    block = {"type": "tool_use", "id": "t1", "name": "Write",
             "input": {"file_path": "/home/jp/file.py", "content": "..."}}
    result = _format_tool_use(block)
    assert result == "[Write /home/jp/file.py]"


def test_format_tool_use_unknown_tool():
    block = {"type": "tool_use", "id": "t1", "name": "mcp__mempalace__search",
             "input": {"query": "firmware probe", "limit": 5}}
    result = _format_tool_use(block)
    assert result.startswith("[mcp__mempalace__search]")
    assert "firmware probe" in result


def test_format_tool_use_unknown_tool_truncates():
    block = {"type": "tool_use", "id": "t1", "name": "SomeTool",
             "input": {"data": "x" * 300}}
    result = _format_tool_use(block)
    assert result.endswith("...")
    assert len(result) <= len("[SomeTool] ") + 200 + len("...")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_normalize.py::test_format_tool_use_bash -v`
Expected: FAIL with `ImportError: cannot import name '_format_tool_use'`

- [ ] **Step 3: Implement `_format_tool_use`**

Add after `_extract_content` (around line 288) in `mempalace/normalize.py`:

```python
def _format_tool_use(block: dict) -> str:
    """Format a tool_use block into a human-readable one-liner."""
    name = block.get("name", "Unknown")
    inp = block.get("input", {})

    if name == "Bash":
        cmd = inp.get("command", "")
        if len(cmd) > 200:
            cmd = cmd[:200] + "..."
        return f"[Bash] {cmd}"

    if name == "Read":
        path = inp.get("file_path", "?")
        offset = inp.get("offset")
        limit = inp.get("limit")
        if offset is not None and limit is not None:
            return f"[Read {path}:{offset}-{offset + limit}]"
        return f"[Read {path}]"

    if name == "Grep":
        pattern = inp.get("pattern", "")
        target = inp.get("path") or inp.get("glob") or ""
        return f"[Grep] {pattern} in {target}"

    if name == "Glob":
        pattern = inp.get("pattern", "")
        return f"[Glob] {pattern}"

    if name in ("Edit", "Write"):
        path = inp.get("file_path", "?")
        return f"[{name} {path}]"

    # Unknown tool — serialize input, truncate
    summary = json.dumps(inp, separators=(",", ":"))
    if len(summary) > 200:
        summary = summary[:200] + "..."
    return f"[{name}] {summary}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_normalize.py -k "test_format_tool_use" -v`
Expected: all 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/normalize.py tests/test_normalize.py
git commit -m "feat: add _format_tool_use for Claude Code JSONL tool blocks"
```

---

### Task 2: Add tool_result formatting helper

**Files:**
- Modify: `mempalace/normalize.py` (add `_format_tool_result` after `_format_tool_use`)
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

```python
from mempalace.normalize import _format_tool_result


def test_format_tool_result_bash_short():
    """Short Bash output is preserved in full."""
    content = "Bus 002 Device 005: ID 1532:0e05 Razer Kiyo Pro"
    result = _format_tool_result(content, "Bash")
    assert result == "→ Bus 002 Device 005: ID 1532:0e05 Razer Kiyo Pro"


def test_format_tool_result_bash_head_tail():
    """Long Bash output gets head+tail with gap marker."""
    lines = [f"line {i}" for i in range(60)]
    content = "\n".join(lines)
    result = _format_tool_result(content, "Bash")
    assert "line 0" in result
    assert "line 19" in result
    assert "line 40" in result
    assert "line 59" in result
    assert "20 lines omitted" in result
    # Lines 20-39 should be gone
    assert "line 20\n" not in result


def test_format_tool_result_bash_exactly_40_lines():
    """Bash output at exactly 40 lines is not truncated."""
    lines = [f"line {i}" for i in range(40)]
    content = "\n".join(lines)
    result = _format_tool_result(content, "Bash")
    assert "omitted" not in result
    assert "line 0" in result
    assert "line 39" in result


def test_format_tool_result_read_omitted():
    """Read results are omitted (content already in palace from project mining)."""
    result = _format_tool_result("lots of file content here...", "Read")
    assert result == ""


def test_format_tool_result_edit_omitted():
    """Edit results are omitted (diff is in git)."""
    result = _format_tool_result("file updated", "Edit")
    assert result == ""


def test_format_tool_result_write_omitted():
    """Write results are omitted."""
    result = _format_tool_result("file created", "Write")
    assert result == ""


def test_format_tool_result_grep_short():
    """Short Grep output is kept."""
    content = "src/foo.py\nsrc/bar.py\nsrc/baz.py"
    result = _format_tool_result(content, "Grep")
    assert "→ src/foo.py" in result
    assert "→ src/baz.py" in result


def test_format_tool_result_grep_caps_at_20():
    """Grep output beyond 20 lines is truncated."""
    lines = [f"match_{i}.py" for i in range(30)]
    content = "\n".join(lines)
    result = _format_tool_result(content, "Grep")
    assert "match_19.py" in result
    assert "match_20.py" not in result
    assert "10 more matches" in result


def test_format_tool_result_glob_caps_at_20():
    """Glob output beyond 20 lines is truncated."""
    lines = [f"/path/file_{i}.py" for i in range(25)]
    content = "\n".join(lines)
    result = _format_tool_result(content, "Glob")
    assert "file_19.py" in result
    assert "file_20.py" not in result
    assert "5 more matches" in result


def test_format_tool_result_unknown_short():
    """Unknown tool with short output is kept."""
    result = _format_tool_result("some output", "mcp__mempalace__search")
    assert result == "→ some output"


def test_format_tool_result_unknown_truncates():
    """Unknown tool output over 2KB is truncated."""
    content = "x" * 3000
    result = _format_tool_result(content, "SomeTool")
    assert result.endswith("... [truncated, 3000 chars]")
    assert len(result) < 2200


def test_format_tool_result_list_content():
    """tool_result content can be a list of text blocks."""
    content = [{"type": "text", "text": "result line 1"}, {"type": "text", "text": "result line 2"}]
    result = _format_tool_result(content, "Bash")
    assert "result line 1" in result
    assert "result line 2" in result


def test_format_tool_result_empty():
    """Empty result returns empty string."""
    result = _format_tool_result("", "Bash")
    assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_normalize.py::test_format_tool_result_bash_short -v`
Expected: FAIL with `ImportError: cannot import name '_format_tool_result'`

- [ ] **Step 3: Implement `_format_tool_result`**

Add after `_format_tool_use` in `mempalace/normalize.py`:

```python
_TOOL_RESULT_MAX_LINES_BASH = 20  # head and tail line count
_TOOL_RESULT_MAX_MATCHES = 20     # Grep/Glob cap
_TOOL_RESULT_MAX_BYTES = 2048     # fallback cap for unknown tools


def _format_tool_result(content, tool_name: str) -> str:
    """Format a tool_result based on the originating tool's type.

    Args:
        content: Result text (str) or list of content blocks (list of dicts).
        tool_name: Name of the tool that produced this result.

    Returns:
        Formatted string prefixed with ``→ ``, or empty string if omitted.
    """
    # Normalize list-of-blocks to plain text
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    else:
        text = str(content) if content else ""

    text = text.strip()
    if not text:
        return ""

    # Read/Edit/Write — omit result (content is in palace or git)
    if tool_name in ("Read", "Edit", "Write"):
        return ""

    lines = text.split("\n")

    # Bash — head + tail
    if tool_name == "Bash":
        n = _TOOL_RESULT_MAX_LINES_BASH
        if len(lines) <= n * 2:
            return "→ " + "\n→ ".join(lines)
        head = lines[:n]
        tail = lines[-n:]
        omitted = len(lines) - 2 * n
        return (
            "→ " + "\n→ ".join(head)
            + f"\n→ ... [{omitted} lines omitted] ..."
            + "\n→ " + "\n→ ".join(tail)
        )

    # Grep/Glob — cap matches
    if tool_name in ("Grep", "Glob"):
        cap = _TOOL_RESULT_MAX_MATCHES
        if len(lines) <= cap:
            return "→ " + "\n→ ".join(lines)
        kept = lines[:cap]
        remaining = len(lines) - cap
        return "→ " + "\n→ ".join(kept) + f"\n→ ... [{remaining} more matches]"

    # Unknown — byte cap
    if len(text) > _TOOL_RESULT_MAX_BYTES:
        return "→ " + text[:_TOOL_RESULT_MAX_BYTES] + f"... [truncated, {len(text)} chars]"
    return "→ " + text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_normalize.py -k "test_format_tool_result" -v`
Expected: all 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mempalace/normalize.py tests/test_normalize.py
git commit -m "feat: add _format_tool_result with per-tool strategies"
```

---

### Task 3: Wire tool blocks into `_extract_content` and `_try_claude_code_jsonl`

**Files:**
- Modify: `mempalace/normalize.py` — `_extract_content` and `_try_claude_code_jsonl`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing integration tests**

```python
def test_extract_content_with_tool_use():
    """_extract_content includes formatted tool_use blocks."""
    content = [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "lsusb"}},
    ]
    result = _extract_content(content)
    assert "Let me check." in result
    assert "[Bash] lsusb" in result


def test_extract_content_with_tool_result():
    """_extract_content includes formatted tool_result blocks (needs tool_use_map)."""
    content = [
        {"type": "tool_result", "tool_use_id": "t1", "content": "some output"},
    ]
    result = _extract_content(content, tool_use_map={"t1": "Bash"})
    assert "→ some output" in result


def test_extract_content_tool_result_without_map_uses_fallback():
    """tool_result without a map entry uses fallback strategy."""
    content = [
        {"type": "tool_result", "tool_use_id": "t1", "content": "some output"},
    ]
    result = _extract_content(content)
    assert "→ some output" in result


def test_claude_code_jsonl_captures_tool_output():
    """Full integration: tool_use + tool_result appear in normalized transcript."""
    lines = [
        json.dumps({"type": "human", "message": {"content": "Check the camera"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "lsusb | grep razer"}},
        ]}}),
        json.dumps({"type": "human", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Bus 002 Device 005: ID 1532:0e05 Razer Kiyo Pro"},
        ]}}),
        json.dumps({"type": "assistant", "message": {"content": "Found it."}}),
    ]
    result = _try_claude_code_jsonl("\n".join(lines))
    assert result is not None
    assert "> Check the camera" in result
    assert "[Bash] lsusb | grep razer" in result
    assert "→ Bus 002 Device 005" in result
    assert "Found it." in result


def test_claude_code_jsonl_read_result_omitted():
    """Read tool results are omitted but the path breadcrumb is kept."""
    lines = [
        json.dumps({"type": "human", "message": {"content": "Show me the file"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading it."},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/home/jp/file.py"}},
        ]}}),
        json.dumps({"type": "human", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "entire file contents here that should not appear"},
        ]}}),
        json.dumps({"type": "assistant", "message": {"content": "Here it is."}}),
    ]
    result = _try_claude_code_jsonl("\n".join(lines))
    assert result is not None
    assert "[Read /home/jp/file.py]" in result
    assert "entire file contents here" not in result


def test_claude_code_jsonl_tool_only_user_message_not_counted():
    """A user message containing ONLY tool_results (no text) should not
    be added as a separate user turn with '>'."""
    lines = [
        json.dumps({"type": "human", "message": {"content": "Do it"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Running."},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "echo hi"}},
        ]}}),
        json.dumps({"type": "human", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "hi"},
        ]}}),
        json.dumps({"type": "assistant", "message": {"content": "Done."}}),
    ]
    result = _try_claude_code_jsonl("\n".join(lines))
    assert result is not None
    # Only one user turn marker — the original "Do it"
    user_turns = [l for l in result.split("\n") if l.strip().startswith(">")]
    assert len(user_turns) == 1
    assert "> Do it" in result


def test_extract_content_text_only_backward_compat():
    """Text-only content blocks still work (backward compat)."""
    content = [
        {"type": "text", "text": "Hello"},
        {"type": "text", "text": "World"},
    ]
    result = _extract_content(content)
    assert "Hello" in result
    assert "World" in result


def test_extract_content_string_unchanged():
    """Plain string content still works."""
    result = _extract_content("just a string")
    assert result == "just a string"


def test_claude_code_jsonl_thinking_blocks_ignored():
    """Thinking blocks are still ignored."""
    lines = [
        json.dumps({"type": "human", "message": {"content": "Q"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "", "signature": "abc"},
            {"type": "text", "text": "A"},
        ]}}),
    ]
    result = _try_claude_code_jsonl("\n".join(lines))
    assert result is not None
    assert "thinking" not in result.lower()
    assert "signature" not in result
    assert "A" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_normalize.py::test_claude_code_jsonl_captures_tool_output -v`
Expected: FAIL — tool output not in result

- [ ] **Step 3: Modify `_extract_content` to handle tool blocks**

Update the function signature and list-handling branch in `mempalace/normalize.py`:

```python
def _extract_content(content, tool_use_map: dict = None) -> str:
    """Pull text from content — handles str, list of blocks, or dict.

    Args:
        content: Message content — string, list of content blocks, or dict.
        tool_use_map: Optional mapping of tool_use_id → tool_name, used to
                      select the right formatting strategy for tool_result blocks.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                block_type = item.get("type")
                if block_type == "text":
                    parts.append(item.get("text", ""))
                elif block_type == "tool_use":
                    parts.append(_format_tool_use(item))
                elif block_type == "tool_result":
                    tid = item.get("tool_use_id", "")
                    tname = (tool_use_map or {}).get(tid, "Unknown")
                    result_content = item.get("content", "")
                    formatted = _format_tool_result(result_content, tname)
                    if formatted:
                        parts.append(formatted)
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, dict):
        return content.get("text", "").strip()
    return ""
```

Note: the join changes from `" ".join(parts)` to `"\n".join(p for p in parts if p)` — tool blocks need newline separation, not space.

- [ ] **Step 4: Modify `_try_claude_code_jsonl` to track tool IDs and handle tool-only messages**

Replace the function in `mempalace/normalize.py`:

```python
def _try_claude_code_jsonl(content: str) -> Optional[str]:
    """Claude Code JSONL sessions."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    messages = []
    tool_use_map = {}  # tool_use_id → tool_name

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        msg_type = entry.get("type", "")
        message = entry.get("message", {})
        msg_content = message.get("content", "")

        # Build tool_use_map from assistant messages
        if msg_type == "assistant" and isinstance(msg_content, list):
            for block in msg_content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_use_map[block.get("id", "")] = block.get("name", "Unknown")

        if msg_type in ("human", "user"):
            # Check if this message is tool_results only (no user text)
            is_tool_only = (
                isinstance(msg_content, list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in msg_content
                )
            )
            text = _extract_content(msg_content, tool_use_map=tool_use_map)
            if text:
                if is_tool_only and messages and messages[-1][0] == "assistant":
                    # Append tool results to the previous assistant message
                    prev_role, prev_text = messages[-1]
                    messages[-1] = (prev_role, prev_text + "\n" + text)
                elif not is_tool_only:
                    messages.append(("user", text))
        elif msg_type == "assistant":
            text = _extract_content(msg_content, tool_use_map=tool_use_map)
            if text:
                # If previous message is also assistant (multi-turn tool loop),
                # merge into the same assistant turn
                if messages and messages[-1][0] == "assistant":
                    prev_role, prev_text = messages[-1]
                    messages[-1] = (prev_role, prev_text + "\n" + text)
                else:
                    messages.append(("assistant", text))

    if len(messages) >= 2:
        return _messages_to_transcript(messages)
    return None
```

Key changes:
1. `tool_use_map` dict built as we scan assistant messages
2. Tool-result-only user messages are merged into the previous assistant turn (not added as `> ` user turns)
3. Consecutive assistant messages are merged (handles tool loops: assistant→tool_result→assistant)
4. `_extract_content` receives `tool_use_map` for result formatting

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: all tests PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add mempalace/normalize.py tests/test_normalize.py
git commit -m "feat: wire tool_use/tool_result into Claude Code JSONL normalization"
```

---

### Task 4: Run full test suite and verify no regressions

**Files:**
- None modified — verification only

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: 615+ tests pass, 0 failures

- [ ] **Step 2: Test with a real JSONL transcript**

```bash
python -c "
from mempalace.normalize import normalize
result = normalize('/home/jp/.claude/projects/-home-jp-Projects-kiyo-xhci-fix/f5a0d9cf-38fa-43c0-9684-790c4145b695.jsonl')
lines = result.split('\n')
tool_lines = [l for l in lines if l.startswith('[Bash]') or l.startswith('→') or l.startswith('[Read') or l.startswith('[Grep]')]
print(f'Total lines: {len(lines)}')
print(f'Tool lines: {len(tool_lines)}')
print()
print('Sample tool output:')
for l in tool_lines[:10]:
    print(l[:120])
"
```

Expected: tool_lines count > 0, showing `[Bash]` commands and `→` result prefixes

- [ ] **Step 3: Commit (if any test fixes needed)**

```bash
git add -p  # only if fixes were needed
git commit -m "fix: test adjustments for tool output mining"
```

---

### Task 5: Final commit and push

- [ ] **Step 1: Push to origin**

```bash
git push origin main
```

- [ ] **Step 2: Update PR #562 with comment about the new feature**

Comment on the PR noting tool output capture was added.
