"""Independent structural and semantic checks for a frozen dataset bundle."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.core.pipeline_config import RuleConfig
from app.domain.enums import Attribute, ClaimType, Operator, Qualifier, Verdict
from app.domain.models import ReferenceRecord
from app.evaluation.coverage import build_coverage
from app.evaluation.generator import (
    CLEAN_QUOTAS,
    DATASET_VERSION,
    DEFAULT_SAMPLE_COUNT,
    GENERATOR_VERSION,
    MUTATION_QUOTAS,
    build_manifest,
    load_catalog,
)
from app.evaluation.models import (
    CoverageReport,
    DatasetManifest,
    EvaluationSample,
    MutationType,
    SourceKind,
)
from app.rules.base import compare_numeric, tolerance_for


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    samples: int
    coverage: CoverageReport
    verdicts: dict[str, int]
    claim_types: dict[str, int]
    mutations: dict[str, int]
    difficulties: dict[str, int]


def load_samples(path: Path) -> list[EvaluationSample]:
    return [
        EvaluationSample.model_validate_json(line)
        for line in path.read_bytes().splitlines()
        if line.strip()
    ]


def validate_bundle(
    dataset_path: Path,
    *,
    catalog_path: Path,
    manifest_path: Path | None = None,
) -> ValidationSummary:
    dataset_bytes = dataset_path.read_bytes()
    samples = load_samples(dataset_path)
    records, catalog_fingerprint = load_catalog(catalog_path)
    manifest_file = manifest_path or dataset_path.parent / "manifest.json"
    manifest = DatasetManifest.model_validate_json(manifest_file.read_bytes())
    errors = validate_samples(samples, records)
    actual_hash = hashlib.sha256(dataset_bytes).hexdigest()
    if manifest.sample_count != len(samples):
        errors.append("manifest sample_count does not match dataset")
    if manifest.sha256 != actual_hash:
        errors.append("manifest SHA-256 does not match dataset bytes")
    if manifest.catalog_fingerprint != catalog_fingerprint:
        errors.append("manifest catalog fingerprint does not match source catalog")
    if manifest.generator_version != GENERATOR_VERSION:
        errors.append("manifest generator version is not current")
    derived_manifest = build_manifest(
        samples,
        seed=manifest.seed,
        catalog_fingerprint=catalog_fingerprint,
    )
    if manifest != derived_manifest:
        errors.append("manifest distributions or freeze metadata do not match dataset")
    if errors:
        raise DatasetValidationError("; ".join(errors))
    coverage = build_coverage(
        samples,
        dataset_version=manifest.dataset_version,
        catalog_records=len(records),
    )
    return ValidationSummary(
        samples=len(samples),
        coverage=coverage,
        verdicts=_counts(sample.expected_verdict.value for sample in samples),
        claim_types=_counts(sample.claim_type.value for sample in samples),
        mutations=_counts(
            sample.mutation.type.value for sample in samples if sample.mutation is not None
        ),
        difficulties=_counts(sample.difficulty.value for sample in samples),
    )


def validate_samples(
    samples: list[EvaluationSample],
    records: list[ReferenceRecord],
    *,
    expected_count: int = DEFAULT_SAMPLE_COUNT,
    enforce_frozen_quotas: bool = True,
    minimum_unique_references: int = 250,
) -> list[str]:
    errors: list[str] = []
    by_id = {record.record_id: record for record in records}
    if len(samples) != expected_count:
        errors.append(f"expected {expected_count} samples, found {len(samples)}")
    _unique(errors, "sample IDs", [sample.sample_id for sample in samples])
    _unique(errors, "claims", [sample.claim.casefold() for sample in samples])
    _unique(
        errors,
        "claim/reference pairs",
        [(sample.claim.casefold(), sample.reference_id) for sample in samples],
    )
    semantic_mutations = [
        (sample.reference_id, sample.mutation.type.value, repr(sample.mutation.mutated_value))
        for sample in samples
        if sample.mutation is not None
    ]
    _unique(errors, "semantic mutations", semantic_mutations)

    for sample in samples:
        if sample.reference_id not in by_id:
            errors.append(f"{sample.sample_id}: unknown reference ID")
            continue
        record = by_id[sample.reference_id]
        errors.extend(_validate_source_kind(sample))
        if sample.claim_type is ClaimType.PROMOTION_DATES and sample.as_of is None:
            errors.append(f"{sample.sample_id}: date claim has no as_of")
        if sample.generation.generator_version != GENERATOR_VERSION:
            errors.append(f"{sample.sample_id}: wrong generator version")
        if sample.mutation is not None:
            error = _validate_mutation(sample, record)
            if error:
                errors.append(f"{sample.sample_id}: {error}")
        if sample.source_kind is SourceKind.INSUFFICIENT:
            error = _validate_insufficient(sample, record)
            if error:
                errors.append(f"{sample.sample_id}: {error}")

    verdicts = Counter(sample.expected_verdict for sample in samples)
    expected_verdicts = {
        Verdict.SUPPORTED: sum(CLEAN_QUOTAS.values()),
        Verdict.CONTRADICTED: sum(MUTATION_QUOTAS.values()),
        Verdict.INSUFFICIENT_EVIDENCE: 50,
        Verdict.INVALID_CLAIM: 30,
    }
    if enforce_frozen_quotas and verdicts != Counter(expected_verdicts):
        errors.append("verdict composition does not match frozen V1 quotas")
    for claim_type in CLEAN_QUOTAS:
        if sum(sample.claim_type is claim_type for sample in samples) < 20:
            errors.append(f"claim type {claim_type.value} lacks meaningful coverage")
    coverage = build_coverage(
        samples,
        dataset_version=DATASET_VERSION,
        catalog_records=len(records),
    )
    if coverage.unique_reference_records < minimum_unique_references:
        errors.append(
            f"catalog coverage uses fewer than {minimum_unique_references} unique records"
        )
    if coverage.duplicate_claims or coverage.duplicate_semantic_mutations:
        errors.append("duplicate statistics are non-zero")
    return errors


def _validate_source_kind(sample: EvaluationSample) -> list[str]:
    expected = {
        SourceKind.CLEAN: Verdict.SUPPORTED,
        SourceKind.MUTATED: Verdict.CONTRADICTED,
        SourceKind.INSUFFICIENT: Verdict.INSUFFICIENT_EVIDENCE,
        SourceKind.INVALID: Verdict.INVALID_CLAIM,
    }[sample.source_kind]
    return (
        []
        if sample.expected_verdict is expected
        else [f"{sample.sample_id}: source kind and expected verdict disagree"]
    )


def _validate_mutation(sample: EvaluationSample, record: ReferenceRecord) -> str | None:
    mutation = sample.mutation
    if mutation is None:
        return "missing mutation metadata"
    mutation_type = mutation.type
    if mutation_type in {
        MutationType.PRICE_MISMATCH,
        MutationType.SUBSCRIPTION_PRICE_MISMATCH,
        MutationType.DISCOUNT_MISMATCH,
        MutationType.TRIAL_DURATION_MISMATCH,
        MutationType.MINIMUM_PURCHASE_MISMATCH,
    }:
        attribute = {
            MutationType.PRICE_MISMATCH: Attribute.PRICE,
            MutationType.SUBSCRIPTION_PRICE_MISMATCH: Attribute.SUBSCRIPTION_PRICE,
            MutationType.DISCOUNT_MISMATCH: Attribute.DISCOUNT_PERCENT,
            MutationType.TRIAL_DURATION_MISMATCH: Attribute.TRIAL_DAYS,
            MutationType.MINIMUM_PURCHASE_MISMATCH: Attribute.MINIMUM_PURCHASE,
        }[mutation_type]
        source = _decimal(record.get_attribute(attribute))
        mutated = _decimal(mutation.mutated_value)
        if source is None or mutated is None:
            return "numeric mutation has missing/non-numeric values"
        if _decimal(mutation.source_value) != source:
            return "numeric mutation source_value does not match the reference"
        tolerance = tolerance_for(
            attribute,
            reference=source,
            qualifier=Qualifier.NONE,
            config=RuleConfig(),
        )
        if compare_numeric(
            claimed=mutated,
            reference=source,
            operator=Operator.EQUALS,
            tolerance=tolerance,
        ):
            return "numeric mutation falls inside production tolerance"
    elif mutation_type is MutationType.SHIPPING_MISMATCH:
        if mutation.source_value is not record.free_shipping:
            return "shipping source_value does not match the reference"
        if bool(mutation.mutated_value) is record.free_shipping:
            return "shipping mutation does not invert the source value"
    elif mutation_type is MutationType.AVAILABILITY_MISMATCH:
        if str(mutation.source_value) != str(record.inventory_status):
            return "availability source_value does not match the reference"
        if str(mutation.mutated_value) == str(record.inventory_status):
            return "availability mutation equals reference status"
    elif mutation_type is MutationType.FEATURE_INCLUSION_MISMATCH:
        if str(mutation.source_value) not in record.included_features:
            return "included-feature source_value is not explicitly included"
        if str(mutation.mutated_value) not in record.excluded_features:
            return "included-feature mutation is not explicitly excluded"
    elif mutation_type is MutationType.FEATURE_EXCLUSION_MISMATCH:
        if str(mutation.source_value) not in record.excluded_features:
            return "excluded-feature source_value is not explicitly excluded"
        if str(mutation.mutated_value) not in record.included_features:
            return "excluded-feature mutation is not explicitly included"
    elif mutation_type is MutationType.PROMOTION_END_MISMATCH:
        if date.fromisoformat(str(mutation.source_value)) != record.offer_end:
            return "promotion source_value does not match the reference"
        if date.fromisoformat(str(mutation.mutated_value)) == record.offer_end:
            return "promotion end mutation equals reference date"
    elif mutation_type is MutationType.REGION_MISMATCH:
        if str(mutation.source_value) not in record.eligible_regions:
            return "region source_value is outside the eligibility list"
        if str(mutation.mutated_value) in record.eligible_regions:
            return "region mutation is inside exhaustive eligibility list"
    return None


def _validate_insufficient(sample: EvaluationSample, record: ReferenceRecord) -> str | None:
    if sample.claim_type is ClaimType.FEATURE_INCLUSION:
        words = sample.claim.casefold()
        known = [
            feature.replace("_", " ")
            for feature in (*record.included_features, *record.excluded_features)
        ]
        if any(feature in words for feature in known):
            return "unknown-feature sample mentions an explicitly known feature"
    elif sample.claim_type is ClaimType.MINIMUM_PURCHASE:
        if record.minimum_purchase is not None:
            return "missing-evidence sample has a minimum-purchase value"
    elif sample.claim_type is ClaimType.UNKNOWN and "warranty" not in sample.claim.casefold():
        return "unknown missing-evidence sample is not the documented warranty case"
    return None


def _unique(errors: list[str], label: str, values: Sequence[Hashable]) -> None:
    if len(values) != len(set(values)):
        errors.append(f"duplicate {label} detected")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except ValueError:
        return None


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
