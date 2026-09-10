"""Shared fixtures.

Two ground rules enforced here:

* No test reaches the network. ``ACV_LLM__PROVIDER`` is pinned to ``fake`` and
  the environment is scrubbed of any real ``ACV_*`` variables so a developer's
  local ``.env`` cannot change test behaviour.
* Time is fixed. Date-boundary logic is exercised against
  :data:`FIXED_NOW`, not against whatever day CI happens to run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.bootstrap import ServiceContainer
from app.core.clock import FixedClock
from app.core.config import DataSettings, ObservabilitySettings, Settings
from app.core.metrics import Metrics, NullMetrics
from app.core.pipeline_config import PipelineConfig, load_pipeline_config
from app.domain.enums import Verdict
from app.domain.interfaces import ClaimExtractor, LLMRater
from app.domain.models import RaterResult
from app.main import create_app
from app.normalization.claim_parser import DeterministicClaimNormalizer
from app.normalization.features import FeatureResolver, vocabulary_from_records
from app.raters.fake import FakeRater
from app.retrieval.evidence import ReferenceEvidenceRetriever
from app.retrieval.repository import InMemoryReferenceRepository
from app.rules.engine import DeterministicRuleEngine
from app.verification.decision import ConservativeDecisionEngine
from app.verification.escalation import ConservativeEscalationPolicy
from app.verification.service import HybridVerificationService
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SMALL_CATALOG = FIXTURES_DIR / "catalog_small.jsonl"

#: Sits inside the fixture catalog's active offer window (2026-09-01 ->
#: 2026-09-30) and after the expired one, so both branches are reachable.
FIXED_NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip host configuration so tests see only what they set explicitly."""
    for key in list(os.environ):
        if key.startswith("ACV_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ACV_ENVIRONMENT", "test")
    monkeypatch.setenv("ACV_LLM__PROVIDER", "fake")
    monkeypatch.setenv("ACV_OBSERVABILITY__METRICS_ENABLED", "false")


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(moment=FIXED_NOW)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        data=DataSettings(reference_catalog_path=SMALL_CATALOG),
        observability=ObservabilitySettings(metrics_enabled=False, log_format="console"),
    )


@pytest.fixture
def optimized_config() -> PipelineConfig:
    return load_pipeline_config(REPO_ROOT / "configs" / "optimized.yaml")


@pytest.fixture
def baseline_config() -> PipelineConfig:
    return load_pipeline_config(REPO_ROOT / "configs" / "baseline.yaml")


@pytest.fixture
def repository() -> InMemoryReferenceRepository:
    return InMemoryReferenceRepository.from_jsonl(SMALL_CATALOG)


@pytest.fixture
def fake_rater() -> FakeRater:
    return FakeRater(
        RaterResult(
            verdict=Verdict.SUPPORTED,
            confidence=0.8,
            explanation="The supplied evidence semantically supports the claim.",
        )
    )


def build_test_service(
    *,
    settings: Settings,
    config: PipelineConfig,
    repository: InMemoryReferenceRepository,
    clock: FixedClock,
    rater: LLMRater | None,
    extractor: ClaimExtractor | None = None,
    metrics: Metrics | None = None,
    batch_concurrency: int | None = None,
    timeout_seconds: float | None = None,
) -> HybridVerificationService:
    sink = metrics or NullMetrics()
    normalizer = DeterministicClaimNormalizer(
        config=config.normalization,
        feature_resolver=FeatureResolver(vocabulary_from_records(repository.all_records())),
        clock=clock,
        extractor=extractor,
    )
    return HybridVerificationService(
        config=config,
        normalizer=normalizer,
        retriever=ReferenceEvidenceRetriever(repository=repository, config=config.retrieval),
        rules=DeterministicRuleEngine(config=config.rules, clock=clock),
        escalation=ConservativeEscalationPolicy(config.rater),
        decision=ConservativeDecisionEngine(),
        rater=rater,
        metrics=sink,
        timeout_seconds=timeout_seconds or settings.server.verification_timeout_seconds,
        batch_concurrency=batch_concurrency or settings.server.batch_concurrency,
        max_batch_items=settings.server.max_batch_items,
    )


@pytest.fixture
def container(
    settings: Settings,
    optimized_config: PipelineConfig,
    repository: InMemoryReferenceRepository,
    fixed_clock: FixedClock,
    fake_rater: FakeRater,
) -> ServiceContainer:
    container = ServiceContainer(
        settings=settings,
        pipeline_config=optimized_config,
        repository=repository,
        metrics=NullMetrics(),
        clock=fixed_clock,
        rater=fake_rater,
    )
    container.service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=fake_rater,
    )
    return container


async def _noop_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


@pytest.fixture
def app_with_container(container: ServiceContainer) -> Iterator[FastAPI]:
    """FastAPI app with the fixture container injected, bypassing lifespan startup.

    Overriding ``app.state.container`` directly (rather than letting the
    lifespan build one) keeps integration tests off the full 420-record catalog
    and off any host configuration.
    """
    app = create_app(settings=container.settings)
    app.state.container = container
    app.router.lifespan_context = _noop_lifespan
    yield app


@pytest.fixture
async def client(app_with_container: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_with_container)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture(scope="session")
def full_catalog_path() -> Path:
    """The committed 420-record catalog, skipping if it has not been generated."""
    path = REPO_ROOT / "data" / "reference" / "catalog.jsonl"
    if not path.is_file():
        pytest.skip("run `make reference-data` to generate the reference catalog")
    return path
