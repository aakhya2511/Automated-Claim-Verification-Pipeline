# Design decisions and configuration audit

## Deterministic first

Money, percentages, dates, availability, feature membership, shipping, regions, and field
absence have exact authoritative semantics. Rules make those outcomes fast and reproducible.
The LLM handles semantic ambiguity only after evidence retrieval. This is why an LLM-only
baseline is useful as a comparison but unsafe as the product architecture.

The distinction between contradiction and missing evidence is deliberate: absence is not a
negative fact. Deterministic terminal decisions win; provider confidence is not averaged with
rule confidence. Provider output is JSON-Schema constrained and Pydantic validated because a
transport-level JSON guarantee is not a domain-validity guarantee.

## Runtime resource strategy

The installed wheel embeds only runtime necessities: frozen final YAML, V1 prompts, and the
synthetic demonstration catalog. Evaluation corpora and reports are not service dependencies.
Deployments can replace resource paths with governed files. This keeps an installed wheel
self-contained without pretending the demonstration catalog is production data.

## Audited configuration status

`ACTIVE` means runtime code consumes the value. `DORMANT` means it remains parse-compatible
with frozen historical YAML but has no final decision effect. `DEPRECATED` means compatibility
only and should not be used in new profiles. `EXPERIMENT_ONLY` is recorded/validated by
evaluation tooling but deployment settings are the operational source.

| Fields | Status | Reason |
| --- | --- | --- |
| server limits/timeouts; data runtime paths; log/metrics settings | ACTIVE | API, bootstrap, logging, metrics |
| `llm.provider`, model, URL/key, timeouts, retry/pool limits | ACTIVE | provider selection and transport |
| `llm.max_concurrency`, `llm.max_output_tokens`, `llm.temperature`, `llm.schema_version` | ACTIVE | provider semaphore/request/metadata |
| normalization, retrieval, rules, rater routing flags/prompt version | ACTIVE | frozen pipeline stages |
| `decision.heuristics.block_support_without_evidence` | ACTIVE | final narrow post-rater guard |
| decision thresholds, conflict policy, confidence scale | DORMANT | retained to parse frozen profiles; final engine does not consult them |
| four other post-rater heuristic flags | DORMANT | retained historical declarations; no runtime branches |
| `cache.*` | DEPRECATED | no production cache implementation; Phase 7 tooling only rejects enabled candidates |
| pipeline `rater.temperature`, output tokens, concurrency, schema version | EXPERIMENT_ONLY | experiment identity; active values come from typed `llm.*` deployment settings |
| evaluation dataset and artifact paths | EXPERIMENT_ONLY | evaluation CLIs only, never service request handling |

The machine-readable audit is `app/core/config_status.py` and consistency tests protect its
highest-risk declarations. `observability.log_claim_text` and
`observability.expose_debug_endpoints` were removed: neither had a runtime implementation, and
their presence falsely implied claim logging/debug routes. Raw claims remain unlogged.

Dormant fields were not wired during release engineering because doing so would alter frozen
prediction behavior. Removing them from the schema would make authoritative Phase 7 YAML and
historical profiles unreadable, so a versioned config-schema migration is deferred.

## Deliberately omitted

There is no production response cache, Internet-facing auth layer, Kubernetes bundle, remote
catalog control plane, or automatic external-provider readiness probe. These need deployment
requirements and threat modeling, not speculative switches. CI validates code and immutable
identities but never spends provider compute or reruns official evaluation.
