"""Unit tests for the postgres write-path wing guard (issue #381).

Pure-function coverage of ``_coerce_wing`` — no database needed, so these
always run. Integration coverage of the guarded write paths lives in
``tests/test_backends_postgres.py`` (TEST_POSTGRES_DSN-gated).

Background: the production drawers table's ``wing`` column defaults to
``''::text``, so writers that omitted the key filed drawers under an
unreachable empty wing (54 smoke-test drawers, 2026-05-12), and writers
that passed raw dirnames forked near-duplicate wings
(``kiyo-xhci-fix`` (3) vs ``kiyo_xhci_fix`` (6004)).
"""

import pytest

from mempalace.backends.postgres import FALLBACK_WING, _coerce_wing


class TestCoerceWingFallback:
    """Missing/empty/degenerate wings land in FALLBACK_WING, never ''."""

    @pytest.mark.parametrize(
        "raw",
        [None, "", " ", "\t", "---", "___", "- -", "_-_"],
        ids=["none", "empty", "space", "tab", "dashes", "underscores", "mixed", "sandwich"],
    )
    def test_degenerate_values_fall_back(self, raw):
        assert _coerce_wing(raw) == FALLBACK_WING

    def test_fallback_is_general(self):
        # The daemon's default wing — where wing-less writes are expected
        # to land, and where the #381 migration re-files stragglers.
        assert FALLBACK_WING == "general"


class TestCoerceWingNormalization:
    """Separator/case variants collapse to the init-time slug rule."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("kiyo-xhci-fix", "kiyo_xhci_fix"),
            ("Kiyo-XHCI-Fix", "kiyo_xhci_fix"),
            ("My Project", "my_project"),
            ("-home-jp-Projects-memorypalace", "home_jp_projects_memorypalace"),
        ],
    )
    def test_variants_normalize(self, raw, expected):
        assert _coerce_wing(raw) == expected

    @pytest.mark.parametrize(
        "already_normal",
        ["kiyo_xhci_fix", "memorypalace", "general", "palace_daemon"],
    )
    def test_normalized_names_pass_through(self, already_normal):
        assert _coerce_wing(already_normal) == already_normal

    def test_non_string_values_coerce_before_normalizing(self):
        # _metadata_value semantics: bools stringify to true/false, other
        # scalars via str(). They become searchable slugs, not crashes.
        assert _coerce_wing(True) == "true"
        assert _coerce_wing(42) == "42"
