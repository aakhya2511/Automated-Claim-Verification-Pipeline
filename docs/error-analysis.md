# Error analysis: Phase 6 to Phase 7

The 400-case development corpus was the only tuning surface. The official local-Qwen baseline
was intentionally simple: every claim used the unchanged V1 policy, full evidence, and a single
schema-validated rating. It reached many semantic answers but frequently treated missing or
nearby evidence as contradiction, missed controlled factual mismatches, and incurred timeout
failures. These were pipeline-architecture failures, not evidence that a more persuasive prompt
was needed.

Phase 7 introduced changes as measured candidates: deterministic entity/field checks, exact
numeric and currency relations, shipping/features/availability/region rules, authoritative
promotion-window semantics, conservative invalid-claim handling, and finally strict
claim-scoped evidence with a narrow no-support-without-evidence guard. Prompt V1 was unchanged.

Candidate 5 is an important negative result. Its relation-aware date work fixed 11 development
errors and reduced model routing, but an active-offer ablation introduced 13 regressions and a
5.63% false-positive rate. It was rejected despite improving mismatch recall. Candidate 5b kept
only authoritative promotion-date semantics and introduced no regressions.

The offer-end-date false positive mattered because marking a valid boundary claim
`CONTRADICTED` is a user-visible accusation against true copy. Aggregate accuracy can hide that
cost. The selection gates therefore treated false-positive protection as a first-class metric,
not a secondary tie-breaker.

Claim-scoped evidence helped the remaining LLM cases by removing irrelevant fields and long
terms that invited comparisons the claim never made. The final Candidate 6d paired those scopes
with conservative paraphrase coverage and became the locked optimized profile.

The final 500-case holdout was inspected only after candidate selection and is now consumed.
Its 100% result cannot be used to justify more tuning; it is final evidence for the already
locked behavior, not a reusable error-analysis set.
