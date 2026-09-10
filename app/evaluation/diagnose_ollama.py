"""Local-only Ollama structured-output diagnostic; never used by production."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from pydantic import ValidationError

from app.core.config import Settings
from app.domain.enums import Attribute, ClaimType
from app.domain.models import NormalizedClaim, RatingContext, ReferenceEvidence
from app.raters.ollama import OLLAMA_RATING_JSON_SCHEMA, OllamaTransport, _find_model
from app.raters.openai import _rating_input
from app.raters.prompts import load_prompt
from app.raters.schemas import SemanticRatingPayload


async def diagnose() -> int:
    settings = Settings()
    if settings.llm.provider != "ollama":
        print("diagnostic=FAILED: configure ACV_LLM__PROVIDER=ollama")
        return 2
    transport = OllamaTransport(settings.llm)
    try:
        version = await transport.get_json("version")
        tags = await transport.get_json("tags")
        model = _find_model(tags, settings.llm.model)
        claim = NormalizedClaim(
            raw_text="This product includes free shipping.",
            claim_type=ClaimType.SHIPPING,
            attribute=Attribute.FREE_SHIPPING,
            value=True,
        )
        evidence = ReferenceEvidence(record_id="preflight", fields={"free_shipping": True})
        context = RatingContext(evaluation_date=date(2026, 9, 9), request_id="preflight")
        response = await transport.chat(
            messages=[
                {
                    "role": "system",
                    "content": load_prompt("rater", settings.llm.rater_prompt_version),
                },
                {
                    "role": "user",
                    "content": _rating_input(claim, evidence, context),
                },
            ],
            schema=OLLAMA_RATING_JSON_SCHEMA,
        )
        print("http_status=200")
        print(f"model={model.get('name') or model.get('model')}")
        print(f"model_digest={model.get('digest')}")
        print(f"ollama_version={version.get('version')}")
        print(f"raw_message_content={response.text}")
        try:
            parsed_json = json.loads(response.text)
        except json.JSONDecodeError as exc:
            print(f"parsed_json=FAILED: {exc}")
            return 2
        print(f"parsed_json={json.dumps(parsed_json, sort_keys=True)}")
        try:
            SemanticRatingPayload.model_validate_json(response.text)
        except ValidationError as exc:
            print(f"pydantic_errors={json.dumps(exc.errors(), default=str, sort_keys=True)}")
            return 2
        print("pydantic_validation=PASS")
        return 0
    finally:
        await transport.aclose()


def main() -> int:
    return asyncio.run(diagnose())


if __name__ == "__main__":
    raise SystemExit(main())
