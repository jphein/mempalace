"""Predicate normalization for the AGE knowledge graph (issue #50).

The LLM triple extractor emits ~1000+ distinct ``relation_type`` strings.
Three classes of contamination bloat the predicate vocabulary far past the
underlying semantic relation count:

1. **Code tokens** treated as predicates (``appendchild``, ``createelement``,
   ``executemany``, ``setattribute``, ``getelementbyid``) — JS/Python API
   method names the extractor pulled out of source-code drawers. These should
   be dropped.
2. **Near-synonyms** not collapsed (``is`` / ``is_a`` / ``is_an_instance_of``;
   ``was_a`` / ``is_a_kind_of``) — canonicalized to one relation type.
3. **Grammatical fragments** with negation/punctuation glued in
   (``don't_adapt``, ``aren't_merged``, ``'doesn't_appear'``) — apostrophes
   and quotes are stripped and the negation polarity is denormalized into a
   ``not_<base>`` form rather than left as an arbitrary contraction.

This is a **pure module** — no DB, no AGE imports, no network. The single
public entry point is :func:`normalize_predicate`, which returns the
canonical predicate string or ``None`` to signal "drop this triple". That
makes it trivially unit-testable and safe to run as a read-only dry-run pass
over the live vocabulary without touching the graph.

Wiring this into the write path is the daemon's choice and must be opt-in;
this module never mutates anything on its own.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = [
    "normalize_predicate",
    "CODE_TOKEN_BLOCKLIST",
    "SHELL_COMMAND_BLOCKLIST",
    "STOPWORD_BLOCKLIST",
    "SYNONYM_MAP",
    "NEGATION_PREFIXES",
]


# ─── Class 1: code-token blocklist ──────────────────────────────────────
# Method / DOM / DB-API names the extractor mistook for relations. These are
# camelCase or lowercase identifiers with no semantic relation meaning.
#
# The extractor emits these in mixed case (``appendChild`` *and*
# ``appendchild``). Folding splits camelCase into snake_case, so we match
# against the blocklist in BOTH the camel-split form (``append_child``) and
# the de-underscored form (``appendchild``) — see ``_is_code_token``. Entries
# below are therefore listed once in the bare-lowercase form (issue-observed)
# and the matcher handles the camel-split variant.
#
# Kept as an explicit blocklist rather than a heuristic because a pure
# "drop all single lowercase tokens" rule would also kill legitimate verbs
# like ``uses`` / ``owns``. The blocklist is conservative and additive.
CODE_TOKEN_BLOCKLIST: frozenset[str] = frozenset(
    {
        # DOM API (issue examples)
        "appendchild",
        "createelement",
        "setattribute",
        "getelementbyid",
        "getelementsbyclassname",
        "getelementsbytagname",
        "queryselector",
        "queryselectorall",
        "addeventlistener",
        "removeeventlistener",
        "removechild",
        "insertbefore",
        "createtextnode",
        "getattribute",
        "classlist",
        "innerhtml",
        "textcontent",
        # DB-API / ORM (issue examples)
        "executemany",
        "executescript",
        "fetchone",
        "fetchall",
        "fetchmany",
        "rowcount",
        "lastrowid",
        # generic stdlib / language method noise
        "tostring",
        "valueof",
        "hasownproperty",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
    }
)


# ─── Class 1b: shell-command blocklist ──────────────────────────────────
# Shell / CLI command names the extractor mistook for relations when mining
# terminal-transcript and code-walkthrough drawers. Unlike CODE_TOKEN_BLOCKLIST
# (camelCase API methods, caught partly by the digit heuristic), these are
# short all-lowercase words with no digit, so the heuristic can't see them —
# they have to be enumerated. Measured on the production AGE graph (issue #45),
# these accounted for ~tens of thousands of triples wrongly typed as the
# `other` long-tail bucket (``grep``=2750, ``cd``=2543, ``ls``=2020, …). They
# carry no entity→entity semantic relation and are dropped outright.
#
# Conservative on collisions with real verbs: ``run`` / ``set`` / ``add`` /
# ``push`` / ``commit`` / ``merge`` are NOT here because they double as
# legitimate relations (and are routed to canonicals via SYNONYM_MAP below).
SHELL_COMMAND_BLOCKLIST: frozenset[str] = frozenset(
    {
        "grep",
        "cd",
        "ls",
        "diff",
        "echo",
        "cat",
        "sed",
        "awk",
        "find",
        "mv",
        "cp",
        "rm",
        "mkdir",
        "rmdir",
        "touch",
        "chmod",
        "chown",
        "tail",
        "head",
        "sort",
        "uniq",
        "wc",
        "curl",
        "wget",
        "ssh",
        "scp",
        "sudo",
        "apt",
        "yum",
        "brew",
        "pip",
        "npm",
        "yarn",
        "tar",
        "gzip",
        "unzip",
        "ps",
        "kill",
        "top",
        "df",
        "du",
        "ln",
        "env",
        "export",
        "source",
        "alias",
        "pwd",
        "which",
        "man",
    }
)


# ─── Class 1c: content-free function-word blocklist ──────────────────────
# Modal / auxiliary / preposition fragments the extractor emits as standalone
# predicates (``can``, ``will``, ``should``, ``does``, ``for``, ``on``, …).
# They carry no relation by themselves; the LLM glued a bare grammatical word
# where a verb phrase belonged. On the production graph (issue #45) these were
# another large slice of the `other` bucket (``can``=3540, ``will``=1969,
# ``should``=1735, ``does``=1619, ``for``=1267, ``on``=1054, ``had``=1043).
#
# NB: ``is`` / ``are`` / ``was`` / ``has`` / ``have`` are deliberately NOT here
# — they are routed to ``is_a`` / ``contains`` via SYNONYM_MAP because they
# usually express a real copular/possessive relation. Only the truly empty
# auxiliaries and bare prepositions are dropped.
STOPWORD_BLOCKLIST: frozenset[str] = frozenset(
    {
        "can",
        "could",
        "will",
        "would",
        "shall",
        "may",
        "might",
        "must",
        "should",
        "do",
        "does",
        "did",
        "had",
        "for",
        "on",
        "of",
        "to",
        "from",
        "with",
        "by",
        "as",
        "at",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "it",
    }
)


# ─── Class 2: synonym → canonical map ────────────────────────────────────
# Conservative collapse. We merge only relations that are clearly the *same*
# semantic edge under surface-form / tense / article variation. We do NOT
# merge semantically distinct relations (``part_of`` stays separate from
# ``is_a``; ``created_by`` stays separate from ``owned_by``).
#
# Keys are post-fold (snake_case, lowercased, punctuation-stripped) forms.
# Values are the chosen canonical form.
SYNONYM_MAP: dict[str, str] = {
    # identity / instance-of family → is_a
    "is": "is_a",
    "is_an": "is_a",
    "are": "is_a",
    "was": "is_a",
    "was_a": "is_a",
    "was_an": "is_a",
    "were": "is_a",
    "is_a_kind_of": "is_a",
    "is_a_type_of": "is_a",
    "is_an_instance_of": "is_a",
    "is_instance_of": "is_a",
    "instance_of": "is_a",
    "a_kind_of": "is_a",
    "type_of": "is_a",
    "kind_of": "is_a",
    # composition family → part_of
    "is_part_of": "part_of",
    "is_a_part_of": "part_of",
    "a_part_of": "part_of",
    "belongs_to": "part_of",
    "member_of": "part_of",
    # reference family → references
    "is_a_reference": "references",
    "is_a_reference_to": "references",
    "is_reference_to": "references",
    "reference": "references",
    "refers_to": "references",
    "references_to": "references",
    # usage family → uses
    "use": "uses",
    "used": "uses",
    "uses_a": "uses",
    "makes_use_of": "uses",
    "utilizes": "uses",
    # dependency family → depends_on
    "depend_on": "depends_on",
    "depends_upon": "depends_on",
    "requires": "depends_on",
    "relies_on": "depends_on",
    # authorship family → created_by
    "authored_by": "created_by",
    "written_by": "created_by",
    "made_by": "created_by",
    "built_by": "created_by",
    # containment family → contains
    "contain": "contains",
    "includes": "contains",
    "has": "contains",
    "have": "contains",
    # work family → works_on
    "work_on": "works_on",
    "working_on": "works_on",
    "works_with": "works_on",
    # ── issue #45: high-frequency genuine-relation paraphrases that were
    # landing in the `other` bucket because the embedding nearest-canonical
    # gate (threshold 0.45) couldn't bind them. The synonym map is a
    # deterministic short-circuit that runs *before* the embedding scorer, so
    # these resolve exactly rather than scoring ~0.30-0.44 and falling to
    # `other`. Each family below was seeded from the production frequency
    # table of `other` raw_relation_types (see scripts + #45 diagnosis).
    # read family → reads
    "read": "reads",
    "get": "reads",
    "gets": "reads",
    "got": "reads",
    "fetch": "reads",
    "fetches": "reads",
    "fetched": "reads",
    "load": "reads",
    "loads": "reads",
    "loaded": "reads",
    "retrieves": "reads",
    "retrieve": "reads",
    # write family → writes
    "write": "writes",
    "wrote": "writes",
    "set": "writes",
    "sets": "writes",
    "is_set_to": "writes",
    "store": "writes",
    "stores": "writes",
    "stored": "writes",
    "save": "writes",
    "saves": "writes",
    "saved": "writes",
    "assigns_value": "writes",
    # modify family → modifies
    "modify": "modifies",
    "modified": "modifies",
    "update": "modifies",
    "updates": "modifies",
    "updated": "modifies",
    "has_been_updated": "modifies",
    "change": "modifies",
    "changes": "modifies",
    "changed": "modifies",
    "edit": "modifies",
    "edits": "modifies",
    "edited": "modifies",
    "fix": "modifies",
    "fixes": "modifies",
    "fixed": "modifies",
    "patches": "modifies",
    # add family → adds
    "add": "adds",
    "added": "adds",
    "append": "adds",
    "appends": "adds",
    "appended": "adds",
    "insert": "adds",
    "inserts": "adds",
    "inserted": "adds",
    "merge": "adds",
    "merges": "adds",
    "merged": "adds",
    # run family → runs
    "run": "runs",
    "ran": "runs",
    "execute": "runs",
    "executes": "runs",
    "executed": "runs",
    "start": "runs",
    "starts": "runs",
    "started": "runs",
    "launch": "runs",
    "launches": "runs",
    "launched": "runs",
    "invoke": "runs",
    "invokes": "runs",
    "invoked": "runs",
    # use family → uses (extends existing use/used/utilizes group)
    "take": "uses",
    "takes": "uses",
    "took": "uses",
    "accept": "uses",
    "accepts": "uses",
    "accepted": "uses",
    "consume": "uses",
    "consumes": "uses",
    "consumed": "uses",
    # send family → sends
    "send": "sends",
    "sent": "sends",
    "emit": "sends",
    "emits": "sends",
    "emitted": "sends",
    "fire": "sends",
    "fires": "sends",
    "fired": "sends",
    "dispatch": "sends",
    "dispatches": "sends",
    "dispatched": "sends",
    "publish": "sends",
    "publishes": "sends",
    "published": "sends",
    # provide family → provides
    "provide": "provides",
    "offer": "provides",
    "offers": "provides",
    "offered": "provides",
    "expose": "provides",
    "exposes": "provides",
    "exposed": "provides",
    "supply": "provides",
    "supplies": "provides",
    "supplied": "provides",
    # location family → located_at
    "is_in": "located_at",
    "located_in": "located_at",
    "lives_in": "located_at",
    "resides_in": "located_at",
    "stored_in": "located_at",
    "found_in": "located_at",
    "is_located_at": "located_at",
    "is_at": "located_at",
    # property family → has_property
    "has_method": "has_property",
    "has_methods": "has_property",
    "has_feature": "has_property",
    "has_features": "has_property",
    "has_version": "has_property",
    "has_status": "has_property",
    "has_id": "has_property",
    "has_field": "has_property",
    "has_fields": "has_property",
    "has_attribute": "has_property",
    "has_value": "has_property",
    "has_name": "has_property",
    "has_type": "has_property",
    "has_property": "has_property",
    # identity family → is_a (extends existing is/are/was group)
    "is_called": "is_a",
    "called": "is_a",
    "named": "is_a",
    "was_named": "is_a",
    "exists": "is_a",
    "exist": "is_a",
    "represents": "is_a",
    # creation family → creates
    "make": "creates",
    "makes": "creates",
    "made": "creates",
    "generate": "creates",
    "generates": "creates",
    "generated": "creates",
    "build": "creates",
    "builds": "creates",
    "produce": "creates",
    "produces": "creates",
    "produced": "creates",
    # description family → describes
    "describe": "describes",
    "described": "describes",
    "report": "describes",
    "reports": "describes",
    "reported": "describes",
    "say": "describes",
    "says": "describes",
    "said": "describes",
    "state": "describes",
    "states": "describes",
    "stated": "describes",
    "suggest": "describes",
    "suggests": "describes",
    "ask": "describes",
    "asks": "describes",
    "note": "describes",
    "notes": "describes",
    "explain": "describes",
    "explains": "describes",
    "indicates": "describes",
    "indicate": "describes",
    # completion family → completed
    "complete": "completed",
    "completes": "completed",
    "finish": "completed",
    "finishes": "completed",
    "finished": "completed",
    "done": "completed",
    "pass": "completed",
    "passes": "completed",
    "passed": "completed",
    "close": "completed",
    "closes": "completed",
    "closed": "completed",
    "resolve": "completed",
    "resolves": "completed",
    "resolved": "completed",
    # containment extensions → contains
    "cover": "contains",
    "covers": "contains",
    "covered": "contains",
    "wraps": "contains",
    "wrap": "contains",
    # check family → checks
    "check": "checks",
    "verify": "checks",
    "verifies": "checks",
    "verified": "checks",
    "validate": "checks",
    "validates": "checks",
    "validated": "checks",
    "test": "checks",
    "tests": "checks",
    "tested": "checks",
    "ensure": "checks",
    "ensures": "checks",
    # dependency extensions → depends_on
    "need": "depends_on",
    "needs": "depends_on",
    "needed": "depends_on",
    # handling family → handles
    "handle": "handles",
    "process": "handles",
    "processes": "handles",
    "processed": "handles",
    "manage": "handles",
    "manages": "handles",
    "managed": "handles",
    # failure family → failed
    "fail": "failed",
    "fails": "failed",
    "errors": "failed",
    "errored": "failed",
    "crashed": "failed",
    "broke": "failed",
    "broken": "failed",
}


# ─── Class 3: negation handling ──────────────────────────────────────────
# Contractions and negation words the extractor glues into the predicate.
# We strip the negation off, normalize the remaining base, and re-prefix
# with a uniform ``not_`` so polarity lives in a predictable facet rather
# than fanning out across ``dont_*`` / ``arent_*`` / ``doesnt_*`` variants.
#
# Order matters: longer/more-specific prefixes first so ``does_not_`` is not
# shadowed by ``not_``.
NEGATION_PREFIXES: tuple[str, ...] = (
    "does_not_",
    "do_not_",
    "did_not_",
    "is_not_",
    "are_not_",
    "was_not_",
    "were_not_",
    "has_not_",
    "have_not_",
    "had_not_",
    "can_not_",
    "could_not_",
    "should_not_",
    "would_not_",
    "will_not_",
    "doesnt_",
    "dont_",
    "didnt_",
    "isnt_",
    "arent_",
    "wasnt_",
    "werent_",
    "hasnt_",
    "havent_",
    "hadnt_",
    "cant_",
    "cannot_",
    "couldnt_",
    "shouldnt_",
    "wouldnt_",
    "wont_",
    "not_",
)


# Identifiers that are pure code noise even if not in the blocklist: a single
# lowercase run with an embedded digit or that is implausibly long for a verb
# phrase. Kept narrow on purpose — see _looks_like_code.
_DIGIT_RE = re.compile(r"\d")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_PUNCT_STRIP_RE = re.compile(r"[\"'`’‘“”]")
_NONWORD_RE = re.compile(r"[^a-z0-9_]+")
_MULTI_US_RE = re.compile(r"_{2,}")


def _fold(raw: str) -> str:
    """Lowercase + snake_case + strip punctuation, mirroring the extractor's
    ``_normalize_predicate`` but with apostrophe/quote stripping (class 3).

    Steps:
      * split camelCase boundaries so ``appendChild`` → ``append_child``
        and matches the blocklist's folded forms
      * strip surrounding/embedded quotes and apostrophes
      * lowercase, spaces/hyphens → underscore
      * drop any remaining non-word chars, collapse repeated underscores
    """
    s = _CAMEL_BOUNDARY_RE.sub("_", raw.strip())
    s = _PUNCT_STRIP_RE.sub("", s)
    s = s.lower().replace(" ", "_").replace("-", "_")
    s = _NONWORD_RE.sub("_", s)
    s = _MULTI_US_RE.sub("_", s)
    return s.strip("_")


def _looks_like_code(folded: str) -> bool:
    """Heuristic for code tokens beyond the explicit blocklist.

    Conservative: only flags single-token identifiers (no underscores) that
    also contain a digit (``utf8decode``, ``sha256hash``). A bare verb like
    ``uses`` has no digit and survives; a snake_case phrase like
    ``works_on`` has an underscore and survives.
    """
    if "_" in folded:
        return False
    return bool(_DIGIT_RE.search(folded))


def _is_code_token(folded: str) -> bool:
    """True if ``folded`` is a code token to drop.

    Checks the blocklist against both the camel-split form (``append_child``)
    and the de-underscored form (``appendchild``), since the extractor emits
    method names in mixed case and the fold normalizes camelCase to
    snake_case. Also applies the digit heuristic.
    """
    if folded in CODE_TOKEN_BLOCKLIST:
        return True
    if folded.replace("_", "") in CODE_TOKEN_BLOCKLIST:
        return True
    # Shell / CLI command names (issue #45) — single all-lowercase words with
    # no digit, so the heuristic below can't catch them; enumerated explicitly.
    if folded in SHELL_COMMAND_BLOCKLIST:
        return True
    return _looks_like_code(folded)


# Negation words that, standing alone (after the trailing underscore is
# folded away), carry no relation — e.g. a raw predicate of just ``not_``
# folds to ``not`` and must drop.
_BARE_NEGATION_WORDS: frozenset[str] = frozenset(p.rstrip("_") for p in NEGATION_PREFIXES)


def _strip_negation(folded: str) -> tuple[str, bool]:
    """Return (base, negated). Strips a leading negation prefix if present."""
    for prefix in NEGATION_PREFIXES:
        if folded.startswith(prefix) and len(folded) > len(prefix):
            return folded[len(prefix) :], True
    return folded, False


def _canonicalize(base: str) -> str:
    """Apply the synonym map (idempotent — canonical forms map to themselves
    or are absent, which is a no-op)."""
    return SYNONYM_MAP.get(base, base)


def normalize_predicate(raw: str) -> Optional[str]:
    """Normalize a raw extractor predicate to its canonical form.

    Returns the canonical predicate string, or ``None`` to signal that the
    triple should be dropped (code token, empty, or punctuation-only input).

    Pipeline:
      1. fold — lowercase, snake_case, strip quotes/apostrophes (class 3 prep)
      2. drop if empty or a known/heuristic code or shell token (class 1)
      3. drop if a content-free function word / modal (class 1c, issue #45)
      4. strip negation prefix, remember polarity (class 3)
      5. canonicalize the base via the synonym map (class 2)
      6. re-apply ``not_`` prefix if it was negated

    The negation prefix is applied *after* canonicalization so that
    ``doesn't_appear`` and ``does_not_appear`` both land on ``not_appear``,
    and a negated synonym (``is not a part of`` → base ``a_part_of`` →
    ``part_of``) collapses to ``not_part_of``.

    Note the base is whatever remains *after* the negation prefix is peeled:
    ``isn't_a`` strips ``isnt_`` leaving base ``a`` (not in ``SYNONYM_MAP``),
    so it yields ``not_a``, not ``not_is_a``.
    """
    if not isinstance(raw, str):
        return None

    folded = _fold(raw)
    if not folded:
        return None

    # A bare negation word with nothing attached (raw ``not_`` → folds to
    # ``not``) carries no relation.
    if folded in _BARE_NEGATION_WORDS:
        return None

    # Class 1: code / shell tokens are dropped outright (no negation/synonym
    # pass).
    if _is_code_token(folded):
        return None

    # Class 1c (issue #45): a bare content-free function word / modal /
    # preposition carries no relation on its own. Checked before negation so
    # that e.g. ``does`` drops but ``does_not_exist`` (a negation prefix +
    # base) still reaches the negation peel below. STOPWORD_BLOCKLIST is
    # disjoint from SYNONYM_MAP keys and canonical names, so this never eats a
    # legitimate relation.
    if folded in STOPWORD_BLOCKLIST:
        return None

    # Class 3: peel negation, normalize the base, then re-prefix.
    base, negated = _strip_negation(folded)
    if not base:
        # The whole token was a negation prefix (e.g. "not_"); nothing left.
        return None

    # A dropped base (code/shell token or a stopword hiding behind a negation)
    # drops the whole predicate too.
    if _is_code_token(base) or base in STOPWORD_BLOCKLIST:
        return None

    # Class 2: collapse synonyms on the base.
    canonical = _canonicalize(base)

    return f"not_{canonical}" if negated else canonical
