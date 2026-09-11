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
    state: Literal["available", "unavailable", "configured_not_checked", "not_required"]
    detail: str | None = None


class HealthResponse(BaseModel):
    """Cheap process liveness response; it performs no dependency probes."""

    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    """Local readiness plus honest, non-probed provider state."""

    status: Literal["ready", "not_ready"]
    version: str
    environment: str
    pipeline_config: str
    pipeline_version: str
    reference_records: int
    dependencies: list[DependencyStatus] = Field(default_factory=list)


@router.get("/health", response_model=HealthResponse, summary="Process liveness")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=ReadinessResponse, summary="Runtime readiness")
async def ready(container: ContainerDep, response: Response) -> ReadinessResponse:
    repository_healthy = await container.repository.health_check()
    stats = container.repository.stats()
    if not repository_healthy:
        response.status_code = 503

    dependencies = [
        DependencyStatus(
            name="reference_repository",
            state="available" if repository_healthy else "unavailable",
            detail=f"{stats['records']} records, {stats['distinct_tokens']} indexed tokens",
        ),
        # The rater is intentionally reported but not probed: a provider outage
        # must not mark the service unhealthy, because deterministic
        # verification keeps working without it.
        DependencyStatus(
            name="llm_rater",
            state=(
                "not_required"
                if container.settings.llm.provider == "fake"
                else "configured_not_checked"
            ),
            detail=(
                "offline fake provider; no external dependency"
                if container.settings.llm.provider == "fake"
                else f"provider={container.settings.llm.provider}; no readiness probe performed"
            ),
        ),
    ]

    return ReadinessResponse(
        status="ready" if repository_healthy else "not_ready",
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
