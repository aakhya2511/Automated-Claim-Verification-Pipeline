"""Dedicated non-corpus smoke requests for the real Phase 6 baseline path."""

from __future__ import annotations

import asyncio
from datetime import date
from statistics import fmean

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
    VerificationRequest(
        claim="BACK163 is available to customers in the United States.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-region",
    ),
    VerificationRequest(
        claim="The BACK163 promotion ends on November 24, 2026.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-date",
    ),
    VerificationRequest(
        claim="A $75 purchase qualifies for BACK163.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-minimum",
    ),
    VerificationRequest(
        claim="Volterra Mesh Max supports parental controls.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-feature",
    ),
    VerificationRequest(
        claim="Volterra Mesh Max does not include a VPN server.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-exclusion",
    ),
    VerificationRequest(
        claim="BACK163 cannot be combined with another offer.",
        reference_id="offer-back-to-school-back163",
        as_of=date(2026, 9, 10),
        request_id="baseline-smoke-terms",
    ),
)


async def run_smoke() -> None:
    settings = official_baseline_settings(Settings())
    validate_real_provider(settings)
    container = build_container(settings=settings)
    if container.service is None:
        raise EvaluationRunError("verification service was not constructed")
    latencies: list[float] = []
    timed_out = 0
    errors = 0
    try:
        for request in SMOKE_REQUESTS:
            try:
                result = await container.service.verify(request)
            except ClaimVerificationError as exc:
                if exc.code in {"llm_timeout", "verification_timeout"}:
                    timed_out += 1
                else:
                    errors += 1
                print(f"request_id={request.request_id} execution_error={exc.code}")
                continue
            if not result.audit.llm_invoked:
                raise EvaluationRunError(
                    f"baseline smoke request {request.request_id} did not invoke the LLM"
                )
            latencies.append(result.latency_ms.total)
            print(
                f"request_id={request.request_id} "
                f"path={result.verification_path.value} "
                f"llm_invoked={result.audit.llm_invoked} "
                f"latency_ms={result.latency_ms.total}"
            )
    finally:
        await container.aclose()
    print(f"completed={len(latencies)}")
    print(f"timed_out={timed_out}")
    print(f"other_errors={errors}")
    print(f"mean_latency_ms={fmean(latencies) if latencies else 0.0:.3f}")
    print(f"max_latency_ms={max(latencies, default=0.0):.3f}")
    if timed_out or errors or len(latencies) != len(SMOKE_REQUESTS):
        raise EvaluationRunError("one or more serial baseline smoke requests failed")


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
