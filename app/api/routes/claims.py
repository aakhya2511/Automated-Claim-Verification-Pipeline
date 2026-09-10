"""Thin HTTP mapping for single and bounded-batch verification."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from app.api.dependencies import VerificationServiceDep
from app.api.schemas import (
    BatchItemResponse,
    BatchVerifyRequest,
    BatchVerifyResponse,
    ErrorEnvelope,
    VerifyClaimRequest,
    VerifyClaimResponse,
)
from app.domain.models import VerificationRequest

router = APIRouter(prefix="/v1/claims", tags=["claim verification"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorEnvelope, "description": "Reference record not found"},
    422: {"model": ErrorEnvelope, "description": "Invalid request or claim"},
    429: {"model": ErrorEnvelope, "description": "Model provider rate limited"},
    503: {"model": ErrorEnvelope, "description": "Semantic rater unavailable"},
    504: {"model": ErrorEnvelope, "description": "Verification deadline exceeded"},
}


@router.post(
    "/verify",
    response_model=VerifyClaimResponse,
    responses=ERROR_RESPONSES,
    summary="Verify one commercial claim",
    operation_id="verify_claim",
)
async def verify_claim(
    payload: VerifyClaimRequest,
    request: Request,
    service: VerificationServiceDep,
) -> VerifyClaimResponse:
    result = await service.verify(_to_domain(payload, request.state.request_id))
    return VerifyClaimResponse.from_domain(result)


@router.post(
    "/verify/batch",
    response_model=BatchVerifyResponse,
    responses={413: {"model": ErrorEnvelope, "description": "Batch or body too large"}},
    summary="Verify an ordered batch of commercial claims",
    operation_id="verify_claim_batch",
)
async def verify_claim_batch(
    payload: BatchVerifyRequest,
    request: Request,
    service: VerificationServiceDep,
) -> BatchVerifyResponse:
    batch_id: str = request.state.request_id
    requests = [
        _to_domain(item, f"{batch_id}:{index}:{uuid4().hex[:8]}")
        for index, item in enumerate(payload.claims, start=1)
    ]
    results = await service.verify_batch(requests)
    return BatchVerifyResponse(
        batch_request_id=batch_id,
        items=[BatchItemResponse.from_domain(item) for item in results],
    )


def _to_domain(payload: VerifyClaimRequest, request_id: str) -> VerificationRequest:
    return VerificationRequest(
        claim=payload.claim,
        reference_id=payload.reference_id,
        sku=payload.sku,
        region=payload.region,
        as_of=payload.as_of,
        request_id=request_id,
    )
