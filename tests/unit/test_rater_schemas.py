"""Strict model-output validation."""

from __future__ import annotations

import pytest
from app.raters.schemas import SemanticRatingPayload
from pydantic import ValidationError


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"verdict":"MAYBE","confidence":0.5,"reason_codes":[],"explanation":"x"}',
        '{"verdict":"SUPPORTED","confidence":-0.1,"reason_codes":[],"explanation":"x"}',
        '{"verdict":"SUPPORTED","confidence":1.7,"reason_codes":[],"explanation":"x"}',
        '{"confidence":0.5,"reason_codes":[],"explanation":"x"}',
        '{"verdict":"SUPPORTED","confidence":0.5,"reason_codes":["MADE_UP"],"explanation":"x"}',
        '{"verdict":"SUPPORTED","confidence":0.5,"reason_codes":[],"explanation":"x","extra":1}',
        '```json\n{"verdict":"SUPPORTED"}\n```',
    ],
)
def test_rejects_invalid_model_output(payload: str) -> None:
    with pytest.raises(ValidationError):
        SemanticRatingPayload.model_validate_json(payload)


def test_rejects_duplicate_reason_codes() -> None:
    payload = (
        '{"verdict":"INSUFFICIENT_EVIDENCE","confidence":0.7,'
        '"reason_codes":["INSUFFICIENT_EVIDENCE","INSUFFICIENT_EVIDENCE"],'
        '"explanation":"The field is absent."}'
    )
    with pytest.raises(ValidationError, match="duplicates"):
        SemanticRatingPayload.model_validate_json(payload)


def test_rejects_verdict_reason_conflict() -> None:
    payload = (
        '{"verdict":"SUPPORTED","confidence":0.9,'
        '"reason_codes":["PRICE_MISMATCH"],"explanation":"conflicting"}'
    )
    with pytest.raises(ValidationError, match="SUPPORTED cannot carry"):
        SemanticRatingPayload.model_validate_json(payload)


def test_accepts_missing_evidence_as_abstention() -> None:
    payload = (
        '{"verdict":"INSUFFICIENT_EVIDENCE","confidence":0.8,'
        '"reason_codes":["INSUFFICIENT_EVIDENCE"],'
        '"explanation":"The reference does not contain the asserted field."}'
    )
    parsed = SemanticRatingPayload.model_validate_json(payload)
    assert parsed.verdict.value == "INSUFFICIENT_EVIDENCE"
