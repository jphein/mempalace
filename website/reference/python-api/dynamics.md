# `mempalace.dynamics`

Source: [`mempalace/dynamics.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/dynamics.py)

dynamics.py — Living-connection math for halls + tunnels.

Hebbian potentiation (strength grows on co-access) and Ebbinghaus exponential
decay (strength fades with time since last activation), with the Cepeda
spacing effect: stability grows when reinforcement is spaced rather than
massed.

This module is pure. No I/O, no DB, no chromadb. It operates on plain
dicts (hall records, tunnel records) and mutates them in place. Callers
in ``hallways.py`` and ``palace_graph.py`` invoke these functions; the
math lives here in one place so both connection kinds share identical
semantics.

Schema fields added to hall + tunnel records (all default-safe — existing
records without them work via ``initialize_dynamics_fields``):

    strength: float           — Hebbian connection weight, floored at STRENGTH_FLOOR,
                                capped at MAX_STRENGTH
    stability: float          — decay resistance; grows with spaced reinforcement
    last_activated: str       — ISO datetime; updates on potentiation
    access_count: int         — cumulative co-access events

Research grounding:
    - Hebb (1949): "neurons that fire together, wire together" → potentiation
    - Ebbinghaus (1885): exponential forgetting curve → apply_decay
    - Cepeda et al. (2006): spacing effect → stability growth on spaced reinforcement

## Functions

### `initialize_dynamics_fields`

```python
def initialize_dynamics_fields(connection: dict, *, now: Optional[datetime] = None) -> dict
```

Populate strength/stability/last_activated/access_count if missing.

Existing fields are NOT overwritten — this is a backfill helper for
records created before L7 dynamics shipped. Safe to call on any record;
a no-op when all fields are already present.

The ``now`` parameter is dependency injection for tests; defaults to
current UTC time. Same pattern as the rest of this module.

### `potentiate`

```python
def potentiate(connection: dict, *, increment: float = POTENTIATION_INCREMENT, now: Optional[datetime] = None) -> dict
```

Strengthen ``connection`` on a co-access event.

Updates ``strength`` (capped at ``MAX_STRENGTH``), ``last_activated``,
and ``access_count``. Grows ``stability`` by ``STABILITY_INCREMENT``
only if the gap since the prior activation is at least
``SPACED_INTERVAL_HOURS`` (the Cepeda spacing effect — rapid bursts
don't build durability; distributed practice does).

Mutates and returns the same dict for chaining. Pure aside from that
mutation — no I/O.

### `apply_decay`

```python
def apply_decay(connection: dict, *, now: Optional[datetime] = None) -> dict
```

Apply Ebbinghaus exponential decay to ``connection``'s strength.

The decay model is ``new = old * exp(-days_since_last / stability)``,
floored at ``STRENGTH_FLOOR`` so connections never reach zero. Higher
stability = slower decay (the Cepeda principle: spaced reinforcement
builds durability).

Idempotent at the same instant — calling twice at the same ``now``
without a potentiation in between produces the same final strength.

Mutates and returns the same dict for chaining. Pure aside from that
mutation — no I/O.

``now`` is dependency injection for tests.
