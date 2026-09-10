"""FastAPI dependency providers.

Thin accessors over the :class:`ServiceContainer` built at startup. Routes
depend on interfaces resolved here rather than importing concrete
implementations, which is what lets integration tests override a single
collaborator (e.g. a failing repository) without rebuilding the app.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.bootstrap import ServiceContainer
from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.core.metrics import Metrics
from app.core.pipeline_config import PipelineConfig
from app.retrieval.repository import InMemoryReferenceRepository
from app.verification.service import HybridVerificationService


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise ConfigurationError("service container is not initialized")
    return container  # type: ignore[no-any-return]


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def get_settings_dep(container: ContainerDep) -> Settings:
    return container.settings


def get_pipeline_config(container: ContainerDep) -> PipelineConfig:
    return container.pipeline_config


def get_repository(container: ContainerDep) -> InMemoryReferenceRepository:
    return container.repository


def get_metrics(container: ContainerDep) -> Metrics:
    return container.metrics


def get_verification_service(container: ContainerDep) -> HybridVerificationService:
    if container.service is None:
        raise ConfigurationError("verification service is not initialized")
    return container.service


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
PipelineConfigDep = Annotated[PipelineConfig, Depends(get_pipeline_config)]
RepositoryDep = Annotated[InMemoryReferenceRepository, Depends(get_repository)]
MetricsDep = Annotated[Metrics, Depends(get_metrics)]
VerificationServiceDep = Annotated[HybridVerificationService, Depends(get_verification_service)]
