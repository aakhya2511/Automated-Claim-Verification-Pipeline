"""Production metrics cover slow local inference and expose build identity."""

from app.core.metrics import PrometheusMetrics


def test_latency_histogram_has_slow_inference_buckets_and_build_info() -> None:
    metrics = PrometheusMetrics()
    metrics.observe_latency(stage="rating", seconds=45.0)

    rendered = metrics.render().decode()

    assert 'verification_latency_seconds_bucket{le="60.0",stage="rating"} 1.0' in rendered
    assert 'verification_latency_seconds_bucket{le="300.0",stage="rating"} 1.0' in rendered
    assert "claim_verification_build_info" in rendered
