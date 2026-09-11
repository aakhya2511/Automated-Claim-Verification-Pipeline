"""Composition root.

All object graph construction happens here and nowhere else. Stages receive
their collaborators through constructors, so the API server, the evaluation
harness, the benchmark runner, and the tests all build the same pipeline with
different leaves substituted (fake rater, null metrics, fixture catalog).

This is the only module allowed to know which concrete implementation backs
each interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import Metrics, build_metrics
from app.core.pipeline_config import PipelineConfig, load_pipeline_config
from app.domain.interfaces import ClaimExtractor, LLMRater
from app.normalization.claim_parser import DeterministicClaimNormalizer
from app.normalization.features import FeatureResolver, vocabulary_from_records
from app.raters.extractor import OpenAIClaimExtractor
from app.raters.ollama import OllamaClaimExtractor, OllamaRater, OllamaTransport
from app.raters.openai import OpenAIRater
from app.raters.transport import OpenAIResponsesTransport
from app.retrieval.evidence import ReferenceEvidenceRetriever
from app.retrieval.repository import InMemoryReferenceRepository
from app.rules.engine import DeterministicRuleEngine
from app.verification.decision import ConservativeDecisionEngine
from app.verification.escalation import ConservativeEscalationPolicy
from app.verification.service import HybridVerificationService

logger = get_logger(__name__)


@dataclass
class ServiceContainer:
    """Resolved dependencies for one process.

    Held on ``app.state`` for the API and constructed directly by CLI entry
    points. Everything in here is safe to share across concurrent requests:
    the repository is read-only after indexing and the metrics sink is
    thread-safe.
    """

    settings: Settings
    pipeline_config: PipelineConfig
    repository: InMemoryReferenceRepository
    metrics: Metrics
    clock: Clock
    service: HybridVerificationService | None = None
    rater: LLMRater | None = None
    extractor: ClaimExtractor | None = None

    async def aclose(self) -> None:
        """Release resources held by long-lived collaborators.

        The rater and extractor share the rater's transport in real-provider
        mode, so closing the rater closes the pool exactly once.
        """
        if self.rater is not None:
            await self.rater.aclose()


def build_container(
    *,
    settings: Settings | None = None,
    pipeline_config: PipelineConfig | None = None,
    catalog_path: Path | None = None,
) -> ServiceContainer:
    """Construct the dependency graph, validating configuration eagerly.

    Failures here (missing credentials, unreadable catalog, invalid profile)
    raise at startup instead of surfacing as 500s on the first request.
    """
    resolved_settings = settings or get_settings()
    resolved_config = pipeline_config or load_pipeline_config(
        resolved_settings.pipeline_config_path
    )
    resolved_catalog = catalog_path or resolved_settings.data.reference_catalog_path

    repository = InMemoryReferenceRepository.from_jsonl(resolved_catalog)
    metrics = build_metrics(enabled=resolved_settings.observability.metrics_enabled)
    rater: LLMRater | None = None
    extractor: ClaimExtractor | None = None
    if resolved_settings.llm.provider == "openai":
        transport = OpenAIResponsesTransport(resolved_settings.llm)
        rater = OpenAIRater(
            resolved_settings.llm,
            transport=transport,
            prompt_version=resolved_config.rater.prompt_version,
            prompts_dir=resolved_settings.data.prompts_dir,
        )
        extractor = OpenAIClaimExtractor(
            resolved_settings.llm,
            transport=transport,
            prompts_dir=resolved_settings.data.prompts_dir,
        )
    elif resolved_settings.llm.provider == "ollama":
        ollama_transport = OllamaTransport(resolved_settings.llm)
        rater = OllamaRater(
            resolved_settings.llm,
            transport=ollama_transport,
            prompt_version=resolved_config.rater.prompt_version,
            prompts_dir=resolved_settings.data.prompts_dir,
        )
        extractor = OllamaClaimExtractor(
            resolved_settings.llm,
            transport=ollama_transport,
            prompt_version=resolved_settings.llm.extraction_prompt_version,
            prompts_dir=resolved_settings.data.prompts_dir,
        )

    clock = SystemClock()
    feature_resolver = FeatureResolver(vocabulary_from_records(repository.all_records()))
    normalizer = DeterministicClaimNormalizer(
        config=resolved_config.normalization,
        feature_resolver=feature_resolver,
        clock=clock,
        extractor=extractor,
    )
    retriever = ReferenceEvidenceRetriever(
        repository=repository,
        config=resolved_config.retrieval,
    )
    rule_engine = DeterministicRuleEngine(config=resolved_config.rules, clock=clock)
    escalation = ConservativeEscalationPolicy(resolved_config.rater)
    decision = ConservativeDecisionEngine(resolved_config.decision)
    service = HybridVerificationService(
        config=resolved_config,
        normalizer=normalizer,
        retriever=retriever,
        rules=rule_engine,
        escalation=escalation,
        decision=decision,
        rater=rater,
        metrics=metrics,
        timeout_seconds=resolved_settings.server.verification_timeout_seconds,
        batch_concurrency=resolved_settings.server.batch_concurrency,
        max_batch_items=resolved_settings.server.max_batch_items,
    )

    logger.info(
        "container_initialized",
        **resolved_settings.describe(),
        **resolved_config.fingerprint(),
        reference_records=repository.stats()["records"],
    )

    return ServiceContainer(
        settings=resolved_settings,
        pipeline_config=resolved_config,
        repository=repository,
        metrics=metrics,
        clock=clock,
        service=service,
        rater=rater,
        extractor=extractor,
    )
