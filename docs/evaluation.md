# Evaluation and benchmark finality

All benchmark labels derive from the structured synthetic catalog, never from an LLM. Samples
carry only claim, reference selector, region, evaluation date, and request identity into the
production pipeline; expected labels and mutation metadata stay in evaluation code.

## Three separate datasets

| Dataset | Purpose | Final status |
| --- | --- | --- |
| 400-case development diagnostic | Phase 6 diagnosis and Phase 7 candidate selection | tuning data |
| 500-case V1 holdout | one final locked baseline/optimized comparison | consumed |
| 1,200-request workload | operational path and latency measurement | consumed performance run |

The 500-case holdout contains 200 supported, 220 controlled contradictions, 50 known-record
insufficient-evidence, and 30 invalid-marketing claims. Contradictions change exactly one
populated authoritative fact. Template families cover paraphrase, negation, qualifiers,
aliases, regions, punctuation, numeric changes, and offer boundaries. The development corpus
is content-separated from the holdout and was the only source used for optimization.

## Finality

`holdout_evaluated = true`. It was evaluated exactly once per locked arm in Phase 8. It must
not be rerun for tuning, relabeled, regenerated in place, described as untouched, or used for
another independent comparison after a behavior change. A proven dataset correction requires
a new version and explicit provenance.

The committed `artifacts/phase8/holdout/finality.json` binds the holdout SHA-256 to both final
prediction hashes. Small summaries, metrics, routing, confusion matrices, latency, candidate
identity, and finality records are public evidence. Raw predictions/checkpoints and intermediate
error-analysis payloads remain ignored. CI checks frozen hashes but executes no official corpus.

## Reproducibility controls

The generator seeds are `20260910` (development) and `20260909` (holdout). Evaluation snapshots
record complete behavioral and operational config, stable config hash, catalog fingerprint,
prompt/schema identities, provider/model, Ollama version, model digest, retry/concurrency values,
and source identity. The final local model was `qwen2.5:7b`, digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`, on Ollama `0.33.3`.
Credentials are excluded.

Accuracy includes execution failures in its denominator. Mismatch recall is detected injected
contradictions divided by 220. A false positive is a non-contradictory case predicted
`CONTRADICTED`. Quality evaluation used serial Ollama inference to remove concurrency as a
comparison variable; the performance workload was run separately.

## Published results

The optimized holdout produced 500/500 correct, 220/220 mismatches detected, 0/280 false
positives, and zero execution failures. The baseline produced 46.40% accuracy, 17/220 mismatch
recall, 2/280 false positives, and eight timeouts. Attempted model calls fell from 500 to 35
(93%); recorded tokens fell from 368,191 to 21,722 (94.1%).

The 1,200-request workload routed 1,110 requests deterministically and 90 to the LLM. Overall
mean/p50/p95 were 1,012.75/1.512/11,569.27 ms. Deterministic mean/p95 were 2.328/6.861 ms.
The LLM tail explains the gap; an overall sub-200 ms claim is unsupported.

## Limitations

The catalog and corpora are synthetic and their language distribution is controlled. A local
Qwen result does not characterize all models or real commercial traffic. One perfect consumed
holdout does not establish universal correctness, robustness under distribution shift, or
confidence calibration.
