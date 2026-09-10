# Automated Claim Verification Pipeline

A service that verifies natural-language commercial claims — "AirPods Pro are $199
today", "the Premium plan includes unlimited API requests", "20% off through
September 30" — against an authoritative reference dataset, and returns a
structured, auditable verdict.

```
SUPPORTED | CONTRADICTED | INSUFFICIENT_EVIDENCE | INVALID_CLAIM
```

The interesting engineering problem is not asking a model whether two strings
agree. It is building a pipeline where most claims are settled deterministically
(cheap, fast, reproducible), a model is consulted only for genuinely ambiguous
language, and every verdict can be explained after the fact: what was claimed,
which source-of-truth fields were consulted, which rule fired, whether a model
was involved, and why the final verdict was chosen.

> **Status: work in progress.** This README is filled in phase by phase. No
> performance numbers appear here until they are produced by the evaluation and
> benchmark scripts in this repository. See `docs/` for methodology.

## Quick start

```bash
make setup     # create .venv on Python 3.12, install deps, generate reference data
make test      # unit + integration tests (no network, no model calls)
make run       # start the API on http://127.0.0.1:8000
```

```bash
curl -s localhost:8000/health | python -m json.tool
```

The default configuration uses a deterministic in-process rater, so the service
runs with no credentials and no external dependencies. Point
`ACV_LLM__PROVIDER` at a real provider (see `.env.example`) to enable the
semantic rater.

## Repository layout

```
app/
  api/            HTTP layer: routes, middleware, error envelope, DI wiring
  core/           config, pipeline profiles, logging, metrics, clock, exceptions
  domain/         enums, models, and the interfaces every stage is written against
  normalization/  text folding and claim parsing into a structured representation
  retrieval/      reference repository and evidence selection
  rules/          deterministic rule engine
  raters/         provider-neutral LLM rater abstraction and adapters
  verification/   orchestration and the decision engine
  evaluation/     dataset generation, metrics, error analysis
  datagen/        deterministic reference-catalog generator
configs/          behavioural profiles (baseline.yaml vs optimized.yaml)
prompts/          versioned rater prompts
docs/             architecture, evaluation methodology, design decisions
tests/            unit, integration, fixtures
```

## Documentation

| Document | Contents |
| --- | --- |
| `docs/architecture.md` | Stage-by-stage design and data flow |
| `docs/evaluation.md` | Dataset construction and metric definitions |
| `docs/error-analysis.md` | Baseline failure taxonomy and the fixes it motivated |
| `docs/design-decisions.md` | Trade-offs, and what was deliberately not built |
| `docs/interview-notes.md` | Deep-dive Q&A on the implementation |

## License

MIT
