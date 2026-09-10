from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from app.bootstrap import build_container
from app.core.config import DataSettings, LLMSettings, Settings
from app.core.exceptions import (
    OllamaModelNotFoundError,
    RaterInvalidResponseError,
    RaterSchemaValidationError,
    RaterTimeoutError,
    RaterUnavailableError,
)
from app.core.pipeline_config import PipelineConfig
from app.domain.enums import Attribute, ClaimType, Verdict, VerificationPath
from app.domain.models import (
    ExtractionContext,
    NormalizedClaim,
    RatingContext,
    ReferenceEvidence,
    VerificationRequest,
)
from app.raters.extractor import EXTRACTION_JSON_SCHEMA
from app.raters.ollama import (
    OllamaClaimExtractor,
    OllamaRater,
    OllamaTransport,
    preflight_ollama,
)
from app.raters.schemas import RATING_JSON_SCHEMA


def settings(*, retries: int = 0) -> LLMSettings:
    return LLMSettings(
        provider="ollama",
        base_url="http://ollama.test/api",
        model="qwen-test:7b",
        max_retries=retries,
        retry_base_delay_seconds=0,
    )


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test/api/"
    )


def chat_body(content: str) -> dict[str, object]:
    return {
        "model": "qwen-test:7b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 7,
    }


def rating_json() -> str:
    return json.dumps(
        {
            "verdict": "SUPPORTED",
            "confidence": 0.9,
            "reason_codes": [],
            "explanation": "The evidence explicitly states free shipping.",
        }
    )


async def test_successful_rating_uses_schema_and_provider_metadata() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=chat_body(rating_json()))

    async with client_for(handler) as client:
        transport = OllamaTransport(settings(), client=client)
        result = await OllamaRater(settings(), transport=transport).rate(
            NormalizedClaim(
                raw_text="Shipping won't cost anything.",
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.FREE_SHIPPING,
                value=True,
            ),
            ReferenceEvidence(record_id="x", fields={"free_shipping": True}),
            context=RatingContext(evaluation_date=date(2026, 9, 9), request_id="req"),
        )

    assert captured["format"] == RATING_JSON_SCHEMA
    assert captured["stream"] is False
    assert captured["options"] == {"temperature": 0.0, "num_predict": 256}
    assert result.verdict is Verdict.SUPPORTED
    assert result.metadata is not None and result.metadata.provider == "ollama"
    assert result.metadata.model == "qwen-test:7b"
    assert result.metadata.prompt_version == "v1"
    assert result.trace is not None and result.trace.usage is not None
    assert result.trace.usage.total_tokens == 19


async def test_successful_extraction_uses_same_strict_schema() -> None:
    captured: dict[str, object] = {}
    extracted = {
        "claim_type": "feature_inclusion",
        "attribute": "included_features",
        "operator": "includes",
        "qualifier": "none",
        "value": None,
        "unit": None,
        "feature": "priority_support",
        "negated": False,
        "entity_name": "Pro plan",
        "region": None,
        "confidence": 0.91,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=chat_body(json.dumps(extracted)))

    async with client_for(handler) as client:
        result = await OllamaClaimExtractor(
            settings(), transport=OllamaTransport(settings(), client=client)
        ).extract(
            "Priority help comes with Pro.",
            context=ExtractionContext(evaluation_date=date(2026, 9, 9)),
        )

    assert captured["format"] == EXTRACTION_JSON_SCHEMA
    assert result.feature == "priority_support"
    assert result.confidence == 0.91


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("not-json", RaterInvalidResponseError),
        (
            '{"verdict":"SUPPORTED","confidence":5,"reason_codes":[],"explanation":"x"}',
            RaterSchemaValidationError,
        ),
        (
            '{"verdict":"CONTRADICTED","confidence":0.9,"reason_codes":[],"explanation":"x"}',
            RaterSchemaValidationError,
        ),
    ],
)
async def test_malformed_schema_invalid_and_conflicting_rating_fail(
    content: str, error: type[Exception]
) -> None:
    async with client_for(lambda request: httpx.Response(200, json=chat_body(content))) as client:
        rater = OllamaRater(settings(), transport=OllamaTransport(settings(), client=client))
        with pytest.raises(error):
            await rater.rate(
                NormalizedClaim(raw_text="A claim"),
                ReferenceEvidence(),
                context=RatingContext(evaluation_date=date(2026, 9, 9)),
            )


async def test_malformed_ollama_envelope_fails() -> None:
    async with client_for(lambda request: httpx.Response(200, content=b"not-json")) as client:
        with pytest.raises(RaterInvalidResponseError):
            await OllamaTransport(settings(), client=client).chat(messages=[], schema={})


async def test_unavailable_local_server_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with client_for(handler) as client:
        with pytest.raises(RaterUnavailableError):
            await OllamaTransport(settings(), client=client).chat(messages=[], schema={})


async def test_timeout_is_typed_and_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with client_for(handler) as client:
        with pytest.raises(RaterTimeoutError):
            await OllamaTransport(settings(), client=client).chat(messages=[], schema={})


async def test_preflight_rejects_missing_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "0.12.0"})
        return httpx.Response(200, json={"models": []})

    async with client_for(handler) as client:
        with pytest.raises(OllamaModelNotFoundError, match="ollama pull"):
            await preflight_ollama(settings(), transport=OllamaTransport(settings(), client=client))


async def test_preflight_records_version_digest_and_validates_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "0.12.0"})
        if request.url.path.endswith("/tags"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen-test:7b",
                            "model": "qwen-test:7b",
                            "digest": f"sha256:{'a' * 64}",
                        }
                    ]
                },
            )
        return httpx.Response(200, json=chat_body(rating_json()))

    async with client_for(handler) as client:
        identity = await preflight_ollama(
            settings(), transport=OllamaTransport(settings(), client=client)
        )

    assert identity.ollama_version == "0.12.0"
    assert identity.model_digest == f"sha256:{'a' * 64}"
    assert identity.structured_output_validated is True


async def test_bootstrap_selects_ollama_and_deterministic_path_needs_no_server(
    optimized_config: PipelineConfig,
) -> None:
    configured = Settings(
        _env_file=None,
        llm=settings(),
        data=DataSettings(reference_catalog_path=Path("tests/fixtures/catalog_small.jsonl")),
    )
    container = build_container(settings=configured, pipeline_config=optimized_config)
    try:
        assert isinstance(container.rater, OllamaRater)
        assert isinstance(container.extractor, OllamaClaimExtractor)
        assert container.service is not None
        result = await container.service.verify(
            VerificationRequest(
                claim="The headphones cost $199.99.",
                reference_id="prod-headphones-1",
                allow_llm=False,
            )
        )
        assert result.verdict is Verdict.SUPPORTED
        assert result.verification_path is VerificationPath.DETERMINISTIC
        assert result.audit.llm_invoked is False
    finally:
        await container.aclose()
