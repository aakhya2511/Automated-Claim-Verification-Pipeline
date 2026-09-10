"""Shared deterministic wording families for clean and mutated claims."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.enums import ClaimType, InventoryStatus
from app.domain.models import ReferenceRecord
from app.evaluation.models import Difficulty

REGION_NAMES = {
    "US": "the United States",
    "CA": "Canada",
    "GB": "the United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "MX": "Mexico",
    "BR": "Brazil",
    "AU": "Australia",
    "NZ": "New Zealand",
    "EU": "the European Union",
}


def subject(record: ReferenceRecord) -> str:
    if record.plan_name:
        return f"{record.brand} {record.plan_name} plan"
    if record.entity_type.value == "offer" and record.aliases:
        return f"offer {record.aliases[0]}"
    if record.brand and record.display_name.casefold().startswith(record.brand.casefold()):
        return record.display_name
    return f"{record.brand} {record.display_name}"


def feature_text(value: str) -> str:
    return value.replace("_", " ")


def money(value: Decimal | int | float | str) -> str:
    return f"{Decimal(str(value)):.2f}"


def percent(value: float | Decimal) -> str:
    decimal = Decimal(str(value))
    return format(decimal.normalize(), "f")


def difficulty_for(index: int) -> Difficulty:
    return (Difficulty.EASY, Difficulty.MODERATE, Difficulty.HARD)[index % 3]


def render(
    claim_type: ClaimType,
    record: ReferenceRecord,
    value: object,
    *,
    variant: int,
    qualifier_mode: str = "exact",
) -> tuple[str, str]:
    name = subject(record)
    family = variant % 3
    match claim_type:
        case ClaimType.PRICE:
            amount = money(value)  # type: ignore[arg-type]
            if qualifier_mode == "approximately":
                return f"{name} costs approximately ${amount}.", "price_approx_v1"
            if qualifier_mode == "starting_at":
                return f"{name} starts at ${amount}.", "price_starting_v1"
            if qualifier_mode == "under":
                return f"{name} costs under ${amount}.", "price_under_v1"
            options = (
                f"{name} costs ${amount}.",
                f"{name} is priced at ${amount}.",
                f"The listed price for {name} is ${amount}.",
            )
            return options[family], f"price_exact_{family + 1}_v1"
        case ClaimType.DISCOUNT:
            amount = percent(value)  # type: ignore[arg-type]
            if qualifier_mode == "up_to":
                return f"{name} gives up to {amount}% off.", "discount_up_to_v1"
            if qualifier_mode == "at_least":
                return f"{name} gives at least {amount}% off.", "discount_at_least_v1"
            options = (
                f"{name} gives {amount}% off.",
                f"You can save {amount}% with {name}.",
                f"A {amount} percent discount applies to {name}.",
            )
            return options[family], f"discount_exact_{family + 1}_v1"
        case ClaimType.SHIPPING:
            is_free = bool(value)
            if is_free:
                options = (
                    f"Shipping is free for {name}.",
                    f"{name} includes complimentary delivery.",
                    f"No shipping charge applies to {name}.",
                )
            else:
                options = (
                    f"Shipping is not free for {name}.",
                    f"{name} does not include free delivery.",
                    f"A shipping charge applies to {name}.",
                )
            return options[family], f"shipping_free_{family + 1}_v1"
        case ClaimType.AVAILABILITY:
            status = value.value if isinstance(value, InventoryStatus) else str(value)
            phrase = status.replace("_", " ")
            options = (
                f"{name} is {phrase}.",
                f"The current availability for {name} is {phrase}.",
                f"According to the listing, {name} is {phrase}.",
            )
            return options[family], f"availability_{family + 1}_v1"
        case ClaimType.FEATURE_INCLUSION:
            feature = feature_text(str(value))
            options = (
                f"{name} includes {feature}.",
                f"{feature.capitalize()} comes with {name}.",
                f"Customers get {feature} with {name}.",
            )
            return options[family], f"feature_included_{family + 1}_v1"
        case ClaimType.FEATURE_EXCLUSION:
            feature = feature_text(str(value))
            options = (
                f"{name} does not include {feature}.",
                f"{feature.capitalize()} is excluded from {name}.",
                f"{name} comes without {feature}.",
            )
            return options[family], f"feature_excluded_{family + 1}_v1"
        case ClaimType.SUBSCRIPTION_TERMS:
            amount = money(value)  # type: ignore[arg-type]
            options = (
                f"{name} costs ${amount} per month.",
                f"The monthly price for {name} is ${amount}.",
                f"{name} is billed monthly at ${amount}.",
            )
            return options[family], f"subscription_price_{family + 1}_v1"
        case ClaimType.TRIAL_DURATION:
            days = int(str(value))
            options = (
                f"{name} includes a {days}-day free trial.",
                f"You can try {name} free for {days} days.",
                f"The free trial for {name} lasts {days} days.",
            )
            return options[family], f"trial_duration_{family + 1}_v1"
        case ClaimType.PROMOTION_DATES:
            end = value if isinstance(value, date) else date.fromisoformat(str(value))
            rendered = f"{end.strftime('%B')} {end.day}, {end.year}"
            if family == 2:
                rendered = f"{end.strftime('%B')} {end.day}"
            options = (
                f"{name} is valid through {rendered}.",
                f"{name} ends on {rendered}.",
                f"The promotion for {name} runs until {rendered}.",
            )
            return options[family], f"promotion_end_{family + 1}_v1"
        case ClaimType.GEO_ELIGIBILITY:
            region = str(value)
            place = REGION_NAMES.get(region, region)
            options = (
                f"{name} is available in {place}.",
                f"Customers in {place} are eligible for {name}.",
                f"{name} applies to the {region} region.",
            )
            return options[family], f"region_eligibility_{family + 1}_v1"
        case ClaimType.MINIMUM_PURCHASE:
            amount = money(value)  # type: ignore[arg-type]
            options = (
                f"{name} requires a minimum purchase of ${amount}.",
                f"The minimum order for {name} is ${amount}.",
                f"You must spend at least ${amount} to use {name}.",
            )
            return options[family], f"minimum_purchase_{family + 1}_v1"
        case _:
            raise ValueError(f"no template family for {claim_type.value}")
