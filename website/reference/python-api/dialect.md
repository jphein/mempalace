# `mempalace.dialect`

Source: [`mempalace/dialect.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/dialect.py)

AAAK Dialect -- Structured Symbolic Summary Format
====================================================

A lossy summarization format that extracts entities, topics, key sentences,
emotions, and flags from plain text into a compact structured representation.
Any LLM reads it natively — no decoder required.

Works with: Claude, ChatGPT, Gemini, Llama, Mistral -- any model that reads text.

NOTE: AAAK is NOT lossless compression. The original text cannot be reconstructed
from AAAK output. It is a structured summary layer (closets) that points to the
original verbatim content (drawers). The 96.6% benchmark score is from raw mode,
not AAAK mode.

Adapted for mempalace: works standalone on plain text and ChromaDB drawers.
No dependency on palace.py or layers.py.

FORMAT:
  Header:   FILE_NUM|PRIMARY_ENTITY|DATE|TITLE
  Zettel:   ZID:ENTITIES|topic_keywords|"key_quote"|WEIGHT|EMOTIONS|FLAGS
  Tunnel:   T:ZID<->ZID|label
  Arc:      ARC:emotion->emotion->emotion

EMOTION CODES (universal):
  vul=vulnerability, joy=joy, fear=fear, trust=trust
  grief=grief, wonder=wonder, rage=rage, love=love
  hope=hope, despair=despair, peace=peace, humor=humor
  tender=tenderness, raw=raw_honesty, doubt=self_doubt
  relief=relief, anx=anxiety, exhaust=exhaustion
  convict=conviction, passion=quiet_passion

FLAGS:
  ORIGIN = origin moment (birth of something)
  CORE = core belief or identity pillar
  SENSITIVE = handle with absolute care
  PIVOT = emotional turning point
  GENESIS = led directly to something existing
  DECISION = explicit decision or choice
  TECHNICAL = technical architecture or implementation detail

## Classes

### `class Dialect`

AAAK Dialect encoder -- works on plain text or structured zettel data.

Usage:
    # Basic: compress any text
    dialect = Dialect()
    compressed = dialect.compress("We decided to use GraphQL instead of REST...")

    # With entity mappings
    dialect = Dialect(entities=&#123;"Alice": "ALC", "Bob": "BOB"})

    # From config file
    dialect = Dialect.from_config("entities.json")

    # Compress zettel JSON (original format)
    compressed = dialect.compress_file("zettels/file_001.json")

    # Generate Layer 1 wake-up file
    dialect.generate_layer1("zettels/", output="LAYER1.aaak")

#### `__init__`

```python
def __init__(self, entities: Dict[str, str] = None, skip_names: List[str] = None, lang: str = None)
```

Args:
    entities: Mapping of full names -> short codes.
              e.g. &#123;"Alice": "ALC", "Bob": "BOB"}
              If None, entities are auto-coded from first 3 chars.
    skip_names: Names to skip (fictional characters, etc.)
    lang: Language code (e.g. "fr", "ko"). Loads AAAK instruction
          and regex patterns from i18n dictionary.

#### `from_config`

```python
def from_config(cls, config_path: str) -> 'Dialect'
```

Load entity mappings from a JSON config file.

Config format:
&#123;
    "entities": &#123;"Alice": "ALC", "Bob": "BOB"},
    "skip_names": ["Gandalf", "Sherlock"]
}

#### `save_config`

```python
def save_config(self, config_path: str)
```

Save current entity mappings to a JSON config file.

#### `encode_entity`

```python
def encode_entity(self, name: str) -> Optional[str]
```

Convert a person/entity name to its short code.

#### `encode_emotions`

```python
def encode_emotions(self, emotions: List[str]) -> str
```

Convert emotion list to compact codes.

#### `get_flags`

```python
def get_flags(self, zettel: dict) -> str
```

Extract flags from zettel metadata.

#### `compress`

```python
def compress(self, text: str, metadata: dict = None) -> str
```

Summarize plain text into AAAK Dialect format.

Extracts entities, topics, a key sentence, emotions, and flags
from the input text. This is lossy — the original text cannot be
reconstructed from the output.

Args:
    text: Plain text content to summarize
    metadata: Optional dict with keys like 'source_file', 'wing',
              'room', 'date', etc.

Returns:
    AAAK-formatted summary string

#### `extract_key_quote`

```python
def extract_key_quote(self, zettel: dict) -> str
```

Pull the most important quote fragment from zettel content.

#### `encode_zettel`

```python
def encode_zettel(self, zettel: dict) -> str
```

Encode a single zettel into AAAK Dialect.

#### `encode_tunnel`

```python
def encode_tunnel(self, tunnel: dict) -> str
```

Encode a tunnel connection.

#### `encode_file`

```python
def encode_file(self, zettel_json: dict) -> str
```

Encode an entire zettel file into AAAK Dialect.

#### `compress_file`

```python
def compress_file(self, zettel_json_path: str, output_path: str = None) -> str
```

Read a zettel JSON file and compress it to AAAK Dialect.

#### `compress_all`

```python
def compress_all(self, zettel_dir: str, output_path: str = None) -> str
```

Compress ALL zettel files into a single AAAK Dialect file.

#### `generate_layer1`

```python
def generate_layer1(self, zettel_dir: str, output_path: str = None, identity_sections: Dict[str, List[str]] = None, weight_threshold: float = 0.85) -> str
```

Auto-generate a Layer 1 wake-up file from all processed zettel files.

Pulls highest-weight moments (>= threshold) and any with ORIGIN/CORE/GENESIS flags.
Groups them by date into MOMENTS sections.

#### `decode`

```python
def decode(self, dialect_text: str) -> dict
```

Parse an AAAK Dialect string back into a readable summary.

#### `count_tokens`

```python
def count_tokens(text: str) -> int
```

Estimate token count using word-based heuristic (~1.3 tokens per word).

This is an approximation. For accurate counts, use a real tokenizer
like tiktoken. The old len(text)//3 heuristic was wildly inaccurate
and made AAAK compression ratios look much better than reality.

#### `compression_stats`

```python
def compression_stats(self, original_text: str, compressed: str) -> dict
```

Get size comparison stats for a text->AAAK conversion.

NOTE: AAAK is lossy summarization, not compression. The "ratio"
reflects how much shorter the summary is, not a compression ratio
in the traditional sense — information is lost.
