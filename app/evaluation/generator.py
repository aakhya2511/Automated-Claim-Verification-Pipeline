"""Deterministic stratified generation of the frozen 500-sample benchmark."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.datagen.catalog import ANCHOR_DATE
from app.domain.enums import ClaimType, Verdict
from app.domain.models import ReferenceRecord
from app.evaluation.coverage import build_coverage
from app.evaluation.models import (
    CoverageReport,
    DatasetManifest,
    Difficulty,
    EvaluationSample,
    GenerationMetadata,
    MutationMetadata,
    SourceKind,
)
from app.evaluation.mutations import mutation_for, source_value
from app.evaluation.templates import difficulty_for, feature_text, render, subject

DATASET_VERSION = "v1"
GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20260909
DEFAULT_SAMPLE_COUNT = 500

SUPPORTED_TYPES = (
    ClaimType.PRICE,
    ClaimType.DISCOUNT,
    ClaimType.SHIPPING,
    ClaimType.AVAILABILITY,
    ClaimType.FEATURE_INCLUSION,
    ClaimType.FEATURE_EXCLUSION,
    ClaimType.SUBSCRIPTION_TERMS,
    ClaimType.TRIAL_DURATION,
    ClaimType.PROMOTION_DATES,
    ClaimType.GEO_ELIGIBILITY,
    ClaimType.MINIMUM_PURCHASE,
)

CLEAN_QUOTAS = dict.fromkeys(SUPPORTED_TYPES, 18) | {
    ClaimType.PRICE: 19,
    ClaimType.FEATURE_INCLUSION: 19,
}
MUTATION_QUOTAS = dict.fromkeys(SUPPORTED_TYPES, 20)

UNKNOWN_FEATURES = (
    "extended_warranty",
    "gift_wrapping",
    "weekend_delivery",
    "accidental_damage_cover",
    "installation_service",
    "loaner_device",
    "trade_in_credit",
    "white_glove_setup",
    "international_support",
    "replacement_guarantee",
)

INVALID_PHRASES = (
    "Best offer ever for {subject}!",
    "Buy {subject} today!",
    "Great value from {subject}.",
    "You will love {subject}.",
    "A smarter choice: {subject}.",
    "Discover {subject} now.",
    "Premium quality with {subject}.",
    "Do not miss {subject}.",
    "An amazing deal on {subject}.",
    "Everything you want in {subject}.",
)


class _Selector:
    def __init__(self, records: list[ReferenceRecord], seed: int) -> None:
        self.records = records
        self.seed = seed
        self.uses: Counter[str] = Counter()

    def choose(self, candidates: list[ReferenceRecord], *, salt: str) -> ReferenceRecord:
        if not candidates:
            raise ValueError(f"catalog has no candidates for {salt}")
        chosen = min(
            candidates,
            key=lambda record: (
                self.uses[record.record_id],
                _stable_rank(self.seed, salt, record.record_id),
            ),
        )
        self.uses[chosen.record_id] += 1
        return chosen


def load_catalog(path: Path) -> tuple[list[ReferenceRecord], str]:
    raw = path.read_bytes()
    records = [
        ReferenceRecord.model_validate_json(line) for line in raw.splitlines() if line.strip()
    ]
    return records, hashlib.sha256(raw).hexdigest()


def generate_samples(
    records: list[ReferenceRecord], *, seed: int = DEFAULT_SEED, count: int = 500
) -> list[EvaluationSample]:
    if count != DEFAULT_SAMPLE_COUNT:
        raise ValueError("frozen benchmark v1 requires exactly 500 samples")
    selector = _Selector(records, seed)
    samples: list[EvaluationSample] = []

    for claim_type in SUPPORTED_TYPES:
        candidates = _candidates(records, claim_type)
        for index in range(CLEAN_QUOTAS[claim_type]):
            record = selector.choose(candidates, salt=f"clean:{claim_type.value}:{index}")
            samples.append(_clean_sample(record, claim_type, index=index, seed=seed))

    for claim_type in SUPPORTED_TYPES:
        candidates = _candidates(records, claim_type)
        for index in range(MUTATION_QUOTAS[claim_type]):
            record = selector.choose(candidates, salt=f"mutated:{claim_type.value}:{index}")
            samples.append(_mutated_sample(record, claim_type, index=index, seed=seed))

    samples.extend(_insufficient_samples(records, selector=selector, seed=seed))
    samples.extend(_invalid_samples(records, selector=selector, seed=seed))
    if len(samples) != count:
        raise AssertionError(f"generator composition produced {len(samples)}, expected {count}")

    random.Random(seed).shuffle(samples)
    return [
        sample.model_copy(update={"sample_id": f"eval-{index:06d}"})
        for index, sample in enumerate(samples, start=1)
    ]


def serialize_samples(samples: list[EvaluationSample]) -> bytes:
    return ("\n".join(sample.model_dump_json() for sample in samples) + "\n").encode()


def build_manifest(
    samples: list[EvaluationSample], *, seed: int, catalog_fingerprint: str
) -> DatasetManifest:
    dataset_bytes = serialize_samples(samples)
    return DatasetManifest(
        dataset_version=DATASET_VERSION,
        sample_count=len(samples),
        seed=seed,
        generator_version=GENERATOR_VERSION,
        catalog_fingerprint=catalog_fingerprint,
        sha256=hashlib.sha256(dataset_bytes).hexdigest(),
        verdict_distribution=_distribution(samples, "expected_verdict"),
        claim_type_distribution=_distribution(samples, "claim_type"),
        mutation_distribution=dict(
            sorted(
                Counter(
                    sample.mutation.type.value for sample in samples if sample.mutation is not None
                ).items()
            )
        ),
        difficulty_distribution=_distribution(samples, "difficulty"),
        source_distribution=_distribution(samples, "source_kind"),
    )


def write_bundle(
    output: Path,
    *,
    catalog_path: Path,
    seed: int = DEFAULT_SEED,
    count: int = DEFAULT_SAMPLE_COUNT,
) -> tuple[DatasetManifest, CoverageReport]:
    records, catalog_fingerprint = load_catalog(catalog_path)
    samples = generate_samples(records, seed=seed, count=count)
    dataset_bytes = serialize_samples(samples)
    manifest = build_manifest(samples, seed=seed, catalog_fingerprint=catalog_fingerprint)
    coverage = build_coverage(
        samples,
        dataset_version=DATASET_VERSION,
        catalog_records=len(records),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(dataset_bytes)
    _write_json(output.parent / "manifest.json", manifest.model_dump(mode="json"))
    _write_json(output.parent / "coverage.json", coverage.model_dump(mode="json"))
    _write_audit(output.parent, samples, records)
    _write_report(output.parent, manifest, coverage)
    return manifest, coverage


def _clean_sample(
    record: ReferenceRecord, claim_type: ClaimType, *, index: int, seed: int
) -> EvaluationSample:
    source = source_value(claim_type, record, variant=index)
    value = source
    qualifier = "exact"
    if claim_type is ClaimType.PRICE and index % 9 == 7:
        qualifier = "approximately"
    elif claim_type is ClaimType.PRICE and index % 9 == 8:
        qualifier = "starting_at"
        value = max(Decimal("0"), Decimal(str(source)) - Decimal("10"))
    elif claim_type is ClaimType.DISCOUNT and index % 8 == 6:
        qualifier = "up_to"
        value = float(str(source)) + 5
    elif claim_type is ClaimType.DISCOUNT and index % 8 == 7:
        qualifier = "at_least"
    claim, template_id = render(
        claim_type,
        record,
        value,
        variant=index,
        qualifier_mode=qualifier,
    )
    return _sample(
        claim=claim,
        record=record,
        expected=Verdict.SUPPORTED,
        claim_type=claim_type,
        source_kind=SourceKind.CLEAN,
        difficulty=difficulty_for(index),
        template_id=template_id,
        seed=seed,
        explanation=f"Reference {claim_type.value} value {source!s} satisfies the claim.",
        as_of=_as_of(record, claim_type, index=index),
        region=str(value) if claim_type is ClaimType.GEO_ELIGIBILITY else None,
    )


def _mutated_sample(
    record: ReferenceRecord, claim_type: ClaimType, *, index: int, seed: int
) -> EvaluationSample:
    mutation = mutation_for(claim_type, record, variant=index)
    qualifier = "exact"
    if claim_type is ClaimType.DISCOUNT and index % 10 == 8:
        qualifier = "at_least"
    elif claim_type is ClaimType.DISCOUNT and index % 10 == 9:
        qualifier = "up_to"
        source_float = float(str(mutation.source_value))
        mutation = mutation.model_copy(update={"mutated_value": max(0.0, source_float - 1.0)})
    elif claim_type is ClaimType.PRICE and index % 10 == 9:
        qualifier = "under"
        source_decimal = Decimal(str(mutation.source_value))
        mutation = mutation.model_copy(
            update={"mutated_value": max(Decimal("0"), source_decimal - 1)}
        )
    claim, template_id = render(
        claim_type,
        record,
        mutation.mutated_value,
        variant=index,
        qualifier_mode=qualifier,
    )
    return _sample(
        claim=claim,
        record=record,
        expected=Verdict.CONTRADICTED,
        claim_type=claim_type,
        source_kind=SourceKind.MUTATED,
        difficulty=difficulty_for(index + 1),
        template_id=template_id,
        seed=seed,
        explanation=(
            f"Injected {mutation.type.value}: reference value {mutation.source_value!s}; "
            f"claim value {mutation.mutated_value!s}."
        ),
        mutation=mutation,
        as_of=_as_of(record, claim_type, index=index),
        region=(str(mutation.mutated_value) if claim_type is ClaimType.GEO_ELIGIBILITY else None),
    )


def _insufficient_samples(
    records: list[ReferenceRecord], *, selector: _Selector, seed: int
) -> list[EvaluationSample]:
    samples: list[EvaluationSample] = []
    feature_records = [record for record in records if record.included_features]
    for index in range(20):
        record = selector.choose(feature_records, salt=f"insufficient:feature:{index}")
        feature = next(
            candidate
            for candidate in UNKNOWN_FEATURES[index % len(UNKNOWN_FEATURES) :] + UNKNOWN_FEATURES
            if candidate not in record.included_features
            and candidate not in record.excluded_features
        )
        claim, template_id = render(ClaimType.FEATURE_INCLUSION, record, feature, variant=index)
        samples.append(
            _sample(
                claim=claim,
                record=record,
                expected=Verdict.INSUFFICIENT_EVIDENCE,
                claim_type=ClaimType.FEATURE_INCLUSION,
                source_kind=SourceKind.INSUFFICIENT,
                difficulty=Difficulty.HARD if index % 2 else Difficulty.MODERATE,
                template_id=f"unknown_{template_id}",
                seed=seed,
                explanation=(
                    f"Feature {feature_text(feature)} is neither explicitly included nor excluded."
                ),
            )
        )

    no_minimum = [record for record in records if record.minimum_purchase is None]
    for index in range(15):
        record = selector.choose(no_minimum, salt=f"insufficient:minimum:{index}")
        amount = Decimal(25 + index)
        claim, template_id = render(ClaimType.MINIMUM_PURCHASE, record, amount, variant=index)
        samples.append(
            _sample(
                claim=claim,
                record=record,
                expected=Verdict.INSUFFICIENT_EVIDENCE,
                claim_type=ClaimType.MINIMUM_PURCHASE,
                source_kind=SourceKind.INSUFFICIENT,
                difficulty=Difficulty.HARD,
                template_id=f"absent_{template_id}",
                seed=seed,
                explanation="The known reference record has no minimum-purchase field.",
            )
        )

    for index in range(15):
        record = selector.choose(records, salt=f"insufficient:warranty:{index}")
        years = 2 + index % 4
        samples.append(
            _sample(
                claim=f"{subject(record)} includes a {years}-year warranty.",
                record=record,
                expected=Verdict.INSUFFICIENT_EVIDENCE,
                claim_type=ClaimType.UNKNOWN,
                source_kind=SourceKind.INSUFFICIENT,
                difficulty=Difficulty.HARD,
                template_id="warranty_absent_v1",
                seed=seed,
                explanation="The catalog schema and known record provide no warranty evidence.",
            )
        )
    return samples


def _invalid_samples(
    records: list[ReferenceRecord], *, selector: _Selector, seed: int
) -> list[EvaluationSample]:
    samples: list[EvaluationSample] = []
    for index in range(30):
        record = selector.choose(records, salt=f"invalid:{index}")
        template = INVALID_PHRASES[index % len(INVALID_PHRASES)]
        claim = template.format(subject=subject(record))
        samples.append(
            _sample(
                claim=claim,
                record=record,
                expected=Verdict.INVALID_CLAIM,
                claim_type=ClaimType.UNKNOWN,
                source_kind=SourceKind.INVALID,
                difficulty=(Difficulty.MODERATE, Difficulty.HARD)[index % 2],
                template_id=f"invalid_marketing_{index % len(INVALID_PHRASES) + 1}_v1",
                seed=seed,
                explanation="Marketing language contains no testable commercial proposition.",
            )
        )
    return samples


def _sample(
    *,
    claim: str,
    record: ReferenceRecord,
    expected: Verdict,
    claim_type: ClaimType,
    source_kind: SourceKind,
    difficulty: Difficulty,
    template_id: str,
    seed: int,
    explanation: str,
    mutation: MutationMetadata | None = None,
    as_of: date | None = ANCHOR_DATE,
    region: str | None = None,
) -> EvaluationSample:
    return EvaluationSample(
        sample_id="eval-000000",
        claim=claim,
        reference_id=record.record_id,
        region=region,
        as_of=as_of,
        expected_verdict=expected,
        claim_type=claim_type,
        source_kind=source_kind,
        difficulty=difficulty,
        mutation=mutation,
        generation=GenerationMetadata(
            template_id=template_id,
            seed=seed,
            generator_version=GENERATOR_VERSION,
        ),
        ground_truth_explanation=explanation,
    )


def _candidates(records: list[ReferenceRecord], claim_type: ClaimType) -> list[ReferenceRecord]:
    return [record for record in records if _supports(record, claim_type)]


def _supports(record: ReferenceRecord, claim_type: ClaimType) -> bool:
    match claim_type:
        case ClaimType.PRICE:
            return record.price is not None
        case ClaimType.DISCOUNT:
            return record.discount_percent is not None
        case ClaimType.SHIPPING:
            return record.free_shipping is not None
        case ClaimType.AVAILABILITY:
            return record.inventory_status is not None
        case ClaimType.FEATURE_INCLUSION:
            return bool(record.included_features and record.excluded_features)
        case ClaimType.FEATURE_EXCLUSION:
            return bool(record.included_features and record.excluded_features)
        case ClaimType.SUBSCRIPTION_TERMS:
            return record.subscription_price is not None
        case ClaimType.TRIAL_DURATION:
            return record.trial_days is not None and record.trial_days > 0
        case ClaimType.PROMOTION_DATES:
            return record.offer_end is not None and record.offer_start is not None
        case ClaimType.GEO_ELIGIBILITY:
            return bool(record.eligible_regions)
        case ClaimType.MINIMUM_PURCHASE:
            return record.minimum_purchase is not None
        case _:
            return False


def _as_of(record: ReferenceRecord, claim_type: ClaimType, *, index: int) -> date:
    if (
        claim_type is ClaimType.PROMOTION_DATES
        and record.offer_start is not None
        and record.offer_end is not None
    ):
        midpoint = record.offer_start + (record.offer_end - record.offer_start) // 2
        contexts = (
            record.offer_start - timedelta(days=1),
            record.offer_start,
            midpoint,
            record.offer_end,
            record.offer_end + timedelta(days=1),
        )
        return contexts[index % len(contexts)]
    return ANCHOR_DATE


def _distribution(samples: list[EvaluationSample], field: str) -> dict[str, int]:
    values = (getattr(sample, field).value for sample in samples)
    return dict(sorted(Counter(values).items()))


def _stable_rank(seed: int, salt: str, record_id: str) -> str:
    return hashlib.sha256(f"{seed}:{salt}:{record_id}".encode()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_audit(
    directory: Path, samples: list[EvaluationSample], records: list[ReferenceRecord]
) -> None:
    by_id = {record.record_id: record for record in records}
    selected: list[EvaluationSample] = []
    seen: set[tuple[str, str]] = set()
    for sample in samples:
        key = (sample.expected_verdict.value, sample.claim_type.value)
        if key not in seen:
            selected.append(sample)
            seen.add(key)
    for sample in samples:
        if len(selected) >= 40:
            break
        if sample not in selected:
            selected.append(sample)
    lines = ["# Evaluation V1 Human Audit", "", f"Stratified examples: {len(selected)}", ""]
    for sample in selected:
        record = by_id.get(sample.reference_id or "")
        lines.extend(
            [
                f"## {sample.sample_id} — {sample.expected_verdict.value}",
                "",
                f"- Claim: {sample.claim}",
                f"- Reference: {sample.reference_id}",
                f"- Claim type: {sample.claim_type.value}",
                f"- Difficulty: {sample.difficulty.value}",
                f"- As of: {sample.as_of}",
                f"- Relevant source value: {_audit_value(sample, record)}",
                f"- Mutation: {sample.mutation.model_dump_json() if sample.mutation else 'none'}",
                f"- Ground truth: {sample.ground_truth_explanation}",
                "",
            ]
        )
    (directory / "audit.md").write_text("\n".join(lines), encoding="utf-8")


def _write_report(directory: Path, manifest: DatasetManifest, coverage: CoverageReport) -> None:
    lines = [
        "# Evaluation Dataset V1",
        "",
        "This directory is the frozen V1 benchmark. Ground truth is generated from structured",
        "catalog data and controlled mutations; no LLM labels are used.",
        "",
        f"- Samples: {manifest.sample_count}",
        (
            f"- Unique catalog records: {coverage.unique_reference_records} / "
            f"{coverage.catalog_records} ({coverage.catalog_coverage_percent:.2f}%)"
        ),
        f"- Dataset SHA-256: `{manifest.sha256}`",
        f"- Catalog fingerprint: `{manifest.catalog_fingerprint}`",
        "",
        "## Verdicts",
        "",
        *_report_counts(manifest.verdict_distribution),
        "",
        "## Claim types",
        "",
        *_report_counts(manifest.claim_type_distribution),
        "",
        "## Difficulty",
        "",
        *_report_counts(manifest.difficulty_distribution),
        "",
        "## Injected mismatches",
        "",
        *_report_counts(manifest.mutation_distribution),
        "",
        "## Freeze policy",
        "",
        "Do not rewrite V1 after inspecting system predictions. Proven label, generator, or",
        "corruption fixes require a documented v1.1 or v2 dataset.",
        "",
    ]
    (directory / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _report_counts(counts: dict[str, int]) -> list[str]:
    width = max((len(key) for key in counts), default=0)
    return [f"- `{key:<{width}}` {value}" for key, value in counts.items()]


def _audit_value(sample: EvaluationSample, record: ReferenceRecord | None) -> object:
    if record is None or sample.claim_type is ClaimType.UNKNOWN:
        return "field absent from catalog schema"
    if sample.claim_type is ClaimType.FEATURE_INCLUSION:
        return record.included_features
    if sample.claim_type is ClaimType.FEATURE_EXCLUSION:
        return record.excluded_features
    if sample.claim_type is ClaimType.GEO_ELIGIBILITY:
        return record.eligible_regions
    if sample.claim_type is ClaimType.PROMOTION_DATES:
        return {"offer_start": record.offer_start, "offer_end": record.offer_end}
    if sample.mutation is not None:
        return sample.mutation.source_value
    try:
        return source_value(sample.claim_type, record, variant=0)
    except (ValueError, ZeroDivisionError):
        return "field absent"
