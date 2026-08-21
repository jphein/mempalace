# `mempalace.integrations.hermes`

Source: [`mempalace/integrations/hermes/__init__.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/integrations/hermes/__init__.py)

MemPalace memory provider for Hermes.

Implements the Hermes ``MemoryProvider`` ABC (``agent/memory_provider.py``)
so MemPalace can be selected as ``memory.provider: mempalace`` in
``~/.hermes/config.yaml``.

Design notes
------------

* ChromaDB access goes through ``mempalace.backends.chroma.ChromaBackend``
  rather than a raw ``chromadb.PersistentClient``. This ensures the
  embedding function returned by ``mempalace.embedding.get_embedding_function``
  is bound to the collection, fixing the embedding-dimension mismatch that
  silently broke the three earlier Hermes-side PRs (NousResearch/hermes-agent
  #5671, #12203, #9761) on existing palaces.

* Per-turn writes go through a bounded background queue. The agent loop
  never blocks on ChromaDB or SQLite.

* ``sync_turn`` is the **sole** filing path. ``on_session_end`` and
  ``on_pre_compress`` intentionally file nothing: re-filing the raw
  message list duplicates every turn ``sync_turn`` already stored —
  ``filed_at`` is hashed into the drawer id, so upserts cannot collapse
  the copies. Any future safety net here must first scan what is
  already filed and add only what is missing.

* The provider is **inactive** under ``agent_context in &#123;"cron", "flush"}``
  or ``platform == "cron"``. Cron-context turns are system-generated and
  would otherwise corrupt the user's representation.

* Configuration precedence: ``$HERMES_HOME/mempalace.json`` is read
  first, then env vars override (``MEMPALACE_PALACE_PATH``,
  ``MEMPALACE_IDENTITY_PATH``, ``MEMPALACE_WING``). An empty env var
  is ignored — ``export MEMPALACE_WING=`` is intent to unset. A palace
  still unset after that defers to mempalace's own config
  (``~/.mempalace/config.json``) before falling back to the default
  location. The resolved palace is then published to
  ``MEMPALACE_PALACE_PATH`` (see ``_bridge_palace_env``) so the
  ``mempalace.mcp_server`` passthrough tools operate on the same palace
  as live filing and search — never a config-file-vs-provider split.
  ``collection_name`` follows mempalace's own config for the same
  reason: it is what ``search_memories`` (used by ``prefetch`` and
  ``_tool_search``) and the mcp_server passthrough read, so live writes
  land in the collection recall actually searches. It is intentionally
  not configurable on the Hermes side — a second knob would let the
  write and read sides diverge silently again.

* ``~/.mempalace/identity.txt`` (L0) and ``~/.mempalace/wing_config.json``
  are loaded if present but never created here. Run
  ``mempalace init &lt;project-dir>`` to generate them.

* All palace state (ChromaDB, knowledge graph, diary, identity) lives
  under ``~/.mempalace/`` by design — the palace is the user's central
  memory shared across agents, not per-agent Hermes state. This means
  ``hermes backup`` (which archives only ``$HERMES_HOME``) does NOT
  cover it; users must back up ``~/.mempalace/`` separately. Hermes'
  ``MemoryProvider`` ABC currently offers no hook for contributing
  external paths to its backup.

## Classes

### `class MempalaceProvider(MemoryProvider)`

Hermes memory provider backed by MemPalace.

#### `__init__`

```python
def __init__(self) -> None
```

#### `name`

```python
def name(self) -> str
```

#### `is_available`

```python
def is_available(self) -> bool
```

Always True — module-level imports prove mempalace is installed.

If mempalace were missing, ``import`` of this module would have failed
before Hermes' plugin loader called ``is_available``. The check is
kept for ABC conformance and so a future config flag can disable the
provider here without surgery elsewhere.

#### `initialize`

```python
def initialize(self, session_id: str, **kwargs: Any) -> None
```

#### `get_tool_schemas`

```python
def get_tool_schemas(self) -> List[Dict[str, Any]]
```

#### `system_prompt_block`

```python
def system_prompt_block(self) -> str
```

#### `prefetch`

```python
def prefetch(self, query: str, *, session_id: str = '') -> str
```

#### `sync_turn`

```python
def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = '', messages: Optional[List[Dict[str, Any]]] = None) -> None
```

#### `on_turn_start`

```python
def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None
```

#### `on_session_end`

```python
def on_session_end(self, messages: List[Dict[str, Any]]) -> None
```

#### `on_session_switch`

```python
def on_session_switch(self, new_session_id: str, *, parent_session_id: str = '', reset: bool = False, rewound: bool = False, **kwargs: Any) -> None
```

#### `on_pre_compress`

```python
def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str
```

Intentionally a no-op that returns no hint.

Blind-filing the compression window duplicates every turn
``sync_turn`` already filed. Returning ``""`` keeps the
summarizer on its default conservative discarding — a hint must
never promise persistence this provider hasn't performed.

#### `on_memory_write`

```python
def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None
```

#### `on_delegation`

```python
def on_delegation(self, task: str, result: str, *, child_session_id: str = '', **kwargs: Any) -> None
```

#### `handle_tool_call`

```python
def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str
```

#### `get_config_schema`

```python
def get_config_schema(self) -> List[Dict[str, Any]]
```

#### `save_config`

```python
def save_config(self, values: Dict[str, Any], hermes_home: str) -> None
```

#### `post_setup`

```python
def post_setup(self, hermes_home: str, config: Dict[str, Any]) -> None
```

#### `shutdown`

```python
def shutdown(self) -> None
```

## Functions

### `register`

```python
def register(ctx: Any) -> None
```

Register the MemPalace memory provider with Hermes.
