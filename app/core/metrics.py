"""Metrics facade with a Prometheus implementation and a no-op implementation.

Call sites depend on the :class:`Metrics` protocol, not on ``prometheus_client``.
That keeps the instrumentation vocabulary domain-specific (``record_verdict``
rather than ``counter.labels(...).inc()``), makes metrics trivially disableable
in tests and evaluation runs, and means swapping to OpenTelemetry or StatsD is
a single new implementation rather than an edit to every stage.
"""

from __future__ import annotations

from typing import Protocol

from prometheus_client import CollectorRegistry, Counter, Histogram, Info, generate_latest

from app import __version__

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

#: Dense buckets preserve millisecond fast-path resolution; the upper range
#: captures local semantic inference that can legitimately take tens of seconds.
_LATENCY_BUCKETS = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


class Metrics(Protocol):
    """Domain-specific metrics sink."""

    def record_request(self, *, claim_type: str, outcome: str) -> None: ...

    def observe_latency(self, *, stage: str, seconds: float) -> None: ...

    def record_verdict(self, *, verdict: str, path: str) -> None: ...

    def record_rule_decision(self, *, rule_id: str, terminal: bool) -> None: ...

    def record_llm_request(self, *, model: str, prompt_version: str) -> None: ...

    def record_llm_failure(self, *, model: str, reason: str) -> None: ...

    def record_llm_retries(self, *, count: int) -> None: ...

    def record_error(self, *, code: str) -> None: ...

    def record_escalation(self, *, reason: str) -> None: ...

    def observe_batch_size(self, *, size: int) -> None: ...

    def render(self) -> bytes: ...


class PrometheusMetrics:
    """Prometheus-backed implementation over a private registry.

    A private :class:`CollectorRegistry` (rather than the global default) keeps
    repeated app construction in tests from raising duplicate-timeseries errors.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self._build = Info(
            "claim_verification_build",
            "Build metadata for the running service.",
            registry=self.registry,
        )
        self._build.info({"version": __version__})

        self._requests = Counter(
            "verification_requests_total",
            "Claim verification requests handled.",
            labelnames=("claim_type", "outcome"),
            registry=self.registry,
        )
        self._latency = Histogram(
            "verification_latency_seconds",
            "Verification latency by pipeline stage.",
            labelnames=("stage",),
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self._verdicts = Counter(
            "verification_results_total",
            "Verdicts emitted, by verdict and deciding path.",
            labelnames=("verdict", "path"),
            registry=self.registry,
        )
        self._paths = Counter(
            "verification_path_total",
            "Verification results by deciding path.",
            labelnames=("path",),
            registry=self.registry,
        )
        self._rule_decisions = Counter(
            "rule_engine_decisions_total",
            "Deterministic rule outcomes, by rule and whether they terminated the pipeline.",
            labelnames=("rule_id", "terminal"),
            registry=self.registry,
        )
        self._llm_requests = Counter(
            "llm_requests_total",
            "Semantic rater invocations.",
            labelnames=("model", "prompt_version"),
            registry=self.registry,
        )
        self._llm_failures = Counter(
            "llm_failures_total",
            "Semantic rater failures, by failure class.",
            labelnames=("model", "reason"),
            registry=self.registry,
        )
        self._llm_retries = Counter(
            "llm_retry_total",
            "Semantic-rater retry attempts.",
            registry=self.registry,
        )
        self._errors = Counter(
            "verification_errors_total",
            "Verification execution failures by stable code.",
            labelnames=("code",),
            registry=self.registry,
        )
        self._escalations = Counter(
            "escalation_total",
            "Semantic escalations by reason.",
            labelnames=("reason",),
            registry=self.registry,
        )
        self._batch_size = Histogram(
            "batch_size",
            "Claims per verification batch.",
            buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500),
            registry=self.registry,
        )

    def record_request(self, *, claim_type: str, outcome: str) -> None:
        self._requests.labels(claim_type=claim_type, outcome=outcome).inc()

    def observe_latency(self, *, stage: str, seconds: float) -> None:
        self._latency.labels(stage=stage).observe(seconds)

    def record_verdict(self, *, verdict: str, path: str) -> None:
        self._verdicts.labels(verdict=verdict, path=path).inc()
        self._paths.labels(path=path).inc()

    def record_rule_decision(self, *, rule_id: str, terminal: bool) -> None:
        self._rule_decisions.labels(rule_id=rule_id, terminal=str(terminal).lower()).inc()

    def record_llm_request(self, *, model: str, prompt_version: str) -> None:
        self._llm_requests.labels(model=model, prompt_version=prompt_version).inc()

    def record_llm_failure(self, *, model: str, reason: str) -> None:
        self._llm_failures.labels(model=model, reason=reason).inc()

    def record_llm_retries(self, *, count: int) -> None:
        self._llm_retries.inc(count)

    def record_error(self, *, code: str) -> None:
        self._errors.labels(code=code).inc()

    def record_escalation(self, *, reason: str) -> None:
        self._escalations.labels(reason=reason).inc()

    def observe_batch_size(self, *, size: int) -> None:
        self._batch_size.observe(size)

    def render(self) -> bytes:
        return generate_latest(self.registry)


class NullMetrics:
    """No-op sink used by unit tests, the evaluation harness, and benchmarks.

    Benchmarks in particular should not pay for label lookups and histogram
    observations while measuring the pipeline's own latency.
    """

    def record_request(self, *, claim_type: str, outcome: str) -> None: ...

    def observe_latency(self, *, stage: str, seconds: float) -> None: ...

    def record_verdict(self, *, verdict: str, path: str) -> None: ...

    def record_rule_decision(self, *, rule_id: str, terminal: bool) -> None: ...

    def record_llm_request(self, *, model: str, prompt_version: str) -> None: ...

    def record_llm_failure(self, *, model: str, reason: str) -> None: ...

    def record_llm_retries(self, *, count: int) -> None: ...

    def record_error(self, *, code: str) -> None: ...

    def record_escalation(self, *, reason: str) -> None: ...

    def observe_batch_size(self, *, size: int) -> None: ...

    def render(self) -> bytes:
        return b""


def build_metrics(*, enabled: bool) -> Metrics:
    return PrometheusMetrics() if enabled else NullMetrics()
