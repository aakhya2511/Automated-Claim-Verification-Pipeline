# Evaluation methodology

## V1 benchmark design

The V1 benchmark is a frozen 500-sample holdout corpus. It is not a tuning split. A separate
development corpus may be generated in a later phase; Phase 7 must not tune against these 500
examples. After predictions are inspected, V1 may change only to correct a proven label,
generator, or corruption bug, and that correction must be released as v1.1 or v2.

Ground truth comes exclusively from the structured source-of-truth catalog, never from an
LLM. The fixed generator seed is `20260909`. The generator chooses records through a
deterministic least-used selector with stable hash tie-breaking, rather than taking the first
N catalog rows. This spreads 500 examples across products, plans, offers, brands, regions,
feature densities, price bands, and offer windows.

## Composition and wording

The corpus contains 200 clean supported claims, 220 controlled contradictions, 50 known-record
insufficient-evidence claims, and 30 realistic invalid marketing claims. Each supported
production claim category receives a clean quota and exactly 20 injected mismatches. Clean and
mutated examples use the same template families so verdicts cannot be inferred from mutation-
specific prose.

Templates include canonical forms, safe paraphrases, negation, punctuation variation, numeric
qualifiers, aliases, region names, and year-omitted promotion dates. Safe equivalence is narrow:
for example, free shipping may be rendered as complimentary delivery, while trial durations
remain expressed in days rather than assuming every calendar month is 30 days.

Difficulty is generation-only metadata. `EASY` means direct wording, `MODERATE` covers
paraphrases and aliases, and `HARD` covers negation, qualifiers, subtle numeric changes,
temporal phrasing, missing evidence, and semantic marketing language. It is never passed to the
verifier.

## Controlled mutations

Every contradiction begins with a populated authoritative field and changes one semantic
property. Numeric mutations include large, moderate, and subtle magnitudes. The independent
validator uses the production-compatible comparison tolerance only to prove that a mutation is
outside the accepted band; it does not call the production verifier or derive predictions.

Feature mutations preserve three states: an inclusion contradiction selects an explicitly
excluded feature, an exclusion contradiction selects an explicitly included feature, and an
unknown feature becomes `INSUFFICIENT_EVIDENCE`. Region mutations choose a code outside the
exhaustive eligibility list. Date mutations change the authoritative offer end and always carry
an explicit `as_of`. Date contexts are deterministically stratified across one day before the
start, the exact start boundary, the active window, the exact end boundary, and one day after
expiry; one template family omits the year to exercise `year_inferred` semantics. Shipping and
availability mutations explicitly invert/change their source values. Each mutation records its
type, source value, changed value, and magnitude.

## Insufficient and invalid cases

Insufficient-evidence examples always use known records. They cover unknown features, absent
minimum-purchase fields, and warranty assertions for a catalog that carries no warranty field.
They are never labeled contradictions. Unknown record IDs are excluded because reference
resolution failure is an execution concern, not a semantic label.

Invalid examples are realistic marketing language without a verifiable proposition. They use
varied, nontrivial wording and named entities rather than only empty strings or gibberish.

## Leakage prevention

`EvaluationSample.to_verification_request()` explicitly copies only claim, reference ID, region,
evaluation date, and request identity. Expected verdict, mutation values, source kind,
difficulty, template ID, generator version, and ground-truth explanation remain evaluation-only.
Tests assert these fields never cross the production request boundary. The audit and coverage
artifacts are not imported by the production pipeline.

## Validation and reproducibility

`make generate-eval` reproduces JSONL, manifest, coverage, human-audit, and readable report
artifacts. `make validate-eval` independently checks exact count and quotas, model schemas,
unique IDs and claims, known references, mutation metadata, numeric tolerance, explicit feature
and region conflicts, date context, missing-field semantics, catalog diversity, duplicates,
manifest distributions, catalog fingerprint, and dataset SHA-256.

No generation timestamp is stored. The same catalog bytes, generator version, and seed produce
byte-identical JSONL and identical manifests. `manifest.json` is the machine-readable freeze
record; `README.md` and `coverage.json` report statistics derived from the actual samples;
`audit.md` contains a stratified 40-example manual-review view with source values and label
explanations.

## Limitations

The catalog is synthetic and structurally realistic, so it does not reproduce every linguistic
or merchandising pattern in live commerce. Templates are deterministic and may underrepresent
open-ended language. Difficulty is a heuristic generation label, not an empirical measurement.
The benchmark establishes controlled mismatch and abstention ground truth; it does not yet
measure system performance, calibrate confidence, or validate resume metrics.

## Phase 6 development baseline

Phase 6 uses `data/evaluation/dev/v1/diagnostic.jsonl`, a separate deterministic
400-sample corpus generated with seed `20260910`. Its validator compares content against the
frozen holdout using exact claims, claim/reference pairs, semantic mutation signatures, and
content fingerprints. The diagnostic corpus may be inspected and used for Phase 7; the frozen
500-sample benchmark may not.

`make freeze-baseline` records the redacted provider/model settings, complete behavioral
configuration, prompt and schema versions, retry settings, catalog fingerprint, Git state when
available, and a source-tree fingerprint. The stable `config_hash` covers every recorded field
except itself. Credentials are never serialized.

`make evaluate-baseline` runs only with a configured real provider and uses the production
`HybridVerificationService`. It rejects the frozen holdout by content hash and rejects the fake
rater. Execution failures remain in the primary accuracy denominator. A false positive is fixed
as a non-`CONTRADICTED` ground truth predicted `CONTRADICTED`; a missed mismatch is an injected
`CONTRADICTED` sample receiving any other verdict or an execution failure. Wilson 95% intervals
are reported for accuracy, mismatch recall, and false-positive rate.
