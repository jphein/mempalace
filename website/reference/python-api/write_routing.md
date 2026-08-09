# `mempalace.write_routing`

Source: [`mempalace/write_routing.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/write_routing.py)

Shared daemon-routing policy for MemPalace write callers.

The policy is deliberately transport-agnostic. Hook and CLI consumers decide
whether a daemon is already available and whether they are allowed to start
one; this module turns those facts plus a policy into one explicit route.

This module changes no caller defaults by itself. Hook and CLI adoption are
separate follow-up PRs.

## Classes

### `class WriteRoutingError(ValueError)`

Raised when a write-routing policy is invalid or cannot be applied.

### `class WriteRoutingPolicy(str, Enum)`

User-selected policy for routine write operations.

### `class WriteRoutingTarget(str, Enum)`

Concrete route selected for one write operation.

### `class RoutingPolicyCandidate`

One precedence-ordered source for a routing policy.

### `class ResolvedWriteRoutingPolicy`

A normalized policy plus the source that selected it.

### `class WriteRoutingDecision`

Concrete routing decision for a single operation.

#### `use_daemon`

```python
def use_daemon(self) -> bool
```

Whether this operation should use the daemon.

#### `blocked`

```python
def blocked(self) -> bool
```

Whether the operation must stop instead of writing directly.

## Functions

### `parse_write_routing_policy`

```python
def parse_write_routing_policy(value: Any, *, legacy_boolean: bool = False) -> WriteRoutingPolicy
```

Normalize one routing-policy value.

New policy settings accept only ``direct``, ``prefer``, and ``require``.

Legacy boolean settings additionally map truthy values to ``prefer`` and
falsy values to ``direct`` so existing ``hooks.daemon`` configurations
retain their historical behavior.

### `resolve_write_routing_policy`

```python
def resolve_write_routing_policy(candidates: Iterable[RoutingPolicyCandidate], *, default: WriteRoutingPolicy = WriteRoutingPolicy.DIRECT) -> ResolvedWriteRoutingPolicy
```

Return the first configured policy from ordered policy sources.

### `choose_write_route`

```python
def choose_write_route(policy: WriteRoutingPolicy, *, daemon_available: bool, daemon_can_start: bool) -> WriteRoutingDecision
```

Choose direct, daemon, or blocked for one routine write.

``daemon_can_start`` is normally false for latency-sensitive hooks and
true for interactive CLI commands.

The key safety guarantee is that ``require`` never degrades to a direct
write when the daemon is unavailable.
