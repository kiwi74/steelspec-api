"""
CONNECTION TYPE ACCOUNTING — what the connection writer did to a drawn value.

`connections.connection_type` is a Postgres enum with four members (bolted,
welded, bolted_and_welded, unspecified). A drawing does not speak in those four
words: it says "bracket", "screwed", "bolted - stringer to block wall". Until
this module existed, `app/pipeline.py::_persist_connections` replaced every
value outside that vocabulary with an inference from the connection's own bolt
and weld readings — and said nothing about it. The persisted row then read
`connection_type = 'unspecified'` with `warnings = []`, which is exactly the
row a drawing that stated NOTHING produces. A later reader of that row could
not tell "the drawing did not say" from "the drawing said something this system
does not recognise".

This module is the vocabulary for saying which it was. Its rule is the same one
the DXF drop accounting follows (app/validation/dxf_drop_accounting.py): a
substitution that discards something the drawing stated must be REASONED and
SURVIVED onto the record, never silently absorbed.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not change the substitution. The enum is the type authority and is not
extended; the drawn token is never written into `connection_type`; the fallback
inference (bolts+welds -> bolted_and_welded, bolts -> bolted, welds -> welded,
neither -> unspecified) is untouched. The drawn value's own home is the page
extraction capture, and the review layer already reads it from there verbatim.
What was missing was a note ON THE ROW that the replacement happened.

AN ABSENT SOURCE IS NOT AN UNRECOGNISED ONE
-------------------------------------------
`unspecified` legitimately means two things already: "the drawing stated
unspecified" and "the drawing stated nothing at all". Only the third case — a
value that was STATED and is outside the vocabulary — is accounted here. A
missing or blank source is never reported as an unrecognised token; conflating
that with a bad token would be repeating, in the warning, the very ambiguity the
column already has.

WHY THE PERSISTED FORM IS ONE LINE PER CONNECTION
-------------------------------------------------
The substitute for the DXF path is aggregated per reason because a real drawing
can discard hundreds of items. A connection is one row with one type: there is
nothing to aggregate, so the line is written verbatim onto the row it accounts
for, and only when there is something to say. A row written from a recognised
type carries no accounting at all — absence of a line means nothing was
replaced, never that nothing was checked.
"""
from __future__ import annotations

from types import MappingProxyType

# --------------------------------------------------------------------------
# The recognised vocabulary — the enum's own four members, and nothing else.
# One definition: app/pipeline.py normalises against THIS set, so the writer
# that substitutes and the accounting that reports the substitution cannot
# drift apart.
# --------------------------------------------------------------------------
RECOGNISED_CONNECTION_TYPES = frozenset({
    "bolted", "welded", "bolted_and_welded", "unspecified",
})

# The value the existing logic lands on when the source states nothing.
# Named so the accounting can tell "absent" from "unrecognised" without
# restating the literal.
DEFAULT_CONNECTION_TYPE = "unspecified"

# --------------------------------------------------------------------------
# REASON — why. Stable, machine-readable, and never reworded: this string is
# persisted and is what a test or a later reader matches on.
# --------------------------------------------------------------------------
# A source value that was STATED and is not one of the recognised types. The
# row is still written, with the existing fallback inference; this only makes
# the replacement visible.
REASON_UNRECOGNISED_CONNECTION_TYPE = "UNRECOGNISED_CONNECTION_TYPE"

# What the reason is told to the reader. Factual, in the same voice
# app/validation/rules.py uses for its notes: it says what happened, and it
# does not call a substituted token an error.
REASON_PHRASE = MappingProxyType({
    REASON_UNRECOGNISED_CONNECTION_TYPE: "Connection type not recognised",
})

# Every persisted connection accounting line begins with this, so a later
# reader can find the accounting among a row's warnings by it. Defined once,
# here, in the same shape as the DXF reader's own prefix.
WARNING_PREFIX = "Connection type accounting: "


def normalise_source_token(source) -> str:
    """
    The written value a source connection type normalises to — the SAME
    expression the writer has always applied, character for character
    (`(value or "unspecified").lower().replace(" ", "_")`, extracted here so
    there is one definition rather than two). It says what the token WOULD be
    called; it decides nothing about whether the token is recognised.
    """
    return (source or DEFAULT_CONNECTION_TYPE).lower().replace(" ", "_")


def unrecognised_source_token(source) -> str | None:
    """
    The drawn token, verbatim, when it was stated and is outside the recognised
    vocabulary; None otherwise.

    None covers both of the cases that are NOT this accounting's business: a
    source that is absent or blank (the writer's own 'unspecified' default,
    which is not a bad token), and a source that IS recognised.
    """
    if not isinstance(source, str) or not source.strip():
        return None
    if normalise_source_token(source) in RECOGNISED_CONNECTION_TYPES:
        return None
    return source.strip()


def warning_line(source_token: str, persisted_value: str) -> str:
    """
    The line persisted onto the connection row.

    It names the drawn token VERBATIM (never a cleaned-up paraphrase of it),
    what it was normalised to, and the vocabulary it was measured against — so
    the row explains itself without the reader having to find the capture.
    """
    return (
        f"{WARNING_PREFIX}unrecognised connection type {source_token!r} was "
        f"normalised to {persisted_value!r}; recognised types are "
        f"{', '.join(sorted(RECOGNISED_CONNECTION_TYPES))}."
    )


def warning_for_unrecognised(source, persisted_value: str) -> str | None:
    """
    The accounting line for one connection, or None when there is nothing to
    account for (a recognised type, or an absent one).
    """
    token = unrecognised_source_token(source)
    if token is None:
        return None
    return warning_line(token, persisted_value)
