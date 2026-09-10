"""Generate and validate the development corpus without touching holdout predictions."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.domain.enums import ClaimType
from app.domain.models import ReferenceRecord
from app.evaluation.coverage import build_coverage
from app.evaluation.generator import (
    GENERATOR_VERSION,
    build_manifest,
    generate_samples,
    load_catalog,
    serialize_samples,
)
from app.evaluation.models import DatasetManifest, EvaluationSample
from app.evaluation.templates import subject
from app.evaluation.validator import DatasetValidationError, load_samples, validate_samples

DEV_VERSION = "dev-v1"
DEV_SEED = 20260910
DEV_SAMPLE_COUNT = 400


class OverlapReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnostic_samples: int
    holdout_samples: int
    exact_claim_overlap: int
    claim_reference_overlap: int
    semantic_mutation_overlap: int
    sample_fingerprint_overlap: int

    @property
    def is_clear(self) -> bool:
        return not any(
            (
                self.exact_claim_overlap,
                self.claim_reference_overlap,
                self.semantic_mutation_overlap,
                self.sample_fingerprint_overlap,
            )
        )


def sample_fingerprint(sample: EvaluationSample) -> str:
    payload = sample.model_dump(mode="json", exclude={"sample_id", "generation"})
    payload["template_id"] = sample.generation.template_id
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def semantic_mutation_signature(sample: EvaluationSample) -> tuple[str, ...] | None:
    if sample.mutation is None:
        return None
    return (
        sample.reference_id or "",
        sample.generation.template_id,
        sample.mutation.type.value,
        repr(sample.mutation.source_value),
        repr(sample.mutation.mutated_value),
    )


def overlap_report(
    diagnostic: list[EvaluationSample], holdout: list[EvaluationSample]
) -> OverlapReport:
    holdout_claims = {sample.claim.casefold() for sample in holdout}
    holdout_pairs = {(sample.claim.casefold(), sample.reference_id) for sample in holdout}
    holdout_mutations = {
        signature
        for sample in holdout
        if (signature := semantic_mutation_signature(sample)) is not None
    }
    holdout_fingerprints = {sample_fingerprint(sample) for sample in holdout}
    return OverlapReport(
        diagnostic_samples=len(diagnostic),
        holdout_samples=len(holdout),
        exact_claim_overlap=sum(sample.claim.casefold() in holdout_claims for sample in diagnostic),
        claim_reference_overlap=sum(
            (sample.claim.casefold(), sample.reference_id) in holdout_pairs for sample in diagnostic
        ),
        semantic_mutation_overlap=sum(
            semantic_mutation_signature(sample) in holdout_mutations
            for sample in diagnostic
            if sample.mutation is not None
        ),
        sample_fingerprint_overlap=sum(
            sample_fingerprint(sample) in holdout_fingerprints for sample in diagnostic
        ),
    )


def generate_diagnostic(
    records: list[ReferenceRecord],
    holdout: list[EvaluationSample],
    *,
    seed: int = DEV_SEED,
    count: int = DEV_SAMPLE_COUNT,
) -> list[EvaluationSample]:
    selected: list[EvaluationSample] = []
    seen_claims: set[str] = set()
    seen_mutations: set[tuple[str, ...]] = set()
    holdout_claims = {sample.claim.casefold() for sample in holdout}
    holdout_pairs = {(sample.claim.casefold(), sample.reference_id) for sample in holdout}
    holdout_mutations = {
        signature
        for sample in holdout
        if (signature := semantic_mutation_signature(sample)) is not None
    }
    holdout_fingerprints = {sample_fingerprint(sample) for sample in holdout}
    records_by_id = {record.record_id: record for record in records}
    for generation_seed in range(seed, seed + 100):
        for ordinal, generated in enumerate(generate_samples(records, seed=generation_seed)):
            record = records_by_id[generated.reference_id or ""]
            candidate = _semanticize(generated, record, ordinal=ordinal)
            claim = candidate.claim.casefold()
            mutation = semantic_mutation_signature(candidate)
            if (
                claim in holdout_claims
                or (claim, candidate.reference_id) in holdout_pairs
                or sample_fingerprint(candidate) in holdout_fingerprints
                or mutation in holdout_mutations
                or claim in seen_claims
                or (mutation is not None and mutation in seen_mutations)
            ):
                continue
            selected.append(candidate)
            seen_claims.add(claim)
            if mutation is not None:
                seen_mutations.add(mutation)
            if len(selected) == count:
                return [
                    sample.model_copy(update={"sample_id": f"dev-{index:06d}"})
                    for index, sample in enumerate(selected, start=1)
                ]
    raise ValueError(f"could not produce {count} non-overlapping diagnostic samples")


def _semanticize(
    sample: EvaluationSample, record: ReferenceRecord, *, ordinal: int
) -> EvaluationSample:
    if ordinal % 2:
        return sample
    value = sample.mutation.mutated_value if sample.mutation else None
    name = subject(record)
    if sample.claim_type is ClaimType.SHIPPING:
        is_free = bool(value) if sample.mutation else bool(record.free_shipping)
        claim = (
            f"Delivery for {name} won't add anything to the bill."
            if is_free
            else f"Delivery for {name} adds a charge to the bill."
        )
        return sample.model_copy(
            update={
                "claim": claim,
                "generation": sample.generation.model_copy(
                    update={"template_id": "semantic_shipping_cost_v1"}
                ),
            }
        )
    if sample.claim_type is ClaimType.TRIAL_DURATION:
        days = int(str(value)) if sample.mutation else record.trial_days
        return sample.model_copy(
            update={
                "claim": f"{name} can be test-driven free for {days} days.",
                "generation": sample.generation.model_copy(
                    update={"template_id": "semantic_trial_access_v1"}
                ),
            }
        )
    return sample


def write_dev_bundle(
    output: Path,
    *,
    catalog_path: Path,
    holdout_path: Path,
    seed: int = DEV_SEED,
    count: int = DEV_SAMPLE_COUNT,
) -> tuple[DatasetManifest, OverlapReport]:
    records, catalog_fingerprint = load_catalog(catalog_path)
    holdout = load_samples(holdout_path)
    samples = generate_diagnostic(records, holdout, seed=seed, count=count)
    errors = validate_samples(
        samples,
        records,
        expected_count=count,
        enforce_frozen_quotas=False,
        minimum_unique_references=250,
    )
    overlap = overlap_report(samples, holdout)
    if not overlap.is_clear:
        errors.append("diagnostic corpus overlaps frozen holdout")
    if errors:
        raise DatasetValidationError("; ".join(errors))
    dataset_bytes = serialize_samples(samples)
    manifest = build_manifest(
        samples, seed=seed, catalog_fingerprint=catalog_fingerprint
    ).model_copy(update={"dataset_version": DEV_VERSION, "generator_version": GENERATOR_VERSION})
    coverage = build_coverage(samples, dataset_version=DEV_VERSION, catalog_records=len(records))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(dataset_bytes)
    _write_json(output.parent / "manifest.json", manifest.model_dump(mode="json"))
    _write_json(output.parent / "coverage.json", coverage.model_dump(mode="json"))
    _write_json(output.parent / "overlap.json", overlap.model_dump(mode="json"))
    return manifest, overlap


def validate_dev_bundle(
    dataset_path: Path, *, catalog_path: Path, holdout_path: Path
) -> tuple[DatasetManifest, OverlapReport]:
    records, catalog_fingerprint = load_catalog(catalog_path)
    samples = load_samples(dataset_path)
    holdout = load_samples(holdout_path)
    manifest = DatasetManifest.model_validate_json(
        (dataset_path.parent / "manifest.json").read_bytes()
    )
    errors = validate_samples(
        samples,
        records,
        expected_count=DEV_SAMPLE_COUNT,
        enforce_frozen_quotas=False,
        minimum_unique_references=250,
    )
    actual_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if manifest.dataset_version != DEV_VERSION:
        errors.append("wrong diagnostic dataset version")
    if manifest.sample_count != len(samples) or manifest.sha256 != actual_hash:
        errors.append("diagnostic manifest count or hash mismatch")
    if manifest.catalog_fingerprint != catalog_fingerprint:
        errors.append("diagnostic catalog fingerprint mismatch")
    overlap = overlap_report(samples, holdout)
    if not overlap.is_clear:
        errors.append("diagnostic corpus overlaps frozen holdout")
    if errors:
        raise DatasetValidationError("; ".join(errors))
    return manifest, overlap


def distribution(samples: list[EvaluationSample], field: str) -> dict[str, int]:
    return dict(sorted(Counter(getattr(sample, field).value for sample in samples).items()))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
