from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from app.domain.enums import ClaimType, Verdict
from app.evaluation.generator import (
    DEFAULT_SEED,
    build_manifest,
    generate_samples,
    load_catalog,
    serialize_samples,
    write_bundle,
)
from app.evaluation.models import MutationType, SourceKind
from app.evaluation.validator import (
    DatasetValidationError,
    load_samples,
    validate_bundle,
    validate_samples,
)

CATALOG = Path("data/reference/catalog.jsonl")
FROZEN = Path("data/evaluation/v1/benchmark_500.jsonl")


@pytest.fixture(scope="module")
def generated():
    records, fingerprint = load_catalog(CATALOG)
    return records, fingerprint, generate_samples(records, seed=DEFAULT_SEED)


def test_generation_is_byte_deterministic(generated) -> None:
    records, fingerprint, first = generated
    second = generate_samples(records, seed=DEFAULT_SEED)
    assert first == second
    assert serialize_samples(first) == serialize_samples(second)
    assert build_manifest(first, seed=DEFAULT_SEED, catalog_fingerprint=fingerprint) == (
        build_manifest(second, seed=DEFAULT_SEED, catalog_fingerprint=fingerprint)
    )


def test_exact_composition_and_all_claim_types(generated) -> None:
    _records, _fingerprint, samples = generated
    assert Counter(sample.expected_verdict for sample in samples) == {
        Verdict.SUPPORTED: 200,
        Verdict.CONTRADICTED: 220,
        Verdict.INSUFFICIENT_EVIDENCE: 50,
        Verdict.INVALID_CLAIM: 30,
    }
    represented = {sample.claim_type for sample in samples}
    assert set(ClaimType) - {ClaimType.UNKNOWN} <= represented
    assert all(sum(sample.claim_type is kind for sample in samples) >= 20 for kind in represented)


def test_every_mutation_category_is_independently_validated(generated) -> None:
    records, _fingerprint, samples = generated
    assert validate_samples(samples, records) == []
    assert {sample.mutation.type for sample in samples if sample.mutation} == set(MutationType)


def test_qualifier_cases_cover_boundaries(generated) -> None:
    _records, _fingerprint, samples = generated
    claims = [sample.claim.casefold() for sample in samples]
    assert any("up to" in claim for claim in claims)
    assert any("at least" in claim for claim in claims)
    assert any("starts at" in claim for claim in claims)
    assert any("approximately" in claim for claim in claims)
    assert any(
        sample.expected_verdict is Verdict.CONTRADICTED and "up to" in sample.claim.casefold()
        for sample in samples
    )


def test_missing_evidence_is_never_a_mutated_contradiction(generated) -> None:
    records, _fingerprint, samples = generated
    by_id = {record.record_id: record for record in records}
    insufficient = [sample for sample in samples if sample.source_kind is SourceKind.INSUFFICIENT]
    assert len(insufficient) == 50
    assert all(sample.mutation is None for sample in insufficient)
    for sample in insufficient:
        record = by_id[sample.reference_id]
        if sample.claim_type is ClaimType.MINIMUM_PURCHASE:
            assert record.minimum_purchase is None


def test_feature_three_way_semantics(generated) -> None:
    records, _fingerprint, samples = generated
    by_id = {record.record_id: record for record in records}
    for sample in samples:
        if sample.mutation and sample.mutation.type is MutationType.FEATURE_INCLUSION_MISMATCH:
            assert (
                str(sample.mutation.mutated_value) in by_id[sample.reference_id].excluded_features
            )
        if sample.mutation and sample.mutation.type is MutationType.FEATURE_EXCLUSION_MISMATCH:
            assert (
                str(sample.mutation.mutated_value) in by_id[sample.reference_id].included_features
            )


def test_dates_and_regions_have_explicit_valid_ground_truth(generated) -> None:
    records, _fingerprint, samples = generated
    by_id = {record.record_id: record for record in records}
    date_samples = [sample for sample in samples if sample.claim_type is ClaimType.PROMOTION_DATES]
    assert all(sample.as_of is not None for sample in date_samples)
    assert any(sample.generation.template_id == "promotion_end_3_v1" for sample in date_samples)
    assert any(sample.as_of < by_id[sample.reference_id].offer_start for sample in date_samples)
    assert any(sample.as_of == by_id[sample.reference_id].offer_start for sample in date_samples)
    assert any(
        by_id[sample.reference_id].offer_start < sample.as_of < by_id[sample.reference_id].offer_end
        for sample in date_samples
    )
    assert any(sample.as_of == by_id[sample.reference_id].offer_end for sample in date_samples)
    assert any(sample.as_of > by_id[sample.reference_id].offer_end for sample in date_samples)
    for sample in samples:
        if sample.mutation and sample.mutation.type is MutationType.REGION_MISMATCH:
            assert (
                str(sample.mutation.mutated_value)
                not in by_id[sample.reference_id].eligible_regions
            )


def test_conversion_drops_all_evaluation_only_fields(generated) -> None:
    _records, _fingerprint, samples = generated
    request = samples[0].to_verification_request()
    assert set(request.model_dump()) == {
        "claim",
        "reference_id",
        "sku",
        "region",
        "as_of",
        "request_id",
        "allow_llm",
    }
    dumped = request.model_dump_json()
    for forbidden in ("expected_verdict", "mutation", "difficulty", "source_value"):
        assert forbidden not in dumped


def test_duplicate_sample_is_rejected(generated) -> None:
    records, _fingerprint, samples = generated
    duplicate = samples[0].model_copy(update={"sample_id": "eval-999999"})
    errors = validate_samples([*samples, duplicate], records)
    assert any("duplicate claims" in error for error in errors)
    assert any("claim/reference" in error for error in errors)


def test_bundle_manifest_hash_and_tampering(tmp_path: Path) -> None:
    output = tmp_path / "benchmark_500.jsonl"
    manifest, coverage = write_bundle(output, catalog_path=CATALOG)
    summary = validate_bundle(output, catalog_path=CATALOG)
    assert summary.samples == manifest.sample_count == 500
    assert coverage.duplicate_claims == 0
    output.write_bytes(output.read_bytes() + b"\n")
    with pytest.raises(DatasetValidationError, match="SHA-256"):
        validate_bundle(output, catalog_path=CATALOG)


def test_frozen_dataset_matches_fresh_generation(generated) -> None:
    _records, fingerprint, samples = generated
    frozen = load_samples(FROZEN)
    assert FROZEN.read_bytes() == serialize_samples(samples)
    manifest = build_manifest(samples, seed=DEFAULT_SEED, catalog_fingerprint=fingerprint)
    assert manifest.sample_count == len(frozen) == 500
