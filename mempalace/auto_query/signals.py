"""Signal extraction for auto-query context classifier.

Pure functions — no I/O, no MCP calls. All external state
(wings, entities, drawer existence) is passed in by the caller.
"""

import re

from mempalace.auto_query import Signal, SignalSet


# Entity regex — same pattern as searcher.py:1191
_ENTITY_REGEX = re.compile(r"\b([A-Z][a-zA-Z0-9_]+(?:\s+[A-Z][a-zA-Z0-9_]+)*)\b")

# Temporal signal patterns (spec section 1.1.2)
_TEMPORAL_RE = re.compile(
    r"\b(last\s+(?:time|week|session|night|run|sprint)"
    r"|earlier\s+(?:today|this\s+week)"
    r"|yesterday"
    r"|that\s+time\s+we"
    r"|when\s+(?:did|we)\s+"
    r"|previously"
    r"|recently"
    r"|a\s+while\s+ago"
    r"|(?:a\s+)?few\s+days\s+ago"
    r"|back\s+when"
    r"|used\s+to"
    r"|before\s+we)\b",
    re.IGNORECASE,
)

# Explicit hint patterns (spec section 1.1.4)
_EXPLICIT_RE = re.compile(
    r"\b(remind\s+me"
    r"|(?:do\s+you\s+)?remember"
    r"|do\s+(?:we|you)\s+(?:have|know)"
    r"|did\s+(?:we|you)(?:\s+ever)?"
    r"|what\s+(?:did|was|were)\s+(?:we|the)"
    r"|history\s+of"
    r"|prior\s+to"
    r"|earlier\s+we"
    r"|have\s+we\s+ever"
    r"|recall"
    r"|was\s+there"
    r"|were\s+there"
    r"|check\s+(?:if|whether))\b",
    re.IGNORECASE,
)

# Stopwords — common capitalized words that are NOT entities
_STOPWORDS = {
    "The",
    "This",
    "That",
    "These",
    "Those",
    "What",
    "When",
    "Where",
    "Which",
    "Who",
    "How",
    "Why",
    "Yes",
    "No",
    "True",
    "False",
    "None",
    "And",
    "But",
    "Or",
    "Not",
    "Also",
    "Just",
    "Only",
    "Can",
    "Could",
    "Would",
    "Should",
    "Will",
    "May",
    "Must",
    "Let",
    "Here",
    "There",
    "Now",
    "Then",
    # Path / orchestration noise — capitalized non-entities common in JP's
    # prompts and pasted tool output (e.g. ~/Projects paths, "run in the
    # Background"). These previously mis-fired auto-query on pure noise.
    "Projects",
    "Background",
    "Wait",
    "Monitor",
    "Poll",
    "Watch",
    "Build",
    "Agent",
    "Agents",
    "Phase",
    "Please",
    "Ran",
    "Research",
    "Robust",
    "Task",
    "Clean",
}

# Alias map: JP-vocabulary → canonical wing. An alias only fires if its
# target wing is actually present in known_wings.
_WING_ALIASES = {
    "mempalace": "memorypalace",
    "memory palace": "memorypalace",
}

# Common English words that are also wing names — never lowercase-match
# these, or they fire on ordinary prose ("in general", "the projects list").
_WING_MATCH_BLOCKLIST = frozenset(
    {
        "general",
        "projects",
        "sessions",
        "update",
        "test",
        "tools",
        "watch",
        "claude",
        "oracle",
    }
)

# Minimum length for a lowercase wing/alias surface form to be matchable.
# Below this, short names ("ha", "jp", "sdp") false-match inside words.
_MIN_LOWERCASE_WING_LEN = 5

# Max results per signal class
_MAX_ENTITY_SIGNALS = 5
_MAX_TEMPORAL_SIGNALS = 3


def extract_signals(
    text,  # type: str
    session_state,  # type: SessionState
    project_wing,  # type: str
    known_wings,  # type: set
    known_entities=None,  # type: Optional[set]
    has_recent_drawers=False,  # type: bool
):
    # type: (...) -> SignalSet
    """Extract auto-query signals from a user message.

    Pure function — no I/O, no MCP calls. All external state
    (wings, entities, drawer existence) is passed in by the caller.
    """
    entity_signals = _extract_entity_signals(text, session_state, known_wings, known_entities)
    temporal_signals = _extract_temporal_signals(text)
    resumption = _check_resumption(session_state, project_wing, known_wings, has_recent_drawers)
    explicit = _check_explicit(text)

    total = sum(s.score for s in entity_signals) + sum(s.score for s in temporal_signals)
    if resumption:
        total += 4
    if explicit:
        total += 5

    # Compound bonus: entity + temporal together (spec section 1.1.2)
    if entity_signals and temporal_signals:
        total += 1

    # Periodic depth refresh — auto-fire regardless of message content to
    # counteract mid-context attention degradation ("lost in the middle").
    # Fires on turn 1 (a recall floor for the very short sessions that
    # dominate real usage — many are a single turn) and every 10th turn
    # thereafter. turn_index > 0 guards the turn-0 sentinel so it never fires
    # before the session has a real turn.
    depth_fire = session_state.turn_index > 0 and (
        session_state.turn_index == 1 or session_state.turn_index % 10 == 0
    )
    if depth_fire:
        total += 4  # enough to fire in balanced mode (threshold 4)

    return SignalSet(
        entity=entity_signals,
        temporal=temporal_signals,
        resumption=resumption,
        explicit=explicit,
        total_score=total,
        project_wing=project_wing,
        query_text=text,
        depth_fire=depth_fire,
    )


def _extract_entity_signals(
    text,  # type: str
    session_state,  # type: SessionState
    known_wings,  # type: set
    known_entities,  # type: Optional[set]
):
    # type: (...) -> List[Signal]
    """Extract entity signals from capitalized noun phrases.

    Uses the same regex as searcher.py:1191 (_ENTITY_REGEX).
    Filters stopwords, deduplicates against session_state.queried_entities,
    and scores by wing/entity match.
    """
    signals = []  # type: List[Signal]
    seen = set()  # type: Set[str]

    # Pass 1: capitalized noun phrases via regex
    for m in _ENTITY_REGEX.finditer(text):
        token = m.group(1).strip()
        if len(token) < 3:
            continue
        if token in seen:
            continue
        # Stopword check — compare first word for multi-word phrases too
        first_word = token.split()[0]
        if first_word in _STOPWORDS:
            continue
        if token in session_state.queried_entities:
            continue
        seen.add(token)

        signal = _score_entity(token, known_wings, known_entities)
        if signal.wing:
            seen.add(signal.wing)
        signals.append(signal)

    # Pass 1.5: lowercase wing / alias match. JP types wing names in
    # lowercase ("candela", "mempalace", "familiar"), which the capital-only
    # regex above never sees. Match known wings and a small alias map against
    # the lowercased text at word boundaries, with a min length and a
    # common-word blocklist to keep precision high.
    q_lower = text.lower()
    for surface, wing in _wing_surface_forms(known_wings):
        if len(surface) < _MIN_LOWERCASE_WING_LEN:
            continue
        if surface in _WING_MATCH_BLOCKLIST:
            continue
        if wing in seen:
            continue
        if wing in session_state.queried_entities:
            continue
        if _word_present(surface, q_lower):
            seen.add(wing)
            signals.append(Signal(kind="entity", name=surface, score=3, wing=wing))

    # Pass 2: lowercase substring match against known entities
    # (same pattern as _ner_from_query in searcher.py:1218-1225)
    if known_entities:
        q_lower = text.lower()
        for ent in sorted(known_entities):  # sorted for determinism
            if ent and ent.lower() in q_lower and ent not in seen:
                if ent in session_state.queried_entities:
                    continue
                seen.add(ent)
                signal = _score_entity(ent, known_wings, known_entities)
                signals.append(signal)

    return signals[:_MAX_ENTITY_SIGNALS]


def _score_entity(
    name,  # type: str
    known_wings,  # type: set
    known_entities,  # type: Optional[set]
):
    # type: (...) -> Signal
    """Score an entity candidate against known wings and entities."""
    # Check wing match: try common slug patterns
    wing_slugs = _entity_to_wing_slugs(name)
    for slug in wing_slugs:
        if slug in known_wings:
            return Signal(kind="entity", name=name, score=3, wing=slug)

    # Check known entity match
    if known_entities and name in known_entities:
        return Signal(kind="entity", name=name, score=2)

    # Unknown entity — score 1. A bare capitalized name is a weak-but-real
    # recall signal: two unknown entities reach the aggressive threshold (2),
    # four reach balanced (4). (Was 0, which made unknown names invisible.)
    return Signal(kind="entity", name=name, score=1)


def _entity_to_wing_slugs(name):
    # type: (str) -> List[str]
    """Generate candidate wing slugs from an entity name.

    Tries common patterns: wing_<lower>, wing_<lower_underscored>,
    and the raw lowercase name.
    """
    lower = name.lower()
    underscored = lower.replace(" ", "_").replace("-", "_")
    return [
        "wing_{}".format(underscored),
        "wing_{}".format(lower.replace(" ", "")),
        underscored,
        lower,
    ]


def _wing_surface_forms(known_wings):
    # type: (set) -> list
    """Yield (surface_form, canonical_wing) pairs for lowercase matching.

    For each wing: the lowercased wing name and an underscores-as-spaces
    form; plus any alias whose target wing is present in known_wings.
    """
    forms = []  # type: list
    for wing in known_wings:
        wl = wing.lower()
        forms.append((wl, wing))
        spaced = wl.replace("_", " ")
        if spaced != wl:
            forms.append((spaced, wing))
    for alias, wing in _WING_ALIASES.items():
        if wing in known_wings:
            forms.append((alias, wing))
    return forms


def _word_present(needle, haystack_lower):
    # type: (str, str) -> bool
    """True if needle appears in haystack at word boundaries (both lowercased)."""
    return re.search(r"\b{}\b".format(re.escape(needle)), haystack_lower) is not None


def _extract_temporal_signals(text):
    # type: (str) -> List[Signal]
    """Extract temporal signals from time-reference phrases.

    Uses _TEMPORAL_RE to match phrases like "last time", "yesterday",
    "when did we", etc. Returns up to _MAX_TEMPORAL_SIGNALS matches.
    """
    signals = []  # type: List[Signal]
    seen_phrases = set()  # type: Set[str]

    for m in _TEMPORAL_RE.finditer(text):
        phrase = m.group().strip()
        if phrase.lower() in seen_phrases:
            continue
        seen_phrases.add(phrase.lower())
        signals.append(Signal(kind="temporal", name=phrase, score=2, phrase=phrase))

    return signals[:_MAX_TEMPORAL_SIGNALS]


def _check_resumption(
    session_state,  # type: SessionState
    project_wing,  # type: str
    known_wings,  # type: set
    has_recent_drawers,  # type: bool
):
    # type: (...) -> bool
    """Check for task-resumption signal.

    Returns True only when ALL conditions hold:
    - turn_index == 1 (first user message in session)
    - project_wing is in known_wings
    - has_recent_drawers is True (wing has drawers filed in last 7 days)
    """
    return session_state.turn_index == 1 and project_wing in known_wings and has_recent_drawers


def _check_explicit(text):
    # type: (str) -> bool
    """Check for explicit memory-request hints.

    Returns True if the text contains an explicit hint phrase
    AND a question mark — the user is effectively asking for
    memory recall without phrasing it as a tool call.
    """
    return bool(_EXPLICIT_RE.search(text)) and "?" in text
