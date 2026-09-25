"""
Section matcher — loads the steel_sections table FROM SUPABASE
(not a local JSON file) so the API service always matches against
the same reference data the rest of the app uses.

TWO ENTRY POINTS, ONE MATCHING ALGORITHM:

    match(raw_name)     the original contract, unchanged: the matched
                        catalogue row, or None.
    resolve(raw_name)   additive: a frozen SectionMatch recording WHICH route
                        produced that row — an exact hit on the requested
                        token, or the ".0"..".9" suffix fallback — while
                        preserving the requested drawing token.

Both are thin wrappers over the single private primitive _match_candidate(),
which owns the candidate generation. The fallback therefore exists in exactly
one place, and the two entry points cannot drift apart.

WHY THE ROUTE MATTERS: the fallback is name RESOLUTION, never identity. A bare
drawing token such as "310UB40" whose own row is absent from the reference
table resolves to a DIFFERENT section ("310UB40.4"). `match()` reports only
the row, so that substitution is invisible to its callers. `resolve()` reports
the route as well, which is what a caller needs in order to treat a
substituted row differently from an exact one.

`resolve()` makes no decision and asserts no authority: it reports what the
existing matching algorithm did. Which source is authoritative for a section's
geometry is a separate question, deliberately not answered here.

REFERENCE-DATA PROVENANCE (see ReferenceIdentity, reference_data_digest and
reference_data_projection — the last being the one conversion of an identity
into the form a caller persists): a
matcher also records WHICH reference dataset it consulted, and whether that
dataset is formally versioned. The live steel_sections table is LIVE and
UNVERSIONED — it carries no declared catalogue version — so a SectionMatcher
states exactly that, together with a digest of the rows it actually loaded.

TWO QUESTIONS, KEPT APART, NEITHER ANSWERING THE OTHER:

  A. MATCHER / REFERENCE-DATA provenance — which dataset did this matcher
     consult? That is what this module records, and it is recorded for every
     matcher, whether or not any token resolved.
  B. SECTION / MEMBER provenance — which catalogue row supplied a member's
     standard properties? That is decided downstream, and for a refused
     suffix-fallback row the answer is "none": the offered row was refused,
     so it never supplied anything. A does not prove B.

THE PROVENANCE NEVER PARTICIPATES IN SECTION AUTHORITY. It cannot promote a
NONE or a SUFFIX_FALLBACK to EXACT, cannot authorise a catalogue row, and
cannot change an identity, a geometry, a weight or a review status. Nothing in
the matching path reads it — the matching algorithm alone decides what a token
means, exactly as before, and the provenance only describes the source it read
from.

A CONTENT DIGEST IS NOT A VERSION. The digest records the exact rows this
matcher observed; it is not an approved catalogue version and is never
presented as one. The live source is UNVERSIONED, and saying so is the honest
answer — see ReferenceIdentity.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from app.supabase_client import supabase

__all__ = [
    "RESOLUTION_EXACT",
    "RESOLUTION_SUFFIX_FALLBACK",
    "RESOLUTION_NONE",
    "RESOLUTIONS",
    "SOURCE_LIVE_SUPABASE",
    "SOURCE_KINDS",
    "IDENTITY_UNVERSIONED",
    "IDENTITY_STATUSES",
    "ReferenceIdentity",
    "reference_data_digest",
    "reference_data_projection",
    "SectionMatch",
    "SectionMatcher",
]

# The three resolution outcomes, as plain module constants — the vocabulary
# convention already used across this codebase (see source_of_truth.py's
# VERDICT_* / RESOLUTION_* and catalogue_conflict_resolution.py's
# RESULTING_* sets). There is no ranking between them and no default: every
# SectionMatch carries exactly one, and an unrecognised value cannot be
# constructed.
RESOLUTION_EXACT = "EXACT"                      # the requested token IS a catalogue key
RESOLUTION_SUFFIX_FALLBACK = "SUFFIX_FALLBACK"  # a ".0"..".9" extension of it is
RESOLUTION_NONE = "NONE"                        # nothing resolved
RESOLUTIONS = (RESOLUTION_EXACT, RESOLUTION_SUFFIX_FALLBACK, RESOLUTION_NONE)

# ---------------------------------------------------------------------------
# Reference-data provenance vocabulary — a closed set of plain constants, the
# same convention as RESOLUTIONS above: no ranking, no default, and an
# unrecognised value cannot be constructed.
# ---------------------------------------------------------------------------

# Where a matcher's rows were read from. Only the live table exists for this
# matcher; a local, in-process catalogue is a different source and belongs to
# the module that owns it, not to this list.
SOURCE_LIVE_SUPABASE = "LIVE_SUPABASE"
SOURCE_KINDS = (SOURCE_LIVE_SUPABASE,)

# The FORMAL status of a dataset — whether a catalogue version has actually
# been declared for it. The live steel_sections table has not: no formal
# catalogue version exists for it, and no content hash makes one exist. A
# declared version, when one is ever real, is a NEW status added here together
# with the field that carries it — never a value smuggled into this one, and
# never a string that merely looks like a version.
IDENTITY_UNVERSIONED = "UNVERSIONED"
IDENTITY_STATUSES = (IDENTITY_UNVERSIONED,)

# The reference table this matcher reads. Named once, so the query and the
# digest's domain separator cannot drift apart.
_REFERENCE_TABLE = "steel_sections"


def _is_sha256_hex(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _canonical_scalar(value):
    """
    Render one non-JSON value deterministically, or refuse it.

    The reference table is read through a JSON API, so only JSON values
    (str/int/float/bool/None/list/dict) can arrive from it. The two types below
    are accepted because they have a canonical textual form and are the ones a
    caller supplying rows in-process is most likely to use. Anything else is
    REFUSED rather than stringified: str() on an arbitrary object can embed an
    address or an insertion order, which would make the digest non-reproducible
    — and a provenance digest that cannot be reproduced is worse than none.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(
        f"the reference-data digest cannot canonicalise a {type(value).__name__} value; "
        "the reference table is read as JSON, so only JSON-representable values are "
        "expected. An unexpected type means the source changed shape, and no digest may "
        "be invented for it."
    )


def reference_data_digest(rows) -> str:
    """
    A deterministic sha256 digest of the reference rows a matcher loaded.

    WHAT IT MEANS: "these are the exact rows this matcher observed." It is a
    content fingerprint of the data actually served — NOT a catalogue version,
    NOT an approval, and NOT evidence that anything was reviewed. The live
    source is unversioned, and this digest does not change that.

    THE CANONICALISATION RULE, in full:

      * each row is serialised as JSON with its keys sorted, compact
        separators and unicode preserved, so key order inside a row and
        non-ASCII text both round-trip unchanged;
      * every value is preserved exactly as loaded — numeric fields are NOT
        rounded, NOT reformatted, and NOT converted (a float stays a float,
        null stays null), and no field is dropped or added;
      * the per-row texts are then SORTED before hashing, so the database's
        row order cannot affect the result — the same rows in any order give
        the same digest;
      * a row that appears twice contributes twice, so collapsing or
        duplicating rows changes the digest;
      * nothing about the runtime enters the hash: no timestamp, no client,
        no credential, no URL, no path, no host, and no dependency version.
        The only input is the rows themselves, under the table name above.

    Consequently, changing any meaningful value in any row changes the digest,
    and re-reading unchanged rows reproduces it exactly.
    """
    canonical = sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   default=_canonical_scalar)
        for row in rows
    )
    payload = _REFERENCE_TABLE + "\n" + "\n".join(canonical)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReferenceIdentity:
    """
    WHICH reference dataset a matcher consulted, and whether that dataset is
    formally versioned. Nothing here identifies a member's section, and nothing
    here may be used to decide one.

    This is QUESTION A (reference-data provenance), not QUESTION B (which
    catalogue row supplied a member's properties). For an exact match the two
    coincide and a later boundary may legitimately say "these properties came
    from dataset X". For a refused suffix-fallback row they do NOT: the offered
    row was refused, so this object describes the source that was consulted and
    makes no claim at all about what supplied the member.

    `source_kind`  where the rows were read from — SOURCE_LIVE_SUPABASE today.

    `identity_status`  the FORMAL status of that dataset, and the reason this
        object exists. The live table is IDENTITY_UNVERSIONED: no catalogue
        version has been declared for it. That is a complete, honest statement
        about the source. It is NOT "nothing is known" and NOT a defect — the
        absence of provenance is the absence of THIS OBJECT (a matcher that
        consulted no reference data exposes none), never a value inside it.
        This field is what keeps "no provenance exists" and "provenance exists
        but the source is formally unversioned" from being the same empty
        value; collapsing them is precisely the confusion this type prevents.

    `reference_data_digest`  the digest of the exact rows observed — see
        reference_data_digest for what it means and how it is computed. It is
        a content fingerprint and NOT a version: it is deliberately kept
        separate from `identity_status`, so that observing stable content can
        never be mistaken for holding a declared version. None means no digest
        was recorded, which is again not the same as UNVERSIONED.

    Frozen, and with no methods: a value to be read, never something that can
    be called to change a decision.
    """
    source_kind: str
    identity_status: str
    reference_data_digest: str | None = None

    def __post_init__(self):
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(
                f"source_kind must be exactly one of {list(SOURCE_KINDS)} "
                f"(got {self.source_kind!r}); there is no default source."
            )
        if self.identity_status not in IDENTITY_STATUSES:
            raise ValueError(
                f"identity_status must be exactly one of {list(IDENTITY_STATUSES)} "
                f"(got {self.identity_status!r}). A declared catalogue version is a new "
                "status added to IDENTITY_STATUSES together with the field that carries "
                "it — it is never a value smuggled into an existing status, and never a "
                "digest standing in for a version."
            )
        if self.reference_data_digest is not None and not _is_sha256_hex(self.reference_data_digest):
            raise ValueError(
                "reference_data_digest must be a lowercase sha256 hex digest "
                f"(got {self.reference_data_digest!r})."
            )


def reference_data_projection(identity) -> dict | None:
    """
    The serialisable, persistable form of a matcher's reference identity — or
    None when there is no identity to record.

        ReferenceIdentity  ->  {"source_kind": ..., "identity_status": ...,
                                "reference_data_digest": ...}
        None               ->  None

    THIS IS THE ONLY CONVERSION. The caller reads `matcher.reference_identity`
    (the read-only property) once, next to the resolution it is recording, and
    stores the result verbatim. Nothing downstream reconstructs an identity from
    a digest string, recomputes a digest, or re-derives one from a later read of
    the catalogue: a persisted digest is a statement about the rows the matcher
    ACTUALLY loaded, and a catalogue can change after the fact.

    None means NO IDENTITY WAS RECORDED. A recorded identity is a dict carrying
    the keys above, so the two are never the same persisted value — which is the
    whole reason the keys travel together rather than as a bare digest.

    The returned dict is DETACHED: freshly built on every call, so a caller
    mutating it cannot reach the matcher's frozen identity, and the identity
    object's own state is never exposed.

    The values are re-validated by constructing a ReferenceIdentity from exactly
    the three named attributes, so this reuses the type's own validation rather
    than restating it: an unknown source_kind, an unknown identity_status or a
    malformed digest RAISES instead of being persisted. Silently dropping such a
    value would report "no provenance" for a row that has some; silently
    accepting it would put a value in the member row that this module does not
    understand. There are no other keys and no defaults — a missing attribute
    becomes None and is refused by the same validation.

    This is a pure function of its argument: it never queries Supabase, never
    touches the digest algorithm, and is never consulted when resolving a token.
    It describes the SOURCE, not any member's section.
    """
    if identity is None:
        return None
    confirmed = ReferenceIdentity(
        source_kind=getattr(identity, "source_kind", None),
        identity_status=getattr(identity, "identity_status", None),
        reference_data_digest=getattr(identity, "reference_data_digest", None),
    )
    return {
        "source_kind": confirmed.source_kind,
        "identity_status": confirmed.identity_status,
        "reference_data_digest": confirmed.reference_data_digest,
    }


@dataclass(frozen=True)
class SectionMatch:
    """
    The frozen outcome of one resolution attempt.

    `drawing_token` is the requested token in the matcher's own canonical
    form — the same whitespace-collapsing, case-folding normalisation
    `match()` already applies to build its keys. That normalisation carries
    no section meaning and never changes which section is meant, so the token
    is preserved: it is NEVER replaced by the matched catalogue row's name.
    `drawing_token_raw` additionally keeps the exact input string, so nothing
    the caller passed is lost.

    `catalogue_row` is a detached copy of the matched catalogue row, or None.
    A copy, so a caller mutating it can never corrupt the matcher's index.

    `resolution` is exactly one of RESOLUTIONS.

    ENFORCED INVARIANT: `resolution == RESOLUTION_NONE` if and only if
    `catalogue_row is None`. A "NONE that still carries a row" and an "EXACT
    with no row" are both unconstructable — the two fields are never
    independent.
    """
    drawing_token: str
    drawing_token_raw: str
    catalogue_row: dict | None
    resolution: str

    def __post_init__(self):
        if self.resolution not in RESOLUTIONS:
            raise ValueError(
                f"resolution must be exactly one of {list(RESOLUTIONS)} "
                f"(got {self.resolution!r}); there is no default resolution."
            )
        if (self.catalogue_row is None) != (self.resolution == RESOLUTION_NONE):
            raise ValueError(
                f"a SectionMatch with resolution {self.resolution!r} must "
                f"{'not ' if self.resolution == RESOLUTION_NONE else ''}carry a "
                "catalogue row; the outcome and the row are never independent."
            )
        if self.catalogue_row is not None:
            object.__setattr__(self, "catalogue_row", dict(self.catalogue_row))


class SectionMatcher:
    def __init__(self):
        result = supabase.table(_REFERENCE_TABLE).select("*").execute()
        self._lookup = {self._normalise(row["name"]): row for row in result.data}
        # The reference-data provenance for this matcher, computed once from the
        # rows just loaded. It is a snapshot: it records what this matcher
        # observed, and the matching methods below never read it back.
        self._reference_identity = ReferenceIdentity(
            source_kind=SOURCE_LIVE_SUPABASE,
            identity_status=IDENTITY_UNVERSIONED,
            reference_data_digest=reference_data_digest(result.data),
        )

    @property
    def reference_identity(self) -> ReferenceIdentity:
        """
        QUESTION A: which reference dataset this matcher consulted, and whether
        it is formally versioned. For the live table that is always
        LIVE_SUPABASE / UNVERSIONED plus the digest of the rows loaded.

        Read-only (a property with no setter), so the binding cannot be
        replaced on a live matcher either. It describes the SOURCE: it is not
        a statement about any member, and no code path consults it when
        resolving a token.
        """
        return self._reference_identity

    @property
    def catalogue_version(self) -> None:
        """
        DECLARED ABSENCE, stated explicitly.

        The live steel_sections table is UNVERSIONED, so this matcher declares
        no catalogue version and this returns None — exactly what
        getattr(matcher, "catalogue_version", None) has always produced for it,
        now declared rather than merely missing.

        None here means "no formal version is declared", NOT "nothing is
        known": what is known is on `reference_identity` (LIVE_SUPABASE /
        UNVERSIONED, plus the digest of the observed rows). The digest is not a
        version and is never returned here — a content hash does not make a
        catalogue versioned.
        """
        return None

    def _normalise(self, name: str) -> str:
        return re.sub(r"\s+", "", name.strip().upper())

    def _match_candidate(self, raw_name: str):
        """
        THE matching primitive — the single source of truth for both entry
        points. Returns (candidate_index, row), or (None, None) when nothing
        resolves.

        Candidate 0 is the requested token itself. Only when that token
        contains no "." are the ".0"..".9" extensions of it appended, in that
        order. A match at index 0 is therefore an exact hit; a match at any
        later index came from the suffix extension. The caller never has to
        recompute which candidate matched — that is exactly what the returned
        index IS.
        """
        base = self._normalise(raw_name)
        candidates = [base]
        if "." not in base:
            candidates += [base + f".{d}" for d in range(10)]
        for index, candidate in enumerate(candidates):
            if candidate in self._lookup:
                return index, self._lookup[candidate]
        return None, None

    def match(self, raw_name: str):
        """
        The ORIGINAL contract, unchanged: the matched catalogue row as stored
        in the lookup index (or None when nothing resolves). Callers see
        exactly what they saw before — the same object, in the same
        situations, with the same fallback behind it.
        """
        _, row = self._match_candidate(raw_name)
        return row

    def resolve(self, raw_name: str) -> SectionMatch:
        """
        ADDITIVE: the same row `match()` returns, plus the route that produced
        it and the requested drawing token, as a frozen SectionMatch.

        Reports only what the existing matching algorithm did — it decides
        nothing and changes nothing. Existing callers of `match()` need no
        change and are unaffected.
        """
        index, row = self._match_candidate(raw_name)
        if index is None:
            resolution = RESOLUTION_NONE
        elif index == 0:
            resolution = RESOLUTION_EXACT
        else:
            resolution = RESOLUTION_SUFFIX_FALLBACK
        return SectionMatch(
            drawing_token=self._normalise(raw_name),
            drawing_token_raw=raw_name,
            catalogue_row=row,
            resolution=resolution,
        )

    def get_section_regex(self) -> re.Pattern:
        patterns = [
            r"\d{3}UB\d+\.?\d*", r"\d{3}UC\d+\.?\d*", r"\d{2,3}PFC",
            r"\d+x\d+x\d+\.?\d*(?:RHS|SHS|EA|UA)", r"\d+\.?\d*x\d+\.?\d*CHS", r"\d+x\d+FL",
        ]
        return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
