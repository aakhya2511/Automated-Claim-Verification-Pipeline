"""Configuration: startup validation and the baseline/optimized profile split."""

from __future__ import annotations

import pytest
from app.core.config import LLMSettings, Settings
from app.core.exceptions import ConfigurationError
from app.core.pipeline_config import PipelineConfig, load_pipeline_config


class TestSettings:
    def test_defaults_run_without_credentials(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.llm.provider == "fake"
        assert settings.llm.requires_credentials is False
        assert settings.pipeline_config_file == "phase7/optimized_final.yaml"
        assert settings.pipeline_config_path.is_file()

    def test_real_provider_without_key_fails_at_startup(self) -> None:
        # Better to refuse to boot than to 502 on the first ambiguous claim.
        with pytest.raises(ValueError, match="ACV_LLM__API_KEY"):
            Settings(_env_file=None, llm=LLMSettings(provider="openai"))

    def test_ollama_provider_requires_no_key(self) -> None:
        settings = Settings(
            _env_file=None,
            llm=LLMSettings(
                provider="ollama", base_url="http://127.0.0.1:11434/api", model="qwen2.5:7b"
            ),
        )
        assert settings.llm.requires_credentials is False
        assert settings.llm.api_key is None

    def test_ollama_rejects_invalid_endpoint(self) -> None:
        with pytest.raises(ValueError, match="absolute HTTP"):
            LLMSettings(provider="ollama", base_url="localhost:11434/api")

    def test_removed_observability_switches_are_not_part_of_settings(self) -> None:
        fields = Settings.model_fields["observability"].default.__class__.model_fields
        assert "expose_debug_endpoints" not in fields
        assert "log_claim_text" not in fields

    def test_nested_env_vars_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACV_SERVER__PORT", "9123")
        monkeypatch.setenv("ACV_LLM__MAX_CONCURRENCY", "4")
        settings = Settings(_env_file=None)
        assert settings.server.port == 9123
        assert settings.llm.max_concurrency == 4

    def test_describe_never_exposes_the_key(self) -> None:
        settings = Settings(_env_file=None, llm=LLMSettings(provider="openai", api_key="sk-secret"))
        described = settings.describe()
        assert described["llm_credentials_present"] is True
        assert "sk-secret" not in str(described)

    def test_secret_is_not_in_repr(self) -> None:
        settings = Settings(_env_file=None, llm=LLMSettings(provider="openai", api_key="sk-secret"))
        assert "sk-secret" not in repr(settings)


class TestPipelineConfigLoading:
    def test_loads_optimized_profile(self, optimized_config: PipelineConfig) -> None:
        assert optimized_config.name == "optimized"
        assert optimized_config.rules.enabled is True
        assert optimized_config.rater.always_invoke is False

    def test_loads_baseline_profile(self, baseline_config: PipelineConfig) -> None:
        assert baseline_config.name == "baseline"
        assert baseline_config.rules.enabled is False
        assert baseline_config.rater.always_invoke is True
        assert baseline_config.retrieval.evidence_projection == "full_record"

    def test_profiles_differ_only_in_behaviour_flags(
        self, baseline_config: PipelineConfig, optimized_config: PipelineConfig
    ) -> None:
        # The comparison is only meaningful if both arms run the same code at
        # the same pipeline version.
        assert baseline_config.pipeline_version == optimized_config.pipeline_version

    def test_missing_file_raises_configuration_error(self, tmp_path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            load_pipeline_config(tmp_path / "absent.yaml")

    def test_invalid_yaml_raises_configuration_error(self, tmp_path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("name: [unclosed\n")
        with pytest.raises(ConfigurationError, match="not valid YAML"):
            load_pipeline_config(path)

    def test_unknown_key_is_rejected(self, tmp_path) -> None:
        # extra="forbid" turns a config typo into a startup failure rather
        # than a silently ignored setting.
        path = tmp_path / "typo.yaml"
        path.write_text("name: typo\nrulez:\n  enabled: true\n")
        with pytest.raises(ConfigurationError, match="invalid pipeline config"):
            load_pipeline_config(path)


class TestPipelineConfigInvariants:
    def test_cannot_disable_both_layers(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            PipelineConfig.model_validate(
                {
                    "name": "empty",
                    "rules": {"enabled": False},
                    "rater": {"enabled": False},
                }
            )

    def test_rules_disabled_requires_always_invoke_rater(self) -> None:
        with pytest.raises(ValueError, match="always_invoke"):
            PipelineConfig.model_validate(
                {
                    "name": "unjudgeable",
                    "rules": {"enabled": False},
                    "rater": {"enabled": True, "always_invoke": False},
                }
            )

    def test_abstain_threshold_must_not_exceed_decision_thresholds(self) -> None:
        with pytest.raises(ValueError, match="abstain_below"):
            PipelineConfig.model_validate(
                {
                    "name": "inverted",
                    "decision": {
                        "contradiction_threshold": 0.4,
                        "support_threshold": 0.4,
                        "abstain_below": 0.9,
                    },
                }
            )

    def test_fingerprint_captures_experiment_identity(
        self, optimized_config: PipelineConfig
    ) -> None:
        fingerprint = optimized_config.fingerprint()
        assert fingerprint["config_name"] == "optimized"
        assert fingerprint["prompt_version"] == "v1"
        assert fingerprint["temperature"] == 0.0

    def test_config_is_immutable(self, optimized_config: PipelineConfig) -> None:
        with pytest.raises(ValueError):
            optimized_config.rules.enabled = False  # type: ignore[misc]

    def test_rule_allow_list_rejects_duplicates(self) -> None:
        with pytest.raises(ValueError, match="duplicates"):
            PipelineConfig.model_validate(
                {
                    "name": "duplicate-rules",
                    "rules": {"enabled_rule_ids": ["numeric_comparison", "numeric_comparison"]},
                }
            )
