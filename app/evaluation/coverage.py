"""Coverage, duplicate, and shortcut diagnostics derived from samples."""

from __future__ import annotations

import statistics
from collections import Counter

from app.evaluation.models import CoverageReport, EvaluationSample


def build_coverage(
    samples: list[EvaluationSample], *, dataset_version: str, catalog_records: int
) -> CoverageReport:
    reference_counts = Counter(
        sample.reference_id for sample in samples if sample.reference_id is not None
    )
    claim_counts = Counter(sample.claim.casefold() for sample in samples)
    pair_counts = Counter((sample.claim.casefold(), sample.reference_id) for sample in samples)
    mutation_counts = Counter(
        (
            sample.reference_id,
            sample.mutation.type.value,
            repr(sample.mutation.mutated_value),
        )
        for sample in samples
        if sample.mutation is not None
    )
    warnings = _warnings(samples)
    return CoverageReport(
        dataset_version=dataset_version,
        sample_count=len(samples),
        catalog_records=catalog_records,
        unique_reference_records=len(reference_counts),
        catalog_coverage_percent=round(100 * len(reference_counts) / catalog_records, 2),
        max_samples_per_reference=max(reference_counts.values(), default=0),
        median_samples_per_reference=(
            float(statistics.median(reference_counts.values())) if reference_counts else 0.0
        ),
        duplicate_claims=sum(count - 1 for count in claim_counts.values() if count > 1),
        duplicate_claim_reference_pairs=sum(
            count - 1 for count in pair_counts.values() if count > 1
        ),
        duplicate_semantic_mutations=sum(
            count - 1 for count in mutation_counts.values() if count > 1
        ),
        template_distribution=dict(
            sorted(Counter(sample.generation.template_id for sample in samples).items())
        ),
        warnings=tuple(warnings),
    )


def _warnings(samples: list[EvaluationSample]) -> list[str]:
    warnings: list[str] = []
    hard_verdicts = {
        sample.expected_verdict for sample in samples if sample.difficulty.value == "HARD"
    }
    if len(hard_verdicts) < 3:
        warnings.append("hard difficulty is strongly correlated with verdict")
    invalid_lengths = [
        len(sample.claim) for sample in samples if sample.source_kind.value == "INVALID"
    ]
    if invalid_lengths and max(invalid_lengths) < 20:
        warnings.append("all invalid claims are very short")
    template_verdicts: dict[str, set[str]] = {}
    for sample in samples:
        template_verdicts.setdefault(sample.generation.template_id, set()).add(
            sample.expected_verdict.value
        )
    single_verdict_templates = sum(len(verdicts) == 1 for verdicts in template_verdicts.values())
    if single_verdict_templates == len(template_verdicts):
        warnings.append("every template is exclusive to one verdict")
    return warnings
