"""
Milestone 7C0 — PROVIDER FACT-CAPTURE RECORDING (frozen, human-supplied).

This module freezes the findings of the Jev fact-capture / discovery report as
deterministic, human-supplied provider facts and feeds them through the REAL
7B9 readiness gate. It is RECORDING ONLY.

The evidence boundary is explicit: the facts below were researched and
supplied by a human; SteelSpec's machinery did not discover them and never
pretends it did. This module can never fetch, refresh, re-evaluate or
supplement them — no network access, no web access, no credentials, no
environment reads, no filesystem access, no clock, no randomness. Every
construction produces byte-identical frozen records.

What this module is NOT: it is not an integration. There is no API client, no
adapter, no sending of any request, no provider call, no credential handling
and no decision about integrating any provider. The gate result is a recorded
determination only, and the 7B9 gate itself is unchanged and provider-neutral.

Where the report found no sufficient evidence, the requirement is recorded
exactly UNVERIFIED — which means only that evidence does not establish the
requirement. It is not a negative judgement about any provider, and this
module never converts it into one.
"""

from dataclasses import dataclass

from app.cad_engine.provider_readiness_gate import (
    CLASSIFICATION_BEFORE_ANY_REAL_CALL,
    CLASSIFICATION_BEFORE_PRODUCTION_USE,
    CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
    CLASSIFICATION_OPTIONAL_LATER,
    CLASSIFICATIONS,
    KIND_LOCAL_CONTROL,
    KIND_PROVIDER_FACT,
    KINDS,
    REQUIREMENT_CLASSIFICATIONS,
    REQUIREMENT_IDS,
    STATUS_SATISFIED,
    STATUS_UNVERIFIED,
    STATUSES,
    GateDetermination,
    RequirementRecord,
    evaluate_readiness,
)

PROVIDER_NAME = "Jev (TypeSafe AI)"
CONTROL_OWNER = "SteelSpec"

__all__ = [
    "PROVIDER_NAME", "CONTROL_OWNER",
    "CapturedFact", "ProviderFactCapture",
    "jev_fact_capture", "build_requirement_records", "evaluate_capture",
]


# --------------------------------------------------------------------------------------
# The recording layer: frozen plain data only. No methods, no I/O, no state.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CapturedFact:
    """One frozen finding from the human fact-capture report: a provider fact
    or a SteelSpec local control for exactly one 7B9 requirement.

    `source` is the real evidence reference from the report — empty exactly
    when the report found no sufficient evidence (an UNVERIFIED requirement
    must never acquire a fabricated source). `description` is the factual
    finding; `note` carries nuances the report kept separate (for example the
    telemetry clause, which is deliberately NOT collapsed into the
    no-training commitment)."""
    provider: str
    requirement_id: str
    kind: str
    classification: str
    status: str
    description: str
    source: str
    source_type: str
    note: str


@dataclass(frozen=True)
class ProviderFactCapture:
    """The complete frozen capture for one provider: the provider name and the
    facts in authored (inventory) order. Plain immutable data."""
    provider: str
    facts: tuple[CapturedFact, ...]


# --------------------------------------------------------------------------------------
# The frozen Jev capture — every word below is the human report's finding.
# --------------------------------------------------------------------------------------
def jev_fact_capture() -> ProviderFactCapture:
    """The complete, frozen Jev fact set from the human fact-capture report.
    Deterministic: the same 25 findings in the same order on every call."""
    facts = (
        # --- provider facts, BEFORE_ANY_REAL_CALL ---
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="DATA_RETENTION",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL,
            status=STATUS_SATISFIED,
            description=(
                "Retained 'for as long as reasonably necessary to provide you "
                "with the Services' with no fixed period; MCA s.10.3: provider "
                "under no obligation to retain and may delete Customer Data at "
                "its sole discretion; backups may retain confidential "
                "information; enterprise zero-data-retention documented on the "
                "official legal page."
            ),
            source=(
                "TypeSafe Privacy Policy (updated 2025-11-19) — "
                "https://typesafe.ai/legal/privacy-policy; Data Processing Addendum "
                "Schedule I p.8 — https://typesafe.ai/legal/data-processing; Master "
                "Customer Agreement s.10.3 — https://typesafe.ai/legal/mca"
            ),
            source_type="Official privacy + contractual documentation",
            note=(
                "Deletion SLA, backup purge timing, and whether deleted "
                "requests are actually deleted: UNVERIFIED."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="DATA_TRAINING_USE_POLICY",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL,
            status=STATUS_SATISFIED,
            description=(
                "No training on customer Input, on Customer Data without prior "
                "consent, or on customer requests or responses; abuse "
                "monitoring is documented (MCA s.4.1)."
            ),
            source=(
                "TypeSafe Privacy Policy — https://typesafe.ai/legal/privacy-policy "
                "('We will not train or fine tune any artificial intelligence "
                "or machine learning models on your prompts or other Input'); "
                "MCA s.4.1 — https://typesafe.ai/legal/mca; TypeSafe Models — "
                "https://docs.typesafe.ai/models.md ('Jev is not trained on customer "
                "requests or responses')"
            ),
            source_type="Official privacy + contractual documentation",
            note=(
                "SEPARATE nuance, kept apart from the no-training commitment: "
                "MCA s.4.3 defines Telemetry (logs, hashes, statistics, "
                "metrics, 'learnings') and permits processing it without "
                "restriction."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="SUBPROCESSORS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL,
            status=STATUS_SATISFIED,
            description=(
                "Subprocessors engaged under general authorization; advance "
                "notice and a 15-day objection window on reasonable privacy or "
                "security grounds; provider remains responsible for "
                "subprocessors."
            ),
            source=(
                "Data Processing Addendum s.3.1-s.3.2 — "
                "https://typesafe.ai/legal/data-processing (list published at "
                "https://trust.typesafe.ai/subprocessors)"
            ),
            source_type="Official contractual documentation",
            note=(
                "Live list page is client-rendered (names not captured from "
                "the official page); an independent review names AWS, Modal, "
                "Slack, Google Workspace — all US-based."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="PRIVACY_CONTRACTUAL_TERMS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL,
            status=STATUS_SATISFIED,
            description=(
                "Public MCA + DPA + Privacy Policy; customer retains Input IP "
                "and receives assigned Output rights; confidentiality s.14; "
                "audit right once per 12 months (DPA s.5.3); California law, "
                "San Francisco venue; SCCs + UK Addendum."
            ),
            source=(
                "Master Customer Agreement ss.4.2, 11, 14, 15, 16 — "
                "https://typesafe.ai/legal/mca; Data Processing Addendum ss.5, 6 — "
                "https://typesafe.ai/legal/data-processing"
            ),
            source_type="Official contractual documentation",
            note=(
                "Deletion/export rights weak (MCA s.10.3, no export or "
                "deletion certificate); controller/processor role not stated."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="SECURITY_CONTROLS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL,
            status=STATUS_UNVERIFIED,
            description=(
                "No official security documentation establishes the controls: "
                "Privacy Policy states 'reasonable efforts... no guarantees'; "
                "DPA s.5.1 states 'reasonable and appropriate' measures with "
                "the annex pointing at the trust-center URL; no certification "
                "(SOC 2/ISO) verifiable from official sources."
            ),
            source="",
            source_type="",
            note=(
                "The report's finding is absence of sufficient evidence — "
                "recorded UNVERIFIED, which is not a negative judgement about "
                "the provider."
            ),
        ),
        # --- provider facts, BEFORE_TECHNICAL_INTEGRATION ---
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="API_INPUT_OUTPUT_CONTRACT",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "POST https://api.typesafe.ai/v1/systemone with "
                "Authorization: Bearer <API_KEY>; request = state (text: "
                "string/object/array) + model + questions; response = model + "
                "answers + usage; synchronous; text-only input; GET /v1/models "
                "lists available models."
            ),
            source="TypeSafe HTTP API reference — https://docs.typesafe.ai/api.md",
            source_type="Official API documentation",
            note="Asynchronous requests and streaming: not documented.",
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="AUTHENTICATION_CREDENTIAL_HANDLING",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "Bearer API key per request; 401 Unauthorized means a missing "
                "or invalid key; the SDK reads the key from the "
                "TYPESAFE_API_KEY environment variable."
            ),
            source=(
                "TypeSafe HTTP API reference — https://docs.typesafe.ai/api.md "
                "(Bearer header, 401 semantics); Python SDK — "
                "https://docs.typesafe.ai/sdk/python.md (TYPESAFE_API_KEY)"
            ),
            source_type="Official API documentation",
            note=(
                "Key rotation, revocation, scoping, service accounts and "
                "official secret-storage guidance: UNVERIFIED."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="ERROR_SEMANTICS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "401 missing/invalid key; 422 request body failed validation "
                "with the offending field identified; 429 rate limit "
                "exceeded; 529 provider temporarily overloaded; retry-after "
                "headers honoured by the SDK; x-typesafe-request-id response "
                "header."
            ),
            source=(
                "TypeSafe HTTP API reference — https://docs.typesafe.ai/api.md "
                "(401/422/429/529); Python SDK exceptions — "
                "https://docs.typesafe.ai/sdk/python/api/exceptions.md"
            ),
            source_type="Official API documentation",
            note="Duplicate-request/idempotency semantics: UNVERIFIED.",
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="TIMEOUT_CANCELLATION_SEMANTICS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_UNVERIFIED,
            description=(
                "No provider-side documentation of request timeouts, maximum "
                "duration, server-side cancellation, post-disconnect "
                "behaviour, or late responses; the SDK's 30-second total "
                "retry budget and client abort are client-side behaviour "
                "only."
            ),
            source="",
            source_type="",
            note=(
                "Recorded UNVERIFIED — evidence does not establish the "
                "requirement."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="RATE_LIMITS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "250,000 tokens/second and 1,200 requests/minute; exceeding "
                "either yields 429; SDK retries with backoff and honours "
                "retry-after."
            ),
            source="TypeSafe Models — https://docs.typesafe.ai/models.md",
            source_type="Official API documentation",
            note=(
                "Limits explicitly volatile: 'Rate limits are adjusting "
                "dynamically' and can change without notice; per-account/"
                "per-plan limits: UNVERIFIED."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="REQUEST_RESPONSE_SIZE_LIMITS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "64k tokens per request, 32k for state plus the longest "
                "question; a Choice accepts up to 255 options; a Score "
                "accepts 2 to 10 levels."
            ),
            source=(
                "TypeSafe Models — https://docs.typesafe.ai/models.md; TypeSafe HTTP "
                "API reference — https://docs.typesafe.ai/api.md"
            ),
            source_type="Official API documentation",
            note="Output-size limit and request-body byte limit: UNVERIFIED.",
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="MODEL_VERSION_RECORDING",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "The response model field reports the versioned ID that "
                "answered; usage reports input_tokens and output_tokens; "
                "x-typesafe-request-id response header identifies the "
                "request."
            ),
            source=(
                "TypeSafe Models — https://docs.typesafe.ai/models.md; Python SDK "
                "exceptions — https://docs.typesafe.ai/sdk/python/api/exceptions.md"
            ),
            source_type="Official API documentation",
            note="",
        ),
        # --- provider facts, BEFORE_PRODUCTION_USE ---
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="AVAILABILITY_SLO",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_UNVERIFIED,
            description=(
                "No SLA or availability commitment is documented anywhere; "
                "MCA s.9.3 provides the services AS-IS and 'AS AVAILABLE'; "
                "the official status page reports measured uptime (api "
                "99.840%, console 99.988%) and dated incident history — "
                "measurement, not commitment."
            ),
            source="",
            source_type="",
            note=(
                "Recorded UNVERIFIED — a measured status page is not a "
                "documented SLO."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="COST_MODEL",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_SATISFIED,
            description=(
                "Input tokens billed at $0.042 per million; output free; "
                "credits expire at the earlier of end-of-Term or 12 months "
                "and are non-refundable; USD invoicing."
            ),
            source=(
                "TypeSafe Models — https://docs.typesafe.ai/models.md ('$42 per Btok "
                "/ $0.042 per Mtok, charged on input tokens; outputs are "
                "free'); MCA s.8 — https://typesafe.ai/legal/mca"
            ),
            source_type="Official pricing + contractual documentation",
            note=(
                "Whether failed, retried or cancelled requests are charged: "
                "UNVERIFIED."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="MODEL_VERSION_PINNING",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_SATISFIED,
            description=(
                "jev-1.13.0 is the documented versioned ID; jev-latest and "
                "jev-preview are aliases that move when a release ships; the "
                "documented pinning approach is 'pin that version's ID "
                "instead of the alias and move to the new one on your own "
                "schedule'."
            ),
            source="TypeSafe Models — https://docs.typesafe.ai/models.md",
            source_type="Official API documentation",
            note="Pinned-version continued availability and immutability: not promised.",
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_UNVERIFIED,
            description=(
                "No deprecation policy, retirement notices, end-of-life "
                "dates, migration windows, compatibility guarantees or model "
                "changelog is documented; aliases moving on release is the "
                "only documented change mechanic."
            ),
            source="",
            source_type="",
            note=(
                "Recorded UNVERIFIED — no deprecation documentation exists; "
                "MCA s.16.7 allows the provider to amend the Agreement with "
                "at least 60 days' notice — recorded, not resolved."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="DATA_RESIDENCY",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_SATISFIED,
            description=(
                "Services hosted in the United States; EEA/UK users transfer "
                "data to the US for storage and processing."
            ),
            source=(
                "TypeSafe Privacy Policy — https://typesafe.ai/legal/privacy-policy "
                "('The Services are hosted in the United States.')"
            ),
            source_type="Official privacy documentation",
            note=(
                "No region selection, no backup-location statement, no plan "
                "differences: UNVERIFIED."
            ),
        ),
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="INCIDENT_BREACH_NOTIFICATION_TERMS",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_BEFORE_PRODUCTION_USE,
            status=STATUS_SATISFIED,
            description=(
                "Notification 'without undue delay and in any case within 72 "
                "hours' after becoming aware of accidental or unauthorized "
                "access, disclosure or use of Customer Personal Data; "
                "investigation and remediation assistance promised; public "
                "status page carries dated incident history."
            ),
            source=(
                "Data Processing Addendum s.5.2 — "
                "https://typesafe.ai/legal/data-processing; TypeSafe Status — "
                "https://status.typesafe.ai"
            ),
            source_type="Official contractual + status documentation",
            note="Notification mechanism details: not specified.",
        ),
        # --- provider facts, OPTIONAL_LATER ---
        CapturedFact(
            provider=PROVIDER_NAME,
            requirement_id="STREAMING_BEHAVIOUR",
            kind=KIND_PROVIDER_FACT,
            classification=CLASSIFICATION_OPTIONAL_LATER,
            status=STATUS_UNVERIFIED,
            description=(
                "No streaming, SSE or chunked-response behaviour is "
                "documented anywhere in the official documentation; the API "
                "reference documents only the single JSON response."
            ),
            source="",
            source_type="",
            note=(
                "Recorded UNVERIFIED — OPTIONAL_LATER in the 7B9 inventory "
                "— reported, never blocking."
            ),
        ),
        # --- SteelSpec local controls, BEFORE_TECHNICAL_INTEGRATION ---
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="WIRE_PROJECTION",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "Only the approved 7B7 role and released fields cross the "
                "provider boundary; bookkeeping never crosses."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="NO_TOOLS",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "No tool or function definitions are ever sent; the Jev "
                "contract itself has no tools field."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="NO_RETRY_RESEND",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "Released content is never silently resent; note that the "
                "official Jev SDK retries by default, so a future adapter "
                "must not adopt SDK default retries."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="STATELESS_TURNS",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "Each request is built from the frozen contract state; no "
                "cross-turn conversation state is kept on the SteelSpec "
                "side."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="RESPONSE_CONTAINMENT",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "Provider responses stay behind the 7B7/7B8 display-only "
                "containment."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
        CapturedFact(
            provider=CONTROL_OWNER,
            requirement_id="LOCAL_COST_CAP",
            kind=KIND_LOCAL_CONTROL,
            classification=CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
            status=STATUS_SATISFIED,
            description=(
                "A per-session cost guard exists (mirroring the existing "
                "MAX_PDF_PAGES pattern)."
            ),
            source="",
            source_type="",
            note=(
                "SteelSpec local control — proven by SteelSpec's own test "
                "suite (7B7-7B9); not a provider fact."
            ),
        ),
    )
    return ProviderFactCapture(provider=PROVIDER_NAME, facts=facts)


# --------------------------------------------------------------------------------------
# Recording -> gate. The 7B9 inventory stays the single source of truth: every
# classification and kind below is checked against it and loudly refused if it
# disagrees. Nothing here is repaired silently.
# --------------------------------------------------------------------------------------
def build_requirement_records(capture=None) -> tuple[RequirementRecord, ...]:
    """Projects the frozen capture onto the real 7B9 RequirementRecords,
    validating it against the pinned inventory: complete coverage, no
    duplicates, no unknown requirements, no invented classifications, no
    satisfied-without-source provider fact and no sourced UNVERIFIED provider
    fact. Returns records in the capture's authored order."""
    if capture is None:
        capture = jev_fact_capture()
    if not isinstance(capture, ProviderFactCapture):
        raise TypeError(
            f"capture must be a ProviderFactCapture (got "
            f"{type(capture).__name__})."
        )
    seen = set()
    for fact in capture.facts:
        if not isinstance(fact, CapturedFact):
            raise TypeError(
                f"every fact must be a CapturedFact (got "
                f"{type(fact).__name__})."
            )
        for name in ("provider", "requirement_id", "kind", "classification",
                     "status", "description", "source", "source_type", "note"):
            if not isinstance(getattr(fact, name), str):
                raise TypeError(
                    f"{name} must be a str (got "
                    f"{type(getattr(fact, name)).__name__})."
                )
        if fact.requirement_id not in REQUIREMENT_IDS:
            raise ValueError(
                f"unknown requirement {fact.requirement_id!r}; the capture "
                "may only record requirements the 7B9 inventory carries."
            )
        if fact.requirement_id in seen:
            raise ValueError(
                f"duplicate record for requirement {fact.requirement_id!r}; "
                "the capture refuses contradictory duplicates instead of "
                "choosing between them."
            )
        seen.add(fact.requirement_id)
        if fact.kind not in KINDS:
            raise ValueError(
                f"invalid kind {fact.kind!r} for requirement "
                f"{fact.requirement_id!r}."
            )
        if fact.status not in STATUSES:
            raise ValueError(
                f"invalid status {fact.status!r} for requirement "
                f"{fact.requirement_id!r}."
            )
        if fact.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"invalid classification {fact.classification!r} for "
                f"requirement {fact.requirement_id!r}."
            )
        expected_classification = next(
            classification
            for (requirement_id, classification) in REQUIREMENT_CLASSIFICATIONS
            if requirement_id == fact.requirement_id)
        if fact.classification != expected_classification:
            raise ValueError(
                f"classification {fact.classification!r} for requirement "
                f"{fact.requirement_id!r} does not match the inventory "
                f"classification {expected_classification!r}."
            )
        expected_kind = next(
            kind
            for (requirement_id, kind, _classification) in _INVENTORY_ENTRIES
            if requirement_id == fact.requirement_id)
        if fact.kind != expected_kind:
            raise ValueError(
                f"kind {fact.kind!r} for requirement {fact.requirement_id!r} "
                f"does not match the inventory kind {expected_kind!r}."
            )
        if fact.kind == KIND_PROVIDER_FACT:
            if fact.status == STATUS_SATISFIED and not fact.source.strip():
                raise ValueError(
                    f"requirement {fact.requirement_id!r} is recorded "
                    "SATISFIED without a source; a provider fact cannot be "
                    "satisfied merely by being asserted."
                )
            if fact.status == STATUS_UNVERIFIED and fact.source.strip():
                raise ValueError(
                    f"requirement {fact.requirement_id!r} is recorded "
                    "UNVERIFIED yet carries a source; an UNVERIFIED "
                    "requirement must never acquire a fabricated source."
                )
    missing = REQUIREMENT_IDS - seen
    if missing:
        raise ValueError(
            f"capture is incomplete; the following 7B9 requirements have no "
            f"record: {sorted(missing)!r}."
        )
    return tuple(
        RequirementRecord(
            requirement_id=fact.requirement_id,
            kind=fact.kind,
            classification=fact.classification,
            status=fact.status,
            source=fact.source,
            note=(fact.description if not fact.note
                  else f"{fact.description} [note: {fact.note}]"),
        )
        for fact in capture.facts
    )


def evaluate_capture(capture=None) -> GateDetermination:
    """The REAL 7B9 gate over the recorded facts — a recorded determination
    only, never an integration decision. Returns the genuine
    GateDetermination; nothing here re-implements or bypasses gate logic."""
    return evaluate_readiness(build_requirement_records(capture))


# Local inventory mirror used only to cross-check `kind` (the 7B9 module keeps
# the authoritative inventory; kinds are pinned there per requirement).
_INVENTORY_ENTRIES = tuple(
    (requirement_id, kind, classification)
    for (requirement_id, kind, classification) in (
        ("DATA_RETENTION", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_ANY_REAL_CALL),
        ("DATA_TRAINING_USE_POLICY", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_ANY_REAL_CALL),
        ("SUBPROCESSORS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_ANY_REAL_CALL),
        ("PRIVACY_CONTRACTUAL_TERMS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_ANY_REAL_CALL),
        ("SECURITY_CONTROLS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_ANY_REAL_CALL),
        ("API_INPUT_OUTPUT_CONTRACT", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("AUTHENTICATION_CREDENTIAL_HANDLING", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("ERROR_SEMANTICS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("TIMEOUT_CANCELLATION_SEMANTICS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("RATE_LIMITS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("REQUEST_RESPONSE_SIZE_LIMITS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("MODEL_VERSION_RECORDING", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("AVAILABILITY_SLO", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("COST_MODEL", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("MODEL_VERSION_PINNING", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("DEPRECATION_VERSION_CHANGE_BEHAVIOUR", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("DATA_RESIDENCY", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("INCIDENT_BREACH_NOTIFICATION_TERMS", KIND_PROVIDER_FACT,
         CLASSIFICATION_BEFORE_PRODUCTION_USE),
        ("STREAMING_BEHAVIOUR", KIND_PROVIDER_FACT,
         CLASSIFICATION_OPTIONAL_LATER),
        ("WIRE_PROJECTION", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("NO_TOOLS", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("NO_RETRY_RESEND", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("STATELESS_TURNS", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("RESPONSE_CONTAINMENT", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
        ("LOCAL_COST_CAP", KIND_LOCAL_CONTROL,
         CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    )
)
