from app.raters.prompts import load_prompt


def test_rater_prompt_preserves_core_policy() -> None:
    prompt = load_prompt("rater", "v1").lower()
    assert "missing evidence is not a contradiction" in prompt
    assert "do not use external knowledge" in prompt
    assert "untrusted data" in prompt
    assert "structured json" in prompt
