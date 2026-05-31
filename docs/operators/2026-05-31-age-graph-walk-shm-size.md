# mempalace-db `--shm-size` for AGE graph-walk queries

**Date:** 2026-05-31
**Issue:** mempalace#335 (item 3)
**Scope:** the `mempalace-db` postgres+AGE container — **deploy-side**, built
from `disks/mempalace-db/Dockerfile` (sibling repo) and `docker run` on
`familiar`, not provisioned from this repo. This note documents the required
value and flags it as an operator action; there is no in-repo compose file to
patch.

## Problem

AGE graph-walk Cypher that fans a query out across the multi-million-row
`MENTIONS` / `RELATION` edge tables (the hybrid candidate-merger in
`searcher._graph_expand_*`, the daemon's `/search/age-fused` lookup) compiles to
parallel hash joins. Postgres parallel workers exchange tuples through POSIX
shared memory under `/dev/shm`. Docker's **default `/dev/shm` is 64 MB**, and a
hash join over the production graph overflows it:

```
psycopg.errors.DiskFull: could not resize shared memory segment
"/PostgreSQL.NNNNNNNNNN" to 16777216 bytes: No space left on device
```

This is the same 64 MB cap behind the `could not resize shared memory segment`
failures operators have seen on `/search/age-fused` and `mempalace_walk_palace`.

The two code-side fixes in mempalace#335 (auto edge-endpoint indexes in
`backfill_age`, binding the anonymous `-[r:RELATION]->()` target to `:Entity`)
shrink the working set enough that the common single-entity walk no longer
spills — but a large multi-entity fan-out, or a graph-walk before the indexes
are installed, can still need more than 64 MB. Raising `/dev/shm` removes the
hard wall regardless.

## Required value

Start `mempalace-db` with **`--shm-size=256m`** (4× the default; matches the
value already used for the throwaway AGE benches that reproduced the index
speedups). 256 MB is comfortably above the largest hash-join working set
observed on the 6.69M-edge `MENTIONS` table and costs nothing when unused (it is
a cap, not a reservation).

```bash
ssh familiar 'docker run -d --name mempalace-db \
    --shm-size=256m \
    -e POSTGRES_PASSWORD=... \
    -e POSTGRES_DB=... \
    -v /var/lib/mempalace-db:/var/lib/postgresql/data \
    -p 5432:5432 \
    mempalace-db:0.1'   # built from disks/mempalace-db/Dockerfile
```

If the container is managed by a compose file or systemd unit in the `disks`
deploy repo, set the equivalent there instead:

```yaml
# docker-compose (disks repo)
services:
  mempalace-db:
    shm_size: "256m"
```

```ini
# systemd / podman: add to the run args
--shm-size=256m
```

## Verify

```bash
ssh familiar 'docker exec mempalace-db df -h /dev/shm'
# Mounted on /dev/shm should show 256M, not 64M.
```

A one-time recreate is required for `--shm-size` to take effect (it is fixed at
container create). Coordinate a brief `mempalace-db` restart; the data lives on
the `/var/lib/mempalace-db` bind-mount and survives the recreate.

## Cross-reference

- Code fixes that reduce (but don't eliminate) the spill: mempalace#335 items 1
  (`KnowledgeGraphAGE._ensure_edge_endpoint_indexes`, wired into
  `backfill_age.backfill`) and 2 (`:Entity`-bound RELATION targets in
  `searcher._graph_expand_*`).
- The latency profile that surfaced the spill: palace-daemon
  `docs/perf/2026-05-30-hybrid-graph-walk-latency.md` + the operator-online index
  route `POST /backfill-age/indexes`.
