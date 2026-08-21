# `mempalace.backups`

Source: [`mempalace/backups.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/backups.py)

Writing and pruning palace backups.

``mempalace migrate`` and ``mempalace repair max-seq-id`` each write a fresh,
timestamped backup every time they run and historically never deleted the old
ones. On a machine that mines or repairs on a schedule those full-size copies
accumulate silently — a real palace was found with hundreds of gigabytes of
backups sitting beside only a few hundred megabytes of live data, nearly
filling the disk. This module prunes the backup set down to a bounded count
after each new backup is written.

The retention count comes from ``MempalaceConfig.max_backups`` (default 10).

``copy_palace_dir`` is the other half: the whole-directory copy that
``mempalace repair`` in its default mode and ``mempalace migrate`` take before
they overwrite a live palace. The other backup paths copy a single file and do
not use it.

## Functions

### `copy_palace_dir`

```python
def copy_palace_dir(src, dst, *, symlinks = False, log = None)
```

Copy a palace directory to ``dst``, skipping entries no copy can carry.

``shutil.copytree`` finishes the copy but raises ``shutil.Error`` at the
end when a directory entry is not something it knows how to duplicate: a
Unix domain socket, or a named pipe. The caller has to treat that as a
failed backup, so the command died before the rebuild the backup was
guarding (#2207). Such entries are runtime artifacts of whatever process
created them and never hold palace data, so a backup without them is
still a complete backup of the palace.

Args:
    src: Palace directory to copy.
    dst: Destination path. Must not already exist.
    symlinks: Passed to ``shutil.copytree``. ``True`` recreates symlinks
        as symlinks, ``False`` copies what they point at.
    log: Optional callable (e.g. ``print``) for human-readable progress.

Returns:
    The list of ``(path, reason)`` pairs that were skipped: sorted within
    each directory, in the order the copy visited the directories. Like
    ``prune_backups``, this both returns what it did and logs it.

Every other copy failure still raises out of ``shutil.copytree``. A caller
about to overwrite the live palace must still stop when its safety copy
did not come out whole, so this narrows what the copy attempts rather
than swallowing what it reports.

``shutil.copytree`` hands the callback names rather than the ``os.scandir``
entries it already holds, so classifying costs one extra ``os.lstat`` per
directory entry, and it classifies a whole directory before copying any of
it. An entry whose type changes inside that window is handled by the
earlier reading, which cuts both ways: one that became a socket still
aborts the copy, and one that was a socket and became a regular file is
skipped with its contents. Nothing in MemPalace replaces an entry that
way, and the skipped name is printed either way.

### `prune_backups`

```python
def prune_backups(pattern, max_backups, *, log = None)
```

Delete the oldest backups matching ``pattern`` so at most ``max_backups`` remain.

Args:
    pattern: A glob pattern matching the backup paths (files or
        directories). The caller is responsible for ``glob.escape``-ing
        any literal, non-wildcard portion that can contain glob
        metacharacters — palace paths sometimes do (e.g. a ``[``).
    max_backups: Number of most-recent backups to keep. ``None`` or any
        value ``<= 0`` disables pruning and returns immediately, so a
        backup set is never touched when the user has opted out.
    log: Optional callable (e.g. ``print``) for human-readable progress.

Returns:
    The list of paths that were successfully removed.

Recency is determined by filesystem mtime rather than by parsing the
timestamp out of the name, so it stays correct even when two backup
producers use different timestamp formats. Deletion failures are logged
and skipped: pruning is best-effort cleanup and must never abort the
migrate/repair operation that just completed successfully.
