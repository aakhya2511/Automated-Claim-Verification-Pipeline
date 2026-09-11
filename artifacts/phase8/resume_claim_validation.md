# Phase 8 resume claim validation

## Claim 1: “Caught 92% of injected mismatches across a 500-sample test set”

**NEEDS REVISION.** The frozen optimized holdout result was 220/220, or 100.00%, not 92%.

## Claim 2: “Cut the false-positive rate by 30%”

**NEEDS REVISION.** The holdout false-positive rate fell from 2/280 (0.7143%) to 0/280 (0.0000%), a 100.00% relative reduction, not 30%.

## Claim 3: “keeping per-claim latency under 200ms”

**UNSUPPORTED as written.** On the separate 1,200-request benchmark, overall service latency was 1012.746 ms mean, 1.512 ms median, and 11569.266 ms p95. Only the deterministic fast path was wholly sub-200 ms: 2.328 ms mean, 6.861 ms p95, and 30.199 ms maximum across 1,110 requests.

## Recommended resume bullets

- Built a hybrid claim-verification pipeline that achieved 100% accuracy and caught 220/220 injected mismatches on a frozen 500-case holdout with 0/280 false positives, outperforming an LLM-only baseline by 53.6 percentage points.
- Engineered deterministic short-circuiting with schema-validated local Ollama fallback to cut attempted LLM calls 93.0% and recorded tokens 94.1%; benchmarked 1,110 deterministic requests at 2.33 ms mean and 6.86 ms p95 in a frozen 1,200-request workload.
