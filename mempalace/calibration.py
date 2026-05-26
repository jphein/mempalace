"""Calibrated confidence for search results.

Maps the raw ``similarity`` field a hit carries (a transformed cosine
distance, ``max(0, 1 - distance)``) to a calibrated probability that the
hit is relevant. ``similarity`` has the *range* of a probability but no
statistical guarantee of being calibrated; this module supplies the
guarantee empirically via isotonic regression over a labeled probe set.

Design constraints (see docs/research/uncertainty-aware-retrieval.md):

* **No required heavy dependency.** ``sklearn.isotonic.IsotonicRegression``
  is used when available (it is pulled in transitively by
  sentence-transformers in the ``[gpu]`` extra), but a pure-python
  Pool-Adjacent-Violators (PAV) implementation is the fallback so the
  calibrator works in a CPU-only / minimal install.
* **Missing calibrator → no fake confidence.** Callers that have no
  configured calibrator simply omit the ``confidence`` field rather than
  emitting an identity passthrough that would look calibrated but isn't.
* **Pure / offline.** Fitting and applying touch no palace, no network,
  no model. Fit is millisecond-scale; apply is a single bisect per hit.

The calibrator persists as a small JSON file: a sorted list of
``(x, y)`` breakpoints of the monotone step function plus provenance
metadata (``source`` label, ``n_samples``). Apply is piecewise-constant
interpolation between breakpoints, clamped to ``[0, 1]``.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


@dataclass
class Calibrator:
    """A monotone similarity→confidence map fit by isotonic regression.

    ``x`` is the sorted list of similarity breakpoints; ``y`` is the
    calibrated confidence at each breakpoint (non-decreasing). ``apply``
    does piecewise-constant interpolation: for a query similarity ``s``,
    return the ``y`` of the largest breakpoint ``<= s`` (clamped to the
    endpoints).
    """

    x: list = field(default_factory=list)
    y: list = field(default_factory=list)
    source: str = "unknown"
    n_samples: int = 0

    def apply(self, similarity: float) -> float:
        """Return calibrated confidence in ``[0, 1]`` for a raw similarity.

        An empty calibrator (no breakpoints) is the identity — but
        callers should treat "no calibrator configured" as "omit the
        field" rather than constructing an empty one. Out-of-range
        similarities clamp to the nearest endpoint's confidence.
        """
        if not self.x:
            return _clamp01(similarity)
        if similarity <= self.x[0]:
            return _clamp01(self.y[0])
        if similarity >= self.x[-1]:
            return _clamp01(self.y[-1])
        # Largest breakpoint <= similarity. bisect_right - 1 gives that index.
        idx = bisect.bisect_right(self.x, similarity) - 1
        return _clamp01(self.y[idx])

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "kind": "isotonic",
            "x": [float(v) for v in self.x],
            "y": [float(v) for v in self.y],
            "source": self.source,
            "n_samples": int(self.n_samples),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Calibrator":
        return cls(
            x=[float(v) for v in d.get("x", [])],
            y=[float(v) for v in d.get("y", [])],
            source=str(d.get("source", "unknown")),
            n_samples=int(d.get("n_samples", 0)),
        )

    def save(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path) -> Optional["Calibrator"]:
        """Load a calibrator from JSON, or ``None`` if missing/unreadable.

        A missing or malformed file yields ``None`` so the search path can
        cleanly omit the ``confidence`` field rather than faking one.
        """
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p, "r") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(d, dict) or "x" not in d or "y" not in d:
            return None
        return cls.from_dict(d)


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return float(v)


def _pav(xs: Sequence[float], ys: Sequence[float]) -> tuple:
    """Pool-Adjacent-Violators isotonic regression (increasing).

    Pure-python fallback for when sklearn is unavailable. ``xs`` must be
    sorted ascending; ties in ``xs`` are merged into one breakpoint with
    the pooled mean. Returns ``(bx, by)`` — deduplicated breakpoints with
    a non-decreasing ``by``.
    """
    # Merge duplicate x into single (x, mean(y), weight) blocks first so
    # the step function has one value per distinct similarity.
    merged_x: list = []
    sums: list = []
    weights: list = []
    for x, y in zip(xs, ys):
        if merged_x and x == merged_x[-1]:
            sums[-1] += y
            weights[-1] += 1
        else:
            merged_x.append(x)
            sums.append(float(y))
            weights.append(1)

    # PAV over the per-x means.
    vals = [s / w for s, w in zip(sums, weights)]
    block_val: list = []
    block_w: list = []
    block_x: list = []
    for x, v, w in zip(merged_x, vals, weights):
        block_x.append(x)
        block_val.append(v)
        block_w.append(w)
        # Merge backwards while monotonicity is violated.
        while len(block_val) > 1 and block_val[-2] > block_val[-1]:
            w2 = block_w[-1] + block_w[-2]
            v2 = (block_val[-1] * block_w[-1] + block_val[-2] * block_w[-2]) / w2
            block_val.pop()
            block_w.pop()
            x_last = block_x.pop()
            block_val[-1] = v2
            block_w[-1] = w2
            block_x[-1] = x_last  # right edge of the pooled block

    # Expand pooled block values back to every distinct x in merged_x, so
    # the breakpoints have one (x, y) per distinct similarity. Returning
    # only block right-edges would drop interior x's and bisect them onto
    # the previous block's value — a step function that diverges from
    # sklearn's IsotonicRegression on non-fully-pooled inputs.
    by: list = []
    for v, w in zip(block_val, block_w):
        by.extend([v] * w)
    return merged_x, by


def fit_calibrator(
    labeled_hits: Sequence[tuple],
    source: str = "unknown",
) -> Calibrator:
    """Fit a similarity→P(relevant) calibrator from labeled hits.

    Args:
        labeled_hits: sequence of ``(similarity, relevant)`` pairs.
            ``similarity`` is a float in ``[0, 1]`` (the field the search
            path already returns); ``relevant`` is truthy/falsy.
        source: provenance label stored on the calibrator (e.g.
            ``"git_probes_v2"``) so downstream consumers know what
            distribution it was fit on.

    Returns a :class:`Calibrator`. Uses ``sklearn.isotonic`` when
    importable, else the pure-python PAV fallback. Empty input yields an
    empty (identity) calibrator.
    """
    pairs = [(float(s), 1.0 if r else 0.0) for s, r in labeled_hits]
    if not pairs:
        return Calibrator(source=source, n_samples=0)
    pairs.sort(key=lambda p: p[0])
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    try:
        from sklearn.isotonic import IsotonicRegression  # type: ignore

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
        fitted = iso.fit_transform(xs, ys)
        # Deduplicate to distinct x breakpoints (sklearn returns one y per
        # input row; collapse equal-x rows to a single step).
        bx: list = []
        by: list = []
        for x, yv in zip(xs, fitted):
            if bx and x == bx[-1]:
                by[-1] = yv
            else:
                bx.append(x)
                by.append(yv)
    except Exception:
        bx, by = _pav(xs, ys)

    return Calibrator(
        x=[float(v) for v in bx],
        y=[_clamp01(v) for v in by],
        source=source,
        n_samples=len(pairs),
    )


def apply_calibrator(cal: Optional[Calibrator], similarity: Optional[float]) -> Optional[float]:
    """Convenience wrapper: calibrated confidence, or ``None``.

    Returns ``None`` when there is no calibrator or no similarity to
    calibrate (e.g. BM25-only / graph-source hits carry ``similarity=None``).
    Rounded to 3 decimals to match the ``similarity`` field's precision.
    """
    if cal is None or similarity is None:
        return None
    return round(cal.apply(float(similarity)), 3)


# ── scoring rules ────────────────────────────────────────────────────────


def brier_score(confidences: Sequence[float], outcomes: Sequence[float]) -> float:
    """Mean squared error between predicted confidence and binary outcome.

    The Brier score is a bounded proper scoring rule:
    ``(1/N) Σ (conf_i − outcome_i)²``. Lower is better; 0 is perfect,
    0.25 is the score of always predicting 0.5, 1.0 is maximally wrong.

    Raises ``ValueError`` on length mismatch or empty input — a Brier
    score over nothing is undefined, and silently returning 0.0 would
    read as "perfectly calibrated".
    """
    confs = list(confidences)
    outs = list(outcomes)
    if len(confs) != len(outs):
        raise ValueError(
            f"brier_score: length mismatch ({len(confs)} confidences, {len(outs)} outcomes)"
        )
    if not confs:
        raise ValueError("brier_score: empty input is undefined")
    total = 0.0
    for c, o in zip(confs, outs):
        diff = float(c) - (1.0 if o else 0.0)
        total += diff * diff
    return total / len(confs)


def expected_calibration_error(
    confidences: Sequence[float],
    outcomes: Sequence[float],
    n_bins: int = 10,
) -> float:
    """Expected Calibration Error (ECE) over equal-width confidence bins.

    ``ECE = Σ_m (|B_m|/N) · |acc(B_m) − conf(B_m)|`` where each bin ``B_m``
    is a ``[m/n_bins, (m+1)/n_bins)`` slice of the confidence range (the
    top bin is closed on the right so ``conf == 1.0`` lands somewhere).
    ``acc`` is the empirical relevance frequency in the bin; ``conf`` is
    the mean predicted confidence in the bin. Empty bins contribute 0.

    Raises ``ValueError`` on length mismatch, empty input, or
    ``n_bins < 1``.
    """
    confs = list(confidences)
    outs = list(outcomes)
    if len(confs) != len(outs):
        raise ValueError(
            f"expected_calibration_error: length mismatch "
            f"({len(confs)} confidences, {len(outs)} outcomes)"
        )
    if not confs:
        raise ValueError("expected_calibration_error: empty input is undefined")
    if n_bins < 1:
        raise ValueError(f"expected_calibration_error: n_bins must be >= 1, got {n_bins}")

    n = len(confs)
    bin_conf_sum = [0.0] * n_bins
    bin_out_sum = [0.0] * n_bins
    bin_count = [0] * n_bins
    for c, o in zip(confs, outs):
        cf = _clamp01(float(c))
        # Map to bin index; cf == 1.0 goes in the top bin.
        b = int(cf * n_bins)
        if b == n_bins:
            b = n_bins - 1
        bin_conf_sum[b] += cf
        bin_out_sum[b] += 1.0 if o else 0.0
        bin_count[b] += 1

    ece = 0.0
    for m in range(n_bins):
        if bin_count[m] == 0:
            continue
        acc = bin_out_sum[m] / bin_count[m]
        conf = bin_conf_sum[m] / bin_count[m]
        ece += (bin_count[m] / n) * abs(acc - conf)
    return ece
