# `mempalace.onboarding`

Source: [`mempalace/onboarding.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/onboarding.py)

onboarding.py — MemPalace first-run setup.

Asks the user:
  1. How they're using MemPalace (work / personal / combo)
  2. Who the people in their life are (names, nicknames, relationships)
  3. What their projects are
  4. What they want their wings called

Seeds the entity_registry with confirmed data so MemPalace knows your world
from minute one — before a single session is indexed.

Usage:
    python3 -m mempalace.onboarding
    or: mempalace init

## Functions

### `run_onboarding`

```python
def run_onboarding(directory: str = '.', config_dir: Path = None, auto_detect: bool = True) -> EntityRegistry
```

Run the full onboarding flow.
Returns the seeded EntityRegistry.

### `quick_setup`

```python
def quick_setup(mode: str, people: list, projects: list = None, aliases: dict = None, config_dir: Path = None, embedding_model: str = None) -> EntityRegistry
```

Programmatic setup without interactive prompts.
Used in tests and benchmark scripts.

people: list of dicts &#123;"name": str, "relationship": str, "context": str}
embedding_model: optional ``"minilm"`` or ``"embeddinggemma"``. When set,
    writes the choice to ``config.json`` so subsequent runs pick the
    right EF. When omitted, the config stays untouched and the hard
    default (``"minilm"``) governs.
