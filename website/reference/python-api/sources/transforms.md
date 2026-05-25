# `mempalace.sources.transforms`

Source: [`mempalace/sources/transforms.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/sources/transforms.py)

Reference implementations of the reserved content transformations (RFC 002 §1.4).

Every source adapter declares the set of transformations it applies to source
bytes via ``declared_transformations``. The conformance suite then verifies
that the adapter's output can be reproduced from the source bytes by applying
*only* the declared transformations in declaration order, using these
reference implementations.

Each transformation is a pure function on strings (text content after UTF-8
decoding). ``utf8_replace_invalid`` is the one that operates on bytes.

The invariant the spec enforces: **no transformation is applied that is not
declared in the adapter's set**. Adapters with an empty set are byte-preserving
end-to-end (modulo the initial UTF-8 decode itself, which is captured by
``utf8_replace_invalid`` when applicable).

Adapters MAY add custom transformations beyond the reserved set; third-party
names SHOULD be prefixed with the adapter name (``cursor.composer_ordering``).
Custom transformations MUST expose a reference implementation under
``mempalace.sources.transforms.&lt;adapter_name>_&lt;transform_name>`` so the
conformance suite can locate and apply them.

## Classes

### `class Transformation(Protocol)`

Callable signature every reserved transformation conforms to.

Accepts the current stage of the pipeline — ``bytes`` on input
(``utf8_replace_invalid``) or ``str`` after decoding — and returns ``str``.
Adapters compose them in declaration order; the first step operates on the
original source bytes, every subsequent step on the prior step's output.

## Functions

### `utf8_replace_invalid`

```python
def utf8_replace_invalid(raw: bytes) -> str
```

Decode bytes as UTF-8; replace invalid sequences with U+FFFD.

Equivalent to ``raw.decode("utf-8", errors="replace")``. This is the one
reserved transformation that operates on bytes rather than decoded text.

### `newline_normalize`

```python
def newline_normalize(text: str) -> str
```

Convert CRLF and bare-CR line endings to LF.

### `whitespace_trim`

```python
def whitespace_trim(text: str) -> str
```

Strip leading and trailing whitespace at the record boundary only.

### `whitespace_collapse_internal`

```python
def whitespace_collapse_internal(text: str) -> str
```

Collapse runs of three or more blank lines to exactly two blank lines.

A "blank line" here is a line containing only spaces or tabs. Single and
double blank-line runs are preserved.

### `line_trim`

```python
def line_trim(text: str) -> str
```

Strip leading and trailing whitespace from each individual line.

### `line_join_spaces`

```python
def line_join_spaces(text: str) -> str
```

Join adjacent non-blank lines with a single space, preserving paragraph breaks.

Two lines separated by at least one blank line remain on separate lines;
runs of non-blank lines collapse into a single space-separated line.

### `blank_line_drop`

```python
def blank_line_drop(text: str) -> str
```

Drop blank lines between non-blank lines, keeping non-blank lines only.

### `strip_tool_chrome`

```python
def strip_tool_chrome(text: str) -> str
```

Adapter-supplied: remove system tags, hook output, tool UI chrome.

The reference implementation here is intentionally an identity function
because the noise patterns differ per transcript format (Claude Code,
Codex, ChatGPT, Slack). The conversations adapter, when migrated, will
register a concrete reference implementation under
``mempalace.sources.transforms.conversations_strip_tool_chrome``.

### `tool_result_truncate`

```python
def tool_result_truncate(text: str) -> str
```

Adapter-supplied: head/tail window on tool output with a middle marker.

### `tool_result_omitted`

```python
def tool_result_omitted(text: str) -> str
```

Adapter-supplied: fully omit some tool outputs (e.g., Read/Edit/Write).

### `spellcheck_user`

```python
def spellcheck_user(text: str) -> str
```

Adapter-supplied: rewrite user turns via autocorrect.

Requires the optional ``spellcheck`` extra and a tokenizer; the spec does
not mandate a specific language model, so the reference is adapter-owned.

### `synthesized_marker`

```python
def synthesized_marker(text: str) -> str
```

Adapter-supplied: adapter inserts its own strings (e.g., '[N lines omitted]').

### `speaker_role_assignment`

```python
def speaker_role_assignment(text: str) -> str
```

Adapter-supplied: multi-party speakers alternately assigned user/assistant.

### `opencode_extract_text_parts`

```python
def opencode_extract_text_parts(text: str) -> str
```

Pluck ``data.text`` from each JSON-blob line where ``data.type == "text"``.

Input lines are ``"&lt;role>\t&lt;part_json>"``. The output preserves the
``&lt;role>\t`` prefix on each kept line so downstream merge and format
transformations can still see roles. Tool-input, tool-output, and
whitespace-only ``text`` parts are dropped — same skip the live
extraction path applies.

### `opencode_skip_tool_echo`

```python
def opencode_skip_tool_echo(text: str) -> str
```

Drop user-turn echoes of tool invocations (``Called the X tool ...``).

Operates on the role-prefixed line stream produced by
:func:`opencode_extract_text_parts`. A line whose body (everything after
the first tab) opens with ``Called the …  tool with the following input``
is dropped wholesale.

### `opencode_skip_file_injection`

```python
def opencode_skip_file_injection(text: str) -> str
```

Drop ``&lt;path>…&lt;/path>`` file-context injections wrapped around context.

### `opencode_role_coerce`

```python
def opencode_role_coerce(text: str) -> str
```

Coerce non-``user`` roles to ``assistant``.

OpenCode emits a small handful of role values (``user``, ``assistant``,
occasionally ``system`` or ``tool``). For transcript-shaped storage the
adapter only distinguishes ``user`` from everything-else.

### `opencode_same_role_merge`

```python
def opencode_same_role_merge(text: str) -> str
```

Merge consecutive same-role lines into a single line with ``\n\n`` joiner.

### `opencode_format_exchange`

```python
def opencode_format_exchange(text: str) -> str
```

Reformat role-prefixed lines as ``convo_miner`` exchange-pair markdown.

``user`` lines become ``> &lt;body>``; ``assistant`` lines become the body
on its own paragraph. Pairs are separated by blank lines. The output of
this is what ``mempalace.convo_miner.chunk_exchanges`` recognises as an
exchange transcript.

### `get_transformation`

```python
def get_transformation(name: str) -> Transformation
```

Resolve a reserved transformation by name.

Raises :class:`KeyError` if the name is neither reserved nor registered as
an adapter-namespaced reference (``&lt;adapter>_&lt;transform>``). Callers
looking for adapter-specific references SHOULD ``getattr`` on this module
first; this helper only covers the reserved names.
