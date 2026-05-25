# `mempalace.split_mega_files`

Source: [`mempalace/split_mega_files.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/split_mega_files.py)

split_mega_files.py — Split concatenated transcript files into per-session files
=================================================================================

Scans a directory for .txt files that contain multiple Claude Code sessions
(identified by "Claude Code v" headers). Splits each into individual files
named with: date, time, people detected, and subject from first prompt.

Distinguishes true session starts from mid-session context restores
(which show "Ctrl+E to show X previous messages").

Output files are written to --output-dir (default: same dir as source).
Original files are renamed with .mega_backup extension (not deleted).

Usage:
    python3 split_mega_files.py                          # scan ~/Desktop/transcripts
    python3 split_mega_files.py --source ~/Desktop/transcripts  # explicit source
    python3 split_mega_files.py --dry-run                # show what would happen
    python3 split_mega_files.py --min-sessions 2         # only files with 2+ sessions

By: Ben, 2026-03-30

## Functions

### `is_true_session_start`

```python
def is_true_session_start(lines, idx)
```

True session start: 'Claude Code v' header NOT followed by 'Ctrl+E'/'previous messages'
within the next 6 lines (those are context restores, not new sessions).

### `find_session_boundaries`

```python
def find_session_boundaries(lines)
```

Return list of line indices where true new sessions begin.

### `extract_timestamp`

```python
def extract_timestamp(lines)
```

Find the first timestamp line: ⏺ H:MM AM/PM Weekday, Month DD, YYYY
Returns (datetime_str, iso_str) or (None, None).

### `extract_people`

```python
def extract_people(lines)
```

Detect people mentioned as speakers or by name in first 100 lines.
Returns sorted list of detected names.

### `extract_subject`

```python
def extract_subject(lines)
```

Find the first meaningful user prompt (> line that isn't a shell command).
Returns cleaned, filename-safe subject string.

### `split_file`

```python
def split_file(filepath, output_dir, dry_run = False)
```

Split a single mega-file into per-session files.
Returns list of output paths written (or would be written if dry_run).

### `main`

```python
def main()
```
