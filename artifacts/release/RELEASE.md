# Release audit: v0.1.0

Phase 10 audited committed Phase 9 base `b6b908ead8280154cd083735ef5aaaf338bcbf0e`.
The release-only OpenAPI correction and these records require one final audit commit before the
release is mechanically frozen. No verification, normalization, evidence, routing, prompt, or
benchmark semantics changed.

## Frozen evidence

- The consumed 500-case holdout remains byte-identical and records
  `holdout_evaluated = true`.
- Optimized: 500/500 correct, 220/220 injected mismatches detected, 0/280 false positives,
  and no execution failures.
- Baseline: 46.40% accuracy, 17/220 mismatch recall, 2/280 false positives, and eight execution
  failures.
- Attempted LLM calls fell 93%; recorded tokens fell 94.1%.
- The separate 1,200-request performance workload contained 1,110 deterministic and 90
  LLM-routed requests with no errors. Overall mean/p50/p95 were
  1,012.75/1.512/11,569.27 ms; deterministic mean/p95 were 2.328/6.861 ms.
- An overall sub-200 ms claim is unsupported.

## Audit outcome

The Python 3.12 locked install, 516 offline tests, 84% measured coverage, Ruff, formatting,
strict mypy, diff check, release-integrity check, sdist/wheel builds, isolated installed-wheel
runtime, and Docker runtime all passed. The image runs as UID/GID 10001, passed liveness and a
deterministic request, and stopped without a forced kill. Hosted CI is `HOSTED_CI_PENDING`
because this checkout has no configured Git remote.

Tracked-tree and lightweight-history scans found no credential-like material. No generated
build output, cache, model weights, or unexpected binaries are tracked. README and documentation
links resolve, limitations and the deployment security boundary are explicit, and obsolete
92%, 30%, and overall-under-200-ms resume claims appear only in the clearly labeled rejection
record.

After the final audit commit passes hosted CI, tag that commit `v0.1.0`.
