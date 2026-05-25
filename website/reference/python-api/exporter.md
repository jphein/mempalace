# `mempalace.exporter`

Source: [`mempalace/exporter.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/exporter.py)

exporter.py — Export the palace as a browsable folder of markdown files.

Produces:
  output_dir/
    index.md              — table of contents
    wing_name/
      room_name.md        — one file per room, drawers as sections

Streams drawers in paginated batches so memory usage stays bounded
regardless of palace size.

## Functions

### `export_palace`

```python
def export_palace(palace_path: str, output_dir: str, format: str = 'markdown') -> dict
```

Export all palace drawers as markdown files organized by wing/room.

Streams drawers in batches of 1000 and writes each wing/room file
incrementally, keeping memory usage proportional to batch size rather
than total palace size.

Args:
    palace_path: Path to the ChromaDB palace directory.
    output_dir: Where to write the exported markdown tree.
    format: Output format (currently only "markdown").

Returns:
    Stats dict: &#123;"wings": N, "rooms": N, "drawers": N}
