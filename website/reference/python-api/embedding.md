# `mempalace.embedding`

Source: [`mempalace/embedding.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/embedding.py)

Embedding function factory with hardware acceleration.

Returns a ChromaDB-compatible embedding function — either a local ONNX model
bound to a user-selected ONNX Runtime execution provider, or an
OpenAI-compatible HTTP ``/v1/embeddings`` endpoint.

Four embedding-model options are available, selected via
``MEMPALACE_EMBEDDING_MODEL`` or ``embedding_model`` in
``~/.mempalace/config.json``:

* ``minilm`` (default) — ``all-MiniLM-L6-v2``, 384-dim, English-only training.
  ChromaDB's default; what every existing palace was built with.
* ``embeddinggemma`` — ``onnx-community/embeddinggemma-300m-ONNX`` (q8), 384-dim
  via Matryoshka truncation, multilingual (100+ languages). Cross-lingual cos
  ~0.88 on parallel translations vs MiniLM's ~0.35. Recommended for any
  non-English use; onboarding offers it as the default. The ~300 MB ONNX
  model is lazy-downloaded from HuggingFace on first use. Switching models
  on an existing palace requires ``mempalace repair rebuild-index``
  (different vector space).
* ``adaptmem_ft`` — a SentenceTransformer-shaped fine-tuned checkpoint from
  techempower-org/adaptmem, loaded from a local path (``MEMPALACE_ADAPTMEM_PATH``
  or ``adaptmem_path`` in config). Nothing is downloaded. Same 384-dim shape as
  MiniLM, so it drops into existing collections, but it is a different vector
  space — switching requires ``mempalace repair rebuild-index``. Requires the
  ``sentence-transformers`` package.
* ``openai-compat`` — embeddings served by any OpenAI-compatible
  ``/v1/embeddings`` endpoint (LM Studio, llama.cpp, vLLM, Ollama's OpenAI
  shim, or a self-hosted server) instead of a local ONNX model. Useful for
  larger / multilingual embedders (e.g. Qwen3-Embedding) or GPU offload.
  Endpoint settings are read from ``config.json`` as ``embedding_api_url`` /
  ``embedding_api_model`` / ``embedding_api_key`` (each overridable via the
  matching ``MEMPALACE_EMBEDDING_API_*`` env var). Vectors are L2-normalized
  for the cosine collection; the dimension is whatever the server returns, so
  switching to/from this backend also requires ``mempalace repair
  rebuild-index``. Stays local when the endpoint is on your machine/LAN.

Supported devices (env ``MEMPALACE_EMBEDDING_DEVICE`` or ``embedding_device``
in ``~/.mempalace/config.json``):

* ``auto`` — prefer CUDA ▸ CoreML ▸ DirectML, fall back to CPU
* ``cpu`` — force CPU (the historical default)
* ``cuda`` — NVIDIA GPU via ``onnxruntime-gpu`` (``pip install mempalace[gpu]``)
* ``coreml`` — Apple Neural Engine (macOS)
* ``dml`` — DirectML (Windows / AMD / Intel GPUs)

Requesting an unavailable accelerator emits a warning and falls back to CPU
rather than hard-failing — mining must still work on a laptop without CUDA.
The same applies to an accelerator that runs but computes the model wrongly:
``embeddinggemma`` on CoreML returns NaN or all-zero vectors without raising,
so ``auto`` never selects CoreML for it and an explicitly requested one is
rejected by a witness embedding at load time.

## Classes

### `class EmbeddinggemmaONNX`

ChromaDB-compatible EF using embeddinggemma-300m ONNX (q8, MRL→384d).

Cross-lingual cosine similarity on parallel-translated text averages 0.88
across DE/FR/HI/IT/KO/RU vs 0.35 for ``all-MiniLM-L6-v2``. Output dim is
truncated to 384 via Matryoshka Representation Learning so the model is a
drop-in replacement for the MiniLM-shaped 384-dim collections ChromaDB
creates by default — same vector width, no schema change.

Switching an existing palace from minilm → embeddinggemma still requires
re-embedding (different vector space) — collections persist the EF name
and ChromaDB rejects mismatched reads. Run ``mempalace repair rebuild-index``.

#### `name`

```python
def name() -> str
```

#### `__init__`

```python
def __init__(self, preferred_providers = None, batch_size: int = _EMBEDDINGGEMMA_BATCH_SIZE, intra_op_num_threads: int = 0)
```

#### `embed_query`

```python
def embed_query(self, input: list[str]) -> list[list[float]]
```

Embed query documents (ChromaDB EF protocol).

#### `embed_documents`

```python
def embed_documents(self, input: list[str]) -> list[list[float]]
```

Embed a batch of documents (ChromaDB EF protocol).

### `class EmbeddingAPIError(RuntimeError)`

Raised when the embedding API is unreachable or returns an invalid body.

Module-specific subclass mirroring ``llm_client.LLMError`` so callers can
distinguish embedding-endpoint failures; subclasses ``RuntimeError`` so
existing ``except RuntimeError`` paths still catch it.

### `class OpenAICompatEmbeddingFunction`

ChromaDB-compatible EF backed by an OpenAI-compatible ``/v1/embeddings``
endpoint (LM Studio, llama.cpp, vLLM, Ollama's OpenAI shim, etc.).

Selected via ``embedding_model == "openai-compat"``. Vectors are produced
server-side and fetched over HTTP, which changes the vector space — so
``name()`` encodes the model id: ChromaDB persists the EF name on the
collection and rejects mismatched reads, the signal to run ``mempalace
repair rebuild-index`` after changing model/endpoint. stdlib ``urllib``
only, no new dependency.

#### `__init__`

```python
def __init__(self, base_url: str, model: str, api_key: Optional[str] = None)
```

#### `name`

```python
def name(self) -> str
```

#### `embed_query`

```python
def embed_query(self, input)
```

## Functions

### `get_embedding_function`

```python
def get_embedding_function(device: Optional[str] = None, model: Optional[str] = None)
```

Return a cached embedding function for the requested device + model.

``device=None`` reads :attr:`MempalaceConfig.embedding_device`;
``model=None`` reads :attr:`MempalaceConfig.embedding_model`.
The returned function is shared across calls with the same resolved
provider list + model so we only pay model-load cost once per process.

### `describe_device`

```python
def describe_device(device: Optional[str] = None, model: Optional[str] = None) -> str
```

Return a short human-readable label for the resolved embedding backend.

Used by the miner CLI header / MCP status so users can see at a glance
whether GPU acceleration engaged — or, for the ``openai-compat`` backend,
that embeddings are served by a remote endpoint rather than local hardware
(in which case the ``embedding_device`` accelerator label is irrelevant).

### `current_model_name`

```python
def current_model_name(model: Optional[str] = None) -> str
```

Resolve the canonical embedder model name (cheap, no model load).

This is the configured ``embedding_model`` (``"minilm"`` /
``"embeddinggemma"`` / ...), not the embedding function's internal
``name()`` (which is spoofed to ``"default"`` for ChromaDB compatibility).

### `probe_dimension`

```python
def probe_dimension(device: Optional[str] = None, model: Optional[str] = None) -> int
```

Return the embedder's output dimension by embedding a short probe.

Model-agnostic — works for any model without a hardcoded table — and
cached per resolved model name so the probe is paid at most once per
process. Returns ``0`` if the probe fails (treated as "dimension unknown"
by the identity check, so a probe failure never blocks normal operation).

### `get_embedder_identity`

```python
def get_embedder_identity(device: Optional[str] = None, model: Optional[str] = None)
```

Resolve the current embedder identity (RFC 001).

``model_name`` from config (cheap); ``dimension`` from a cached one-time
probe. Returns an :class:`~mempalace.backends.base.EmbedderIdentity`.
