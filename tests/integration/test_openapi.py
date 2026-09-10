from __future__ import annotations

import httpx


async def test_openapi_describes_claim_endpoints_and_typed_verdicts(
    client: httpx.AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert paths["/v1/claims/verify"]["post"]["operationId"] == "verify_claim"
    assert paths["/v1/claims/verify/batch"]["post"]["operationId"] == "verify_claim_batch"
    verdict = schema["components"]["schemas"]["Verdict"]
    assert verdict["enum"] == [
        "SUPPORTED",
        "CONTRADICTED",
        "INSUFFICIENT_EVIDENCE",
        "INVALID_CLAIM",
    ]
    assert "api_key" not in str(schema).lower()
