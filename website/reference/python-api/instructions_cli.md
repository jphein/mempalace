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
