# How to Use MemPalace Hooks (Auto-Save)

MemPalace hooks act as an "Auto-Save" feature. They help your AI keep a permanent memory without you needing to run manual commands.

### 1. What are these hooks?
* **Save Hook** (`mempal_save_hook.sh`): Saves new facts and decisions every 15 messages.
* **PreCompact Hook** (`mempal_precompact_hook.sh`): Saves your context right before the AI's memory window fills up.

### 2. Setup for Claude Code
Add this to your configuration file to enable automatic background saving:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "", 
        "hooks": [{"type": "command", "command": "./hooks/mempal_save_hook.sh"}]
      }
    ],
    "PreCompact": [
      {
        "matcher": "", 
        "hooks": [{"type": "command", "command": "./hooks/mempal_precompact_hook.sh"}]
      }
    ]
  }
}
```

### 3. What changed (v3.1.0+)

Hooks now have **two save modes** (set `hook_silent_save` in `~/.mempalace/config.json`):

1. **Silent mode** (default, recommended): Saves a diary entry directly via Python API — plain text, no AI involved, deterministic. Shows a one-line terminal notification. Save marker advances only after confirmed write, so data loss is impossible.

2. **Block mode** (legacy): Blocks the AI and asks it to call MemPalace MCP tools. Non-deterministic — the AI may ignore the instruction or fail.

Both modes also **auto-mine the JSONL transcript** into the palace, capturing raw tool output (Bash results, search findings, build errors) that the AI would otherwise summarize away.

### 4. Backfill past conversations (one-time)

The hooks capture conversations going forward, but you probably have months of past sessions. Run this once to mine them all:

```bash
mempalace mine ~/.claude/projects/ --mode convos
```

### 5. Configuration

- **`SAVE_INTERVAL=15`** — How many human messages between saves
- **`MEMPAL_PYTHON`** — Python interpreter with mempalace + chromadb. Auto-detects: env var → repo venv → system python3
- **`MEMPAL_DIR`** — Optional directory for auto-ingest via `mempalace mine`