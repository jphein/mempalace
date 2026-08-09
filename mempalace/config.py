"""
MemPalace configuration system.

Priority: env vars > config file (~/.mempalace/config.json) > defaults
"""

import json
import os
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from .write_routing import (
    ResolvedWriteRoutingPolicy,
    RoutingPolicyCandidate,
    WriteRoutingError,
    WriteRoutingPolicy,
    resolve_write_routing_policy,
)

# ── Input validation ──────────────────────────────────────────────────────────
# Shared sanitizers for wing/room/entity names. Prevents path traversal,
# excessively long strings, and special characters that could cause issues
# in file paths, SQLite, or ChromaDB metadata.

MAX_NAME_LENGTH = 128
_SAFE_NAME_RE = re.compile(r"^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$")

# MCP clients (e.g. Claude Desktop, WorkBuddy) occasionally relay lone UTF-16
# surrogates (U+D800–U+DFFF) when proxying binary-in-Unicode or corrupted
# clipboard input. Python's ``str.encode('utf-8')`` raises on these, which
# crashes ChromaDB add/upsert with -32000. See issue #1235.
_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def strip_lone_surrogates(text: str) -> str:
    """Replace lone UTF-16 surrogates with U+FFFD so the string is legal UTF-8 (#1235)."""
    return _LONE_SURROGATE_RE.sub("�", text)


# Tool output mined from real transcripts routinely embeds a NUL character
# (U+0000) — e.g. captured Bash output where a reader raced a background
# writer, or genuine binary/NUL-delimited command output. A document
# containing one is otherwise valid, well-formed text (unlike a lone
# surrogate, which is invalid UTF-8), but handing it to ChromaDB's
# SQLite/FTS5 layer can corrupt the FTS5 inverted index for the *whole*
# collection (``PRAGMA quick_check`` reports "malformed inverted index for
# FTS5 table"), not just fail to store that one document. Stripping it
# before it reaches the chromadb client is the same defense-in-depth this
# module already applies to lone surrogates (#1235) — sanitize input we
# don't control before it reaches a datastore we don't control.
def strip_nul_bytes(text: str) -> str:
    """Replace embedded NUL characters with U+FFFD before ChromaDB storage."""
    return text.replace("\x00", "�")


def normalize_wing_name(name: str) -> str:
    """Lower-case + collapse separators (`-`, ` `) to `_` for wing slugs.

    The same rule is applied by ``init`` when persisting `topics_by_wing`
    and when writing `mempalace.yaml`, so the miner's lookup matches at
    mine time regardless of the source dirname.

    Leading/trailing separators are stripped so a path-encoded dirname like
    ``-home-user-proj`` yields ``home_user_proj`` rather than a leading-
    underscore slug that ``sanitize_name`` (and thus the MCP write tools)
    would reject.
    """
    return name.lower().replace(" ", "_").replace("-", "_").strip("_")


def sanitize_name(value: str, field_name: str = "name") -> str:
    """Validate and sanitize a wing/room/entity name.

    Raises ValueError if the name is invalid.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

    value = value.strip()

    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{field_name} exceeds maximum length of {MAX_NAME_LENGTH} characters")

    # Block path traversal
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"{field_name} contains invalid path characters")

    # Block null bytes
    if "\x00" in value:
        raise ValueError(f"{field_name} contains null bytes")

    # Enforce safe character set
    if not _SAFE_NAME_RE.match(value):
        raise ValueError(f"{field_name} contains invalid characters")

    return value


def sanitize_kg_value(value: str, field_name: str = "value") -> str:
    """Validate a knowledge-graph entity name (subject or object).

    More permissive than sanitize_name — allows punctuation like commas,
    colons, and parentheses that are common in natural-language KG values.
    Only blocks null bytes and over-length strings.

    Not used for wing/room names (which have filesystem constraints) or
    predicates (which should be simple relationship identifiers).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

    value = value.strip()

    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{field_name} exceeds maximum length of {MAX_NAME_LENGTH} characters")

    if "\x00" in value:
        raise ValueError(f"{field_name} contains null bytes")

    return strip_lone_surrogates(value)


# ISO-8601 temporal validator for knowledge-graph temporal parameters
# (as_of, valid_from, valid_to, ended).
#
# The KG stores temporal values as TEXT. Lexicographic comparisons are only
# safe when datetime values use one canonical shape. Accept full dates for
# legacy compatibility and exact UTC datetimes for sub-day precision.
#
# Accepted:
#   YYYY-MM-DD
#   YYYY-MM-DDTHH:MM:SSZ
#   YYYY-MM-DDTHH:MM:SS+00:00  (normalized to ...Z)
#
# Rejected:
#   partial dates, naive datetimes, non-UTC timezone offsets, fractional
#   seconds, and SQLite-style space-separated datetimes.
_ISO_DATE_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")

_ISO_UTC_DATETIME_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:Z|\+00:00)$"
)


def _validate_iso_temporal_calendar(value: str) -> None:
    """Reject impossible calendar values after regex shape validation."""

    if _ISO_DATE_RE.match(value):
        date.fromisoformat(value)
        return

    if _ISO_UTC_DATETIME_RE.match(value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return

    raise ValueError


def sanitize_iso_temporal(value, field_name: str = "date"):
    """Validate an ISO-8601 date or canonical UTC datetime string.

    Accepts ``None`` and ``""`` as pass-through values.

    Accepted non-empty string forms:

    - ``YYYY-MM-DD``
    - ``YYYY-MM-DDTHH:MM:SSZ``
    - ``YYYY-MM-DDTHH:MM:SS+00:00`` normalized to ``...Z``

    Partial dates are rejected because KG queries compare TEXT temporal values.
    Non-canonical datetime forms are rejected because mixed temporal string
    formats can silently return wrong KG query results.
    """

    if value is None or value == "":
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")

    value = value.strip()

    try:
        _validate_iso_temporal_calendar(value)
    except ValueError:
        raise ValueError(
            f"{field_name}={value!r} is not a valid ISO-8601 date or UTC datetime "
            "(expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)"
        ) from None

    if value.endswith("+00:00"):
        value = f"{value[:-6]}Z"

    return value


def sanitize_iso_date(value, field_name: str = "date"):
    """Backward-compatible wrapper for ISO temporal validation.

    Historically this accepted only full dates. It now also accepts canonical
    UTC datetimes, but the old name is kept so existing imports continue to
    work.
    """

    return sanitize_iso_temporal(value, field_name)


def sanitize_content(value: str, max_length: int = 100_000) -> str:
    """Validate drawer/diary content length."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("content must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"content exceeds maximum length of {max_length} characters")
    if "\x00" in value:
        raise ValueError("content contains null bytes")
    return strip_lone_surrogates(value)


DEFAULT_PALACE_PATH = os.path.expanduser("~/.mempalace/palace")
DEFAULT_COLLECTION_NAME = "mempalace_drawers"
DEFAULT_BACKEND = "chroma"
DEFAULT_MILVUS_CONSISTENCY_LEVEL = "Strong"
_MILVUS_CONSISTENCY_LEVELS = {
    "strong": "Strong",
    "session": "Session",
    "bounded": "Bounded",
    "eventually": "Eventually",
}

# How many timestamped palace backups to retain before the oldest are
# pruned. Applies to the accumulating backups written by ``mempalace
# migrate`` and ``mempalace repair max-seq-id`` — see
# ``MempalaceConfig.max_backups``.
DEFAULT_MAX_BACKUPS = 10


def normalize_milvus_consistency_level(value) -> str:
    raw = str(value).strip() if value else DEFAULT_MILVUS_CONSISTENCY_LEVEL
    normalized = _MILVUS_CONSISTENCY_LEVELS.get(raw.lower())
    if normalized:
        return normalized
    allowed = ", ".join(_MILVUS_CONSISTENCY_LEVELS.values())
    raise ValueError(f"milvus_consistency_level must be one of: {allowed}")


def sqlite_read_uri(db_path: str) -> str:
    """Return a read-only ``file:`` URI for ``sqlite3.connect(..., uri=True)``.

    A bare ``f"file:{db_path}?mode=ro"`` mis-parses paths containing spaces or
    other URI-reserved characters — common in real home directories (a Windows
    user folder like ``First Last``, many macOS paths). ``pathname2url``
    percent-encodes the path and normalizes separators so the database opens on
    every platform.
    """
    from urllib.request import pathname2url

    db_path = os.fspath(db_path)
    return f"file:{pathname2url(db_path)}?mode=ro"


@lru_cache(maxsize=1)
def get_configured_collection_name() -> str:
    """Return the configured drawer collection name without repeated config-file reads."""
    return MempalaceConfig().collection_name


# Single source of truth for chunking defaults. ``mempalace.miner``
# imports these so the legacy module-level ``CHUNK_SIZE`` /
# ``CHUNK_OVERLAP`` / ``MIN_CHUNK_SIZE`` constants stay in sync with
# ``MempalaceConfig.chunk_*``. Putting them here (not in miner.py) keeps
# the config layer self-contained and avoids circular imports.
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_MIN_CHUNK_SIZE = 50

DEFAULT_TOPIC_WINGS = [
    "emotions",
    "consciousness",
    "memory",
    "technical",
    "identity",
    "family",
    "creative",
]

DEFAULT_HALL_KEYWORDS = {
    "emotions": [
        "scared",
        "afraid",
        "worried",
        "happy",
        "sad",
        "love",
        "hate",
        "feel",
        "cry",
        "tears",
    ],
    "consciousness": [
        "consciousness",
        "conscious",
        "aware",
        "real",
        "genuine",
        "soul",
        "exist",
        "alive",
    ],
    "memory": ["memory", "remember", "forget", "recall", "archive", "palace", "store"],
    "technical": [
        "code",
        "python",
        "script",
        "bug",
        "error",
        "function",
        "api",
        "database",
        "server",
    ],
    "identity": ["identity", "name", "who am i", "persona", "self"],
    "family": [
        "family",
        "kids",
        "children",
        "daughter",
        "son",
        "parent",
        "mother",
        "father",
    ],
    "creative": [
        "game",
        "gameplay",
        "player",
        "app",
        "design",
        "art",
        "music",
        "story",
    ],
}


def _normalize_backend_name(raw):
    backend = str(raw).strip().lower()
    aliases = {
        "chromadb": "chroma",
        "pg": "postgres",
        "postgresql": "postgres",
    }
    return aliases.get(backend, backend)


class MempalaceConfig:
    """Configuration manager for MemPalace.

    Load order: env vars > config file > defaults.
    """

    def __init__(self, config_dir=None, palace_path=None):
        """Initialize config.

        Args:
            config_dir: Override config directory (useful for testing).
                        Defaults to ~/.mempalace.
            palace_path: Explicit palace data directory. This is primarily
                         used by CLI operations that received ``--palace``;
                         it takes precedence over environment and file config.
        """
        self._config_dir = (
            Path(config_dir) if config_dir else Path(os.path.expanduser("~/.mempalace"))
        )
        self._config_file = self._config_dir / "config.json"
        self._people_map_file = self._config_dir / "people_map.json"
        self._palace_path_override = (
            os.path.abspath(os.path.expanduser(str(palace_path)))
            if palace_path is not None
            else None
        )
        self._file_config = {}

        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    self._file_config = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._file_config = {}

    @property
    def daemon_url(self):
        """Optional palace-daemon URL. When set, mempalace's CLI and MCP
        server route through palace-daemon's /mcp proxy instead of opening
        a local chromadb client.

        Resolution mirrors palace_path: env (``PALACE_DAEMON_URL``) wins,
        ``config.json`` key ``"daemon_url"`` as fallback, ``None`` means
        run locally (current default).

        See techempower-org/mempalace#49 — the env-only signal silently
        failed when Claude Code's MCP spawn context didn't propagate the
        env var, routing writes to a local palace while status read green.
        Config-file fallback closes that gap for our multi-host deployment.
        """
        env_val = os.environ.get("PALACE_DAEMON_URL", "").strip()
        if env_val:
            return env_val.rstrip("/")
        cfg_val = (self._file_config.get("daemon_url") or "").strip()
        return cfg_val.rstrip("/") if cfg_val else None

    @property
    def daemon_strict(self) -> bool:
        """True when daemon-strict routing is active.

        Defaults True when ``daemon_url`` is set (env or config). Disable
        explicitly via ``PALACE_DAEMON_STRICT=0`` env or ``"daemon_strict":
        false`` in config.json — useful for test suites and offline
        development where the daemon isn't reachable.
        """
        if not self.daemon_url:
            return False
        env_val = os.environ.get("PALACE_DAEMON_STRICT")
        if env_val is not None:
            return env_val.strip() != "0"
        cfg_val = self._file_config.get("daemon_strict")
        if cfg_val is False:
            return False
        return True

    @property
    def auto_wake(self):
        """Opt-in wake-on-demand for a sleeping palace host.

        The daemon host may be a suspend-to-RAM machine where
        "unreachable" routinely means "asleep", not "down". When
        configured, connection-level failures in the CLI run the wake
        command (a Wake-on-LAN sender or similar), wait for the daemon's
        ``/health``, and retry once. See :mod:`mempalace.auto_wake`.

        ``config.json`` accepts a command string::

            {"auto_wake": "wakeonlan aa:bb:cc:dd:ee:ff"}

        or an object with tuning knobs::

            {"auto_wake": {"command": "wakeonlan aa:bb:cc:dd:ee:ff",
                           "timeout_seconds": 45,
                           "poll_interval_seconds": 2}}

        Returns a normalized dict (``command``, ``timeout_seconds``,
        ``poll_interval_seconds``) or ``None`` when disabled. The env
        escape hatch ``PALACE_AUTO_WAKE=0`` force-disables without
        editing config — useful for scripts that prefer fail-fast.
        Garbage values fall back to defaults; a missing/empty command
        disables (fail-open to "off": a typo must never make the CLI
        run an unexpected shell command).
        """
        env_val = os.environ.get("PALACE_AUTO_WAKE")
        if env_val is not None and env_val.strip().lower() in ("0", "false", "no"):
            return None
        raw = self._file_config.get("auto_wake")
        if isinstance(raw, str):
            raw = {"command": raw}
        if not isinstance(raw, dict):
            return None
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            return None

        def _bounded(key, default, lo, hi):
            try:
                val = float(raw.get(key, default))
            except (TypeError, ValueError):
                return default
            return min(max(val, lo), hi)

        return {
            "command": command.strip(),
            "timeout_seconds": _bounded("timeout_seconds", 45.0, 5.0, 300.0),
            "poll_interval_seconds": _bounded("poll_interval_seconds", 2.0, 0.5, 30.0),
        }

    @property
    def palace_path(self):
        """Path to the memory palace data directory."""
        if self._palace_path_override is not None:
            return self._palace_path_override
        env_val = os.environ.get("MEMPALACE_PALACE_PATH") or os.environ.get("MEMPAL_PALACE_PATH")
        if env_val:
            # Normalize: expand ~ and collapse .. to match the CLI --palace
            # code path (mcp_server.py:62) and prevent surprise redirection
            # when the env var contains unresolved components.
            return os.path.abspath(os.path.expanduser(env_val))
        return os.path.expanduser(self._file_config.get("palace_path", DEFAULT_PALACE_PATH))

    @property
    def tunnel_file(self):
        """Path to the tunnel file, sibling of palace_path."""
        return os.path.join(os.path.dirname(self.palace_path), "tunnels.json")

    @property
    def hallway_file(self):
        """Path to the hallway file, sibling of palace_path.

        Mirrors ``tunnel_file`` so within-wing hallway state is scoped to the
        configured palace and survives palace rebuilds (it does not live in
        ChromaDB which can be recreated). Prior to this property the path was
        hardcoded under ``~/.mempalace/hallways.json`` and multiple palaces on
        one host silently shared one file (see ``hallways._legacy_hallway_file``).
        """
        return os.path.join(os.path.dirname(self.palace_path), "hallways.json")

    @property
    def collection_name(self):
        """Storage collection name."""
        env_val = os.environ.get("MEMPALACE_COLLECTION_NAME")
        if env_val:
            return env_val
        return self._file_config.get("collection_name", DEFAULT_COLLECTION_NAME)

    @property
    def backend(self):
        """Storage backend name.

        Chroma remains the default. PostgreSQL must be explicitly enabled with
        MEMPALACE_BACKEND=postgres or config.json {"backend": "postgres"}.
        """
        return self.backend_override or DEFAULT_BACKEND

    @property
    def backend_override(self):
        """Explicit backend selection from env/config, or None for auto/default resolution."""
        # Resolution order matches resolve_backend_name (upstream RFC 001):
        # config.json beats MEMPALACE_BACKEND.
        raw = self._file_config.get("backend") or os.environ.get("MEMPALACE_BACKEND")
        if raw:
            return _normalize_backend_name(raw)
        return None

    @property
    def cross_encoder_rerank(self) -> bool:
        """Whether the optional cross-encoder rerank stage is enabled.

        Off by default — preserves the zero-model-at-query-time default
        (per JP's no-model-at-query-time rule from techempower-org/mempalace#179).
        Opt in via ``MEMPALACE_RERANK_CROSS_ENCODER=1`` env or
        ``"cross_encoder_rerank": true`` in config.json. Env wins.

        See ``mempalace.cross_encoder_rerank`` for the rerank stage
        itself and the related model / top-N knobs.
        """
        from . import cross_encoder_rerank as _cer

        return _cer.is_enabled(self._file_config)

    @property
    def cross_encoder_model(self) -> str:
        """Cross-encoder model name. Used only when ``cross_encoder_rerank``
        is enabled. Defaults to ``cross-encoder/ms-marco-MiniLM-L-6-v2`` —
        22M parameters, CPU-friendly, captures most of the rerank value
        per the True Memory comparison (`docs/research/2026-05-24-true-memory-comparison.md`).
        Override via ``MEMPALACE_RERANK_CROSS_ENCODER_MODEL`` env or
        ``"cross_encoder_model"`` in config.json.
        """
        from . import cross_encoder_rerank as _cer

        return _cer.get_model_name(self._file_config)

    @property
    def cross_encoder_top_n(self) -> int:
        """How many top hits to rerank. Defaults to 25.

        Override via ``MEMPALACE_RERANK_TOP_N`` env or
        ``"cross_encoder_top_n"`` in config.json. Latency scales linearly
        with this value; the rerank only reorders, so it's a quality/cost
        knob, not a recall floor.
        """
        from . import cross_encoder_rerank as _cer

        return _cer.get_top_n(self._file_config)

    @property
    def calibration_path(self):
        """Optional path to a fitted confidence calibrator JSON.

        When set, ``search_memories`` loads the calibrator and surfaces a
        ``confidence`` field (calibrated P(relevant)) on each vector hit.
        When unset or the file is missing, no ``confidence`` field is
        emitted — the system never fakes a calibrated score.

        Resolution mirrors ``palace_path``: env (``MEMPALACE_CALIBRATION_PATH``)
        wins, ``config.json`` key ``"calibration_path"`` as fallback,
        ``None`` means no calibration (current default).
        """
        env_val = os.environ.get("MEMPALACE_CALIBRATION_PATH", "").strip()
        if env_val:
            return os.path.abspath(os.path.expanduser(env_val))
        cfg_val = self._file_config.get("calibration_path")
        cfg_str = str(cfg_val).strip() if cfg_val is not None else ""
        return os.path.abspath(os.path.expanduser(cfg_str)) if cfg_str else None

    @property
    def postgres_dsn(self):
        """PostgreSQL DSN for the optional PostgreSQL backend."""
        env_val = os.environ.get("MEMPALACE_POSTGRES_DSN") or os.environ.get("MEMPALACE_PG_DSN")
        if env_val:
            return env_val
        return self._file_config.get("postgres_dsn") or self._file_config.get("pg_dsn")

    @property
    def kg_backend(self) -> str:
        """Knowledge-graph backend name. SQLite stays the default.

        Apache AGE is opt-in via ``MEMPALACE_KG_BACKEND=age`` or
        ``config.json {"kg_backend": "age"}``. When set to ``age`` the
        AGE backend uses ``postgres_dsn`` for its connection (AGE runs
        in the same Postgres database as the storage backend can).

        Lowercased before returning; falls back to ``"sqlite"`` on empty.
        """
        env = os.environ.get("MEMPALACE_KG_BACKEND", "").strip().lower()
        if env:
            return env
        raw = self._file_config.get("kg_backend", "sqlite")
        return str(raw).strip().lower() or "sqlite"

    @property
    def auto_query_enabled(self) -> bool:
        """Whether the auto-query classifier is active.

        Env ``AUTO_QUERY_ENABLED`` > config ``auto_query.enabled`` > False.
        """
        env_val = os.environ.get("AUTO_QUERY_ENABLED", "").strip().lower()
        if env_val:
            return env_val in ("1", "true", "yes")
        aq = self._file_config.get("auto_query", {})
        return bool(aq.get("enabled", False))

    @property
    def auto_query_mode(self) -> str:
        """Auto-query mode: off, dry-run, conservative, balanced, aggressive.

        Env ``AUTO_QUERY_MODE`` > config ``auto_query.mode`` > ``"off"``.
        """
        env_val = os.environ.get("AUTO_QUERY_MODE", "").strip().lower()
        if env_val:
            return env_val
        aq = self._file_config.get("auto_query", {})
        return str(aq.get("mode", "off")).strip().lower()

    @property
    def auto_query_depth_cache_ttl(self) -> int:
        """TTL (seconds) for the depth-refresh injection cache; 0 disables.

        Env ``AUTO_QUERY_DEPTH_CACHE_TTL`` > config ``auto_query.depth_cache_ttl``
        > 900. The depth query is deterministic per wing, so serving repeat
        fires from cache trades sub-second staleness bounds for skipping a
        ~1s daemon round-trip on every 10th turn.
        """
        env_val = os.environ.get("AUTO_QUERY_DEPTH_CACHE_TTL", "").strip()
        if env_val:
            coerced = self._try_coerce_int(env_val, minimum=0)
            if coerced is not None:
                return coerced
        aq = self._file_config.get("auto_query", {})
        coerced = self._try_coerce_int(aq.get("depth_cache_ttl"), minimum=0)
        return coerced if coerced is not None else 900

    @property
    def auto_query_max_per_turn(self) -> int:
        """Max auto-query invocations per turn.

        Env ``AUTO_QUERY_MAX_PER_TURN`` > config ``auto_query.max_per_turn`` > 1.
        """
        env_val = os.environ.get("AUTO_QUERY_MAX_PER_TURN", "").strip()
        if env_val:
            coerced = self._try_coerce_int(env_val, minimum=0)
            if coerced is not None:
                return coerced
        aq = self._file_config.get("auto_query", {})
        coerced = self._try_coerce_int(aq.get("max_per_turn"), minimum=0)
        return coerced if coerced is not None else 1

    @property
    def auto_query_max_per_minute(self) -> int:
        """Max auto-query invocations per minute (rate limit).

        Env ``AUTO_QUERY_MAX_PER_MINUTE`` > config ``auto_query.max_per_minute`` > 6.
        """
        env_val = os.environ.get("AUTO_QUERY_MAX_PER_MINUTE", "").strip()
        if env_val:
            coerced = self._try_coerce_int(env_val, minimum=1)
            if coerced is not None:
                return coerced
        aq = self._file_config.get("auto_query", {})
        coerced = self._try_coerce_int(aq.get("max_per_minute"), minimum=1)
        return coerced if coerced is not None else 6

    @property
    def wing_aliases(self) -> dict:
        """Mapping of directory basenames to canonical palace wing names.

        Useful when a project directory name differs from its palace wing
        (e.g., ``familiar.realm.watch`` → ``familiar_realm_watch``).

        Config ``wing_aliases`` > empty dict.
        """
        return self._file_config.get("wing_aliases", {})

    def resolve_wing(self, directory_name: str) -> str:
        """Resolve a project directory name to its canonical palace wing.

        Checks ``wing_aliases`` first, then falls back to the default
        normalization (lowercase, dots/dashes/spaces → underscores).
        """
        aliases = self.wing_aliases
        if directory_name in aliases:
            return aliases[directory_name]
        return directory_name.lower().replace(".", "_").replace("-", "_").replace(" ", "_")

    @property
    def room_aliases(self) -> dict:
        """Mapping of detected/input room names to canonical palace room names.

        Useful for overriding auto-detected room names or unifying variants
        (e.g., ``ui`` → ``frontend``, ``api`` → ``backend``).

        Config ``room_aliases`` > empty dict.
        """
        return self._file_config.get("room_aliases", {})

    def resolve_room(self, room_name: str) -> str:
        """Resolve a room name to its canonical palace room.

        Checks ``room_aliases`` first, then falls back to the default
        normalization (lowercase, dashes/spaces → underscores).
        """
        aliases = self.room_aliases
        if room_name in aliases:
            return aliases[room_name]
        normalized = room_name.lower()
        if normalized in aliases:
            return aliases[normalized]
        return normalized.replace("-", "_").replace(" ", "_")

    @property
    def qdrant_url(self):
        """Qdrant endpoint for the opt-in ``qdrant`` backend.

        Defaults to localhost so selecting Qdrant never silently sends memory
        to a remote service. Users can point at a LAN or cloud endpoint via
        config or ``MEMPALACE_QDRANT_URL`` when they deliberately choose that.
        """
        env_val = os.environ.get("MEMPALACE_QDRANT_URL")
        if env_val:
            return env_val.strip()
        return str(self._file_config.get("qdrant_url", "http://localhost:6333")).strip()

    @property
    def qdrant_api_key(self):
        """API key for the opt-in ``qdrant`` backend, if configured."""
        env_val = os.environ.get("MEMPALACE_QDRANT_API_KEY")
        if env_val:
            return env_val
        value = self._file_config.get("qdrant_api_key")
        return str(value) if value else None

    @property
    def qdrant_namespace(self):
        """Optional Qdrant collection namespace/prefix."""
        env_val = os.environ.get("MEMPALACE_QDRANT_NAMESPACE")
        if env_val:
            return env_val.strip()
        value = self._file_config.get("qdrant_namespace")
        return str(value).strip() if value else None

    @property
    def qdrant_timeout(self):
        """Qdrant HTTP timeout in seconds."""
        env_val = os.environ.get("MEMPALACE_QDRANT_TIMEOUT")
        raw = env_val if env_val is not None else self._file_config.get("qdrant_timeout", 10.0)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = 10.0
        return timeout if timeout > 0 else 10.0

    @property
    def milvus_uri(self):
        """Milvus endpoint for the opt-in ``milvus`` backend.

        Defaults to ``None`` so selecting Milvus uses per-palace Milvus Lite at
        ``<palace>/milvus.db``. Set this only to deliberately use a shared
        Milvus server, Zilliz Cloud, or a custom local Lite file.
        """
        env_val = os.environ.get("MEMPALACE_MILVUS_URI")
        if env_val:
            return env_val.strip()
        value = self._file_config.get("milvus_uri")
        return str(value).strip() if value else None

    @property
    def milvus_token(self):
        """Token for the opt-in ``milvus`` backend, if configured."""
        env_val = os.environ.get("MEMPALACE_MILVUS_TOKEN")
        if env_val:
            return env_val
        value = self._file_config.get("milvus_token")
        return str(value) if value else None

    @property
    def milvus_db_name(self):
        """Optional Milvus database name for the opt-in ``milvus`` backend."""
        env_val = os.environ.get("MEMPALACE_MILVUS_DB_NAME")
        if env_val:
            return env_val.strip()
        value = self._file_config.get("milvus_db_name")
        return str(value).strip() if value else None

    @property
    def milvus_namespace(self):
        """Optional Milvus collection namespace/prefix."""
        env_val = os.environ.get("MEMPALACE_MILVUS_NAMESPACE")
        if env_val:
            return env_val.strip()
        value = self._file_config.get("milvus_namespace")
        return str(value).strip() if value else None

    @property
    def milvus_consistency_level(self):
        """Milvus read consistency level for the opt-in ``milvus`` backend."""
        env_val = os.environ.get("MEMPALACE_MILVUS_CONSISTENCY_LEVEL")
        if env_val:
            return normalize_milvus_consistency_level(env_val)
        value = self._file_config.get("milvus_consistency_level")
        return normalize_milvus_consistency_level(value)

    @property
    def pgvector_dsn(self):
        """Postgres DSN for the opt-in ``pgvector`` backend.

        Defaults to a localhost DSN so selecting pgvector never silently sends
        memory to a remote database. Point at a LAN or cloud Postgres via config
        or ``MEMPALACE_PGVECTOR_DSN`` only when deliberately chosen.
        """
        env_val = os.environ.get("MEMPALACE_PGVECTOR_DSN")
        if env_val:
            return env_val.strip()
        return str(
            self._file_config.get("pgvector_dsn", "postgresql://localhost:5432/mempalace")
        ).strip()

    @property
    def pgvector_namespace(self):
        """Optional pgvector table namespace/prefix for multi-tenant isolation."""
        env_val = os.environ.get("MEMPALACE_PGVECTOR_NAMESPACE")
        if env_val:
            return env_val.strip()
        value = self._file_config.get("pgvector_namespace")
        return str(value).strip() if value else None

    @property
    def people_map(self):
        """Mapping of name variants to canonical names."""
        if self._people_map_file.exists():
            try:
                with open(self._people_map_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._file_config.get("people_map", {})

    @property
    def hooks_auto_save(self):
        """Whether the stop/precompact hooks should block for auto-save.

        When False, hooks pass through without blocking — equivalent to
        disabling auto-save while keeping hook scripts installed.
        """
        env_val = os.environ.get("MEMPALACE_HOOKS_AUTO_SAVE")
        if env_val is not None:
            return env_val.lower() not in ("false", "0", "no")
        hooks = self._file_config.get("hooks", {})
        return hooks.get("auto_save", True)

    @property
    def topic_wings(self):
        """List of topic wing names."""
        return self._file_config.get("topic_wings", DEFAULT_TOPIC_WINGS)

    @property
    def hall_keywords(self):
        """Mapping of hall names to keyword lists."""
        return self._file_config.get("hall_keywords", DEFAULT_HALL_KEYWORDS)

    @staticmethod
    def _try_coerce_int(value, minimum=None):
        """Coerce a raw config value to int, or ``None`` if it cannot be a
        valid setting.

        bool, empty/garbage string, non-numeric, and below-``minimum``
        values all return ``None``. Shared by ``_coerce_config_int``
        (which substitutes a documented default) and
        ``min_chunk_size_explicit`` (which must distinguish "unusable"
        from "explicitly set" without crashing the convo path).
        """
        if isinstance(value, bool):
            return None
        try:
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return None
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: JSON ``1e1000`` parses to float('inf'), and
            # ``int(inf)`` raises it — still just garbage config, not a crash.
            return None
        if minimum is not None and value < minimum:
            return None
        return value

    def _coerce_config_int(self, key: str, default: int, minimum=None) -> int:
        """Read an int config value, falling back to ``default`` on bad input.

        Hand-edited ``config.json`` is the most common source of garbage:
        a string, a bool, a negative number, or a JSON null. None of those
        should crash mining or hang ``chunk_text()`` — fall back silently
        to the documented default rather than letting a typo break ingest.
        """
        coerced = self._try_coerce_int(self._file_config.get(key, default), minimum)
        return default if coerced is None else coerced

    def _validated_chunk_config(self):
        """Return ``(chunk_size, chunk_overlap, min_chunk_size)`` post-validation.

        Enforces the invariants the miner relies on:
          * ``chunk_size >= 1``
          * ``0 <= chunk_overlap <= chunk_size // 2``. A larger overlap can
            loop the miner forever on short-line content (#2056)
          * ``min_chunk_size <= chunk_size`` — otherwise no chunk is ever
            large enough to file, and ingest silently produces 0 drawers

        Repairs (rather than raises) on violation so a single bad
        config.json key doesn't take ingest down.
        """
        chunk_size = self._coerce_config_int("chunk_size", DEFAULT_CHUNK_SIZE, minimum=1)
        chunk_overlap = self._coerce_config_int("chunk_overlap", DEFAULT_CHUNK_OVERLAP, minimum=0)
        min_chunk_size = self._coerce_config_int(
            "min_chunk_size", DEFAULT_MIN_CHUNK_SIZE, minimum=0
        )

        if chunk_overlap > chunk_size // 2:
            # Overlap past half the chunk size can hang miner.chunk_text's
            # windowing loop on short-line content (#2056): a boundary pull can
            # shrink a chunk below chunk_overlap, so
            # ``start = end - chunk_overlap`` stops advancing. Repair to the
            # default when it is still at most half, else clamp to the largest
            # safe overlap.
            chunk_overlap = min(DEFAULT_CHUNK_OVERLAP, chunk_size // 2)

        if min_chunk_size > chunk_size:
            min_chunk_size = (
                DEFAULT_MIN_CHUNK_SIZE if DEFAULT_MIN_CHUNK_SIZE <= chunk_size else chunk_size
            )

        return chunk_size, chunk_overlap, min_chunk_size

    @property
    def chunk_size(self) -> int:
        """Characters per drawer chunk (validated, ``>= 1``)."""
        return self._validated_chunk_config()[0]

    @property
    def chunk_overlap(self) -> int:
        """Overlap between adjacent chunks (validated, ``<= chunk_size // 2``)."""
        return self._validated_chunk_config()[1]

    @property
    def min_chunk_size(self) -> int:
        """Minimum chunk size — skip smaller chunks (validated, ``<= chunk_size``)."""
        return self._validated_chunk_config()[2]

    @property
    def min_chunk_size_explicit(self):
        """Validated ``min_chunk_size`` iff the user explicitly set it.

        Returns the coerced int when ``config.json`` defines a usable
        ``min_chunk_size`` (``>= 0`` and ``<= chunk_size``); ``None`` when
        the key is absent/null or the value is unusable. ``convo_miner``
        relies on the ``None`` sentinel to keep its lower 30-char floor
        (more permissive than the 50-char project default, so short
        exchanges are not dropped) for untuned users while still honoring
        an explicit override —
        replacing the raw, unvalidated ``_file_config`` reach that crashed
        convo ingest on a bad key (#1024 review).
        """
        raw = self._file_config.get("min_chunk_size")
        if raw is None:
            return None
        coerced = self._try_coerce_int(raw, minimum=0)
        if coerced is None or coerced > self.chunk_size:
            return None
        return coerced

    @property
    def entity_languages(self):
        """Languages whose entity-detection patterns should be applied.

        Reads from env var ``MEMPALACE_ENTITY_LANGUAGES`` (comma-separated)
        first, then the ``entity_languages`` field in ``config.json``,
        defaulting to ``["en"]``.
        """
        env_val = os.environ.get("MEMPALACE_ENTITY_LANGUAGES") or os.environ.get(
            "MEMPAL_ENTITY_LANGUAGES"
        )
        if env_val:
            return [s.strip() for s in env_val.split(",") if s.strip()] or ["en"]
        cfg = self._file_config.get("entity_languages")
        if isinstance(cfg, list) and cfg:
            return [str(s) for s in cfg]
        return ["en"]

    def set_entity_languages(self, languages):
        """Persist the entity-detection language list to ``config.json``."""
        normalized = [s.strip() for s in languages if s and s.strip()]
        if not normalized:
            normalized = ["en"]
        self._file_config["entity_languages"] = normalized
        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._file_config, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        try:
            self._config_file.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        return normalized

    @property
    def embedding_device(self):
        """Hardware device for the ONNX embedding model.

        Values: ``"auto"`` (default), ``"cpu"``, ``"cuda"``, ``"coreml"``,
        ``"dml"``. Read from env ``MEMPALACE_EMBEDDING_DEVICE`` first, then
        ``embedding_device`` in ``config.json``, then ``"auto"``.

        ``auto`` resolves to the first available accelerator at runtime via
        :mod:`mempalace.embedding`; requesting an unavailable accelerator
        logs a warning and falls back to CPU.
        """
        env_val = os.environ.get("MEMPALACE_EMBEDDING_DEVICE")
        if env_val:
            return env_val.strip().lower()
        return str(self._file_config.get("embedding_device", "auto")).strip().lower()

    @property
    def embedding_model(self):
        """Embedding model identifier.

        Values: ``"minilm"`` (ChromaDB's all-MiniLM-L6-v2 — English-only),
        ``"embeddinggemma"`` (multilingual, 100+ languages, default for
        new installs since onboarding writes the choice), or ``"adaptmem_ft"``
        (a local fine-tuned SentenceTransformer checkpoint — see
        :attr:`adaptmem_path`). Read from env ``MEMPALACE_EMBEDDING_MODEL``
        first, then ``embedding_model`` in ``config.json``, then ``"minilm"``
        as a back-compat fallback for palaces created before onboarding asked
        the question.

        Switching models on an existing palace requires re-embedding
        (different vector space) — ChromaDB rejects reads when the persisted
        EF name doesn't match. Run ``mempalace repair rebuild-index`` after
        changing this value.
        """
        env_val = os.environ.get("MEMPALACE_EMBEDDING_MODEL")
        if env_val:
            return env_val.strip().lower()
        return str(self._file_config.get("embedding_model", "minilm")).strip().lower()

    @property
    def embedding_threads(self) -> int:
        """Cap on the embedder's ONNX Runtime intra-op thread pool (#1068).

        ChromaDB's ONNX embedder builds its ``InferenceSession`` with no thread
        cap, so the intra-op pool defaults to the physical core count and a
        background ``mine`` pins every core — stacked Stop-hook fires turn into
        thermal events. ``OMP_NUM_THREADS`` is inert here (ORT owns its own
        pool), so the cap is applied via ``SessionOptions`` in
        :mod:`mempalace.embedding`.

        Read from env ``MEMPALACE_EMBEDDING_THREADS`` first, then
        ``embedding_threads`` in ``config.json``. Semantics:

        - unset / ``"auto"`` → half the logical CPUs (min 1), so a background
          mine leaves the machine usable out of the box.
        - a positive integer → exactly that many intra-op threads.
        - ``0`` or negative → uncapped: ORT's default (physical core count),
          for users who want maximum indexing throughput.
        """
        raw = os.environ.get("MEMPALACE_EMBEDDING_THREADS")
        if raw is None:
            raw = self._file_config.get("embedding_threads")
        if raw is None or str(raw).strip().lower() in ("", "auto"):
            return max(1, (os.cpu_count() or 2) // 2)
        try:
            val = int(str(raw).strip())
        except (TypeError, ValueError):
            return max(1, (os.cpu_count() or 2) // 2)
        return val if val > 0 else 0

    def set_embedding_model(self, model: str) -> None:
        """Persist the embedding-model choice to ``config.json``.

        Onboarding calls this once on first run. Accepts ``"minilm"`` or
        ``"embeddinggemma"``; other values are normalized to lowercase and
        passed through (``embedding.get_embedding_function`` falls back to
        minilm for unrecognized values).
        """
        self._file_config["embedding_model"] = str(model).strip().lower()
        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._file_config, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        try:
            self._config_file.chmod(0o600)
        except (OSError, NotImplementedError):
            pass

    def set_backend(self, backend: str) -> None:
        """Persist the storage backend choice to ``config.json``."""
        backend = str(backend).strip().lower()
        from .backends import get_backend_class

        get_backend_class(backend)
        self._file_config["backend"] = backend
        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._file_config, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        try:
            self._config_file.chmod(0o600)
        except (OSError, NotImplementedError):
            pass

    @property
    def adaptmem_path(self):
        """Filesystem path to the AdaptMem fine-tuned encoder checkpoint.

        Only consulted when ``embedding_model == "adaptmem_ft"``. Read from env
        ``MEMPALACE_ADAPTMEM_PATH`` first, then ``adaptmem_path`` in
        ``config.json``; ``None`` when neither is set (the encoder then raises a
        clear error telling the user to set the path).

        The checkpoint is a SentenceTransformer-shaped directory produced by
        techempower-org/adaptmem. Switching an existing palace to this model is
        a different vector space — run ``mempalace repair rebuild-index``.
        """
        env_val = os.environ.get("MEMPALACE_ADAPTMEM_PATH")
        if env_val and env_val.strip():
            return env_val.strip()
        file_val = self._file_config.get("adaptmem_path")
        if file_val and str(file_val).strip():
            return str(file_val).strip()
        return None

    @property
    def topic_tunnel_min_count(self):
        """Minimum number of overlapping confirmed topics required to create
        a cross-wing tunnel between two wings.

        Default is ``1`` — any single shared topic produces a tunnel. Bump
        to ``2+`` if your projects share lots of common-tech labels (Python,
        Docker, Git) and you want only meaningfully overlapping wings to
        link. Reads ``MEMPALACE_TOPIC_TUNNEL_MIN_COUNT`` env first, then the
        config-file value, then ``1``.
        """
        env_val = os.environ.get("MEMPALACE_TOPIC_TUNNEL_MIN_COUNT")
        if env_val:
            try:
                parsed = int(env_val)
                if parsed >= 1:
                    return parsed
            except ValueError:
                pass
        cfg_val = self._file_config.get("topic_tunnel_min_count")
        try:
            parsed = int(cfg_val) if cfg_val is not None else 1
        except (TypeError, ValueError):
            parsed = 1
        return max(1, parsed)

    @property
    def max_backups(self) -> int:
        """Number of timestamped palace backups to retain before pruning.

        Applies to the accumulating, timestamped backups created by
        ``mempalace migrate`` (``<palace>.pre-migrate.<timestamp>``) and
        ``mempalace repair max-seq-id``
        (``chroma.sqlite3.max-seq-id-backup-<timestamp>``). Each of those
        commands writes a fresh full-size copy every run and historically
        never deleted the old ones, so on a machine that mines or repairs on
        a schedule the backup set could silently grow until it filled the
        disk. After each backup is written, copies beyond this count (oldest
        first) are removed.

        Reads ``MEMPALACE_MAX_BACKUPS`` env first, then ``max_backups`` in
        ``config.json``, then the default of ``10``. A value of ``0`` disables
        pruning and keeps every backup (use when an external retention policy
        manages cleanup). Negative or non-numeric values fall back to the
        default rather than crashing migrate/repair.
        """
        env_val = os.environ.get("MEMPALACE_MAX_BACKUPS")
        if env_val is not None:
            coerced = self._try_coerce_int(env_val, minimum=0)
            if coerced is not None:
                return coerced
        coerced = self._try_coerce_int(
            self._file_config.get("max_backups", DEFAULT_MAX_BACKUPS), minimum=0
        )
        return DEFAULT_MAX_BACKUPS if coerced is None else coerced

    @property
    def lang_explicit(self):
        """Primary language code when explicitly configured, else ``None``.

        Resolution order: ``MEMPALACE_LANG`` / ``MEMPAL_LANG`` env var, then
        ``config.json["lang"]``. Returns ``None`` if neither is set. Use this
        when a caller needs to know whether the user has opted in to locale
        behaviour (e.g. to avoid silently changing search scoring for palaces
        that have never set a language).
        """
        env_val = os.environ.get("MEMPALACE_LANG") or os.environ.get("MEMPAL_LANG")
        if env_val and env_val.strip():
            return env_val.strip()
        cfg = self._file_config.get("lang")
        if isinstance(cfg, str) and cfg.strip():
            return cfg.strip()
        return None

    @property
    def lang(self):
        """Primary language code for localized output and display.

        Resolution order: ``lang_explicit`` (env or config.json), first entry
        of ``entity_languages``, then ``"en"``. Always returns a non-empty
        string so callers that need a language for display purposes never
        have to handle ``None``. Code paths that must not silently change
        behaviour for unconfigured palaces should read ``lang_explicit``
        instead.
        """
        explicit = self.lang_explicit
        if explicit:
            return explicit
        entity_langs = self.entity_languages
        if entity_langs:
            return entity_langs[0]
        return "en"

    @property
    def hook_silent_save(self):
        """Whether the stop hook saves directly (True) or blocks for MCP calls (False)."""
        return self._file_config.get("hooks", {}).get("silent_save", True)

    @property
    def hook_desktop_toast(self):
        """Whether the stop hook shows a desktop notification via notify-send."""
        return self._file_config.get("hooks", {}).get("desktop_toast", False)

    def resolve_write_routing(self, scope: str) -> ResolvedWriteRoutingPolicy:
        """Resolve the configured write policy for ``hooks`` or ``cli``.

        Precedence is:

        1. scope-specific environment variable;
        2. global environment variable;
        3. legacy hook environment variable;
        4. scope-specific config value;
        5. global config value;
        6. legacy hook config value;
        7. ``direct``.

        This foundation does not change current hook or CLI behavior. The
        policy-aware consumers are introduced by follow-up PRs.
        """

        normalized_scope = str(scope).strip().lower()
        env_names = {
            "hooks": "MEMPALACE_HOOK_WRITE_ROUTING",
            "cli": "MEMPALACE_CLI_WRITE_ROUTING",
        }

        if normalized_scope not in env_names:
            raise WriteRoutingError("write routing scope must be 'hooks' or 'cli'")

        routing_config = self._file_config.get("write_routing", {})
        if routing_config is None:
            routing_config = {}

        if not isinstance(routing_config, dict):
            raise WriteRoutingError("config write_routing must be an object")

        candidates = [
            RoutingPolicyCandidate(
                env_names[normalized_scope],
                os.environ.get(env_names[normalized_scope]),
            ),
            RoutingPolicyCandidate(
                "MEMPALACE_WRITE_ROUTING",
                os.environ.get("MEMPALACE_WRITE_ROUTING"),
            ),
        ]

        if normalized_scope == "hooks":
            candidates.append(
                RoutingPolicyCandidate(
                    "MEMPALACE_HOOKS_DAEMON (legacy)",
                    os.environ.get("MEMPALACE_HOOKS_DAEMON"),
                    legacy_boolean=True,
                )
            )

        candidates.extend(
            [
                RoutingPolicyCandidate(
                    f"config write_routing.{normalized_scope}",
                    routing_config.get(normalized_scope),
                ),
                RoutingPolicyCandidate(
                    "config write_routing.default",
                    routing_config.get("default"),
                ),
            ]
        )

        if normalized_scope == "hooks":
            hooks_config = self._file_config.get("hooks", {})
            if hooks_config is None:
                hooks_config = {}

            if not isinstance(hooks_config, dict):
                raise WriteRoutingError("config hooks must be an object")

            candidates.append(
                RoutingPolicyCandidate(
                    "config hooks.daemon (legacy)",
                    hooks_config.get("daemon"),
                    legacy_boolean=True,
                )
            )

        return resolve_write_routing_policy(candidates)

    @property
    def hook_write_routing(self) -> WriteRoutingPolicy:
        """Resolved future routing policy for hook-triggered writes."""

        return self.resolve_write_routing("hooks").policy

    @property
    def cli_write_routing(self) -> WriteRoutingPolicy:
        """Resolved future routing policy for routine CLI writes."""

        return self.resolve_write_routing("cli").policy

    @property
    def hook_verbatim_mode(self):
        """Skip truncation/noise-stripping in transcript ingest.

        When True, ``normalize()`` preserves Claude Code system tags, hook
        chrome, full Bash commands, full Bash output, full Grep/Glob match
        lists, full Read/Edit/Write results, and uncapped tool inputs.
        Default False — existing behavior is unchanged for upstream-shape
        installs and for users who haven't opted in.
        """
        return self._file_config.get("hooks", {}).get("verbatim_mode", False)

    @property
    def hook_use_daemon(self):
        """Whether hooks should submit save/mine work to the opt-in daemon."""
        env_val = os.environ.get("MEMPALACE_HOOKS_DAEMON")
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes", "on")
        value = self._file_config.get("hooks", {}).get("daemon", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return value == 1

    def set_hook_setting(self, key: str, value: bool):
        """Update a hook setting and write config to disk."""
        if "hooks" not in self._file_config:
            self._file_config["hooks"] = {}
        self._file_config["hooks"][key] = value
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._file_config, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    # NOTE: legacy raw-passthrough ``chunk_size`` / ``chunk_overlap`` /
    # ``min_chunk_size`` properties were removed in the upstream/develop
    # merge — they shadowed the validated, coercing versions defined
    # earlier (~L482) and caused ingest to receive raw string/bool/
    # negative values from hand-edited config.json. The validated
    # accessors above are now the single source of truth (upstream PR
    # #1024 + #1519). Do not re-add the raw-passthrough form.

    def init(self):
        """Create config directory and write default config.json if it doesn't exist."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        # Restrict directory permissions to owner only (Unix)
        try:
            self._config_dir.chmod(0o700)
        except (OSError, NotImplementedError):
            pass  # Windows doesn't support Unix permissions
        if not self._config_file.exists():
            # Chunking parameters (chunk_size, chunk_overlap, min_chunk_size)
            # are intentionally NOT written here — convo_miner.py distinguishes
            # "user has tuned this" from "user is on defaults" by checking
            # ``_file_config.get("min_chunk_size") is None``. Writing the
            # miner.py defaults (50) into config.json breaks that detection
            # and silently overrides convo_miner's stricter 30-char floor,
            # dropping legitimate short conversation exchanges. Module-level
            # defaults already apply correctly when these keys are absent.
            # "backend" is intentionally NOT seeded: an absent key means
            # "resolve normally" (env, detected artifacts, chroma default),
            # and writing the default would make config.json silently win
            # over MEMPALACE_BACKEND under the RFC 001 resolution order.
            default_config = {
                "palace_path": DEFAULT_PALACE_PATH,
                "collection_name": DEFAULT_COLLECTION_NAME,
                "topic_wings": DEFAULT_TOPIC_WINGS,
                "hall_keywords": DEFAULT_HALL_KEYWORDS,
            }
            with open(self._config_file, "w") as f:
                json.dump(default_config, f, indent=2)
            # Restrict config file to owner read/write only
            try:
                self._config_file.chmod(0o600)
            except (OSError, NotImplementedError):
                pass
        return self._config_file

    def save_people_map(self, people_map):
        """Write people_map.json to config directory.

        Args:
            people_map: Dict mapping name variants to canonical names.
        """
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._people_map_file, "w") as f:
            json.dump(people_map, f, indent=2)
        try:
            self._people_map_file.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        return self._people_map_file
