# `mempalace.replica`

Source: [`mempalace/replica.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/replica.py)

replica.py — Per-palace replica identity (RFC 004 transport seam / provenance)

Every palace replica has one stable ``ReplicaId``, stamped as
``origin_replica`` into every op it authors. For the step-0 pilot the id is
minted locally on first use and persisted in ``replica.json`` inside the
palace directory. Existing 12-hex pilot ids remain valid, but new ids use
128 bits of entropy. When the transport layer lands (MeshGuard), the mesh
Ed25519 identity supersedes it via an alias op — RFC 004 Appendix A.4:
"a rename is just another provenance fact."

The id never syncs and never rotates silently; it names the seat (this
machine's copy of the palace), not the model or the agent.

## Functions

### `get_replica_id`

```python
def get_replica_id(palace_path: str) -> str
```

Return this palace's stable replica id, minting it on first use.

The file is written atomically (tmp + rename) so a crash mid-mint can
never leave a half-written identity; a corrupt or foreign-shaped file
fails loudly rather than silently minting a second identity — two ids
for one replica would fork its op-log provenance.
