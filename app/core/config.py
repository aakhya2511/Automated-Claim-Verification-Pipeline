"""Deployment settings, sourced from the environment and validated at startup.

Credentials live only here, only as :class:`SecretStr`, and only from the
environment. Nothing in this module has a committed default that would work
against a real provider.

All variables use the ``ACV_`` prefix with ``__`` as the nesting delimiter, e.g.
``ACV_LLM__API_KEY``, ``ACV_SERVER__PORT``.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[2]

Environment = Literal["local", "test", "staging", "production"]
RaterProvider = Literal["fake", "openai"]
LogFormat = Literal["json", "console"]


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServerSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65_535)
    #: Rejected before parsing, so a huge body cannot consume memory.
    max_request_bytes: int = Field(default=256 * 1024, ge=1024)
    max_batch_items: int = Field(default=100, ge=1, le=1000)
    #: Bound on concurrent in-flight verifications inside one batch request.
    batch_concurrency: int = Field(default=16, ge=1, le=256)
    verification_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)


class LLMSettings(_Section):
    """Semantic-rater provider settings.

    ``provider="fake"`` selects the deterministic in-process rater, which is
    what unit tests and offline evaluation use; no unit test may perform a
    network call to a model provider.
    """

    provider: RaterProvider = "fake"
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    #: Total budget for one rater call, including retries' own timeouts.
    timeout_seconds: float = Field(default=6.0, gt=0.0, le=120.0)
    connect_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0.0, le=5.0)
    #: Provider-level connection pool; reused across requests to keep TLS
    #: handshakes off the per-claim latency path.
    max_connections: int = Field(default=32, ge=1, le=512)
    max_keepalive_connections: int = Field(default=16, ge=1, le=512)
    #: Global ceiling on concurrent provider calls, independent of batch size.
    max_concurrency: int = Field(default=8, ge=1, le=128)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    rater_prompt_version: str = "v1"
    extraction_prompt_version: str = "v1"
    schema_version: str = "1.0"
    max_output_tokens: int = Field(default=256, ge=32, le=4096)

    @property
    def requires_credentials(self) -> bool:
        return self.provider != "fake"


class DataSettings(_Section):
    reference_catalog_path: Path = REPO_ROOT / "data" / "reference" / "catalog.jsonl"
    evaluation_dataset_path: Path = REPO_ROOT / "data" / "evaluation" / "v1" / "benchmark_500.jsonl"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    prompts_dir: Path = REPO_ROOT / "prompts"
    configs_dir: Path = REPO_ROOT / "configs"


class ObservabilitySettings(_Section):
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    metrics_enabled: bool = True
    #: Raw claim text is user-supplied content; off by default so ordinary
    #: production logs carry only structured, non-sensitive fields.
    log_claim_text: bool = False
    #: Guarded debug endpoints (config echo, cache stats). Never on in prod.
    expose_debug_endpoints: bool = False


class Settings(BaseSettings):
    """Root settings object. Constructed once per process."""

    model_config = SettingsConfigDict(
        env_prefix="ACV_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_name: str = "automated-claim-verification"
    environment: Environment = "local"
    #: Pipeline profile loaded at startup, relative to ``data.configs_dir``.
    pipeline_config_file: str = "optimized.yaml"

    server: ServerSettings = ServerSettings()
    llm: LLMSettings = LLMSettings()
    data: DataSettings = DataSettings()
    observability: ObservabilitySettings = ObservabilitySettings()

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.llm.requires_credentials and not self.llm.api_key:
            raise ValueError(
                f"llm.provider={self.llm.provider!r} requires ACV_LLM__API_KEY to be set"
            )
        if self.environment == "production" and self.observability.expose_debug_endpoints:
            raise ValueError("debug endpoints must not be exposed in production")
        return self

    @property
    def pipeline_config_path(self) -> Path:
        return self.data.configs_dir / self.pipeline_config_file

    def describe(self) -> dict[str, object]:
        """Redacted snapshot for logs and the debug endpoint."""
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "pipeline_config_file": self.pipeline_config_file,
            "llm_provider": self.llm.provider,
            "llm_model": self.llm.model,
            "llm_credentials_present": self.llm.api_key is not None,
            "metrics_enabled": self.observability.metrics_enabled,
            "reference_catalog_path": str(self.data.reference_catalog_path),
        }


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that a misconfiguration surfaces exactly once, at startup, with a
    :class:`ConfigurationError` instead of a pydantic traceback.
    """
    try:
        return Settings()
    except ValueError as exc:
        raise ConfigurationError(f"invalid configuration: {exc}") from exc
