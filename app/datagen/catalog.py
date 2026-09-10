"""Deterministic generator for the reference catalog (the source of truth).

Why generate rather than ship a hand-written fixture: the evaluation set needs
several hundred records with *internally consistent* structure so that injected
corruptions are the only source of error. Hand-authored data drifts, and
scraping a real catalog would put someone else's licensed pricing data in the
repo.

Everything is driven by one seed, so the catalog is byte-stable across machines
and CI runs. Dates are expressed relative to :data:`ANCHOR_DATE` rather than
``today`` so that promotion-window logic is reproducible forever.

Brand and product names are fictional on purpose: the point is realistic
*structure* (aliases, sparse fields, mixed regions, expired offers), not real
commercial data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.enums import BillingPeriod, EntityType, InventoryStatus
from app.domain.models import ReferenceRecord
from app.normalization import text as textutil

#: Seed and anchor date fully determine the catalog.
CATALOG_SEED = 20260909
ANCHOR_DATE = date(2026, 9, 15)

DEFAULT_PRODUCT_COUNT = 240
DEFAULT_PLAN_COUNT = 90
DEFAULT_OFFER_COUNT = 90

BRANDS = (
    "Aurex",
    "Nimbus",
    "Volterra",
    "Kestrel",
    "Lumen",
    "Brightpath",
    "Northwind",
    "Cobalt",
    "Fernwood",
    "Zephyr",
    "Meridian",
    "Halcyon",
)

REGION_SETS: tuple[tuple[str, ...], ...] = (
    ("US",),
    ("US", "CA"),
    ("US", "CA", "GB"),
    ("US", "CA", "GB", "DE", "FR"),
    ("CA",),
    ("GB", "DE", "FR"),
    ("US", "MX", "BR"),
    ("AU", "NZ"),
)


@dataclass(frozen=True)
class ProductCategory:
    """A product family with its own price band and plausible feature pool."""

    key: str
    lines: tuple[str, ...]
    price_range: tuple[int, int]
    features: tuple[str, ...]
    #: Features shoppers plausibly *expect* but this category omits. Modelled
    #: explicitly so "excludes X" claims have a real source of truth.
    typical_exclusions: tuple[str, ...]


CATEGORIES: tuple[ProductCategory, ...] = (
    ProductCategory(
        key="headphones",
        lines=("Pulse", "Echo", "Aria", "Cadence"),
        price_range=(79, 449),
        features=(
            "active_noise_cancellation",
            "spatial_audio",
            "wireless_charging_case",
            "multipoint_pairing",
            "usb_c_charging",
            "ipx4_water_resistance",
            "transparency_mode",
        ),
        typical_exclusions=("lossless_audio", "wired_adapter", "hard_shell_case"),
    ),
    ProductCategory(
        key="laptop",
        lines=("Vector", "Slate", "Forge", "Atlas"),
        price_range=(699, 2799),
        features=(
            "16_gb_ram",
            "32_gb_ram",
            "512_gb_ssd",
            "1_tb_ssd",
            "oled_display",
            "thunderbolt_4",
            "backlit_keyboard",
            "fingerprint_reader",
        ),
        typical_exclusions=("discrete_gpu", "touchscreen", "sd_card_reader"),
    ),
    ProductCategory(
        key="monitor",
        lines=("Vista", "Pane", "Horizon"),
        price_range=(159, 1299),
        features=(
            "4k_resolution",
            "120_hz_refresh",
            "usb_c_power_delivery",
            "height_adjustable_stand",
            "hdr10",
            "built_in_speakers",
        ),
        typical_exclusions=("vesa_mount_included", "hdmi_2_1", "kvm_switch"),
    ),
    ProductCategory(
        key="smartwatch",
        lines=("Orbit", "Tempo", "Meridian"),
        price_range=(129, 799),
        features=(
            "gps_tracking",
            "heart_rate_monitor",
            "blood_oxygen_sensor",
            "always_on_display",
            "lte_connectivity",
            "sleep_tracking",
        ),
        typical_exclusions=("ecg_monitor", "cellular_calling", "third_party_bands"),
    ),
    ProductCategory(
        key="speaker",
        lines=("Resound", "Hearth", "Chorus"),
        price_range=(49, 599),
        features=(
            "room_calibration",
            "multi_room_audio",
            "voice_assistant",
            "battery_powered",
            "stereo_pairing",
        ),
        typical_exclusions=("line_in_port", "wall_mount_kit", "subwoofer_output"),
    ),
    ProductCategory(
        key="router",
        lines=("Mesh", "Relay", "Beacon"),
        price_range=(89, 649),
        features=(
            "wifi_6e",
            "mesh_networking",
            "parental_controls",
            "vpn_server",
            "2_5_gbe_port",
        ),
        typical_exclusions=("built_in_modem", "poe_support", "sim_slot"),
    ),
    ProductCategory(
        key="tablet",
        lines=("Canvas", "Slab", "Folio"),
        price_range=(199, 1499),
        features=(
            "stylus_support",
            "128_gb_storage",
            "256_gb_storage",
            "cellular_option",
            "laminated_display",
        ),
        typical_exclusions=("keyboard_included", "headphone_jack", "microsd_expansion"),
    ),
    ProductCategory(
        key="vacuum",
        lines=("Sweep", "Tidy", "Drift"),
        price_range=(179, 1199),
        features=(
            "lidar_mapping",
            "self_emptying_base",
            "mopping_module",
            "hepa_filter",
            "app_scheduling",
        ),
        typical_exclusions=("auto_mop_washing", "obstacle_camera", "extra_brush_set"),
    ),
    ProductCategory(
        key="coffee",
        lines=("Brewhouse", "Press", "Ember"),
        price_range=(59, 899),
        features=(
            "built_in_grinder",
            "milk_frother",
            "thermal_carafe",
            "programmable_timer",
            "descaling_alert",
        ),
        typical_exclusions=("dual_boiler", "water_filter_included", "cup_warmer"),
    ),
    ProductCategory(
        key="purifier",
        lines=("Breathe", "Clarity", "Aster"),
        price_range=(99, 749),
        features=(
            "hepa_filter",
            "carbon_filter",
            "air_quality_sensor",
            "quiet_night_mode",
            "app_control",
        ),
        typical_exclusions=("humidifier_function", "uv_sterilization", "spare_filter"),
    ),
    ProductCategory(
        key="desk",
        lines=("Rise", "Level", "Uplift"),
        price_range=(299, 1599),
        features=(
            "electric_height_adjustment",
            "memory_presets",
            "cable_tray",
            "bamboo_surface",
            "anti_collision_sensor",
        ),
        typical_exclusions=("monitor_arm", "assembly_service", "keyboard_tray"),
    ),
    ProductCategory(
        key="camera",
        lines=("Lens", "Frame", "Aperture"),
        price_range=(399, 3499),
        features=(
            "in_body_stabilization",
            "4k_60_video",
            "weather_sealing",
            "dual_card_slots",
            "flip_out_screen",
        ),
        typical_exclusions=("battery_grip", "external_flash", "lens_included"),
    ),
    ProductCategory(
        key="keyboard",
        lines=("Keystone", "Clack", "Tactile"),
        price_range=(39, 349),
        features=(
            "hot_swappable_switches",
            "rgb_backlight",
            "wireless_bluetooth",
            "aluminium_frame",
            "programmable_macros",
        ),
        typical_exclusions=("wrist_rest", "numeric_keypad", "usb_passthrough"),
    ),
    ProductCategory(
        key="blender",
        lines=("Vortex", "Whirl", "Cyclone"),
        price_range=(69, 699),
        features=(
            "variable_speed_control",
            "pulse_mode",
            "tamper_included",
            "dishwasher_safe_jar",
            "preset_programs",
        ),
        typical_exclusions=("vacuum_seal_pump", "travel_cup", "extended_warranty"),
    ),
)

VARIANTS = ("", "Pro", "Max", "Lite", "2", "Pro 2", "Air", "Plus", "SE", "Ultra")

PLAN_FAMILIES = (
    "Atlas Cloud",
    "Meridian Analytics",
    "Northwind CRM",
    "Cobalt Observability",
    "Fernwood Commerce",
    "Halcyon Identity",
    "Zephyr Messaging",
    "Lumen Search",
    "Brightpath Billing",
    "Vertex Data",
    "Solstice Support",
    "Ironwood Storage",
    "Cascade Video",
    "Trellis Docs",
    "Kestrel Workflow",
    "Nimbus Edge",
    "Volterra Payments",
    "Orchard Scheduling",
)

PLAN_TIERS = ("Starter", "Standard", "Pro", "Premium", "Business", "Enterprise")

PLAN_FEATURE_LADDER: dict[str, tuple[str, ...]] = {
    "Starter": ("email_support", "single_project", "community_forum"),
    "Standard": ("email_support", "5_projects", "basic_analytics", "api_access"),
    "Pro": (
        "priority_support",
        "unlimited_projects",
        "advanced_analytics",
        "api_access",
        "custom_domains",
    ),
    "Premium": (
        "priority_support",
        "unlimited_projects",
        "advanced_analytics",
        "unlimited_api_requests",
        "sso",
        "audit_logs",
    ),
    "Business": (
        "priority_support",
        "unlimited_projects",
        "advanced_analytics",
        "unlimited_api_requests",
        "sso",
        "audit_logs",
        "dedicated_csm",
    ),
    "Enterprise": (
        "dedicated_support_engineer",
        "unlimited_projects",
        "advanced_analytics",
        "unlimited_api_requests",
        "sso",
        "audit_logs",
        "custom_contracts",
        "on_premise_option",
    ),
}

PLAN_EXCLUSION_LADDER: dict[str, tuple[str, ...]] = {
    "Starter": ("sso", "audit_logs", "unlimited_api_requests", "priority_support"),
    "Standard": ("sso", "audit_logs", "unlimited_api_requests"),
    "Pro": ("sso", "on_premise_option"),
    "Premium": ("on_premise_option", "custom_contracts"),
    "Business": ("on_premise_option",),
    "Enterprise": (),
}

PLAN_TIER_PRICES: dict[str, tuple[int, int]] = {
    "Starter": (0, 19),
    "Standard": (25, 59),
    "Pro": (59, 149),
    "Premium": (149, 349),
    "Business": (349, 799),
    "Enterprise": (899, 2499),
}

PLAN_TRIAL_CHOICES: dict[str, tuple[int | None, ...]] = {
    "Starter": (0, 7, 14),
    "Standard": (14, 14, 30),
    "Pro": (14, 30, 30),
    "Premium": (30, 30, 60),
    "Business": (30, 60, None),
    "Enterprise": (None, None, 30),
}

OFFER_KINDS = (
    "Seasonal Sale",
    "Flash Deal",
    "Bundle Promotion",
    "Clearance Event",
    "Launch Discount",
    "Loyalty Offer",
    "Back to School",
    "Holiday Savings",
)


def _money(value: float) -> Decimal:
    """Round to cents through ``str`` so no binary float error enters the data."""
    return Decimal(f"{value:.2f}")


def _price_point(rng: random.Random, low: int, high: int) -> Decimal:
    """Pick a psychologically realistic price ending in .99/.95/.00/.49."""
    base = rng.randint(low, high)
    ending = rng.choice((0.99, 0.99, 0.95, 0.00, 0.49))
    return _money(base + ending)


def _timestamp(rng: random.Random, days_back: int) -> datetime:
    offset = timedelta(
        days=rng.randint(0, days_back),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )
    anchor = datetime.combine(ANCHOR_DATE, datetime.min.time(), tzinfo=UTC)
    return anchor - offset


def _unique_id(prefix: str, name: str, taken: set[str]) -> str:
    base = f"{prefix}-{textutil.slug(name)}"[:60]
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _sample_features(
    rng: random.Random, category: ProductCategory
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Choose included/excluded features, guaranteeing they never overlap.

    Overlap would make the record self-contradictory and would poison any
    feature claim generated from it.
    """
    pool = list(category.features)
    rng.shuffle(pool)
    include_count = rng.randint(2, min(5, len(pool)))
    included = tuple(sorted(pool[:include_count]))

    remaining = [feature for feature in pool[include_count:] if feature not in included]
    exclusion_pool = [*remaining, *category.typical_exclusions]
    rng.shuffle(exclusion_pool)
    exclude_count = rng.randint(1, min(3, len(exclusion_pool)))
    excluded = tuple(sorted(set(exclusion_pool[:exclude_count]) - set(included)))
    return included, excluded


def _product_aliases(rng: random.Random, brand: str, model: str, sku: str) -> tuple[str, ...]:
    """Realistic alternate surface forms.

    Real claims rarely use the canonical catalog string, so alias coverage is
    part of the source of truth rather than an afterthought in the matcher.
    """
    aliases: list[str] = [model]
    if rng.random() < 0.45:
        aliases.append(f"{brand} {model}".replace(" ", ""))
    if rng.random() < 0.35:
        aliases.append(sku)
    if rng.random() < 0.25:
        aliases.append(f"{model} by {brand}")
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _generate_products(rng: random.Random, count: int) -> list[ReferenceRecord]:
    combos = [
        (brand, category, line, variant)
        for brand in BRANDS
        for category in CATEGORIES
        for line in category.lines
        for variant in VARIANTS
    ]
    rng.shuffle(combos)

    records: list[ReferenceRecord] = []
    taken: set[str] = set()
    seen_names: set[str] = set()

    for brand, category, line, variant in combos:
        if len(records) >= count:
            break
        model = f"{line} {variant}".strip()
        product_name = f"{brand} {model}"
        if product_name in seen_names:
            continue
        seen_names.add(product_name)

        record_id = _unique_id("prod", product_name, taken)
        sku = f"SKU-{category.key[:3].upper()}-{rng.randint(1000, 9999)}"
        price = _price_point(rng, *category.price_range)
        included, excluded = _sample_features(rng, category)

        # Free shipping is correlated with price, as it is in real catalogs;
        # this keeps shipping claims from being trivially predictable.
        free_shipping = price >= Decimal("150") or rng.random() < 0.2
        shipping_cost = (
            _money(0.0) if free_shipping else _money(rng.choice((4.99, 6.99, 9.99, 12.99)))
        )

        has_discount = rng.random() < 0.35
        discount_percent: float | None = None
        offer_start: date | None = None
        offer_end: date | None = None
        if has_discount:
            discount_percent = float(rng.choice((5, 10, 15, 20, 25, 30, 40, 50)))
            start_offset = rng.randint(-120, 20)
            offer_start = ANCHOR_DATE + timedelta(days=start_offset)
            offer_end = offer_start + timedelta(days=rng.choice((7, 14, 21, 30, 45, 60, 90)))

        records.append(
            ReferenceRecord(
                record_id=record_id,
                entity_type=EntityType.PRODUCT,
                product_id=record_id,
                sku=sku,
                product_name=product_name,
                brand=brand,
                aliases=_product_aliases(rng, brand, model, sku),
                price=price,
                currency="USD",
                discount_percent=discount_percent,
                shipping_cost=shipping_cost,
                free_shipping=free_shipping,
                inventory_status=rng.choices(
                    (
                        InventoryStatus.IN_STOCK,
                        InventoryStatus.LIMITED_STOCK,
                        InventoryStatus.OUT_OF_STOCK,
                        InventoryStatus.PREORDER,
                        InventoryStatus.DISCONTINUED,
                    ),
                    weights=(62, 15, 12, 7, 4),
                )[0],
                offer_start=offer_start,
                offer_end=offer_end,
                eligible_regions=rng.choice(REGION_SETS),
                minimum_purchase=(
                    _money(rng.choice((25, 35, 50, 75))) if rng.random() < 0.18 else None
                ),
                included_features=included,
                excluded_features=excluded,
                terms=(
                    f"{category.key.replace('_', ' ').title()} warranty: "
                    f"{rng.choice((12, 24, 36))} months. Returns accepted within "
                    f"{rng.choice((14, 30, 45))} days of delivery."
                ),
                updated_at=_timestamp(rng, days_back=90),
            )
        )
    return records


def _generate_plans(rng: random.Random, count: int) -> list[ReferenceRecord]:
    combos = [(family, tier) for family in PLAN_FAMILIES for tier in PLAN_TIERS]
    rng.shuffle(combos)

    records: list[ReferenceRecord] = []
    taken: set[str] = set()
    #: Only the first family seen gets the bare "<Tier> plan" alias, so that a
    #: claim saying just "the Pro plan" resolves to exactly one record instead
    #: of silently picking one of nine.
    flagship_family = PLAN_FAMILIES[0]

    for family, tier in combos:
        if len(records) >= count:
            break
        record_id = _unique_id("plan", f"{family} {tier}", taken)
        billing_period = rng.choices(
            (BillingPeriod.MONTHLY, BillingPeriod.ANNUAL, BillingPeriod.QUARTERLY),
            weights=(70, 25, 5),
        )[0]
        monthly_price = _price_point(rng, *PLAN_TIER_PRICES[tier])
        if billing_period is BillingPeriod.ANNUAL:
            subscription_price = _money(float(monthly_price) * 10)
        elif billing_period is BillingPeriod.QUARTERLY:
            subscription_price = _money(float(monthly_price) * 2.85)
        else:
            subscription_price = monthly_price

        aliases = [f"{family} {tier} plan", f"{tier} tier"]
        if family == flagship_family:
            aliases.append(f"{tier} plan")

        trial_days = rng.choice(PLAN_TRIAL_CHOICES[tier])

        records.append(
            ReferenceRecord(
                record_id=record_id,
                entity_type=EntityType.PLAN,
                plan_name=tier,
                brand=family,
                aliases=tuple(dict.fromkeys(aliases)),
                currency="USD",
                subscription_price=subscription_price,
                billing_period=billing_period,
                trial_days=trial_days,
                eligible_regions=rng.choice(REGION_SETS),
                included_features=tuple(sorted(PLAN_FEATURE_LADDER[tier])),
                excluded_features=tuple(sorted(PLAN_EXCLUSION_LADDER[tier])),
                minimum_purchase=(
                    _money(rng.choice((100, 250, 500))) if tier == "Enterprise" else None
                ),
                terms=(
                    f"{family} {tier} is billed {billing_period.value}. "
                    "Cancel at any time before the next billing date."
                ),
                updated_at=_timestamp(rng, days_back=60),
            )
        )
    return records


def _generate_offers(
    rng: random.Random, count: int, products: list[ReferenceRecord]
) -> list[ReferenceRecord]:
    """Promotional offers attached to real products.

    Windows are spread deliberately around :data:`ANCHOR_DATE`: roughly a third
    are already expired and a few have not started, which is what gives the
    date rules and the "expired promotion" corruption something real to catch.
    """
    records: list[ReferenceRecord] = []
    taken: set[str] = set()
    targets = rng.sample(products, k=min(count, len(products)))

    for product in targets:
        kind = rng.choice(OFFER_KINDS)
        code = f"{textutil.squash(kind)[:4].upper()}{rng.randint(100, 999)}"
        name = f"{kind} {code}"
        record_id = _unique_id("offer", name, taken)

        window = rng.choices(
            ("expired", "active", "future"),
            weights=(32, 58, 10),
        )[0]
        if window == "expired":
            offer_end = ANCHOR_DATE - timedelta(days=rng.randint(1, 120))
            offer_start = offer_end - timedelta(days=rng.choice((7, 14, 30, 45)))
        elif window == "future":
            offer_start = ANCHOR_DATE + timedelta(days=rng.randint(3, 60))
            offer_end = offer_start + timedelta(days=rng.choice((7, 14, 30)))
        else:
            offer_start = ANCHOR_DATE - timedelta(days=rng.randint(1, 45))
            offer_end = ANCHOR_DATE + timedelta(days=rng.randint(3, 90))

        discount_percent = float(rng.choice((5, 10, 15, 20, 25, 30, 35, 40, 50, 60)))
        free_shipping = rng.random() < 0.55

        records.append(
            ReferenceRecord(
                record_id=record_id,
                entity_type=EntityType.OFFER,
                product_id=product.record_id,
                sku=product.sku,
                product_name=product.product_name,
                brand=product.brand,
                aliases=(code, f"offer {code}", name),
                price=product.price,
                currency="USD",
                discount_percent=discount_percent,
                free_shipping=free_shipping,
                shipping_cost=_money(0.0) if free_shipping else product.shipping_cost or None,
                offer_start=offer_start,
                offer_end=offer_end,
                eligible_regions=rng.choice(REGION_SETS),
                minimum_purchase=(
                    _money(rng.choice((25, 50, 75, 100, 150))) if rng.random() < 0.45 else None
                ),
                included_features=product.included_features,
                excluded_features=product.excluded_features,
                terms=(
                    f"{kind} {code}: {discount_percent:g}% off {product.product_name} "
                    f"from {offer_start.isoformat()} to {offer_end.isoformat()}. "
                    "One redemption per customer. Cannot be combined with other offers."
                ),
                updated_at=_timestamp(rng, days_back=30),
            )
        )
    return records


def generate_catalog(
    *,
    seed: int = CATALOG_SEED,
    product_count: int = DEFAULT_PRODUCT_COUNT,
    plan_count: int = DEFAULT_PLAN_COUNT,
    offer_count: int = DEFAULT_OFFER_COUNT,
) -> list[ReferenceRecord]:
    """Build the full reference catalog deterministically."""
    rng = random.Random(seed)
    products = _generate_products(rng, product_count)
    plans = _generate_plans(rng, plan_count)
    offers = _generate_offers(rng, offer_count, products)
    return [*products, *plans, *offers]


def serialize_catalog(records: list[ReferenceRecord]) -> str:
    """Render records as JSONL, sorted by ``record_id`` for stable diffs."""
    lines = [
        record.model_dump_json(exclude_none=True)
        for record in sorted(records, key=lambda item: item.record_id)
    ]
    return "\n".join(lines) + "\n"
