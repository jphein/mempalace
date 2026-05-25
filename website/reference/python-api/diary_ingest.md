# `mempalace.diary_ingest`

Source: [`mempalace/diary_ingest.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/diary_ingest.py)

diary_ingest.py — Ingest daily summary files into the palace.

Architecture:
- ONE drawer per (wing, day) — full verbatim content, upserted as the day grows.
- Closets pack topics up to CLOSET_CHAR_LIMIT, never split mid-topic.
- A re-ingest fully purges the prior day's closets before rebuilding so a
  shorter day never leaves orphans behind.
- Only new entries are processed by default (tracks entry count in a state
  file under ``~/.mempalace/state/`` — never inside the user's diary dir).
- Per-file ``mine_lock`` so concurrent ingest from two terminals can't race.
- Entities extracted and stamped on metadata for filterable search.

Usage:
    python -m mempalace.diary_ingest --dir ~/daily_summaries --palace ~/.mempalace/palace
    python -m mempalace.diary_ingest --dir ~/daily_summaries --palace ~/.mempalace/palace --force

## Functions

### `ingest_diaries`

```python
def ingest_diaries(diary_dir, palace_path, wing = 'diary', force = False)
```

Ingest daily summary files into the palace.

Each date file gets ONE drawer keyed by ``(wing, date)`` and closets that
pack topics atomically up to ``CLOSET_CHAR_LIMIT``. ``force=True`` rebuilds
every entry's closets from scratch (purging stale ones); the default
incremental mode only processes entries appended since the last run.
