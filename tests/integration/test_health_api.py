"""HTTP-level checks for the operations endpoints and the error envelope."""

from __future__ import annotations

import httpx
from app.api.middleware import REQUEST_ID_HEADER
from app.bootstrap import ServiceContainer
from app.core.config import LLMSettings


class TestHealth:
    async def test_liveness_is_cheap_and_dependency_free(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200

        body = response.json()
        assert body["status"] == "ok"
        assert body["version"]
        assert set(body) == {"status", "version"}

    async def test_readiness_reports_loaded_catalog(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["environment"] == "test"
        assert body["pipeline_config"] == "optimized"
        assert body["reference_records"] == 7

    async def test_readiness_reports_honest_dependency_state(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (await client.get("/ready")).json()
        dependencies = {dep["name"]: dep for dep in body["dependencies"]}
        assert dependencies["reference_repository"]["state"] == "available"
        assert dependencies["llm_rater"]["state"] == "not_required"

    async def test_readiness_does_not_claim_unprobed_provider_is_available(
        self, client: httpx.AsyncClient, container: ServiceContainer
    ) -> None:
        container.settings = container.settings.model_copy(
            update={
                "llm": LLMSettings(
                    provider="ollama",
                    base_url="http://127.0.0.1:11434/api",
                    model="not-probed",
                )
            }
        )

        body = (await client.get("/ready")).json()
        dependencies = {dep["name"]: dep for dep in body["dependencies"]}

        assert dependencies["llm_rater"]["state"] == "configured_not_checked"


class TestRequestIdentity:
    async def test_mints_a_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.headers[REQUEST_ID_HEADER]

    async def test_honours_client_supplied_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc"})
        assert response.headers[REQUEST_ID_HEADER] == "trace-abc"


class TestMetricsEndpoint:
    async def test_exposes_prometheus_text(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/metrics")
        assert response.status_code == 200
        # NullMetrics is wired in tests, so the body is empty but the
        # exposition contract still holds.
        assert response.headers["content-type"].startswith("text/plain")


class TestErrorEnvelope:
    async def test_unknown_route_returns_structured_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "http_404"
        assert "request_id" in body

    async def test_oversized_body_is_rejected_before_parsing(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/v1/anything", content=b"x" * (300 * 1024))
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]

    async def test_unsafe_client_request_id_is_replaced(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health", headers={REQUEST_ID_HEADER: "bad id with spaces"})
        assert response.status_code == 200
        assert response.headers[REQUEST_ID_HEADER] != "bad id with spaces"

    async def test_no_stack_trace_leaks(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/does-not-exist")
        assert "Traceback" not in response.text
