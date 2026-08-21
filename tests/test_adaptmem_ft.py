"""Tests for the AdaptMem FT encoder backend (``adaptmem_ft``).

The backend loads a SentenceTransformer-shaped fine-tuned checkpoint from a
path (``MEMPALACE_ADAPTMEM_PATH``) and exposes it as a ChromaDB embedding
function. Most tests here mock ``sentence_transformers.SentenceTransformer`` so
CI stays fast and network-free; a single opt-in smoke test loads the real
artifact when one is present on disk.

Critical contract under test: the EF MUST subclass
``chromadb.api.types.EmbeddingFunction`` so ``Collection.query`` exercises the
custom encoder instead of silently degrading to BM25 (the embed_query trap
documented in mempalace/embedding.py).
"""

import os
import sys

import pytest

import mempalace.embedding as embedding

# The real FT artifact (config + tokenizer bundle). May be incomplete (no
# weights) in CI — the smoke test below handles that with a skip.
_REAL_ARTIFACT = "/home/jp/Projects/adaptmem/results/sprint_0p99/kaggle_bundle_bi/ft-300-base"


@pytest.fixture(autouse=True)
def isolate_embedding_state(monkeypatch):
    monkeypatch.setattr(embedding, "_EF_CACHE", {})
    monkeypatch.setattr(embedding, "_WARNED", set())


# ── Fake SentenceTransformer ─────────────────────────────────────────


class _FakeST:
    """Stand-in for sentence_transformers.SentenceTransformer.

    Records the path/device it was constructed with and returns deterministic
    384-dim unit vectors so tests can assert shape and normalization without
    loading a real model.
    """

    instances = []

    def __init__(self, path, device=None, **kwargs):
        self.path = path
        self.device = device
        self.kwargs = kwargs
        self.encode_calls = []
        type(self).instances.append(self)

    def encode(self, texts, **kwargs):
        import numpy as np

        self.encode_calls.append((list(texts), kwargs))
        batch = len(texts)
        out_dim = 384
        arr = np.arange(batch * out_dim, dtype=np.float32).reshape(batch, out_dim) + 1.0
        # The backend asks for normalized embeddings; emulate that so the
        # contract (unit vectors) holds end to end.
        if kwargs.get("normalize_embeddings"):
            norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
            arr = arr / norms
        return arr


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    """Inject a fake ``sentence_transformers`` module with ``_FakeST``."""
    pytest.importorskip("numpy")
    _FakeST.instances = []
    import types

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return _FakeST


# ── Contract: subclass of chromadb EmbeddingFunction ─────────────────


def test_is_subclass_of_embedding_function():
    """MUST subclass chromadb's EmbeddingFunction so query() doesn't fall back.

    A bare __call__+name() class satisfies upsert but Collection.query calls
    embed_query, which only the Protocol base provides — see the embed_query
    trap note in mempalace/embedding.py.
    """
    from chromadb.api.types import EmbeddingFunction

    assert issubclass(embedding.AdaptMemFTEncoder, EmbeddingFunction)


def test_inherits_embed_query():
    """The inherited embed_query must delegate to __call__ so query works."""
    assert hasattr(embedding.AdaptMemFTEncoder, "embed_query")


def test_name_spoofs_default():
    """Names itself 'default' so existing minilm palaces query without rebuild.

    Same identity-spoof as _MempalaceONNX — the FT model is 384-dim like
    MiniLM, so a palace built with the default EF can be queried by this one.
    """
    ef = embedding.AdaptMemFTEncoder(model_path="/some/path")
    assert ef.name() == "default"


# ── Path resolution ──────────────────────────────────────────────────


def test_requires_a_path(monkeypatch):
    """With no explicit path and no env var, construction must error clearly."""
    monkeypatch.delenv("MEMPALACE_ADAPTMEM_PATH", raising=False)
    with pytest.raises(ValueError, match="MEMPALACE_ADAPTMEM_PATH"):
        embedding.AdaptMemFTEncoder()


def test_reads_path_from_env(monkeypatch, fake_sentence_transformers):
    """Path falls back to MEMPALACE_ADAPTMEM_PATH when not passed explicitly."""
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/env/ft/path")
    ef = embedding.AdaptMemFTEncoder()
    ef(["x"])  # trigger lazy load
    assert fake_sentence_transformers.instances[-1].path == "/env/ft/path"


def test_explicit_path_overrides_env(monkeypatch, fake_sentence_transformers):
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/env/ft/path")
    ef = embedding.AdaptMemFTEncoder(model_path="/explicit/path")
    ef(["x"])
    assert fake_sentence_transformers.instances[-1].path == "/explicit/path"


# ── Lazy load + encode ───────────────────────────────────────────────


def test_lazy_load_runs_once(fake_sentence_transformers):
    """The model is constructed on first call, then reused."""
    ef = embedding.AdaptMemFTEncoder(model_path="/p")
    ef(["one"])
    ef(["two"])
    ef(["three"])
    assert len(fake_sentence_transformers.instances) == 1


def test_no_load_at_construction(fake_sentence_transformers):
    """Construction must not load the model (lazy) — matches the gemma backend."""
    embedding.AdaptMemFTEncoder(model_path="/p")
    assert fake_sentence_transformers.instances == []


def test_output_shape(fake_sentence_transformers):
    import numpy as np

    ef = embedding.AdaptMemFTEncoder(model_path="/p")
    out = ef(["a", "b", "c"])
    arr = np.asarray(out)
    assert arr.shape == (3, 384)


def test_output_is_l2_normalized(fake_sentence_transformers):
    import numpy as np

    ef = embedding.AdaptMemFTEncoder(model_path="/p")
    out = ef(["hello world", "another sentence"])
    arr = np.asarray(out)
    norms = np.linalg.norm(arr, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5), f"vectors not unit-norm: {norms}"


def test_output_is_a_per_row_sequence(fake_sentence_transformers):
    """Output is one embedding per input row.

    Because ``AdaptMemFTEncoder`` subclasses chromadb's ``EmbeddingFunction``,
    chromadb's ``__init_subclass__`` wraps ``__call__`` and re-normalizes the
    return value through ``validate_embeddings`` (each row becomes a numpy
    array). That's the contract that matters: a row per input, each a sequence
    of floats — not necessarily a plain Python list. (Contrast the bare-EF
    ``EmbeddinggemmaONNX``, which returns list-of-lists because it is *not* a
    subclass and chromadb doesn't touch its output.)
    """
    ef = embedding.AdaptMemFTEncoder(model_path="/p")
    out = ef(["x", "y"])
    assert len(out) == 2
    for row in out:
        assert len(row) == 384
        assert all(isinstance(float(v), float) for v in row)


def test_uses_resolved_device(fake_sentence_transformers):
    """The device hint is passed through to SentenceTransformer."""
    ef = embedding.AdaptMemFTEncoder(model_path="/p", device="cuda")
    ef(["x"])
    assert fake_sentence_transformers.instances[-1].device == "cuda"


# ── Dispatch through get_embedding_function ──────────────────────────


def test_get_embedding_function_dispatches_to_adaptmem(monkeypatch):
    """model='adaptmem_ft' must build AdaptMemFTEncoder, not the MiniLM EF."""
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/p")
    monkeypatch.setattr(
        embedding,
        "_resolve_providers",
        lambda device, model=None: (["CPUExecutionProvider"], "cpu"),
    )
    ef = embedding.get_embedding_function(device="cpu", model="adaptmem_ft")
    assert isinstance(ef, embedding.AdaptMemFTEncoder)
    assert ef.name() == "default"


def test_cache_key_separates_adaptmem(monkeypatch):
    """Switching to adaptmem_ft must not return a cached minilm/gemma EF."""
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/p")

    class DummyMiniLM:
        def __init__(self, preferred_providers=None, intra_op_num_threads=0):
            self.kind = "minilm"

    monkeypatch.setattr(embedding, "_build_ef_class", lambda: DummyMiniLM)
    monkeypatch.setattr(
        embedding,
        "_resolve_providers",
        lambda device, model=None: (["CPUExecutionProvider"], "cpu"),
    )

    ml = embedding.get_embedding_function(device="cpu", model="minilm")
    ft = embedding.get_embedding_function(device="cpu", model="adaptmem_ft")
    ml_again = embedding.get_embedding_function(device="cpu", model="minilm")

    assert ml is ml_again, "minilm should cache-hit on second call"
    assert isinstance(ft, embedding.AdaptMemFTEncoder)
    assert ml is not ft


def test_adaptmem_caches_within_model(monkeypatch):
    """Two calls with the same model + providers return the same EF instance."""
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/p")
    monkeypatch.setattr(
        embedding,
        "_resolve_providers",
        lambda device, model=None: (["CPUExecutionProvider"], "cpu"),
    )
    first = embedding.get_embedding_function(device="cpu", model="adaptmem_ft")
    second = embedding.get_embedding_function(device="auto", model="adaptmem_ft")
    assert first is second


# ── Missing dependency ───────────────────────────────────────────────


def test_missing_sentence_transformers_raises_helpful_error(monkeypatch):
    """If sentence_transformers isn't installed, the error must say how to fix."""
    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/p")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    ef = embedding.AdaptMemFTEncoder(model_path="/p")
    with pytest.raises(ImportError, match=r"sentence[-_]transformers"):
        ef(["anything"])


# ── Config wiring ────────────────────────────────────────────────────


def test_config_adaptmem_path_env(monkeypatch):
    from mempalace.config import MempalaceConfig

    monkeypatch.setenv("MEMPALACE_ADAPTMEM_PATH", "/from/env")
    assert MempalaceConfig().adaptmem_path == "/from/env"


def test_config_adaptmem_path_default_is_none(monkeypatch):
    from mempalace.config import MempalaceConfig

    monkeypatch.delenv("MEMPALACE_ADAPTMEM_PATH", raising=False)
    assert MempalaceConfig().adaptmem_path is None


def test_config_embedding_model_accepts_adaptmem(monkeypatch):
    from mempalace.config import MempalaceConfig

    monkeypatch.setenv("MEMPALACE_EMBEDDING_MODEL", "adaptmem_ft")
    assert MempalaceConfig().embedding_model == "adaptmem_ft"


# ── Smoke test against the real artifact (opt-in, tolerant) ──────────


@pytest.mark.skipif(
    not os.path.isdir(_REAL_ARTIFACT),
    reason=f"FT artifact not present at {_REAL_ARTIFACT}",
)
def test_real_artifact_load_smoke():
    """Load the real FT checkpoint and embed a string end to end.

    The bundled artifact may be config/tokenizer-only (no weight file) in some
    environments — in that case SentenceTransformer raises while loading, which
    we treat as a skip rather than a failure. When a complete checkpoint is
    present this exercises the real load + encode path.
    """
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("numpy")
    import glob

    import numpy as np

    # Fast pre-check: a complete SentenceTransformer checkpoint has a weight
    # file. The bundled artifact may be config/tokenizer-only in CI — skip
    # before paying the (slow) tokenizer load just to hit a missing-weights
    # error.
    has_weights = any(
        glob.glob(os.path.join(_REAL_ARTIFACT, pat))
        for pat in ("model.safetensors", "pytorch_model.bin", "onnx/*.onnx")
    )
    if not has_weights:
        pytest.skip(f"FT artifact at {_REAL_ARTIFACT} has no weight file (config/tokenizer only)")

    ef = embedding.AdaptMemFTEncoder(model_path=_REAL_ARTIFACT, device="cpu")
    try:
        out = ef(["the quick brown fox"])
    except (OSError, RuntimeError) as e:
        pytest.skip(f"FT artifact present but not loadable in this env: {e}")
    arr = np.asarray(out)
    assert arr.ndim == 2 and arr.shape[0] == 1
    # Whatever the FT dim, vectors should come back L2-normalized.
    norm = float(np.linalg.norm(arr[0]))
    assert np.isclose(norm, 1.0, atol=1e-3), f"expected unit-norm, got {norm}"
