# Technical interview notes

## Why deterministic-first instead of LLM-only?

Exact source-of-truth comparisons are cheaper, faster, explainable, and reproducible in code.
The Phase 8 baseline demonstrated the LLM-only failure mode: 46.4% accuracy, 7.73% mismatch
recall, two false positives, and eight timeouts. The hybrid system reserves the model for
language the rules genuinely cannot settle.

## Why JSON Schema and Pydantic?

Schema-constrained generation improves the provider response, but it is not trusted. The same
strict Pydantic model independently rejects malformed JSON, extra/missing fields, invalid enums,
unsafe coercions, duplicate reasons, and bad confidence values. A validation failure is typed
infrastructure failure, not an `INSUFFICIENT_EVIDENCE` verdict.

## Contradiction versus insufficient evidence?

A contradiction requires an authoritative fact that conflicts with the claim. An absent field
means the catalog is silent. Letting a model turn silence into contradiction caused baseline
over-inference, so missing evidence terminates as insufficient before rating.

## How did Phase 6 error analysis drive Phase 7?

Failures clustered around exact numeric/date relations, missing fields, entity resolution,
feature/region membership, and excess evidence. Each cluster became a bounded candidate and was
measured on the 400-case development corpus. Prompt V1 never needed modification because the
fixes belonged in retrieval and deterministic semantics.

## Why was Candidate 5 rejected?

It fixed 11 cases but regressed 13 and raised false positives to 5.63%. Its broad active-offer
ablation confused valid end-date claims. Candidate 5b retained only authoritative date relation
semantics and avoided the regression. The offer-end-date FP mattered because false accusations
against valid claims are a high-cost failure hidden by average accuracy.

## Why claim-scoped evidence?

Only evidence relevant to the asserted attribute is useful. Removing unrelated fields reduced
over-inference and token use while keeping the model grounded. It also made the audit response
clearer.

## Why separate quality and performance benchmarks?

Serial Ollama inference held concurrency constant for fair baseline/candidate quality comparison.
It was not a throughput measurement. The separate 1,200-request workload measured the deployed
mix and revealed a 1.512 ms median but 11.57 s p95 because 7.5% of requests used local inference.

## Why does 100% not prove general correctness?

The holdout is one frozen synthetic distribution with controlled templates and catalog facts.
It does not cover real copy drift, adversarial language, new domains, or other model behavior.
It has also been consumed, so future behavior changes need a new independent benchmark.

## Why remove the “under 200 ms overall” resume claim?

It contradicts measured data: overall mean was about 1.013 s and p95 about 11.57 s. The honest
claim is that 1,110 deterministic requests achieved 2.33 ms mean and 6.86 ms p95, while the
LLM tail remained expensive.
