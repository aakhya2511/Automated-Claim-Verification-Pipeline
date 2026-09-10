"""Evidence selection: resolving the entity and projecting the fields that matter.

Two jobs, both of which turned out to matter more than expected:

**Entity resolution.** Identifier lookups first (they are exact), then an exact
name/alias hit, then scored lexical search. Search results below
``min_match_score`` are discarded rather than used, because a wrong record is
strictly worse than no record: it produces a confident verdict grounded in the
wrong row.

**Field projection.** Only fields relevant to the claim are carried forward.
The baseline arm dumps the whole record instead, and in the error analysis that
was a measurable source of false positives — given a record containing
``price``, ``subscription_price``, ``minimum_purchase`` and ``shipping_cost``,
a model asked about "the price" would sometimes compare the claim against a
different number and report a mismatch that did not exist. Narrowing the
projection removes the distractors and shrinks the prompt at the same time.
"""

from __future__ import annotations

import hashlib

from app.core.pipeline_config import RetrievalConfig
from app.domain.enums import Attribute, ClaimType
from app.domain.models import (
    NormalizedClaim,
    ReferenceEvidence,
    ReferenceRecord,
    VerificationRequest,
)
from app.retrieval.repository import InMemoryReferenceRepository

#: Fields shown for each claim category. The claim's own attribute always
#: comes first; the rest is the minimum context needed to judge it — e.g. a
#: discount claim needs the promotion window to tell "20% off" from "20% off,
#: but the offer ended in June".
_PROJECTIONS: dict[ClaimType, tuple[Attribute, ...]] = {
    ClaimType.PRICE: (Attribute.PRICE, Attribute.CURRENCY, Attribute.DISCOUNT_PERCENT),
    ClaimType.DISCOUNT: (
        Attribute.DISCOUNT_PERCENT,
        Attribute.OFFER_START,
        Attribute.OFFER_END,
        Attribute.PRICE,
    ),
    ClaimType.SHIPPING: (
        Attribute.FREE_SHIPPING,
        Attribute.SHIPPING_COST,
        Attribute.MINIMUM_PURCHASE,
    ),
    ClaimType.AVAILABILITY: (Attribute.INVENTORY_STATUS,),
    ClaimType.FEATURE_INCLUSION: (Attribute.INCLUDED_FEATURES, Attribute.EXCLUDED_FEATURES),
    ClaimType.FEATURE_EXCLUSION: (Attribute.EXCLUDED_FEATURES, Attribute.INCLUDED_FEATURES),
    ClaimType.SUBSCRIPTION_TERMS: (
        Attribute.SUBSCRIPTION_PRICE,
        Attribute.BILLING_PERIOD,
        Attribute.CURRENCY,
        Attribute.TRIAL_DAYS,
    ),
    ClaimType.TRIAL_DURATION: (Attribute.TRIAL_DAYS,),
    ClaimType.PROMOTION_DATES: (
        Attribute.OFFER_START,
        Attribute.OFFER_END,
        Attribute.DISCOUNT_PERCENT,
    ),
    ClaimType.GEO_ELIGIBILITY: (Attribute.ELIGIBLE_REGIONS,),
    ClaimType.MINIMUM_PURCHASE: (Attribute.MINIMUM_PURCHASE, Attribute.PRICE),
}

#: An unclassified assertion has no defensible answering field.  Sending a
#: grab bag of otherwise authoritative values invites the semantic rater to
#: establish or refute a different proposition than the one stated.
_FALLBACK_PROJECTION: tuple[Attribute, ...] = ()

_ALL_ATTRIBUTES: tuple[Attribute, ...] = tuple(
    attribute for attribute in Attribute if attribute is not Attribute.UNKNOWN
)

#: Equivalent columns across entity types. A plan's monetary value lives in
#: ``subscription_price``; a claim that says "$99" about a plan must be checked
#: against that, not reported as a missing field.
_ATTRIBUTE_FALLBACKS: dict[Attribute, tuple[Attribute, ...]] = {
    Attribute.PRICE: (Attribute.SUBSCRIPTION_PRICE,),
    Attribute.SUBSCRIPTION_PRICE: (Attribute.PRICE,),
}


class ReferenceEvidenceRetriever:
    """Resolves claims to reference records and projects the relevant fields.

    Satisfies :class:`app.domain.interfaces.EvidenceRetriever`.
    """

    def __init__(
        self,
        *,
        repository: InMemoryReferenceRepository,
        config: RetrievalConfig,
    ) -> None:
        self._repository = repository
        self._config = config

    async def retrieve(
        self, claim: NormalizedClaim, request: VerificationRequest
    ) -> ReferenceEvidence:
        record, method, score, candidates = await self._resolve(claim, request)
        if record is None:
            # Empty evidence is a first-class outcome, not an error. The rule
            # engine turns it into INSUFFICIENT_EVIDENCE.
            return ReferenceEvidence(
                match_method=method,
                match_score=0.0,
                candidate_ids=candidates,
                effective_attribute=claim.attribute,
            )

        effective = self._effective_attribute(claim, record)
        return ReferenceEvidence(
            record_id=record.record_id,
            entity_type=record.entity_type,
            display_name=record.display_name,
            fields=self._project(claim, record, effective),
            effective_attribute=effective,
            match_method=method,
            match_score=score,
            candidate_ids=candidates,
            record_updated_at=record.updated_at,
            reference_version=_reference_version(record),
        )

    # ------------------------------------------------------------------ #
    # Entity resolution
    # ------------------------------------------------------------------ #
    async def _resolve(
        self, claim: NormalizedClaim, request: VerificationRequest
    ) -> tuple[ReferenceRecord | None, str, float, tuple[str, ...]]:
        """Return the resolved record plus how it was found, for the audit trail."""
        if request.reference_id:
            record = await self._repository.get_by_id(request.reference_id)
            return (record, "reference_id", 1.0 if record else 0.0, ())

        if request.sku:
            record = await self._repository.get_by_sku(request.sku)
            return (record, "sku", 1.0 if record else 0.0, ())

        if claim.entity.id:
            record = await self._repository.get_by_id(claim.entity.id)
            if record is None:
                record = await self._repository.get_by_sku(claim.entity.id)
            if record is not None:
                return (record, "claim_entity_id", 1.0, ())

        if self._config.strategy == "id_only":
            # Baseline arm: no alias resolution, no lexical fallback. A claim
            # that names its product in prose resolves to nothing.
            return (None, "unresolved_id_only", 0.0, ())

        name = claim.entity.name or claim.raw_text
        exact = await self._repository.get_by_name(name)
        if exact is not None:
            return (exact, "exact_name", 1.0, (exact.record_id,))

        ranked = await self._repository.search(name, limit=self._config.max_candidates)
        candidates = tuple(record.record_id for record, _ in ranked)
        if not ranked:
            return (None, "unresolved_no_candidates", 0.0, candidates)

        best_record, best_score = ranked[0]
        if best_score < self._config.min_match_score:
            # A wrong record is worse than no record: it yields a confident
            # verdict grounded in the wrong row.
            return (None, "unresolved_low_score", best_score, candidates)
        return (best_record, "lexical_search", best_score, candidates)

    # ------------------------------------------------------------------ #
    # Field projection
    # ------------------------------------------------------------------ #
    def _effective_attribute(self, claim: NormalizedClaim, record: ReferenceRecord) -> Attribute:
        """Redirect the claim's attribute to the column that actually holds it."""
        if claim.attribute is Attribute.UNKNOWN:
            return Attribute.UNKNOWN
        if record.has_attribute(claim.attribute):
            return claim.attribute
        for fallback in _ATTRIBUTE_FALLBACKS.get(claim.attribute, ()):
            if record.has_attribute(fallback):
                return fallback
        return claim.attribute

    def _project(
        self, claim: NormalizedClaim, record: ReferenceRecord, effective: Attribute
    ) -> dict[str, object]:
        if self._config.evidence_projection == "full_record":
            attributes = _ALL_ATTRIBUTES
        else:
            attributes = self._scoped_attributes(claim, effective)

        fields: dict[str, object] = {}
        for attribute in attributes:
            value = record.get_attribute(attribute)
            if value is not None:
                fields[attribute.value] = value

        if not self._config.include_terms_text:
            fields.pop(Attribute.TERMS.value, None)

        # Even when the answering field is unset, name it so the absence is
        # explicit in the audit trail rather than inferred from a missing key.
        if effective is not Attribute.UNKNOWN and effective.value not in fields:
            fields.setdefault(f"{effective.value}__absent", True)
        return fields

    def _scoped_attributes(
        self, claim: NormalizedClaim, effective: Attribute
    ) -> tuple[Attribute, ...]:
        base = _PROJECTIONS.get(claim.claim_type, _FALLBACK_PROJECTION)
        attributes: list[Attribute] = []
        for attribute in (effective, *base):
            if attribute is not Attribute.UNKNOWN and attribute not in attributes:
                attributes.append(attribute)

        # A geographic claim needs the eligibility list even when the claim is
        # primarily about something else, and any date-bounded claim needs the
        # promotion window to distinguish "wrong" from "no longer active".
        if claim.region and Attribute.ELIGIBLE_REGIONS not in attributes:
            attributes.append(Attribute.ELIGIBLE_REGIONS)
        if claim.time_context.end or claim.time_context.start:
            for attribute in (Attribute.OFFER_START, Attribute.OFFER_END):
                if attribute not in attributes:
                    attributes.append(attribute)

        if self._config.include_terms_text and claim.claim_type is not ClaimType.UNKNOWN:
            attributes.append(Attribute.TERMS)
        return tuple(attributes)


def _reference_version(record: ReferenceRecord) -> str:
    identity = f"{record.record_id}:{record.updated_at.isoformat()}".encode()
    return hashlib.sha256(identity).hexdigest()[:16]
