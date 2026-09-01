# `mempalace.instructions_cli`

Source: [`mempalace/instructions_cli.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/instructions_cli.py)

Instruction text output for MemPalace CLI commands.

Each instruction lives as a .md file in the instructions/ directory
inside the package. The CLI reads and prints the file content.

## Functions

### `run_instructions`

```python
def run_instructions(name: str)
```

Read and print the instruction .md file for the given name.

### `render_shared_brain_rules`

```python
def render_shared_brain_rules(agent_id: str) -> str
```

Render the canonical shared-brain rules block for one agent identity.

The template ships inside the package and is test-pinned to the
System-Prompt Snippet in integrations/shared/coordination-protocol.md,
so every harness pastes the same battle-tested block and a protocol
lesson lands in one file instead of N system prompts. The output is
wrapped in HTML-comment markers so a later re-render can replace the
block in place.

### `run_rules`

```python
def run_rules(agent_id: str)
```

Print the rendered shared-brain rules block for the CLI.
