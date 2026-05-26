"""Unit tests for mempalace.calibration (techempower-org/mempalace#167).

Pure math + serialization — no palace, no network, no model. Covers:

* isotonic fit produces a monotone non-decreasing map
* apply clamps to [0, 1] and to endpoints out of range
* JSON save/load roundtrip preserves the map and provenance
* missing/malformed calibrator file → load returns None (so the search
  path omits confidence rather than faking it)
* brier_score and expected_calibration_error math on hand-checkable inputs
* the pure-python PAV fallback matches sklearn on the same data
"""

import json

import pytest

from mempalace.calibration import (
    Calibrator,
    apply_calibrator,
    brier_score,
    expected_calibration_error,
    fit_calibrator,
    _pav,
)


class TestFitAndApply:
    def test_perfectly_separable_is_monotone_and_bounded(self):
        # similarity < 0.5 → not relevant, >= 0.5 → relevant.
        labeled = [(s / 100.0, s >= 50) for s in range(0, 100)]
        cal = fit_calibrator(labeled, source="unit")
        # Non-decreasing y.
        assert all(cal.y[i] <= cal.y[i + 1] + 1e-9 for i in range(len(cal.y) - 1))
        # Low similarity maps low, high maps high.
        assert cal.apply(0.1) < 0.5
        assert cal.apply(0.9) > 0.5
        # Bounded.
        assert 0.0 <= cal.apply(0.1) <= 1.0
        assert 0.0 <= cal.apply(0.9) <= 1.0
        assert cal.source == "unit"
        assert cal.n_samples == 100

    def test_apply_clamps_out_of_range_to_endpoints(self):
        labeled = [(0.2, False), (0.4, False), (0.6, True), (0.8, True)]
        cal = fit_calibrator(labeled)
        below = cal.apply(-5.0)
        above = cal.apply(5.0)
        assert below == cal.apply(cal.x[0])
        assert above == cal.apply(cal.x[-1])
        assert 0.0 <= below <= 1.0
        assert 0.0 <= above <= 1.0

    def test_empty_input_is_identity_calibrator(self):
        cal = fit_calibrator([])
        assert cal.x == []
        assert cal.n_samples == 0
        # Empty calibrator is identity (but callers should omit confidence).
        assert cal.apply(0.42) == pytest.approx(0.42)

    def test_apply_is_monotone_nondecreasing_across_range(self):
        labeled = [(s / 50.0, (s % 7) < 3) for s in range(0, 50)]
        cal = fit_calibrator(labeled)
        xs = [i / 100.0 for i in range(0, 101)]
        ys = [cal.apply(x) for x in xs]
        assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))


class TestSerialization:
    def test_save_load_roundtrip(self, tmp_path):
        labeled = [(0.1, False), (0.3, False), (0.7, True), (0.9, True)]
        cal = fit_calibrator(labeled, source="git_probes_v2")
        path = tmp_path / "cal.json"
        cal.save(path)
        loaded = Calibrator.load(path)
        assert loaded is not None
        assert loaded.source == "git_probes_v2"
        assert loaded.n_samples == 4
        for x in (0.0, 0.2, 0.5, 0.8, 1.0):
            assert loaded.apply(x) == pytest.approx(cal.apply(x))

    def test_load_missing_file_returns_none(self, tmp_path):
        assert Calibrator.load(tmp_path / "does-not-exist.json") is None

    def test_load_malformed_json_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        assert Calibrator.load(p) is None

    def test_load_missing_keys_returns_none(self, tmp_path):
        p = tmp_path / "incomplete.json"
        p.write_text(json.dumps({"source": "x"}))
        assert Calibrator.load(p) is None

    def test_save_creates_parent_dirs(self, tmp_path):
        cal = fit_calibrator([(0.2, False), (0.8, True)])
        nested = tmp_path / "a" / "b" / "cal.json"
        cal.save(nested)
        assert nested.exists()


class TestApplyCalibratorWrapper:
    def test_none_calibrator_returns_none(self):
        assert apply_calibrator(None, 0.5) is None

    def test_none_similarity_returns_none(self):
        cal = fit_calibrator([(0.2, False), (0.8, True)])
        assert apply_calibrator(cal, None) is None

    def test_rounds_to_three_decimals(self):
        cal = fit_calibrator([(0.2, False), (0.8, True)])
        out = apply_calibrator(cal, 0.8)
        assert out is not None
        assert round(out, 3) == out


class TestBrierScore:
    def test_perfect_prediction_is_zero(self):
        assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)

    def test_always_half_is_quarter(self):
        # (0.5 - 1)^2 = (0.5 - 0)^2 = 0.25 for every sample.
        assert brier_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == pytest.approx(0.25)

    def test_maximally_wrong_is_one(self):
        assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)

    def test_known_value(self):
        # (0.9-1)^2 + (0.2-0)^2 = 0.01 + 0.04 = 0.05; /2 = 0.025
        assert brier_score([0.9, 0.2], [1, 0]) == pytest.approx(0.025)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            brier_score([0.5, 0.5], [1])

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            brier_score([], [])


class TestExpectedCalibrationError:
    def test_perfectly_calibrated_low_ece(self):
        # 10 samples at conf 0.0 all-negative, 10 at conf 1.0 all-positive.
        confs = [0.0] * 10 + [1.0] * 10
        outs = [0] * 10 + [1] * 10
        assert expected_calibration_error(confs, outs, n_bins=10) == pytest.approx(0.0)

    def test_overconfident_has_positive_ece(self):
        # Predict 1.0 but only half are relevant → |acc - conf| = 0.5.
        confs = [1.0] * 4
        outs = [1, 0, 1, 0]
        assert expected_calibration_error(confs, outs, n_bins=10) == pytest.approx(0.5)

    def test_conf_one_lands_in_top_bin(self):
        # A lone conf=1.0 relevant hit is perfectly calibrated in its bin.
        assert expected_calibration_error([1.0], [1], n_bins=10) == pytest.approx(0.0)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            expected_calibration_error([0.5], [1, 0])

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            expected_calibration_error([], [])

    def test_bad_n_bins_raises(self):
        with pytest.raises(ValueError):
            expected_calibration_error([0.5], [1], n_bins=0)


class TestPavFallback:
    def test_pav_is_monotone(self):
        xs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
        ys = [0.0, 1.0, 0.0, 1.0, 1.0, 1.0]
        bx, by = _pav(xs, ys)
        assert all(by[i] <= by[i + 1] + 1e-9 for i in range(len(by) - 1))
        assert all(0.0 <= v <= 1.0 for v in by)

    def test_pav_matches_sklearn_when_available(self):
        pytest.importorskip("sklearn")
        from sklearn.isotonic import IsotonicRegression

        xs = [s / 30.0 for s in range(0, 30)]
        ys = [1.0 if (s % 5) < 2 else 0.0 for s in range(0, 30)]

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
        sk_pred = iso.fit_transform(xs, ys)

        bx, by = _pav(xs, ys)
        # Reconstruct a step function from PAV breakpoints and compare on the
        # same xs. PAV breakpoints are right-edges of pooled blocks.
        cal = Calibrator(x=bx, y=by)
        for x, sk in zip(xs, sk_pred):
            assert cal.apply(x) == pytest.approx(float(sk), abs=1e-6)
