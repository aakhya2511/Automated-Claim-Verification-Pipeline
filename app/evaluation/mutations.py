"""Controlled one-property mutations with recorded provenance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.enums import ClaimType, InventoryStatus
from app.domain.models import ReferenceRecord
from app.evaluation.models import MutationMetadata, MutationType

REGION_UNIVERSE = ("US", "CA", "GB", "DE", "FR", "MX", "BR", "AU", "NZ", "EU")

TYPE_BY_CLAIM: dict[ClaimType, MutationType] = {
    ClaimType.PRICE: MutationType.PRICE_MISMATCH,
    ClaimType.DISCOUNT: MutationType.DISCOUNT_MISMATCH,
    ClaimType.SHIPPING: MutationType.SHIPPING_MISMATCH,
    ClaimType.AVAILABILITY: MutationType.AVAILABILITY_MISMATCH,
    ClaimType.FEATURE_INCLUSION: MutationType.FEATURE_INCLUSION_MISMATCH,
    ClaimType.FEATURE_EXCLUSION: MutationType.FEATURE_EXCLUSION_MISMATCH,
    ClaimType.SUBSCRIPTION_TERMS: MutationType.SUBSCRIPTION_PRICE_MISMATCH,
    ClaimType.TRIAL_DURATION: MutationType.TRIAL_DURATION_MISMATCH,
    ClaimType.PROMOTION_DATES: MutationType.PROMOTION_END_MISMATCH,
    ClaimType.GEO_ELIGIBILITY: MutationType.REGION_MISMATCH,
    ClaimType.MINIMUM_PURCHASE: MutationType.MINIMUM_PURCHASE_MISMATCH,
}


def mutation_for(
    claim_type: ClaimType, record: ReferenceRecord, *, variant: int
) -> MutationMetadata:
    magnitude = ("easy", "moderate", "subtle")[variant % 3]
    source = source_value(claim_type, record, variant=variant)
    mutated: object
    if claim_type in {ClaimType.PRICE, ClaimType.SUBSCRIPTION_TERMS}:
        deltas = (Decimal("100"), Decimal("20"), Decimal("0.50"))
        mutated = Decimal(str(source)) + deltas[variant % 3]
    elif claim_type is ClaimType.DISCOUNT:
        delta = (20.0, 7.0, 1.0)[variant % 3]
        mutated = float(str(source)) + delta
    elif claim_type is ClaimType.SHIPPING:
        mutated = not bool(source)
    elif claim_type is ClaimType.AVAILABILITY:
        statuses = tuple(InventoryStatus)
        current = source if isinstance(source, InventoryStatus) else InventoryStatus(str(source))
        mutated = statuses[(statuses.index(current) + 1) % len(statuses)]
    elif claim_type is ClaimType.FEATURE_INCLUSION:
        mutated = record.excluded_features[variant % len(record.excluded_features)]
    elif claim_type is ClaimType.FEATURE_EXCLUSION:
        mutated = record.included_features[variant % len(record.included_features)]
    elif claim_type is ClaimType.TRIAL_DURATION:
        mutated = int(str(source)) + (30, 7, 1)[variant % 3]
    elif claim_type is ClaimType.PROMOTION_DATES:
        mutated = _as_date(source) + timedelta(days=(31, 7, 1)[variant % 3])
    elif claim_type is ClaimType.GEO_ELIGIBILITY:
        mutated = next(
            region for region in REGION_UNIVERSE if region not in record.eligible_regions
        )
    elif claim_type is ClaimType.MINIMUM_PURCHASE:
        mutated = Decimal(str(source)) + (Decimal("20"), Decimal("5"), Decimal("0.50"))[variant % 3]
    else:
        raise ValueError(f"no mutation for {claim_type.value}")
    return MutationMetadata(
        type=TYPE_BY_CLAIM[claim_type],
        source_value=source,
        mutated_value=mutated,
        magnitude=magnitude,
    )


def source_value(claim_type: ClaimType, record: ReferenceRecord, *, variant: int) -> object:
    match claim_type:
        case ClaimType.PRICE:
            return record.price
        case ClaimType.DISCOUNT:
            return record.discount_percent
        case ClaimType.SHIPPING:
            return record.free_shipping
        case ClaimType.AVAILABILITY:
            return record.inventory_status
        case ClaimType.FEATURE_INCLUSION:
            return record.included_features[variant % len(record.included_features)]
        case ClaimType.FEATURE_EXCLUSION:
            return record.excluded_features[variant % len(record.excluded_features)]
        case ClaimType.SUBSCRIPTION_TERMS:
            return record.subscription_price
        case ClaimType.TRIAL_DURATION:
            return record.trial_days
        case ClaimType.PROMOTION_DATES:
            return record.offer_end
        case ClaimType.GEO_ELIGIBILITY:
            return record.eligible_regions[variant % len(record.eligible_regions)]
        case ClaimType.MINIMUM_PURCHASE:
            return record.minimum_purchase
        case _:
            raise ValueError(f"no source value for {claim_type.value}")


def _as_date(value: object) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
