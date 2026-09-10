"""Safe Ollama preflight for endpoint, model digest, and structured output."""

from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.core.exceptions import RaterError
from app.raters.ollama import OLLAMA_DEFAULT_BASE_URL, preflight_ollama


def main() -> int:
    settings = Settings()
    if settings.llm.provider != "ollama":
        print("ollama_preflight=NOT_READY: set ACV_LLM__PROVIDER=ollama")
        print(f"endpoint={settings.llm.base_url or OLLAMA_DEFAULT_BASE_URL}")
        print(f"configured_model={settings.llm.model}")
        return 2
    try:
        identity = asyncio.run(preflight_ollama(settings.llm))
    except RaterError as exc:
        print(f"ollama_preflight=FAILED: {exc.message}")
        print(f"endpoint={settings.llm.base_url or OLLAMA_DEFAULT_BASE_URL}")
        print(f"configured_model={settings.llm.model}")
        return 2
    print("ollama_preflight=PASS")
    for key, value in identity.model_dump().items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
