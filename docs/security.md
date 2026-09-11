# Security boundary

This repository is a production-style internal service, not an Internet-ready multi-tenant SaaS.
It does not implement authentication, authorization, tenant isolation, distributed rate limits,
TLS termination, WAF controls, or abuse billing. Put it behind an authenticated gateway/private
network and apply those controls at the deployment boundary. Interactive Swagger and OpenAPI are
enabled for internal use; disable or gateway-restrict them for sensitive deployments.

## Implemented controls

- Typed request and response models forbid unknown fields and constrain IDs, regions, claims,
  batch counts, and body bytes. The public and semantic claim limit is consistently 1,000 chars.
- Resource paths are operator configuration, not request input. Requests cannot select files.
- Provider output is untrusted: JSON Schema guides generation and strict Pydantic validation is
  mandatory. Raw provider output and internal exception detail are not returned to clients.
- Provider credentials use `SecretStr`. Recursive log redaction covers authorization, API keys,
  access/refresh tokens, client secrets, passwords, and secrets in nested mappings/lists.
- Production logs use request/reference identity, claim type, claim length, and a short hash;
  raw claim text, authorization headers, API keys, and provider bodies are excluded.
- Prometheus labels are bounded enums/stable identifiers. Claims, request IDs, reference IDs,
  exception messages, and other user-controlled high-cardinality values are not labels.
- Provider deadlines, retries, pool/concurrency limits, batch concurrency, and request sizes are
  bounded. The container runs without root and ships no secret.

## Residual risks

Claims are untrusted prompt content on the semantic path. Structural separation and evidence
grounding reduce prompt injection but cannot eliminate model-level manipulation. The bundled
catalog is demonstrative, not governed production data. A process-local body/batch limit is not
a substitute for gateway rate limiting. Metrics and internal docs may reveal operational detail,
so restrict them by network policy. External provider availability is intentionally not probed
on every readiness request.

The developer-only `scripts/dev/diagnose_ollama.py` can print raw model response/schema detail.
Run it only in a controlled debugging environment; it is excluded from startup and CI.
