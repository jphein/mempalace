"""Tests for the filesystem source adapter."""

import os

import pytest

from mempalace.sources.base import DrawerRecord, SourceItemMetadata, SourceRef
from mempalace.sources.context import PalaceContext
from mempalace.sources.filesystem import FilesystemSourceAdapter


@pytest.fixture
def adapter():
    return FilesystemSourceAdapter()


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory with a few files."""
    (tmp_path / "mempalace.yaml").write_text(
        "wing: test_wing\nrooms:\n  - name: code\n    description: source code\n"
    )
    (tmp_path / "hello.py").write_text(
        "def hello():\n" + "    x = 1\n" * 20 + "    return 'world'\n"
    )
    (tmp_path / "README.md").write_text(
        "# Test Project\n\n" + "This is a detailed test project README.\n" * 10
    )
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "main.py").write_text("import os\nimport sys\n\n" + "def main():\n    pass\n" * 10)
    return tmp_path


@pytest.fixture
def palace_ctx():
    """Minimal PalaceContext stub for adapter tests."""

    class _FakeCollection:
        def add(self, **kw):
            pass

        def upsert(self, **kw):
            pass

        def query(self, **kw):
            return {"ids": [], "documents": []}

        def get(self, **kw):
            return {"ids": [], "documents": [], "metadatas": []}

        def delete(self, **kw):
            pass

        def count(self):
            return 0

    class _FakeKG:
        def add_triple(self, *a, **kw):
            pass

    return PalaceContext(
        drawer_collection=_FakeCollection(),
        knowledge_graph=_FakeKG(),
        palace_path="/tmp/fake-palace",
    )


class TestFilesystemAdapter:
    def test_class_attributes(self):
        assert FilesystemSourceAdapter.name == "filesystem"
        assert FilesystemSourceAdapter.spec_version == "1.0"
        assert FilesystemSourceAdapter.adapter_version == "1.0.0"
        assert "supports_incremental" in FilesystemSourceAdapter.capabilities
        assert "chunk_text" in FilesystemSourceAdapter.declared_transformations

    def test_describe_schema(self, adapter):
        schema = adapter.describe_schema()
        assert schema.version == "1.0.0"
        assert "source_file" in schema.fields
        assert schema.fields["source_file"].indexed is True

    def test_ingest_yields_records(self, adapter, project_dir, palace_ctx):
        source = SourceRef(local_path=str(project_dir))
        results = list(adapter.ingest(source=source, palace=palace_ctx))

        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        drawers = [r for r in results if isinstance(r, DrawerRecord)]

        assert len(items) >= 2
        assert len(drawers) >= 2

        for drawer in drawers:
            assert drawer.content
            assert drawer.source_file
            assert drawer.route_hint is not None
            assert drawer.route_hint.wing == "test_wing"

    def test_ingest_respects_wing_override(self, adapter, project_dir, palace_ctx):
        source = SourceRef(
            local_path=str(project_dir),
            options={"wing": "custom_wing"},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        assert all(d.route_hint.wing == "custom_wing" for d in drawers)

    def test_ingest_requires_local_path(self, adapter, palace_ctx):
        source = SourceRef(uri="https://example.com")
        with pytest.raises(Exception, match="requires source.local_path"):
            list(adapter.ingest(source=source, palace=palace_ctx))

    def test_ingest_raises_on_missing_dir(self, adapter, palace_ctx):
        source = SourceRef(local_path="/nonexistent/path/xyz")
        with pytest.raises(Exception, match="Not a directory"):
            list(adapter.ingest(source=source, palace=palace_ctx))

    def test_ingest_limit(self, adapter, project_dir, palace_ctx):
        source = SourceRef(
            local_path=str(project_dir),
            options={"limit": 1},
        )
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        items = [r for r in results if isinstance(r, SourceItemMetadata)]
        assert len(items) == 1

    def test_is_current_returns_false_for_none(self, adapter):
        item = SourceItemMetadata(source_file="/tmp/x.py", version="v1")
        assert adapter.is_current(item=item, existing_metadata=None) is False

    def test_is_current_checks_mtime(self, adapter, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        mtime = os.path.getmtime(str(f))
        item = SourceItemMetadata(source_file=str(f), version="v1")

        assert adapter.is_current(item=item, existing_metadata={"source_mtime": mtime}) is True
        assert (
            adapter.is_current(item=item, existing_metadata={"source_mtime": mtime + 100}) is False
        )

    def test_source_summary(self, adapter, project_dir):
        source = SourceRef(local_path=str(project_dir))
        summary = adapter.source_summary(source=source)
        assert "filesystem" in summary.description
        assert summary.item_count is not None
        assert summary.item_count >= 2

    def test_source_summary_no_path(self, adapter):
        source = SourceRef()
        summary = adapter.source_summary(source=source)
        assert "no path" in summary.description

    def test_chunk_metadata_has_line_numbers(self, adapter, project_dir, palace_ctx):
        source = SourceRef(local_path=str(project_dir))
        results = list(adapter.ingest(source=source, palace=palace_ctx))
        drawers = [r for r in results if isinstance(r, DrawerRecord)]
        for d in drawers:
            if d.metadata.get("line_start") is not None:
                assert isinstance(d.metadata["line_start"], int)
                assert isinstance(d.metadata["line_end"], int)
                assert d.metadata["line_start"] >= 1


class TestFilesystemRegistration:
    def test_registered_as_entry_point(self):
        from mempalace.sources.registry import available_adapters, get_adapter_class

        assert "filesystem" in available_adapters()
        cls = get_adapter_class("filesystem")
        assert cls is FilesystemSourceAdapter
