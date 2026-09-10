"""Dedicated non-corpus smoke requests for the real Phase 6 baseline path."""

from __future__ import annotations

import asyncio
from datetime import date

from app.bootstrap import build_container
from app.core.config import Settings
from app.core.exceptions import ClaimVerificationError
from app.domain.models import VerificationRequest
from app.evaluation.baseline import official_baseline_settings
from app.evaluation.runner import EvaluationRunError, validate_real_provider

SMOKE_REQUESTS = (
    VerificationRequest(
        claim="Back to School BACK163 gives 40% off.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-numeric",
    ),
    VerificationRequest(
        claim="The BACK163 offer includes complimentary delivery.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-semantic",
    ),
)


async def run_smoke() -> None:
    settings = official_baseline_settings(Settings())
    validate_real_provider(settings)
    container = build_container(settings=settings)
    if container.service is None:
        raise EvaluationRunError("verification service was not constructed")
    try:
        for request in SMOKE_REQUESTS:
            result = await container.service.verify(request)
            if not result.audit.llm_invoked:
                raise EvaluationRunError(
                    f"baseline smoke request {request.request_id} did not invoke the LLM"
                )
            print(
                f"request_id={request.request_id} "
                f"path={result.verification_path.value} "
                f"llm_invoked={result.audit.llm_invoked} "
                f"latency_ms={result.latency_ms.total}"
            )
    finally:
        await container.aclose()


def main() -> int:
    try:
        asyncio.run(run_smoke())
    except (ClaimVerificationError, RuntimeError) as exc:
        print(f"baseline_smoke=FAILED: {exc}")
        return 2
    print("baseline_smoke=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
