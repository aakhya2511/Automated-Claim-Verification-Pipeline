"""Credential redaction is recursive and independent of key spelling."""

from app.core.logging import redact_secrets


def test_recursive_redaction_covers_nested_headers_and_provider_payloads() -> None:
    value = {
        "headers": {"Authorization": "Bearer private", "x-api-key": "private"},
        "provider": [{"access_token": "private", "model": "local"}],
        "safe": "visible",
    }

    redacted = redact_secrets(value)

    assert "private" not in repr(redacted)
    assert redacted["safe"] == "visible"
    assert redacted["provider"][0]["model"] == "local"


def test_non_secret_claim_metadata_is_preserved() -> None:
    value = {"claim_hash": "abc123", "claim_length": 42, "reference_id": "offer-1"}
    assert redact_secrets(value) == value
