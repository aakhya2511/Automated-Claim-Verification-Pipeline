from __future__ import annotations

import pytest
from app.core.clock import FixedClock
from app.core.pipeline_config import NormalizationConfig
from app.domain.enums import Attribute, ClaimType, ExtractionMethod, Operator
from app.domain.models import ClaimExtraction, VerificationRequest
from app.normalization.claim_parser import DeterministicClaimNormalizer
from app.normalization.features import FeatureResolver, vocabulary_from_records
from app.raters.fake import FakeClaimExtractor

from tests.conftest import FIXED_NOW


def build_normalizer(repository, extractor: FakeClaimExtractor) -> DeterministicClaimNormalizer:
    return DeterministicClaimNormalizer(
        config=NormalizationConfig(
            llm_extraction_fallback=True, llm_extraction_below_confidence=0.8
        ),
        feature_resolver=FeatureResolver(vocabulary_from_records(repository.all_records())),
        clock=FixedClock(moment=FIXED_NOW),
        extractor=extractor,
    )


async def test_high_confidence_deterministic_parse_avoids_extractor(repository) -> None:
    extractor = FakeClaimExtractor(
        ClaimExtraction(claim_type=ClaimType.UNKNOWN, attribute=Attribute.UNKNOWN)
    )
    result = await build_normalizer(repository, extractor).normalize(
        VerificationRequest(claim="Product X includes free shipping.")
    )
    assert result.extraction_method is ExtractionMethod.DETERMINISTIC
    assert extractor.call_count == 0


async def test_low_confidence_parse_uses_existing_normalized_claim(repository) -> None:
    extractor = FakeClaimExtractor(
        ClaimExtraction(
            claim_type=ClaimType.FEATURE_INCLUSION,
            attribute=Attribute.INCLUDED_FEATURES,
            operator=Operator.INCLUDES,
            feature="priority_support",
            entity_name="Basic plan",
            confidence=0.91,
        )
    )
    result = await build_normalizer(repository, extractor).normalize(
        VerificationRequest(claim="Some unusually phrased priority support assertion")
    )
    assert extractor.call_count == 1
    assert result.extraction_method is ExtractionMethod.LLM_ASSISTED
    assert result.feature == "priority_support"
    assert result.parse_confidence == 0.91


async def test_extractor_failure_propagates_safely(repository) -> None:
    extractor = FakeClaimExtractor(
        ClaimExtraction(claim_type=ClaimType.UNKNOWN, attribute=Attribute.UNKNOWN),
        failure=RuntimeError("invalid extraction"),
    )
    with pytest.raises(RuntimeError, match="invalid extraction"):
        await build_normalizer(repository, extractor).normalize(
            VerificationRequest(claim="An ambiguous commercial assertion")
        )
