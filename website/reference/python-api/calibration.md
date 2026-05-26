# `mempalace.calibration`

Source: [`mempalace/calibration.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/calibration.py)

Calibrated confidence for search results.

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

## Classes

### `class Calibrator`

A monotone similarity→confidence map fit by isotonic regression.

``x`` is the sorted list of similarity breakpoints; ``y`` is the
calibrated confidence at each breakpoint (non-decreasing). ``apply``
does piecewise-constant interpolation: for a query similarity ``s``,
return the ``y`` of the largest breakpoint ``<= s`` (clamped to the
endpoints).

#### `apply`

```python
def apply(self, similarity: float) -> float
```

Return calibrated confidence in ``[0, 1]`` for a raw similarity.

An empty calibrator (no breakpoints) is the identity — but
callers should treat "no calibrator configured" as "omit the
field" rather than constructing an empty one. Out-of-range
similarities clamp to the nearest endpoint's confidence.

#### `to_dict`

```python
def to_dict(self) -> dict
```

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'Calibrator'
```

#### `save`

```python
def save(self, path) -> None
```

#### `load`

```python
def load(cls, path) -> Optional['Calibrator']
```

Load a calibrator from JSON, or ``None`` if missing/unreadable.

A missing or malformed file yields ``None`` so the search path can
cleanly omit the ``confidence`` field rather than faking one.

## Functions

### `fit_calibrator`

```python
def fit_calibrator(labeled_hits: Sequence[tuple], source: str = 'unknown') -> Calibrator
```

Fit a similarity→P(relevant) calibrator from labeled hits.

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

### `apply_calibrator`

```python
def apply_calibrator(cal: Optional[Calibrator], similarity: Optional[float]) -> Optional[float]
```

Convenience wrapper: calibrated confidence, or ``None``.

Returns ``None`` when there is no calibrator or no similarity to
calibrate (e.g. BM25-only / graph-source hits carry ``similarity=None``).
Rounded to 3 decimals to match the ``similarity`` field's precision.

### `brier_score`

```python
def brier_score(confidences: Sequence[float], outcomes: Sequence[float]) -> float
```

Mean squared error between predicted confidence and binary outcome.

The Brier score is a bounded proper scoring rule:
``(1/N) Σ (conf_i − outcome_i)²``. Lower is better; 0 is perfect,
0.25 is the score of always predicting 0.5, 1.0 is maximally wrong.

Raises ``ValueError`` on length mismatch or empty input — a Brier
score over nothing is undefined, and silently returning 0.0 would
read as "perfectly calibrated".

### `expected_calibration_error`

```python
def expected_calibration_error(confidences: Sequence[float], outcomes: Sequence[float], n_bins: int = 10) -> float
```

Expected Calibration Error (ECE) over equal-width confidence bins.

``ECE = Σ_m (|B_m|/N) · |acc(B_m) − conf(B_m)|`` where each bin ``B_m``
is a ``[m/n_bins, (m+1)/n_bins)`` slice of the confidence range (the
top bin is closed on the right so ``conf == 1.0`` lands somewhere).
``acc`` is the empirical relevance frequency in the bin; ``conf`` is
the mean predicted confidence in the bin. Empty bins contribute 0.

Raises ``ValueError`` on length mismatch, empty input, or
``n_bins < 1``.
