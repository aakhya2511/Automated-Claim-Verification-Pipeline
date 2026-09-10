"""Health, readiness, and metrics endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app import __version__
from app.api.dependencies import ContainerDep, MetricsDep
from app.core.metrics import CONTENT_TYPE_LATEST

router = APIRouter(tags=["operations"])


class DependencyStatus(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    """Liveness plus a summary of the loaded source of truth.

    Reference-record counts are included because "the service is up but the
    catalog failed to load" is the failure that would otherwise silently turn
    every verdict into INSUFFICIENT_EVIDENCE.
    """

    status: Literal["ok", "degraded"]
    version: str
    environment: str
    pipeline_config: str
    pipeline_version: str
    reference_records: int
    dependencies: list[DependencyStatus] = Field(default_factory=list)


@router.get("/health", response_model=HealthResponse, summary="Liveness and readiness")
async def health(container: ContainerDep) -> HealthResponse:
    repository_healthy = await container.repository.health_check()
    stats = container.repository.stats()

    dependencies = [
        DependencyStatus(
            name="reference_repository",
            healthy=repository_healthy,
            detail=f"{stats['records']} records, {stats['distinct_tokens']} indexed tokens",
        ),
        # The rater is intentionally reported but not probed: a provider outage
        # must not mark the service unhealthy, because deterministic
        # verification keeps working without it.
        DependencyStatus(
            name="llm_rater",
            healthy=True,
            detail=f"provider={container.settings.llm.provider}",
        ),
    ]

    return HealthResponse(
        status="ok" if repository_healthy else "degraded",
        version=__version__,
        environment=container.settings.environment,
        pipeline_config=container.pipeline_config.name,
        pipeline_version=container.pipeline_config.pipeline_version,
        reference_records=stats["records"],
        dependencies=dependencies,
    )


@router.get("/metrics", summary="Prometheus exposition", include_in_schema=False)
async def metrics(metrics_sink: MetricsDep) -> Response:
    return Response(content=metrics_sink.render(), media_type=CONTENT_TYPE_LATEST)
