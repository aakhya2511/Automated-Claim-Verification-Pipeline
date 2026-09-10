from __future__ import annotations

from datetime import date

import pytest
from app.core.exceptions import RaterTimeoutError
from app.domain.enums import Verdict
from app.domain.models import NormalizedClaim, RaterResult, RatingContext, ReferenceEvidence
from app.raters.fake import FakeRater


async def test_fake_returns_result_and_captures_one_call() -> None:
    expected = RaterResult(verdict=Verdict.SUPPORTED, confidence=0.9, explanation="matched")
    fake = FakeRater(expected)
    claim = NormalizedClaim(raw_text="Product X includes delivery")
    evidence = ReferenceEvidence(record_id="x", fields={"free_shipping": True})
    context = RatingContext(evaluation_date=date(2026, 9, 15), request_id="req-1")

    result = await fake.rate(claim, evidence, context=context)

    assert result is expected
    assert fake.call_count == 1
    assert fake.calls[0].context.request_id == "req-1"


async def test_fake_can_inject_failure_and_timeout() -> None:
    result = RaterResult(verdict=Verdict.SUPPORTED, confidence=0.9)
    context = RatingContext(evaluation_date=date(2026, 9, 15))
    claim = NormalizedClaim(raw_text="A valid claim")
    evidence = ReferenceEvidence()
    with pytest.raises(RuntimeError, match="boom"):
        await FakeRater(result, failure=RuntimeError("boom")).rate(claim, evidence, context=context)
    with pytest.raises(RaterTimeoutError):
        await FakeRater(result, simulate_timeout=True).rate(claim, evidence, context=context)
