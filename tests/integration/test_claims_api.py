"""End-to-end HTTP checks for the Phase 4 verification service."""

from __future__ import annotations

import httpx
from app.api.middleware import REQUEST_ID_HEADER
from app.core.exceptions import RaterUnavailableError
from app.domain.enums import Verdict
from app.domain.models import RaterResult
from app.raters.fake import FakeRater


async def post_claim(client: httpx.AsyncClient, claim: str, reference_id: str) -> httpx.Response:
    return await client.post(
        "/v1/claims/verify",
        json={"claim": claim, "reference_id": reference_id, "as_of": "2026-09-15"},
    )


class TestSingleVerification:
    async def test_deterministic_support_avoids_llm(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        response = await post_claim(
            client,
            "The Noise Cancelling Headphones are $199.99.",
            "prod-headphones-1",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "SUPPORTED"
        assert body["verification_path"] == "DETERMINISTIC"
        assert body["escalation"] == {"required": False, "reason": None}
        assert fake_rater.call_count == 0

    async def test_deterministic_contradiction_avoids_llm(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        body = (
            await post_claim(
                client,
                "The Noise Cancelling Headphones are $149.",
                "prod-headphones-1",
            )
        ).json()
        assert body["verdict"] == "CONTRADICTED"
        assert body["verification_path"] == "DETERMINISTIC"
        assert fake_rater.call_count == 0

    async def test_semantic_claim_escalates_once(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        body = (
            await post_claim(
                client,
                "This is the best product we have ever made.",
                "prod-headphones-1",
            )
        ).json()
        assert body["verdict"] == "SUPPORTED"
        assert body["verification_path"] == "LLM_RATER"
        assert body["escalation"]["required"] is True
        assert fake_rater.call_count == 1

    async def test_missing_field_stays_insufficient_without_llm(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        body = (
            await post_claim(
                client,
                "This grinder includes a 30-day free trial.",
                "prod-sparse-1",
            )
        ).json()
        assert body["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert body["verification_path"] == "DETERMINISTIC"
        assert body["evidence"]["reference_id"] == "prod-sparse-1"
        assert "trial_days__absent" in body["evidence"]["fields"]
        assert fake_rater.call_count == 0

    async def test_unknown_explicit_reference_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await post_claim(client, "This product costs $10.", "missing-record")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "reference_not_found"

    async def test_provider_failure_is_not_a_semantic_verdict(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        fake_rater.failure = RaterUnavailableError("unsafe raw provider detail")
        response = await post_claim(
            client,
            "This is the best product we have ever made.",
            "prod-headphones-1",
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_unavailable"
        assert "unsafe raw provider detail" not in response.text
        assert "Traceback" not in response.text

    async def test_provider_failure_does_not_affect_deterministic_path(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        fake_rater.failure = RaterUnavailableError("provider down")
        response = await post_claim(
            client,
            "The Noise Cancelling Headphones are $199.99.",
            "prod-headphones-1",
        )
        assert response.status_code == 200
        assert response.json()["verdict"] == "SUPPORTED"
        assert fake_rater.call_count == 0

    async def test_request_id_reaches_result(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/claims/verify",
            headers={REQUEST_ID_HEADER: "client-trace-1"},
            json={
                "claim": "The Pro plan includes a 30-day free trial.",
                "reference_id": "plan-pro",
            },
        )
        assert response.json()["request_id"] == "client-trace-1"
        assert response.headers[REQUEST_ID_HEADER] == "client-trace-1"

    async def test_malformed_input_has_typed_4xx(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/claims/verify", json={"claim": 123})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    async def test_public_evidence_is_claim_scoped(self, client: httpx.AsyncClient) -> None:
        body = (
            await post_claim(
                client,
                "The Pro plan includes a 30-day free trial.",
                "plan-pro",
            )
        ).json()
        assert body["evidence"]["fields"] == {"trial_days": 30}
        assert body["evidence"]["reference_version"]
        assert "terms" not in body["evidence"]["fields"]

    async def test_latency_fields_are_nonnegative_and_contained(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (
            await post_claim(
                client,
                "The Pro plan includes a 30-day free trial.",
                "plan-pro",
            )
        ).json()
        latency = body["latency_ms"]
        assert all(value >= 0 for value in latency.values())
        stages = sum(value for key, value in latency.items() if key != "total")
        assert latency["total"] + 0.01 >= stages


class TestBatchVerification:
    async def test_ordering_partial_failures_ids_and_mixed_routing(
        self, client: httpx.AsyncClient, fake_rater: FakeRater
    ) -> None:
        fake_rater.result = RaterResult(
            verdict=Verdict.CONTRADICTED,
            confidence=0.75,
            explanation="Semantic contradiction in supplied evidence.",
        )
        response = await client.post(
            "/v1/claims/verify/batch",
            json={
                "claims": [
                    {
                        "claim": "The Pro plan includes a 30-day free trial.",
                        "reference_id": "plan-pro",
                    },
                    {"claim": "x", "reference_id": "plan-pro"},
                    {
                        "claim": "This is the best product we have ever made.",
                        "reference_id": "prod-headphones-1",
                    },
                    {
                        "claim": "This grinder includes a 30-day free trial.",
                        "reference_id": "prod-sparse-1",
                    },
                ]
            },
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["result"]["verdict"] == "SUPPORTED"
        assert items[1]["error"]["code"] == "invalid_claim"
        assert items[2]["result"]["verdict"] == "CONTRADICTED"
        assert items[3]["result"]["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert len({item["request_id"] for item in items}) == 4
        assert fake_rater.call_count == 1

    async def test_batch_size_limit_is_enforced(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/claims/verify/batch",
            json={
                "claims": [
                    {"claim": "This product costs $10.", "reference_id": "x"} for _ in range(101)
                ]
            },
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "batch_too_large"

    async def test_empty_batch_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/claims/verify/batch", json={"claims": []})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
