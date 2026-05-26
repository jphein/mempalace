# PG16 → PG17 major upgrade plan + AGE 1.7.0 deferral

**Author:** dream-team / Nebula
**Date:** 2026-05-26
**Issue:** [#212](https://github.com/techempower-org/mempalace/issues/212)
**Status:** Plan (planning only — do not execute against the canonical
palace before review)
**Scope:** `mempalace-db` container on `familiar.jphe.in` (bind-mount
`/var/lib/mempalace-db` → `/var/lib/postgresql/data`, image
`mempalace-db:0.1` built from
[`disks/mempalace-db/Dockerfile`](../../../disks/mempalace-db/Dockerfile)
on the host).

---

## TL;DR

- **Target Postgres major: 17.** Move PG16 → PG17 in one window.
- **AGE stays at 1.6.0.** `apache/age:release_PG17_1.6.0` exists and is
  the same AGE version we run today, so the database-major bump is
  isolated from an extension-major bump.
- **AGE 1.6 → 1.7 is a separate, later upgrade** that must also cross
  to PG18 (1.7.0 only ships for PG18). Planning that step is out of
  scope for issue #212 — it is queued behind PG17 bake-in and the
  AGE 1.7.0 index-build rehearsal.
- **pgvector stays on PGDG apt 0.8.2-1.pgdg13+1** (same package, just
  the PG17 build).
- **Approach: parallel container on port 5434**, dump-and-restore,
  side-by-side smoke tests, then a five-line DSN swap to cut over the
  daemon.

The trade-off in one paragraph: PG18 + AGE 1.7.0 would compound four
risk surfaces (PG18's checksum default + MD5 deprecation + VACUUM
inheritance default + AGE 1.7's RLS / CSV-loader / `age_load`
behavior changes) in a single window. Splitting it lets root-cause be
clean if anything breaks. PG17 alone gives us the immediate
performance wins we care about (B-tree `IN`-list scans on
`mempalace_kg_extraction_queue`, VACUUM overhaul on
`mempalace_drawers`, parallelizable BRIN builds) without forcing the
extension upgrade in the same window.

---

## Current state (verified 2026-05-26)

Read directly from the live `mempalace-db` container:

| Component | Version | Source |
|---|---|---|
| PostgreSQL | 16.10 (Debian 16.10-1.pgdg13+1) | `SELECT version()` |
| Apache AGE | 1.6.0 | `pg_extension` |
| pgvector | 0.8.2 | `pg_extension` |
| Base image | `apache/age:release_PG16_1.6.0` | [`disks/mempalace-db/Dockerfile`](../../../disks/mempalace-db/Dockerfile) |
| pgvector apt | `postgresql-16-pgvector` 0.8.2-1.pgdg13+1 | Dockerfile |
| Database name | `mempalace_2026_05_13` | container `\l` |
| Database size | 3.6 GB | `pg_database_size` |
| `mempalace_drawers` size | 1365 MB | `\dt+ public.*` |
| `mempalace_kg_backfill_state` | 36 MB | `\dt+ public.*` |
| `mempalace_kg_extraction_queue` | 61 MB | `\dt+ public.*` |
| AGE graph entities (any label) | 516,351 | `/stats` |
| AGE triples (current facts) | 535,918 | `/stats` |
| Drawers | 370,808 | `/status/fast` |
| Container data dir on host | `/var/lib/mempalace-db` | docker inspect mounts |
| Host disk free | 40 GB on `/dev/nvme0n1p2` (83% used) | `ssh familiar 'df -h /var/lib/docker'` |
| AGE backfill state | 100% checkpointed (26 in-flight) | `/backfill-age/status` |

The `/var/lib/docker` partition is **83% used with 40 GB free** — the
3.6 GB database fits a side-by-side container twice over with headroom,
but this is a constraint the operator must check again right before
the window (see Pre-flight §1).

---

## Decision: PG17 over PG18

### Why PG17 wins for this window

| Dimension | PG17 + AGE 1.6 | PG18 + AGE 1.7 |
|---|---|---|
| AGE upgrade required? | No (same 1.6.0) | Yes (1.6 → 1.7 index build) |
| pgvector apt path | `postgresql-17-pgvector` (PGDG, 0.8.2-1.pgdg13+1) | `postgresql-18-pgvector` (PGDG, 0.8.2-1.pgdg13+1) |
| Postgres breaking changes vs PG16 | Modest (collation default change, plan-cache invalidation rules) | Many: data-checksum default on, MD5 deprecation warnings, VACUUM/ANALYZE inheritance default, FTS uses cluster default collation provider, `pg_stat_wal` columns removed |
| Headline perf wins for our workload | VACUUM overhaul (removes 1 GB cap on `maintenance_work_mem`, more compact WAL); B-tree `IN`-list multi-value scan; parallel BRIN builds; correlated `IN` → join | All of PG17's wins plus asynchronous I/O (`io_method`), B-tree skip scans, parallel GIN builds, automatic self-join elimination |
| Reader-model relevance | VACUUM speedup matters most on the high-churn `mempalace_kg_extraction_queue` table | AIO benefits sequential scans; pgvector HNSW is already in-memory so AIO has less effect on us |
| AGE 1.7 surfaces we'd inherit | None | RLS-driven permission-check change (#2309), libcsv → pg COPY for `age_load` (#2310), `age_load` restrictions (#2274), index build on every existing label during upgrade script (the issue body and the AGE 1.7.0 release notes both flag the script as slow on graphs with many vertex labels) |
| Risk surface count in one window | 1 (PG major) | 4 (PG major + checksum opt-in/out + MD5 audit + AGE major) |

PG17's perf wins on our workload — the VACUUM overhaul on
`mempalace_kg_extraction_queue` and the B-tree `IN`-list optimization
on hot lookup paths — are the two changes we actually benefit from
this quarter. The PG18 wins (AIO, skip scans) are real but skew toward
sequential-scan-heavy workloads and we are not currently I/O-bound on
the database side; the bottleneck per fork issue #70 is reader-model
throughput, not database read latency.

### Why not PG18 in the same window

Cost-of-rollback grows roughly multiplicatively with risk-surface
count. PG17 lets us:

1. **Pin AGE at 1.6.0** so a regression in graph behavior is provably
   not the extension (we're running 1.6.0 already and we know it
   works after the [PR #228](https://github.com/techempower-org/mempalace/pull/228)
   `statement_timeout` fix).
2. **Defer the checksum decision.** PG18's `initdb` defaults to
   data checksums on. We currently have them off — `pg_upgrade`
   would fail without `--no-data-checksums` matching. Worth a
   deliberate decision later, not bundled into a forced one now.
3. **Defer the MD5 → SCRAM audit.** PG18 emits warnings on every
   `CREATE ROLE` / `ALTER ROLE` with MD5 passwords. The `palace`
   role likely needs SCRAM rotation; we want to do that in a
   focused window.
4. **Bake in PG17** for ~2 weeks before crossing the AGE major and
   the second Postgres major in one shot. If we land PG18 + AGE 1.7
   together later, both upgrades are isolated from each other by a
   long window of PG17 + AGE 1.6 production traffic.

---

## Pre-flight checklist (T-24h)

The operator must complete every item on this list before any
destructive step. If any item fails, abort and reschedule.

### 1. Capacity

```bash
# Host disk for the new container's data dir
ssh familiar 'df -h /var/lib/mempalace-db /var/lib/docker'

# Database size (must comfortably fit twice + WAL + extraction queue growth)
ssh familiar 'docker exec mempalace-db psql -U palace -d mempalace_2026_05_13 \
    -c "SELECT pg_size_pretty(pg_database_size(current_database()));"'
```

**Hard floor:** 3× database size free on `/var/lib/docker`
(currently 3.6 GB DB → need ≥11 GB free). Today: 40 GB free, passes.

### 2. Backup

```bash
# Custom-format dump for restore flexibility (-Fc), single file
ssh familiar 'docker exec mempalace-db bash -c \
    "pg_dump -U palace -Fc -d mempalace_2026_05_13 \
    -f /tmp/mempalace_pre_pg17_$(date +%Y%m%d).dump"'

# Pull off the container, off the host, onto disks
ssh familiar 'docker cp mempalace-db:/tmp/mempalace_pre_pg17_$(date +%Y%m%d).dump \
    /home/jp/backups/'
rsync -avh familiar:/home/jp/backups/mempalace_pre_pg17_*.dump \
    disks:/mnt/raid/backups/mempalace/
```

**Verify:** the dump size is non-trivial (≥1.5 GB for a 3.6 GB DB
with toast compression) and `pg_restore --list` reads it without
error.

```bash
ssh disks 'pg_restore --list /mnt/raid/backups/mempalace/mempalace_pre_pg17_*.dump | head -20'
```

### 3. Filesystem-level snapshot (belt-and-suspenders)

The dump above is the canonical recovery artifact. The bind-mount
copy is the fast-rollback artifact:

```bash
# Stop the daemon's *connection pool*, not the daemon process, by
# pausing the watcher's writes for the snapshot window
ssh familiar 'sudo systemctl stop palace-daemon'

# cp -al → instant hardlink snapshot (same filesystem, atomic)
ssh familiar 'sudo cp -al /var/lib/mempalace-db \
    /var/lib/mempalace-db.pre-pg17-$(date +%Y%m%d)'

ssh familiar 'sudo systemctl start palace-daemon'
```

`cp -al` is O(N) hardlinks, finishes in seconds even on a 4 GB tree,
and gives us a near-instant rollback target if dump-and-restore fails.

### 4. Halt the extraction queue

The KG extraction queue (`mempalace_kg_extraction_queue`) is being
drained by two reader pools (familiar 8w, katana 24w) at ~106
drawers/min. Hold it during the window:

```bash
# Verify it's paused — should show 0 in-flight workers
ssh familiar 'sudo systemctl stop kg-extract@familiar.service'
ssh katana 'sudo systemctl stop kg-extract@katana.service'

curl -sf -H "X-API-Key: $PALACE_API_KEY" \
    http://familiar:8085/backfill-age/status | jq .
```

### 5. Verify upstream test state on a recent commit

```bash
cd ~/Projects/memorypalace
git log -1 --oneline main
./.venv/bin/python -m pytest tests/ -x -q --ignore=tests/benchmarks
```

A clean test run on `main` is the implicit "current shape works"
baseline. If `main` is red, fix that first; do not stack a major
upgrade on top of an unstable baseline.

### 6. Coordinate the window

Notify any downstream consumers of the daemon (Outline integration,
realmwatch search, MCP clients on katana/aurora/morpheus). Estimated
window: **30–45 min** total, of which **~10 min is observed
unavailability** (steps 4-5 in §Cut-over below).

---

## Step-by-step command sequence

### Phase A — build the new image

On katana (where the Dockerfile lives at
`~/Projects/disks/mempalace-db/Dockerfile`):

```bash
cd ~/Projects/disks/mempalace-db

# Bump the base image to PG17, keep AGE at 1.6.0
git switch -c chore/pg17-upgrade
sed -i 's|apache/age:release_PG16_1.6.0|apache/age:release_PG17_1.6.0|' Dockerfile
sed -i 's|postgresql-16-pgvector|postgresql-17-pgvector|' Dockerfile

# Bump the version tag to make the cutover visible in docker ps
docker build -t mempalace-db:0.2-pg17 .

# Verify the image
docker run --rm mempalace-db:0.2-pg17 \
    bash -c "psql --version && dpkg -l | grep -E 'postgresql-17|pgvector'"
```

Push the Dockerfile change to `disks/mempalace-db/` (the repo on
katana that produces this image) so the build is reproducible.

Side-load the image onto familiar:

```bash
docker save mempalace-db:0.2-pg17 | ssh familiar 'sudo docker load'
ssh familiar 'docker images mempalace-db'
```

### Phase B — stand up the new container on port 5434

```bash
ssh familiar 'sudo mkdir -p /var/lib/mempalace-db-pg17 && \
    sudo chown 999:999 /var/lib/mempalace-db-pg17'  # postgres user inside container

ssh familiar 'docker run -d \
    --name mempalace-db-pg17 \
    --restart unless-stopped \
    -p 5434:5432 \
    -e POSTGRES_USER=palace \
    -e POSTGRES_PASSWORD="$(bw get password mempalace-db-postgres)" \
    -e POSTGRES_DB=mempalace_test \
    -v /var/lib/mempalace-db-pg17:/var/lib/postgresql/data \
    -v /opt/mempalace-db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro \
    mempalace-db:0.2-pg17'

# Wait for ready
ssh familiar 'until docker exec mempalace-db-pg17 pg_isready -U palace; do sleep 1; done'
```

The password comes from the Vaultwarden item `mempalace-db-postgres`
(matches whatever the live PG16 container is using — never re-keyed
during a PG-major bump).

### Phase C — restore the dump into the new container

```bash
# Copy dump into the new container
ssh familiar 'docker cp /home/jp/backups/mempalace_pre_pg17_$(date +%Y%m%d).dump \
    mempalace-db-pg17:/tmp/dump.bin'

# Create the target DB
ssh familiar 'docker exec mempalace-db-pg17 psql -U palace -d mempalace_test \
    -c "CREATE DATABASE mempalace_2026_05_13 OWNER palace;"'

# Restore. -j 4 = 4 parallel restore workers. AGE objects must restore
# *after* the AGE extension is created — pg_dump --create would handle
# this if we'd dumped with --create, but we dumped a single DB so we
# create+install extensions before restore.
ssh familiar 'docker exec mempalace-db-pg17 psql -U palace -d mempalace_2026_05_13 \
    -c "CREATE EXTENSION IF NOT EXISTS age; LOAD '\''age'\'';
        CREATE EXTENSION IF NOT EXISTS vector;"'

ssh familiar 'docker exec mempalace-db-pg17 pg_restore \
    -U palace -d mempalace_2026_05_13 \
    --no-owner --no-privileges \
    -j 4 \
    /tmp/dump.bin 2>&1 | tee /tmp/restore.log'
```

Expected timing: 3-5 min for 3.6 GB on local NVMe with `-j 4`. AGE
will emit warnings during restore for `ag_catalog` system objects;
these are normal as long as the final smoke tests pass.

### Phase D — run the smoke-test matrix (still parallel, no cutover yet)

See §Smoke-test matrix below. Block on a clean pass for **every**
test before proceeding. If any test fails:
- Stop. Do not cut over.
- Capture diagnostics: `docker logs mempalace-db-pg17`, the restore
  log, and the failing test's exact command + output.
- Roll forward only after the failure is understood.

### Phase E — cut over

```bash
# 1. Stop the daemon (final flush completes within 5s; daemon supports
#    graceful shutdown per palace-daemon CHANGELOG since 1.7)
ssh familiar 'sudo systemctl stop palace-daemon'

# 2. Stop the old container (do NOT remove yet)
ssh familiar 'sudo docker stop mempalace-db'

# 3. Edit the daemon DSN: point to the new container's port
#    /etc/palace-daemon/env — change PG_PORT from 5432 to 5434
#    OR rename containers so the daemon's existing DSN works
#
#    Pragmatic option: stop the new container, rename it to mempalace-db,
#    restart on port 5432. This makes the DSN swap zero-config.
ssh familiar 'sudo docker stop mempalace-db-pg17 && \
    sudo docker rename mempalace-db mempalace-db-pg16-retired && \
    sudo docker rename mempalace-db-pg17 mempalace-db && \
    sudo docker run -d ... mempalace-db'   # see Phase B but with -p 5432:5432

# 4. Start the daemon
ssh familiar 'sudo systemctl start palace-daemon'

# 5. Verify
sleep 5
curl -sf -H "X-API-Key: $PALACE_API_KEY" http://familiar:8085/health
curl -sf -H "X-API-Key: $PALACE_API_KEY" http://familiar:8085/status/fast | jq '.total_drawers'
```

Expected: `total_drawers` equals the pre-cutover value
(370,808 ± any drawers added during the window — should be zero if
the extraction queue was halted in Pre-flight §4).

### Phase F — restart KG extraction

```bash
ssh familiar 'sudo systemctl start kg-extract@familiar.service'
ssh katana  'sudo systemctl start kg-extract@katana.service'

# Watch throughput recover for ~5 min
ssh familiar 'journalctl -u kg-extract@familiar.service -f' &
sleep 300
```

### Phase G — bake-in (24-48 hours)

- Keep `mempalace-db-pg16-retired` container around (stopped) for
  fast rollback.
- Keep the pre-PG17 dump on disks for at least 7 days.
- Watch `/var/log` and `journalctl -u palace-daemon` for unexpected
  errors.
- Re-run the smoke-test matrix at T+24h before reclaiming the old
  volume.

### Phase H — reclaim (T+7 days)

```bash
ssh familiar 'sudo docker rm mempalace-db-pg16-retired'
ssh familiar 'sudo rm -rf /var/lib/mempalace-db.pre-pg17-*'
# Keep the dump file on disks indefinitely — they're cheap.
```

---

## Smoke-test matrix

Run every test on the new container BEFORE cutover (Phase D), then
re-run them all post-cutover. Block on a clean pass.

| # | Test | Command | Pass condition |
|---|---|---|---|
| 1 | Postgres version | `psql -c "SELECT version();"` | Returns `PostgreSQL 17.x` |
| 2 | Extensions loaded | `psql -c "SELECT extname, extversion FROM pg_extension;"` | `age=1.6.0`, `vector=0.8.2`, `plpgsql=1.0` |
| 3 | AGE load | `psql -c "LOAD 'age';"` | No error |
| 4 | AGE cypher read | `psql -c "LOAD 'age'; SET search_path = ag_catalog, '\$user', public; SELECT * FROM cypher('mempalace_kg', \$\$MATCH (e:Entity) RETURN count(e)\$\$) AS (n agtype);"` | Returns 516,351 (or close — within in-flight tolerance) |
| 5 | AGE cypher write + rollback | Same as above with `BEGIN; CREATE (e:Entity {name: 'smoke-test'}); ROLLBACK;` | No error, count unchanged after |
| 6 | pgvector distance | `psql -c "SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector;"` | Returns `5.196152422706632` |
| 7 | pgvector index exists | `psql -c "\d+ mempalace_drawers"` | Shows the HNSW or IVFFlat index on the embedding column |
| 8 | pgvector ANN search | `psql -c "SELECT id FROM mempalace_drawers ORDER BY embedding <-> (SELECT embedding FROM mempalace_drawers LIMIT 1) LIMIT 5;"` | Returns 5 rows in <100 ms |
| 9 | Drawer count parity | `psql -c "SELECT count(*) FROM mempalace_drawers;"` | Equals pre-dump count: 370,808 |
| 10 | KG triple count parity | `psql -c "SELECT count(*) FROM ag_catalog.cypher('mempalace_kg', \$\$MATCH ()-[r]->() RETURN count(r)\$\$) AS (n agtype);"` | Equals pre-dump triple count: 535,918 |
| 11 | Daemon read path | `curl -sf -H "X-API-Key: $KEY" http://familiar:8085/status/fast \| jq .total_drawers` | Returns 370,808 |
| 12 | Daemon search path (fast) | `curl -sf -H "X-API-Key: $KEY" "http://familiar:8085/search/fast?q=mempalace&limit=3"` | Returns 3 results, no error |
| 13 | Daemon search path (AGE-fused) | `curl -sf -H "X-API-Key: $KEY" "http://familiar:8085/search/age-fused?q=mempalace&limit=3"` | Returns 3 results, AGE entity expansion populated |
| 14 | Statement timeout still applied | `psql -c "LOAD 'age'; BEGIN; SET LOCAL statement_timeout = '3s'; SELECT pg_sleep(5);"` | Errors with `statement timeout` after ~3s |
| 15 | KG extraction queue resumes | After daemon + workers restart, watch `/backfill-age/status` for 5 min | `in_progress: true`, `unprocessed_drawers` decreasing |

Tests 4, 5, 14 cover the same surface that
[PR #228](https://github.com/techempower-org/mempalace/pull/228) and
[PR #229](https://github.com/techempower-org/mempalace/pull/229)
patched — both must still apply cleanly post-upgrade.

---

## Rollback procedure

**Time-bounded:** if the cutover (Phase E) fails its smoke tests OR
the bake-in (Phase G) surfaces an unrecoverable regression in the
first 4 hours, roll back. Beyond 4 hours, accept the regression and
forward-fix instead — too many writes will have landed on PG17 to
discard.

### Within Phase E (haven't started daemon on PG17 yet)

Trivial — both containers are still on disk:

```bash
ssh familiar 'sudo docker stop mempalace-db && \
    sudo docker rename mempalace-db mempalace-db-pg17-failed && \
    sudo docker rename mempalace-db-pg16-retired mempalace-db && \
    sudo docker start mempalace-db && \
    sudo systemctl start palace-daemon'
```

Verify: `curl http://familiar:8085/status/fast` returns the pre-window
drawer count.

### Within 4 hours post-cutover (PG17 has accepted writes)

Two options, in order of preference:

**Option 1 — replay deltas onto PG16.** If the regression is in a
single subsystem (say, the daemon's search path), the writes during
those hours have only added to `mempalace_drawers` and
`mempalace_kg_extraction_queue`. Diff them out of PG17 and apply to
PG16:

```bash
# Identify drawers added since the dump
ssh familiar 'docker exec mempalace-db-pg17 psql -U palace -d mempalace_2026_05_13 \
    -c "COPY (SELECT * FROM mempalace_drawers WHERE created_at > '\''<dump-time>'\'') \
        TO '\''/tmp/delta_drawers.csv'\'' WITH CSV;"'

# Apply to the retired PG16 container after restart
ssh familiar 'sudo docker start mempalace-db-pg16-retired
              docker cp /tmp/delta_drawers.csv mempalace-db-pg16-retired:/tmp/
              docker exec mempalace-db-pg16-retired psql -U palace -d mempalace_2026_05_13 \
                  -c "COPY mempalace_drawers FROM '\''/tmp/delta_drawers.csv'\'' WITH CSV;"'

# Swap container names, restart daemon
```

**Option 2 — accept the window's writes as lost.** Restore from the
filesystem snapshot taken in Pre-flight §3:

```bash
ssh familiar 'sudo systemctl stop palace-daemon
              sudo docker stop mempalace-db
              sudo rm -rf /var/lib/mempalace-db.broken && \
              sudo mv /var/lib/mempalace-db /var/lib/mempalace-db.broken && \
              sudo cp -al /var/lib/mempalace-db.pre-pg17-* /var/lib/mempalace-db
              sudo docker rename mempalace-db mempalace-db-pg17-failed
              sudo docker rename mempalace-db-pg16-retired mempalace-db
              sudo docker start mempalace-db
              sudo systemctl start palace-daemon'
```

You lose every drawer + KG fact added during the bake-in window.
Acceptable if the alternative is a corrupt PG17 database.

### Beyond 4 hours

Forward-fix. Do not roll back. The cost of replaying or discarding
hours of writes exceeds the cost of patching the regression in place.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **pg_dump / pg_restore corruption** of AGE catalog tables | Medium | High | The dry-run Phase D smoke test #10 (triple count parity) catches this before cutover. If hit, fall back to `pg_dumpall` + `psql` text restore, which sequences DDL more strictly. |
| **AGE extension version mismatch** between dump (1.6.0) and restore target (1.6.0) | Low | High | Pinning the base image to `release_PG17_1.6.0` ensures byte-identical AGE versions across the boundary. Confirmed via Smoke #2. |
| **pgvector index corruption** during restore (HNSW segment incompatibility across PG majors) | Medium | High | HNSW indexes on the new cluster get rebuilt from rows during restore — they are not bit-for-bit copied. `pg_dump` ships the row data; the new cluster's pgvector builds new indexes. If this regresses search quality, re-`REINDEX CONCURRENTLY` post-restore. |
| **Daemon DSN mismatch** post-cutover (forgot to swap port/container name) | Medium | Medium | Phase E §3 uses container rename instead of DSN edit — fewer places for the operator to slip. Smoke #11 catches a DSN miss in <30 s. |
| **PG17 plan regression** on a hot search query (planner gets smarter, picks worse plan) | Low | Medium | Re-run `EXPLAIN ANALYZE` for top 10 search shapes on PG17 before cutover. If a regression appears, capture the plan and either tune statistics (`ALTER TABLE ... ALTER COLUMN ... SET STATISTICS`) or extension settings; rollback only if no tunable fix in 30 min. |
| **Disk exhaustion** mid-restore (host disk fills) | Low | Critical | Pre-flight §1 gates on 3× DB size free. Today: 40 GB free, 3.6 GB DB → 11× margin. |
| **Backfill workers reconnect to wrong cluster** post-cutover | Low | Medium | Workers are systemd-managed and read DSN from the daemon's `/embed` and `/silent-save` endpoints, not the PG cluster directly. Phase F restarts them cleanly after the daemon is healthy. |
| **AGE `LOAD 'age'`** fails post-restore due to stale `ag_catalog` references | Low | High | Smoke #3 + #4 catch this before cutover. Fix: `DROP EXTENSION age CASCADE; CREATE EXTENSION age; LOAD 'age';` and re-restore the `mempalace_kg` schema only. |
| **Collation mismatch warning** during restore (PG17's libc collation tweaks vs PG16's) | Medium | Low | PG17 changes `glibc` collation handling in some locales — restoring with `--no-owner` and the same `LC_COLLATE`/`LC_CTYPE` as the dump (`en_US.UTF-8`, confirmed live) should be safe. If `REINDEX` is suggested, run it during Phase G bake-in, not during the cutover window. |
| **Daemon graceful shutdown** doesn't drain in-flight writes | Low | Medium | The daemon's graceful-stop is well-tested in `palace-daemon`. Pre-flight §4 already halts the largest writer (extraction queue) — the only remaining writers are interactive MCP saves, which the daemon flushes within 5 s. |
| **Smoke test regression** (one or more tests fail post-cutover) | Medium | Variable | Block on Phase D's pre-cutover smoke pass. A failure there triggers abort without touching the daemon. A failure post-cutover triggers rollback per §Rollback. |

---

## Open decision points

These are deliberately deferred to the window itself or to a
follow-up. Surfacing them here so they don't surprise the operator:

1. **pgvector source: stay on PGDG apt 0.8.2 or build 0.8.x master /
   0.9.x from source?**
   - PGDG apt for `postgresql-17-pgvector` ships 0.8.2-1.pgdg13+1 —
     same point version we run today, just rebuilt for PG17.
   - 0.9.x is not tagged in the pgvector repo as of this writing
     (latest tag `v0.8.2`, 2026-02-25, plus a fix for PG18
     `EXPLAIN` output) — there is no stable 0.9 to chase.
   - **Recommendation: stay on PGDG apt 0.8.2** for this window.
     Build-from-source is an option for a later window if we need
     a specific 0.8.x master fix (e.g., the buffer-overflow fix
     in parallel HNSW build, currently shipped in 0.8.2).
2. **AGE 1.6 → 1.7 timing.** Per the AGE 1.7.0 release notes the
   `age--1.6.0--1.7.0.sql` upgrade script "may take a while to
   complete for large graphs, due to creation of indexes for
   existing labels." On a graph with 516K vertices and 535K edges
   this is non-trivial — a rehearsal on a `pg_dump`-restored
   side-by-side container is the right way to time it before
   committing to a window.
   - **Recommendation: queue as a separate operator doc**, dated
     after PG17 bake-in. Scope: PG17 + AGE 1.6 → PG18 + AGE 1.7
     (the only path forward — AGE 1.7.0 only ships for PG18).
   - That second upgrade compounds the PG18 breaking changes
     (checksum default, MD5 deprecation, VACUUM/ANALYZE inheritance
     default, FTS collation provider) with the AGE 1.7 RLS /
     CSV-loader / `age_load` behavior changes. Plan accordingly.
3. **MD5 → SCRAM password rotation** for the `palace` role. PG18
   will warn (not block) on MD5. Worth doing during the same
   window as PG18 — adds 5 minutes, removes the warning permanently,
   and lets us drop `md5_password_warnings = off` if we'd otherwise
   set it.
4. **Data checksums.** PG18's `initdb` defaults to on. We currently
   have them off. Enabling them requires either `pg_checksums` on
   the offline cluster (single-pass scan, ~3-5 min for 3.6 GB) or
   re-`initdb` + restore. Worth considering as part of the PG18
   window since the restore path is already paid for there.
5. **Whether to swap `disks/mempalace-db/Dockerfile` to a multi-stage
   build** that bakes pgvector into the image instead of
   apt-installing at build time. The current Dockerfile is 4 lines
   and works; a multi-stage build is only worth it if we want to
   pin a specific pgvector master commit. Defer until #1 forces it.

---

## References

- Issue [#212](https://github.com/techempower-org/mempalace/issues/212) — this plan's tracking issue
- [`docs/operators/pgvector-cutover-runbook.md`](./pgvector-cutover-runbook.md) — prior cutover (Chroma → pgvector, 2026-05-13) for reference on operator-driven runbook style
- [PR #228](https://github.com/techempower-org/mempalace/pull/228) — AGE regex drop + statement_timeout fix (smoke test #14 verifies this still applies)
- [PR #229](https://github.com/techempower-org/mempalace/pull/229) — statement_timeout applied in same transaction as cypher()
- Apache AGE Docker tags — `apache/age` on Docker Hub, confirmed
  `release_PG17_1.6.0` and `release_PG18_1.7.0` both published
- PG17 release notes — https://www.postgresql.org/docs/release/17.0/
- PG18 release notes — https://www.postgresql.org/docs/release/18.0/
- pgvector CHANGELOG — https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md (latest tag `v0.8.2`, 2026-02-25)
- PGDG apt — confirmed `postgresql-17-pgvector` 0.8.2-1.pgdg13+1 and `postgresql-18-pgvector` 0.8.2-1.pgdg13+1 both present in `trixie-pgdg`

---

## Out of scope for this plan

- The PG18 + AGE 1.7.0 upgrade (separate operator doc, dated
  post-PG17 bake-in).
- Cross-host palace replication or HA (single-node by design today;
  see [`docs/postgres_backend.md`](../postgres_backend.md)).
- Changing the database name (`mempalace_2026_05_13` is the
  post-pgvector-cutover name and stays unchanged across this
  upgrade).
- pgvector index parameter tuning (`hnsw.m`, `hnsw.ef_construction`)
  — covered by the chunking-and-retrieval ablation track, not by
  the database-major upgrade.
