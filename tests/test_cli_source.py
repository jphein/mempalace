"""Tests for ``mempalace mine --source <adapter>`` CLI flag (issue #57).

Validates that the ``--source`` argument on the mine subcommand correctly
routes through the source-adapter plugin contract instead of the built-in
mine pipeline.
"""

import argparse
from unittest.mock import patch

import pytest

from mempalace.sources import (
    AdapterSchema,
    BaseSourceAdapter,
    DrawerRecord,
    FieldSpec,
    RouteHint,
    SourceItemMetadata,
    register,
    reset_adapters,
    unregister,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _StubAdapter(BaseSourceAdapter):
    """Minimal adapter that yields predictable records for test assertions."""

    name = "stub"
    adapter_version = "0.0.1"
    capabilities = frozenset()
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset()

    # Test-controlled list of results to yield from ingest
    results_to_yield: list = []

    def ingest(self, *, source, palace):
        for r in self.results_to_yield:
            yield r

    def describe_schema(self):
        return AdapterSchema(
            version="1.0",
            fields={"test": FieldSpec(type="string", required=False, description="test")},
        )


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Clean adapter registry between tests."""
    yield
    reset_adapters()
    for name in ("stub", "_test_adapter"):
        try:
            unregister(name)
        except Exception:
            pass


@pytest.fixture()
def stub_adapter():
    """Register and return a stub adapter for tests."""
    register("stub", _StubAdapter)
    return _StubAdapter()


def _make_mine_args(*, source=None, directory="/tmp/testdir", wing=None,
                    palace=None, dry_run=False, agent="mempalace",
                    limit=0, mode="projects", no_gitignore=False,
                    include_ignored=None, redetect_origin=False,
                    extract="exchange", max_chunks_per_file=None):
    """Build an argparse.Namespace mimicking ``mempalace mine`` args."""
    return argparse.Namespace(
        command="mine",
        dir=directory,
        source=source,
        mode=mode,
        wing=wing,
        palace=palace,
        dry_run=dry_run,
        agent=agent,
        limit=limit,
        no_gitignore=no_gitignore,
        include_ignored=include_ignored or [],
        redetect_origin=redetect_origin,
        extract=extract,
        max_chunks_per_file=max_chunks_per_file,
        json=False,
        quiet=False,
    )


# ---------------------------------------------------------------------------
# --source list: enumerate installed adapters
# ---------------------------------------------------------------------------


def test_source_list_shows_installed_adapters(stub_adapter, capsys):
    """``--source list`` prints adapter names and exits cleanly."""
    from mempalace.cli import _mine_via_adapter

    args = _make_mine_args(source="list")
    _mine_via_adapter(args)
    out = capsys.readouterr().out
    assert "stub" in out
    assert "Installed source adapters:" in out


def test_source_list_empty_when_no_adapters(capsys):
    """``--source list`` with no adapters prints a helpful message."""
    from mempalace.cli import _mine_via_adapter

    # Patch available_adapters to return empty (since entry-point discovery
    # may find in-tree adapters in the dev install). The import inside
    # _mine_via_adapter binds from mempalace.sources, so patch there.
    with patch("mempalace.sources.available_adapters", return_value=[]):
        args = _make_mine_args(source="list")
        _mine_via_adapter(args)
    out = capsys.readouterr().out
    assert "No source adapters installed" in out


# ---------------------------------------------------------------------------
# --source <unknown>: error handling
# ---------------------------------------------------------------------------


def test_source_unknown_adapter_exits_with_error():
    """An unregistered adapter name prints an error and exits 1."""
    from mempalace.cli import _mine_via_adapter

    args = _make_mine_args(source="nonexistent_adapter_xyz")
    with pytest.raises(SystemExit) as exc_info:
        _mine_via_adapter(args)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# --source <adapter> --dry-run
# ---------------------------------------------------------------------------


def test_source_dry_run_prints_summary(stub_adapter, capsys):
    """``--source stub --dry-run`` shows the dry-run banner without filing."""
    from mempalace.cli import _mine_via_adapter

    args = _make_mine_args(source="stub", dry_run=True, directory="/tmp/myproject")
    _mine_via_adapter(args)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "stub" in out


# ---------------------------------------------------------------------------
# --source <adapter>: drawer upsert routing
# ---------------------------------------------------------------------------


class _FakeCollection:
    """In-memory collection stand-in for tests."""

    def __init__(self):
        self.upserts = []

    def add(self, **kwargs):
        pass

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def query(self, **kwargs):
        return {}

    def get(self, **kwargs):
        return {}

    def delete(self, **kwargs):
        pass

    def count(self):
        return len(self.upserts)


class _FakeKG:
    """In-memory knowledge graph stand-in."""

    def add_triple(self, subject, predicate, obj, **kwargs):
        pass

    def close(self):
        pass


class _YieldingAdapter(BaseSourceAdapter):
    """Adapter that yields controlled SourceItemMetadata + DrawerRecord pairs."""

    name = "_test_adapter"
    adapter_version = "1.0.0"
    capabilities = frozenset()
    supported_modes = frozenset({"chunked_content"})
    declared_transformations = frozenset()

    def __init__(self):
        self._items = []

    def set_items(self, items):
        self._items = items

    def ingest(self, *, source, palace):
        for item in self._items:
            yield item

    def describe_schema(self):
        return AdapterSchema(
            version="1.0",
            fields={},
        )


def _register_yielding_adapter(items):
    """Register a _YieldingAdapter with the given items and patch the instance cache."""
    adapter = _YieldingAdapter()
    adapter.set_items(items)
    register("_test_adapter", _YieldingAdapter)
    from mempalace.sources import registry as _reg
    _reg._instances["_test_adapter"] = adapter
    return adapter


def test_source_adapter_upserts_drawers_with_metadata(capsys):
    """Drawers yielded by the adapter are upserted with wing/room/agent metadata."""
    from mempalace.cli import _mine_via_adapter

    fake_col = _FakeCollection()
    fake_kg = _FakeKG()

    _register_yielding_adapter([
        SourceItemMetadata(source_file="/tmp/file.py", version="v1"),
        DrawerRecord(
            content="hello world",
            source_file="/tmp/file.py",
            chunk_index=0,
            route_hint=RouteHint(wing="myproject", room="code"),
        ),
    ])

    args = _make_mine_args(source="_test_adapter", directory="/tmp/myproject", wing="mywing")

    with patch("mempalace.palace.get_collection", return_value=fake_col), \
         patch("mempalace.knowledge_graph.KnowledgeGraph", return_value=fake_kg), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg:
        mock_cfg.return_value.palace_path = "/tmp/fake_palace"
        _mine_via_adapter(args)

    assert len(fake_col.upserts) == 1
    meta = fake_col.upserts[0]["metadatas"][0]
    # Route hint wing is used when present
    assert meta["wing"] == "myproject"
    assert meta["room"] == "code"
    assert meta["agent"] == "mempalace"
    assert meta["source_file"] == "/tmp/file.py"
    out = capsys.readouterr().out
    assert "Filed 1 drawers from 1 items" in out


def test_source_adapter_falls_back_to_cli_wing_when_no_hint(capsys):
    """When DrawerRecord has no route_hint, CLI-supplied wing is used."""
    from mempalace.cli import _mine_via_adapter

    fake_col = _FakeCollection()
    fake_kg = _FakeKG()

    _register_yielding_adapter([
        SourceItemMetadata(source_file="/tmp/f.txt", version="v1"),
        DrawerRecord(
            content="no hint",
            source_file="/tmp/f.txt",
            chunk_index=0,
        ),
    ])

    args = _make_mine_args(source="_test_adapter", directory="/tmp/proj", wing="explicit_wing")

    with patch("mempalace.palace.get_collection", return_value=fake_col), \
         patch("mempalace.knowledge_graph.KnowledgeGraph", return_value=fake_kg), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg:
        mock_cfg.return_value.palace_path = "/tmp/fake_palace"
        _mine_via_adapter(args)

    meta = fake_col.upserts[0]["metadatas"][0]
    assert meta["wing"] == "explicit_wing"
    assert meta["room"] == "general"  # default room


def test_source_adapter_multiple_drawers(capsys):
    """Multiple DrawerRecords from a single source item are all upserted."""
    from mempalace.cli import _mine_via_adapter

    fake_col = _FakeCollection()
    fake_kg = _FakeKG()

    _register_yielding_adapter([
        SourceItemMetadata(source_file="/tmp/big.py", version="v1"),
        DrawerRecord(content="chunk 0", source_file="/tmp/big.py", chunk_index=0,
                     route_hint=RouteHint(wing="w")),
        DrawerRecord(content="chunk 1", source_file="/tmp/big.py", chunk_index=1,
                     route_hint=RouteHint(wing="w")),
        DrawerRecord(content="chunk 2", source_file="/tmp/big.py", chunk_index=2,
                     route_hint=RouteHint(wing="w")),
    ])

    args = _make_mine_args(source="_test_adapter", directory="/tmp/proj")

    with patch("mempalace.palace.get_collection", return_value=fake_col), \
         patch("mempalace.knowledge_graph.KnowledgeGraph", return_value=fake_kg), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg:
        mock_cfg.return_value.palace_path = "/tmp/fake_palace"
        _mine_via_adapter(args)

    assert len(fake_col.upserts) == 3
    out = capsys.readouterr().out
    assert "Filed 3 drawers from 1 items" in out


# ---------------------------------------------------------------------------
# cmd_mine routes to adapter when --source is present
# ---------------------------------------------------------------------------


def test_cmd_mine_dispatches_to_adapter_when_source_set():
    """cmd_mine delegates to _mine_via_adapter when --source is non-None."""
    from mempalace.cli import cmd_mine

    args = _make_mine_args(source="stub")

    with patch("mempalace.cli._mine_via_adapter") as mock_via:
        cmd_mine(args)
    mock_via.assert_called_once_with(args)


def test_cmd_mine_skips_adapter_when_source_is_none():
    """cmd_mine uses the normal mine pipeline when --source is None."""
    from mempalace.cli import cmd_mine

    args = _make_mine_args(source=None, directory="/tmp/proj", palace="/tmp/palace")

    with patch("mempalace.cli._mine_via_adapter") as mock_via, \
         patch("mempalace.cli._daemon_strict", return_value=False), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg, \
         patch("mempalace.miner.mine"):
        mock_cfg.return_value.palace_path = "/tmp/palace"
        cmd_mine(args)

    mock_via.assert_not_called()


# ---------------------------------------------------------------------------
# --source is wired into argparse
# ---------------------------------------------------------------------------


def test_argparse_accepts_source_flag():
    """The mine subcommand's argparse definition includes --source."""
    from mempalace.cli import main

    with patch("sys.argv", ["mempalace", "mine", "/tmp/dir", "--source", "myadapter"]):
        with patch("mempalace.cli.cmd_mine") as mock_cmd, \
             patch("mempalace.cli.MempalaceConfig") as mock_cfg:
            mock_cfg.return_value.daemon_url = ""
            mock_cfg.return_value.daemon_strict = False
            main()
        call_args = mock_cmd.call_args[0][0]
        assert call_args.source == "myadapter"
        assert call_args.dir == "/tmp/dir"


def test_argparse_source_defaults_to_none():
    """--source defaults to None when not specified."""
    from mempalace.cli import main

    with patch("sys.argv", ["mempalace", "mine", "/tmp/dir"]):
        with patch("mempalace.cli.cmd_mine") as mock_cmd, \
             patch("mempalace.cli.MempalaceConfig") as mock_cfg:
            mock_cfg.return_value.daemon_url = ""
            mock_cfg.return_value.daemon_strict = False
            main()
        call_args = mock_cmd.call_args[0][0]
        assert call_args.source is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_source_adapter_wing_derived_from_directory_name(capsys):
    """When --wing is not provided, wing is derived from the directory name."""
    from mempalace.cli import _mine_via_adapter

    fake_col = _FakeCollection()
    fake_kg = _FakeKG()

    _register_yielding_adapter([
        SourceItemMetadata(source_file="/tmp/my-proj/f.txt", version="v1"),
        DrawerRecord(content="data", source_file="/tmp/my-proj/f.txt", chunk_index=0),
    ])

    # No --wing set; directory is /tmp/my-proj
    args = _make_mine_args(source="_test_adapter", directory="/tmp/my-proj", wing=None)

    with patch("mempalace.palace.get_collection", return_value=fake_col), \
         patch("mempalace.knowledge_graph.KnowledgeGraph", return_value=fake_kg), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg:
        mock_cfg.return_value.palace_path = "/tmp/fake_palace"
        _mine_via_adapter(args)

    meta = fake_col.upserts[0]["metadatas"][0]
    # normalize_wing_name converts hyphens to underscores
    assert meta["wing"] == "my_proj"


def test_source_adapter_palace_open_failure_exits():
    """If the palace cannot be opened, _mine_via_adapter exits with code 1."""
    from mempalace.cli import _mine_via_adapter

    register("_test_adapter", _YieldingAdapter)

    args = _make_mine_args(source="_test_adapter", directory="/tmp/proj")

    with patch("mempalace.palace.get_collection", side_effect=RuntimeError("no palace")), \
         patch("mempalace.cli.MempalaceConfig") as mock_cfg:
        mock_cfg.return_value.palace_path = "/tmp/fake_palace"
        with pytest.raises(SystemExit) as exc_info:
            _mine_via_adapter(args)
        assert exc_info.value.code == 1
