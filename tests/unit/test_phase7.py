"""Phase 7 experiment selection and metric helpers."""

from app.domain.enums import Attribute, ClaimType, Verdict
from app.domain.models import NormalizedClaim
from app.evaluation.metrics import PredictionRow
from app.evaluation.models import Difficulty
from app.evaluation.phase7 import _invalid_recall, _reevaluation_ids


def row(
    sample_id: str,
    expected_type: ClaimType,
    *,
    normalized_type: ClaimType | None = None,
    llm_invoked: bool = False,
    execution_error: str | None = None,
    expected_verdict: Verdict = Verdict.SUPPORTED,
    predicted_verdict: Verdict = Verdict.SUPPORTED,
) -> PredictionRow:
    normalized = (
        NormalizedClaim(
            raw_text="test assertion",
            claim_type=normalized_type,
            attribute=Attribute.UNKNOWN,
        )
        if normalized_type is not None
        else None
    )
    return PredictionRow(
        sample_id=sample_id,
        expected_verdict=expected_verdict,
        predicted_verdict=predicted_verdict,
        correct=expected_verdict is predicted_verdict,
        claim_type=expected_type,
        difficulty=Difficulty.MODERATE,
        normalized_claim=normalized,
        llm_invoked=llm_invoked,
        execution_error=execution_error,
    )


def test_incremental_selection_uses_expected_and_normalized_claim_types() -> None:
    rows = [
        row("expected", ClaimType.SHIPPING),
        row("normalized", ClaimType.UNKNOWN, normalized_type=ClaimType.SHIPPING),
        row("unaffected", ClaimType.PRICE, normalized_type=ClaimType.PRICE),
    ]
    assert _reevaluation_ids(rows, frozenset({"shipping"}), False) == {
        "expected",
        "normalized",
    }


def test_llm_only_selection_includes_provider_failures() -> None:
    rows = [
        row("llm", ClaimType.UNKNOWN, llm_invoked=True),
        row("failed", ClaimType.UNKNOWN, execution_error="TIMEOUT"),
        row("deterministic", ClaimType.PRICE),
    ]
    assert _reevaluation_ids(rows, None, True) == {"llm", "failed"}


def test_invalid_recall_uses_only_invalid_expected_rows() -> None:
    rows = [
        row(
            "correct-invalid",
            ClaimType.UNKNOWN,
            expected_verdict=Verdict.INVALID_CLAIM,
            predicted_verdict=Verdict.INVALID_CLAIM,
        ),
        row(
            "missed-invalid",
            ClaimType.UNKNOWN,
            expected_verdict=Verdict.INVALID_CLAIM,
            predicted_verdict=Verdict.INSUFFICIENT_EVIDENCE,
        ),
        row("supported", ClaimType.PRICE),
    ]
    assert _invalid_recall(rows) == 0.5
