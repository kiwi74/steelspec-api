"""
Milestone 7D0 — JEV PROVIDER CONTRACT DISCOVERY (evidence capture only).

This module freezes the findings of the authoritative-documentation review
of Jev (TypeSafe AI) for the five requirements that remain UNVERIFIED in the
7C0 fact capture: SECURITY_CONTROLS, TIMEOUT_CANCELLATION_SEMANTICS,
AVAILABILITY_SLO, DEPRECATION_VERSION_CHANGE_BEHAVIOUR and (optional, never
blocking) STREAMING_BEHAVIOUR.

It is DISCOVERY ONLY. Nothing here calls Jev's API, sends a request, reads
the environment or the clock, touches the filesystem, carries credentials,
creates an adapter, client or registry, changes the 7B9 gate or the 7C0
capture, or makes any decision. It records what the authoritative material
actually establishes — never more.

Every requirement is classified with exactly one of VERIFIED / UNVERIFIED /
NOT_APPLICABLE / CONFLICTING. A finding is VERIFIED only when authoritative
Jev-published material directly supports it; the absence of documentation is
recorded as UNVERIFIED, never upgraded merely because evidence is plausible;
a status page showing incidents is not an availability SLO; and measured
uptime is never converted into a commitment. Where sources disagreed the
record would say CONFLICTING — it would never pick a winner.

The frozen outcome of this discovery is that all five requirements remain
UNVERIFIED: the inspected material documents client-side SDK retry and
timeout behaviour, measured (never committed) uptime, and known-issue
disclosure — none of which is provider-side timeout/cancellation semantics,
an availability commitment or a deprecation policy. Closing the gaps
requires direct confirmation from Jev; the evidence here is not sufficient
to design a Jev adapter, and it is recorded without changing any prior
boundary.

No network, no subprocess, no filesystem, no environment, no clock, no
randomness, no credentials, no UI, no decision-making and no mutation of any
prior boundary (7B9, 7C0, 7C1, 7C2, 7C3) anywhere in this module.
"""

from dataclasses import dataclass

from app.cad_engine.provider_fact_capture import PROVIDER_NAME

__all__ = [
    "DISCOVERY_VERIFIED", "DISCOVERY_UNVERIFIED",
    "DISCOVERY_NOT_APPLICABLE", "DISCOVERY_CONFLICTING",
    "DISCOVERY_STATUSES", "DISCOVERY_REQUIREMENT_IDS",
    "DiscoveryEvidence", "ContractDiscovery", "jev_contract_discovery",
]

# --------------------------------------------------------------------------------------
# The evidence vocabulary — exactly four classifications, deliberately no more.
# --------------------------------------------------------------------------------------
DISCOVERY_VERIFIED = "VERIFIED"
DISCOVERY_UNVERIFIED = "UNVERIFIED"
DISCOVERY_NOT_APPLICABLE = "NOT_APPLICABLE"
DISCOVERY_CONFLICTING = "CONFLICTING"

DISCOVERY_STATUSES = frozenset({
    DISCOVERY_VERIFIED, DISCOVERY_UNVERIFIED,
    DISCOVERY_NOT_APPLICABLE, DISCOVERY_CONFLICTING,
})

# The five in-scope requirements — exactly the 7C0 UNVERIFIED set.
DISCOVERY_REQUIREMENT_IDS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
    "STREAMING_BEHAVIOUR",
})

# Frozen retrieval date: part of the record, never read from a clock.
RETRIEVED_ON = "2026-09-22"


# --------------------------------------------------------------------------------------
# The discovery record: frozen plain data only. No methods, no I/O, no state.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DiscoveryEvidence:
    """One requirement's discovery evidence: what the authoritative material
    establishes, exactly where it says so, and — in `limitations` — what it
    does not establish. `source_version` is the source's own version or
    review date where the source publishes one, else ""."""
    requirement_id: str
    provider: str
    evidence_status: str
    finding: str
    source: str
    source_version: str
    retrieved_on: str
    limitations: str


@dataclass(frozen=True)
class ContractDiscovery:
    """The frozen 7D0 discovery record: one DiscoveryEvidence per in-scope
    requirement, the empty set of conflicting sources when (as here) none
    were found, the items still needing direct confirmation from Jev, and a
    deterministic statement of whether the evidence suffices for an eventual
    adapter design."""
    provider: str
    scope_requirement_ids: tuple[str, ...]
    evidence: tuple[DiscoveryEvidence, ...]
    conflicting_sources: tuple[str, ...]
    needs_direct_confirmation: tuple[str, ...]
    adapter_design_sufficiency: str


def jev_contract_discovery() -> ContractDiscovery:
    """Returns the frozen 7D0 discovery record: one DiscoveryEvidence per
    in-scope requirement, in inventory order. Every finding records only
    what the consulted authoritative Jev-published material establishes;
    nothing is upgraded merely because evidence is plausible, and the
    record never changes readiness — it is evidence for a future human
    review, never a gate input."""
    evidence = (
        DiscoveryEvidence(
            requirement_id="SECURITY_CONTROLS",
            provider=PROVIDER_NAME,
            evidence_status=DISCOVERY_UNVERIFIED,
            finding=(
                "TypeSafe publishes no security controls page: "
                "https://typesafe.ai/security returns 404 at retrieval and "
                "the trust center (https://trust.typesafe.ai) is a "
                "client-rendered shell with no extractable content. The "
                "legal documents (DPA, MCA, Privacy Policy) document "
                "data-handling commitments — no-training, 72-hour breach "
                "notification, disclosed subprocessors, US hosting, "
                "zero-data-retention offered to enterprise — but no access "
                "controls, encryption in transit or at rest, tenant "
                "isolation, webhook signing, retention/deletion mechanics "
                "or certifications are documented. No TypeSafe-published "
                "claim of SOC 2, ISO 27001 or an equivalent certification "
                "was found."
            ),
            source=(
                "https://typesafe.ai/security (404 at retrieval); "
                "https://trust.typesafe.ai; "
                "https://typesafe.ai/legal/data-processing; "
                "https://typesafe.ai/legal/mca; "
                "https://typesafe.ai/legal/privacy-policy; "
                "https://docs.typesafe.ai/legal.md"
            ),
            source_version="Privacy Policy updated 2025-11-19",
            retrieved_on=RETRIEVED_ON,
            limitations=(
                "Absence of documentation is recorded as UNVERIFIED, not as "
                "a negative judgement about the provider, and security "
                "properties are not inferred merely because they are common "
                "industry practice. Third-party commentary reporting no "
                "SOC 2/ISO claims is noted here as context only — it is not "
                "an authoritative source. The data-handling commitments "
                "themselves are already captured in the 7C0 fact capture "
                "and do not constitute security-control documentation."
            ),
        ),
        DiscoveryEvidence(
            requirement_id="TIMEOUT_CANCELLATION_SEMANTICS",
            provider=PROVIDER_NAME,
            evidence_status=DISCOVERY_UNVERIFIED,
            finding=(
                "The Python SDK documents client-side behaviour only: "
                "RetryPolicy (max_retries=2, backoff 0.5 to 5.0 seconds "
                "with jitter 0.25, retry on 408/429/5xx, respect "
                "Retry-After, timeout=30.0 total retry budget per SDK "
                "call), TypeSafeAPITimeoutError for a request exceeding its "
                "configured client-side timeout, and per-call timeout "
                "overrides. The async client is client-side async/await "
                "only — no server-side jobs, job IDs, status polling or "
                "cancel method exist. The API reference documents 429 "
                "'back off and retry' guidance. No provider-side "
                "request-timeout value, cancellation semantics, idempotency "
                "guarantee or interruption behaviour is documented."
            ),
            source=(
                "https://docs.typesafe.ai/sdk/python/usage.md; "
                "https://docs.typesafe.ai/sdk/python/api/exceptions.md; "
                "https://docs.typesafe.ai/sdk/python/api/clients/async.md; "
                "https://docs.typesafe.ai/api.md"
            ),
            source_version="SDK changelog through v0.7.1 (2026-09-21)",
            retrieved_on=RETRIEVED_ON,
            limitations=(
                "Client-side SDK retry budgets and timeout errors are not "
                "provider-side timeout/cancellation semantics and do not "
                "establish them; the SDK default timeout=30.0 is a library "
                "default, not a provider commitment, and a retry policy is "
                "not a timeout value. No async job API is documented, so "
                "server-side job cancellation may be inapplicable — that is "
                "recorded here as part of the UNVERIFIED finding, never "
                "upgraded. Client guidance does not establish complete "
                "operational semantics."
            ),
        ),
        DiscoveryEvidence(
            requirement_id="AVAILABILITY_SLO",
            provider=PROVIDER_NAME,
            evidence_status=DISCOVERY_UNVERIFIED,
            finding=(
                "The public status page reports measurement only: no SLA, "
                "no SLO, no uptime commitment and no credit policy. It "
                "displays measured percentages (API '99.838% uptime', "
                "console '99.988% uptime') and an incident history "
                "(including 2026-09-21 'intermittent downtime and system "
                "instability'). No TypeSafe-published availability "
                "commitment was found in the documentation index, the legal "
                "index or the MCA."
            ),
            source=(
                "https://status.typesafe.ai; "
                "https://docs.typesafe.ai/llms.txt; "
                "https://docs.typesafe.ai/legal.md; "
                "https://typesafe.ai/legal/mca"
            ),
            source_version="status page current at retrieval (2026-09-22 10:10 UTC)",
            retrieved_on=RETRIEVED_ON,
            limitations=(
                "A status page showing incidents is not by itself an "
                "availability SLO, and historical measured uptime is "
                "recorded here exactly as measurement — never converted "
                "into an SLO or a commitment. The absence of an "
                "availability clause in the inspected legal documents is a "
                "documentation gap, not proof that no commercial commitment "
                "could exist in an instrument this review did not inspect."
            ),
        ),
        DiscoveryEvidence(
            requirement_id="DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
            provider=PROVIDER_NAME,
            evidence_status=DISCOVERY_UNVERIFIED,
            finding=(
                "No deprecation policy exists in the documentation index. "
                "The model-jaggedness page discloses known issues for "
                "jev-1.13 and states 'Many of these will be fixed in later "
                "versions' (reviewed 2026-09-17) — a version-improvement "
                "statement, not a deprecation or sunset policy. The SDK "
                "changelog documents client-library breaking changes only "
                "(v0.6.0 Score.criteria ordering; v0.7.0 msgspec to "
                "pydantic) and no API deprecation policy. Model version "
                "pinning (jev-1.13.0) and moving aliases (jev-latest, "
                "jev-preview) are documented. MCA s.16.7's 60-day amendment "
                "notice is a contract term already recorded in 7C0, not a "
                "technical deprecation policy."
            ),
            source=(
                "https://docs.typesafe.ai/model-jaggedness/jev-1.13.md; "
                "https://docs.typesafe.ai/sdk/python/changelog.md; "
                "https://docs.typesafe.ai/api.md; "
                "https://docs.typesafe.ai/llms.txt; "
                "https://typesafe.ai/legal/mca"
            ),
            source_version="jaggedness page reviewed 2026-09-17; SDK changelog through v0.7.1",
            retrieved_on=RETRIEVED_ON,
            limitations=(
                "'Fixed in later versions' is not a migration period, a "
                "breaking-change notice, a backward-compatibility "
                "commitment or a sunset timeline, and no notification "
                "channel for API or model breaking changes is documented. "
                "The disclosure that known issues exist is evidence of "
                "openness about defects, not of a version-change contract."
            ),
        ),
        DiscoveryEvidence(
            requirement_id="STREAMING_BEHAVIOUR",
            provider=PROVIDER_NAME,
            evidence_status=DISCOVERY_UNVERIFIED,
            finding=(
                "No streaming documentation exists: the documentation index "
                "contains no streaming page, and neither the API reference "
                "nor the SDK pages document streaming responses. Jev is "
                "documented as returning structured scores and choices in a "
                "single request/response."
            ),
            source=(
                "https://docs.typesafe.ai/llms.txt (index); "
                "https://docs.typesafe.ai/api.md; "
                "https://docs.typesafe.ai/sdk/python.md"
            ),
            source_version="",
            retrieved_on=RETRIEVED_ON,
            limitations=(
                "Absence of streaming documentation means no streaming "
                "contract can be recorded; the requirement is OPTIONAL_LATER "
                "in the 7B9 inventory and is reported here without ever "
                "blocking readiness."
            ),
        ),
    )
    return ContractDiscovery(
        provider=PROVIDER_NAME,
        scope_requirement_ids=tuple(sorted(DISCOVERY_REQUIREMENT_IDS)),
        evidence=evidence,
        conflicting_sources=(),
        needs_direct_confirmation=(
            "SECURITY_CONTROLS: certifications (SOC 2 / ISO 27001 or "
            "equivalent), access controls, encryption in transit and at "
            "rest, tenant isolation, webhook signing, retention and "
            "deletion mechanics",
            "TIMEOUT_CANCELLATION_SEMANTICS: provider-side request-timeout "
            "values, cancellation support and semantics, idempotency "
            "guarantees, interruption behaviour",
            "AVAILABILITY_SLO: any uptime commitment, SLO, SLA or credit "
            "policy",
            "DEPRECATION_VERSION_CHANGE_BEHAVIOUR: deprecation policy, "
            "model sunset timelines, migration periods, breaking-change "
            "notification channel",
            "STREAMING_BEHAVIOUR: whether streaming exists and, if it does, "
            "its contract",
        ),
        adapter_design_sufficiency=(
            "The evidence is NOT sufficient to design a Jev adapter: four "
            "required 7B9 provider facts remain UNVERIFIED. It is sufficient "
            "to establish exactly which facts must be obtained by direct "
            "confirmation from Jev before an adapter design can proceed."
        ),
    )
