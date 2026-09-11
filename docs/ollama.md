# Local Ollama provider

The Ollama adapter uses the native HTTP API behind the provider-neutral `LLMRater` and
`ClaimExtractor` protocols. It sends the unchanged V1 prompts and existing JSON schemas through
Ollama's structured-output `format`; strict Pydantic validation remains mandatory.

## Local setup

On macOS with Homebrew:

```bash
brew install ollama
brew services start ollama
ollama pull qwen2.5:7b
```

Configure the host process without a credential and run the explicit preflight:

```bash
export ACV_LLM__PROVIDER=ollama
export ACV_LLM__BASE_URL=http://127.0.0.1:11434/api
export ACV_LLM__MODEL=qwen2.5:7b
export ACV_LLM__TEMPERATURE=0.0
make ollama-preflight
```

Preflight checks endpoint reachability, model availability/digest, Ollama version, and a tiny
normal-schema rating. It is manual and never part of CI or service readiness.

## Docker networking

`127.0.0.1` inside Docker refers to the container, not host Ollama. On macOS or Windows Docker
Desktop use the built-in host name:

```bash
docker run --rm -p 8000:8000 \
  -e ACV_LLM__PROVIDER=ollama \
  -e ACV_LLM__MODEL=qwen2.5:7b \
  -e ACV_LLM__BASE_URL=http://host.docker.internal:11434/api \
  automated-claim-verification:local
```

On Linux, explicitly add the host gateway:

```bash
docker run --rm -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e ACV_LLM__PROVIDER=ollama \
  -e ACV_LLM__MODEL=qwen2.5:7b \
  -e ACV_LLM__BASE_URL=http://host.docker.internal:11434/api \
  automated-claim-verification:local
```

Alternatively place an explicitly configured Ollama service on a private Docker network and use
its service DNS name. Do not assume every deployment host has Ollama installed. Optional OpenAI
mode uses `ACV_LLM__PROVIDER=openai` and a runtime secret; fake mode has no external dependency.

## Evaluation finality

The historical snapshot/run tooling binds the exact model digest, prompt/config/schema hashes,
catalog and dataset fingerprints, and checkpoint identity. The Phase 8 holdout is consumed and
must not be rerun. Preflight confirms deployment mechanics; it does not authorize a new official
evaluation.
