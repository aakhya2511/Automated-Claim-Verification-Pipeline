"""Declarative pipeline configuration.

Two distinct kinds of configuration exist in this service and they are kept
apart on purpose:

* :mod:`app.core.config` — *deployment* settings: credentials, host/port, data
  paths, timeouts. Sourced from the environment, never committed.
* this module — *behavioural* settings: which stages run, tolerances,
  thresholds, prompt version. Sourced from versioned YAML under ``configs/``
  and treated as experiment configuration.

The second kind is what makes the baseline-vs-optimized comparison honest: the
two arms are the same code path driven by two config files, so a measured
difference is attributable to the configured behaviour rather than to divergent
implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.exceptions import ConfigurationError

#: Bumped when stage semantics change in a way that invalidates stored results.
PIPELINE_VERSION = "1.0.0"

EvidenceProjection = Literal["full_record", "claim_scoped"]
RetrievalStrategy = Literal["id_only", "id_then_lexical"]
ConflictPolicy = Literal["rules_win", "rater_wins"]


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NormalizationConfig(_Section):
    """Controls how much structure is extracted before verification."""

    #: When false the claim is passed through with only whitespace/case cleanup,
    #: which is the baseline "hand the string to the model" behaviour.
    deterministic_parsing: bool = True
    llm_extraction_fallback: bool = False
    llm_extraction_below_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    alias_resolution: bool = True
    negation_detection: bool = True
    qualifier_detection: bool = True
    currency_normalization: bool = True
    #: Claims longer than this are rejected as INVALID_CLAIM before any work.
    max_claim_chars: int = Field(default=1000, ge=16, le=20_000)
    min_claim_chars: int = Field(default=3, ge=1)


class RetrievalConfig(_Section):
    """Controls how the source-of-truth slice is selected."""

    strategy: RetrievalStrategy = "id_then_lexical"
    max_candidates: int = Field(default=5, ge=1, le=50)
    #: Lexical candidates below this score are not treated as a resolved entity.
    min_match_score: float = Field(default=0.45, ge=0.0, le=1.0)
    #: ``full_record`` dumps every populated field into the evidence (and the
    #: prompt); ``claim_scoped`` sends only fields relevant to the claim type.
    evidence_projection: EvidenceProjection = "claim_scoped"
    include_terms_text: bool = True


class RuleConfig(_Section):
    """Tolerances and permissions for the deterministic layer."""

    enabled: bool = True

    #: Absolute (currency units) and relative tolerance for money comparisons.
    #: Both must be exceeded for a mismatch, which absorbs rounding noise such
    #: as 19.99 vs 19.990 without accepting 149 vs 199.
    price_absolute_tolerance: float = Field(default=0.01, ge=0.0)
    price_relative_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    percent_absolute_tolerance: float = Field(default=0.01, ge=0.0)
    #: Widened band applied only when the claim is hedged with "approximately".
    approximate_relative_tolerance: float = Field(default=0.05, ge=0.0, le=1.0)
    trial_days_tolerance: int = Field(default=0, ge=0)

    #: Days of slack around promotion boundaries, to avoid calling a claim wrong
    #: purely because of a timezone offset at the edge of the window.
    date_grace_days: int = Field(default=0, ge=0, le=7)

    #: Whether deterministic rules may terminate the pipeline without the rater.
    allow_terminal_supported: bool = True
    allow_terminal_contradicted: bool = True
    #: Rules below this confidence escalate instead of deciding.
    min_terminal_confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class RaterConfig(_Section):
    """Semantic-rater routing and prompt selection."""

    enabled: bool = True
    prompt_version: str = "rater_v2"
    schema_version: str = "1.0"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=256, ge=32, le=4096)

    #: Baseline sends everything to the model. The optimized arm only escalates
    #: claims the deterministic layer could not settle.
    always_invoke: bool = False
    #: Whether to spend a model call when the reference has no relevant field.
    #: Disabled in the optimized arm: missing evidence is an abstention, and
    #: asking the model invites it to invent a contradiction.
    invoke_on_missing_evidence: bool = False
    invoke_on_unresolved_entity: bool = False
    max_concurrency: int = Field(default=8, ge=1, le=128)


class PostRaterHeuristics(_Section):
    """Guards applied to rater output before it becomes a verdict.

    Each flag targets a failure mode observed in baseline error analysis; they
    exist to convert over-confident model contradictions into abstentions
    rather than to override genuine mismatches.
    """

    #: Model says CONTRADICTED but the field it judged is absent from the
    #: reference -> demote to INSUFFICIENT_EVIDENCE.
    demote_contradiction_on_missing_field: bool = True
    #: Model ignores "up to"/"starting at"/"approximately" hedging -> re-check
    #: the numeric relation deterministically and demote if the hedge holds.
    respect_qualifiers: bool = True
    #: Numeric claim types must agree with the reference arithmetic; the model
    #: cannot contradict a number that deterministically matches.
    require_numeric_agreement: bool = True
    #: Never emit SUPPORTED when no evidence was retrieved.
    block_support_without_evidence: bool = True
    #: Reject an expired-offer SUPPORTED verdict against the evaluation date.
    enforce_offer_window: bool = True


class DecisionConfig(_Section):
    """Confidence calibration and rule/rater conflict resolution."""

    #: Minimum rater confidence required to emit each verdict class.
    contradiction_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    support_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    #: Below this the pipeline abstains regardless of the claimed verdict.
    abstain_below: float = Field(default=0.50, ge=0.0, le=1.0)
    conflict_policy: ConflictPolicy = "rules_win"
    heuristics: PostRaterHeuristics = PostRaterHeuristics()
    #: Applied to the final confidence of rater-only decisions, reflecting the
    #: measured reliability of that path relative to deterministic rules.
    rater_confidence_scale: float = Field(default=1.0, ge=0.1, le=1.0)

    @model_validator(mode="after")
    def _check_threshold_order(self) -> Self:
        if self.abstain_below > min(self.contradiction_threshold, self.support_threshold):
            raise ValueError(
                "abstain_below must not exceed contradiction_threshold or support_threshold"
            )
        return self


class CacheConfig(_Section):
    """Response caching. Safe only because the key includes reference freshness."""

    enabled: bool = True
    ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    max_entries: int = Field(default=10_000, ge=1)


class PipelineConfig(BaseModel):
    """A complete, named pipeline behaviour profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    pipeline_version: str = PIPELINE_VERSION
    experiment_id: str | None = None

    normalization: NormalizationConfig = NormalizationConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    rules: RuleConfig = RuleConfig()
    rater: RaterConfig = RaterConfig()
    decision: DecisionConfig = DecisionConfig()
    cache: CacheConfig = CacheConfig()

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        if not self.rules.enabled and not self.rater.enabled:
            raise ValueError("at least one of rules.enabled or rater.enabled must be true")
        if not self.rules.enabled and not self.rater.always_invoke:
            raise ValueError(
                "rules are disabled, so rater.always_invoke must be true or claims cannot be judged"
            )
        return self

    def fingerprint(self) -> dict[str, Any]:
        """Compact identity of this configuration, stored with every result set."""
        return {
            "config_name": self.name,
            "pipeline_version": self.pipeline_version,
            "experiment_id": self.experiment_id,
            "prompt_version": self.rater.prompt_version if self.rater.enabled else None,
            "schema_version": self.rater.schema_version if self.rater.enabled else None,
            "temperature": self.rater.temperature,
            "rules_enabled": self.rules.enabled,
            "always_invoke_rater": self.rater.always_invoke,
        }


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    """Load and validate a pipeline profile from YAML.

    Raises :class:`ConfigurationError` rather than a bare pydantic error so
    startup and CLI entry points can report a single, actionable message.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigurationError(f"pipeline config not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"pipeline config {config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(f"pipeline config {config_path} must contain a mapping")

    try:
        return PipelineConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigurationError(f"invalid pipeline config {config_path}: {exc}") from exc
