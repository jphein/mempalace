# `mempalace.config`

Source: [`mempalace/config.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/config.py)

MemPalace configuration system.

Priority: env vars > config file (~/.mempalace/config.json) > defaults

## Classes

### `class MempalaceConfig`

Configuration manager for MemPalace.

Load order: env vars > config file > defaults.

#### `__init__`

```python
def __init__(self, config_dir = None)
```

Initialize config.

Args:
    config_dir: Override config directory (useful for testing).
                Defaults to ~/.mempalace.

#### `daemon_url`

```python
def daemon_url(self)
```

Optional palace-daemon URL. When set, mempalace's CLI and MCP
server route through palace-daemon's /mcp proxy instead of opening
a local chromadb client.

Resolution mirrors palace_path: env (``PALACE_DAEMON_URL``) wins,
``config.json`` key ``"daemon_url"`` as fallback, ``None`` means
run locally (current default).

See techempower-org/mempalace#49 — the env-only signal silently
failed when Claude Code's MCP spawn context didn't propagate the
env var, routing writes to a local palace while status read green.
Config-file fallback closes that gap for our multi-host deployment.

#### `daemon_strict`

```python
def daemon_strict(self) -> bool
```

True when daemon-strict routing is active.

Defaults True when ``daemon_url`` is set (env or config). Disable
explicitly via ``PALACE_DAEMON_STRICT=0`` env or ``"daemon_strict":
false`` in config.json — useful for test suites and offline
development where the daemon isn't reachable.

#### `palace_path`

```python
def palace_path(self)
```

Path to the memory palace data directory.

#### `tunnel_file`

```python
def tunnel_file(self)
```

Path to the tunnel file, sibling of palace_path.

#### `collection_name`

```python
def collection_name(self)
```

Storage collection name.

#### `backend`

```python
def backend(self)
```

Storage backend name.

Chroma remains the default. PostgreSQL must be explicitly enabled with
MEMPALACE_BACKEND=postgres or config.json &#123;"backend": "postgres"}.

#### `backend_override`

```python
def backend_override(self)
```

Explicit backend selection from env/config, or None for auto/default resolution.

#### `postgres_dsn`

```python
def postgres_dsn(self)
```

PostgreSQL DSN for the optional PostgreSQL backend.

#### `kg_backend`

```python
def kg_backend(self) -> str
```

Knowledge-graph backend name. SQLite stays the default.

Apache AGE is opt-in via ``MEMPALACE_KG_BACKEND=age`` or
``config.json &#123;"kg_backend": "age"}``. When set to ``age`` the
AGE backend uses ``postgres_dsn`` for its connection (AGE runs
in the same Postgres database as the storage backend can).

Lowercased before returning; falls back to ``"sqlite"`` on empty.

#### `auto_query_enabled`

```python
def auto_query_enabled(self) -> bool
```

Whether the auto-query classifier is active.

Env ``AUTO_QUERY_ENABLED`` > config ``auto_query.enabled`` > False.

#### `auto_query_mode`

```python
def auto_query_mode(self) -> str
```

Auto-query mode: off, dry-run, conservative, balanced, aggressive.

Env ``AUTO_QUERY_MODE`` > config ``auto_query.mode`` > ``"off"``.

#### `auto_query_max_per_turn`

```python
def auto_query_max_per_turn(self) -> int
```

Max auto-query invocations per turn.

Env ``AUTO_QUERY_MAX_PER_TURN`` > config ``auto_query.max_per_turn`` > 1.

#### `auto_query_max_per_minute`

```python
def auto_query_max_per_minute(self) -> int
```

Max auto-query invocations per minute (rate limit).

Env ``AUTO_QUERY_MAX_PER_MINUTE`` > config ``auto_query.max_per_minute`` > 6.

#### `wing_aliases`

```python
def wing_aliases(self) -> dict
```

Mapping of directory basenames to canonical palace wing names.

Useful when a project directory name differs from its palace wing
(e.g., ``familiar.realm.watch`` → ``familiar_realm_watch``).

Config ``wing_aliases`` > empty dict.

#### `resolve_wing`

```python
def resolve_wing(self, directory_name: str) -> str
```

Resolve a project directory name to its canonical palace wing.

Checks ``wing_aliases`` first, then falls back to the default
normalization (lowercase, dots/dashes/spaces → underscores).

#### `room_aliases`

```python
def room_aliases(self) -> dict
```

Mapping of detected/input room names to canonical palace room names.

Useful for overriding auto-detected room names or unifying variants
(e.g., ``ui`` → ``frontend``, ``api`` → ``backend``).

Config ``room_aliases`` > empty dict.

#### `resolve_room`

```python
def resolve_room(self, room_name: str) -> str
```

Resolve a room name to its canonical palace room.

Checks ``room_aliases`` first, then falls back to the default
normalization (lowercase, dashes/spaces → underscores).

#### `people_map`

```python
def people_map(self)
```

Mapping of name variants to canonical names.

#### `hooks_auto_save`

```python
def hooks_auto_save(self)
```

Whether the stop/precompact hooks should block for auto-save.

When False, hooks pass through without blocking — equivalent to
disabling auto-save while keeping hook scripts installed.

#### `topic_wings`

```python
def topic_wings(self)
```

List of topic wing names.

#### `hall_keywords`

```python
def hall_keywords(self)
```

Mapping of hall names to keyword lists.

#### `chunk_size`

```python
def chunk_size(self) -> int
```

Characters per drawer chunk (validated, ``>= 1``).

#### `chunk_overlap`

```python
def chunk_overlap(self) -> int
```

Overlap between adjacent chunks (validated, ``< chunk_size``).

#### `min_chunk_size`

```python
def min_chunk_size(self) -> int
```

Minimum chunk size — skip smaller chunks (validated, ``<= chunk_size``).

#### `min_chunk_size_explicit`

```python
def min_chunk_size_explicit(self)
```

Validated ``min_chunk_size`` iff the user explicitly set it.

Returns the coerced int when ``config.json`` defines a usable
``min_chunk_size`` (``>= 0`` and ``<= chunk_size``); ``None`` when
the key is absent/null or the value is unusable. ``convo_miner``
relies on the ``None`` sentinel to keep its lower 30-char floor
(more permissive than the 50-char project default, so short
exchanges are not dropped) for untuned users while still honoring
an explicit override —
replacing the raw, unvalidated ``_file_config`` reach that crashed
convo ingest on a bad key (#1024 review).

#### `entity_languages`

```python
def entity_languages(self)
```

Languages whose entity-detection patterns should be applied.

Reads from env var ``MEMPALACE_ENTITY_LANGUAGES`` (comma-separated)
first, then the ``entity_languages`` field in ``config.json``,
defaulting to ``["en"]``.

#### `set_entity_languages`

```python
def set_entity_languages(self, languages)
```

Persist the entity-detection language list to ``config.json``.

#### `embedding_device`

```python
def embedding_device(self)
```

Hardware device for the ONNX embedding model.

Values: ``"auto"`` (default), ``"cpu"``, ``"cuda"``, ``"coreml"``,
``"dml"``. Read from env ``MEMPALACE_EMBEDDING_DEVICE`` first, then
``embedding_device`` in ``config.json``, then ``"auto"``.

``auto`` resolves to the first available accelerator at runtime via
:mod:`mempalace.embedding`; requesting an unavailable accelerator
logs a warning and falls back to CPU.

#### `embedding_model`

```python
def embedding_model(self)
```

Embedding model identifier.

Values: ``"minilm"`` (ChromaDB's all-MiniLM-L6-v2 — English-only),
``"embeddinggemma"`` (multilingual, 100+ languages, default for
new installs since onboarding writes the choice). Read from env
``MEMPALACE_EMBEDDING_MODEL`` first, then ``embedding_model`` in
``config.json``, then ``"minilm"`` as a back-compat fallback for
palaces created before onboarding asked the question.

Switching models on an existing palace requires re-embedding
(different vector space) — ChromaDB rejects reads when the persisted
EF name doesn't match. Run ``mempalace repair rebuild-index`` after
changing this value.

#### `set_embedding_model`

```python
def set_embedding_model(self, model: str) -> None
```

Persist the embedding-model choice to ``config.json``.

Onboarding calls this once on first run. Accepts ``"minilm"`` or
``"embeddinggemma"``; other values are normalized to lowercase and
passed through (``embedding.get_embedding_function`` falls back to
minilm for unrecognized values).

#### `topic_tunnel_min_count`

```python
def topic_tunnel_min_count(self)
```

Minimum number of overlapping confirmed topics required to create
a cross-wing tunnel between two wings.

Default is ``1`` — any single shared topic produces a tunnel. Bump
to ``2+`` if your projects share lots of common-tech labels (Python,
Docker, Git) and you want only meaningfully overlapping wings to
link. Reads ``MEMPALACE_TOPIC_TUNNEL_MIN_COUNT`` env first, then the
config-file value, then ``1``.

#### `hook_silent_save`

```python
def hook_silent_save(self)
```

Whether the stop hook saves directly (True) or blocks for MCP calls (False).

#### `hook_desktop_toast`

```python
def hook_desktop_toast(self)
```

Whether the stop hook shows a desktop notification via notify-send.

#### `hook_verbatim_mode`

```python
def hook_verbatim_mode(self)
```

Skip truncation/noise-stripping in transcript ingest.

When True, ``normalize()`` preserves Claude Code system tags, hook
chrome, full Bash commands, full Bash output, full Grep/Glob match
lists, full Read/Edit/Write results, and uncapped tool inputs.
Default False — existing behavior is unchanged for upstream-shape
installs and for users who haven't opted in.

#### `set_hook_setting`

```python
def set_hook_setting(self, key: str, value: bool)
```

Update a hook setting and write config to disk.

#### `init`

```python
def init(self)
```

Create config directory and write default config.json if it doesn't exist.

#### `save_people_map`

```python
def save_people_map(self, people_map)
```

Write people_map.json to config directory.

Args:
    people_map: Dict mapping name variants to canonical names.

## Functions

### `strip_lone_surrogates`

```python
def strip_lone_surrogates(text: str) -> str
```

Replace lone UTF-16 surrogates with U+FFFD so the string is legal UTF-8 (#1235).

### `normalize_wing_name`

```python
def normalize_wing_name(name: str) -> str
```

Lower-case + collapse separators (`-`, ` `) to `_` for wing slugs.

The same rule is applied by ``init`` when persisting `topics_by_wing`
and when writing `mempalace.yaml`, so the miner's lookup matches at
mine time regardless of the source dirname.

### `sanitize_name`

```python
def sanitize_name(value: str, field_name: str = 'name') -> str
```

Validate and sanitize a wing/room/entity name.

Raises ValueError if the name is invalid.

### `sanitize_kg_value`

```python
def sanitize_kg_value(value: str, field_name: str = 'value') -> str
```

Validate a knowledge-graph entity name (subject or object).

More permissive than sanitize_name — allows punctuation like commas,
colons, and parentheses that are common in natural-language KG values.
Only blocks null bytes and over-length strings.

Not used for wing/room names (which have filesystem constraints) or
predicates (which should be simple relationship identifiers).

### `sanitize_iso_temporal`

```python
def sanitize_iso_temporal(value, field_name: str = 'date')
```

Validate an ISO-8601 date or canonical UTC datetime string.

Accepts ``None`` and ``""`` as pass-through values.

Accepted non-empty string forms:

- ``YYYY-MM-DD``
- ``YYYY-MM-DDTHH:MM:SSZ``
- ``YYYY-MM-DDTHH:MM:SS+00:00`` normalized to ``...Z``

Partial dates are rejected because KG queries compare TEXT temporal values.
Non-canonical datetime forms are rejected because mixed temporal string
formats can silently return wrong KG query results.

### `sanitize_iso_date`

```python
def sanitize_iso_date(value, field_name: str = 'date')
```

Backward-compatible wrapper for ISO temporal validation.

Historically this accepted only full dates. It now also accepts canonical
UTC datetimes, but the old name is kept so existing imports continue to
work.

### `sanitize_content`

```python
def sanitize_content(value: str, max_length: int = 100000) -> str
```

Validate drawer/diary content length.

### `get_configured_collection_name`

```python
def get_configured_collection_name() -> str
```

Return the configured drawer collection name without repeated config-file reads.
