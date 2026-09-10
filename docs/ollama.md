# Local Ollama provider

The Ollama adapter uses the native local HTTP API and implements the existing provider-neutral
`LLMRater` and `ClaimExtractor` protocols. It loads the unchanged V1 prompts and submits the
existing JSON schemas through Ollama's structured-output `format` field. Provider output is
always revalidated with the existing strict Pydantic models.

## macOS setup

This repository is currently on Apple Silicon macOS with Homebrew available. Install Ollama,
start its background service, and pull the selected baseline model:

```bash
brew install ollama
brew services start ollama
ollama pull qwen2.5:7b
```

Configure the process without a credential:

```bash
export ACV_LLM__PROVIDER=ollama
export ACV_LLM__BASE_URL=http://127.0.0.1:11434/api
export ACV_LLM__MODEL=qwen2.5:7b
export ACV_LLM__TEMPERATURE=0.0
```

Then run the preflight:

```bash
make ollama-preflight
```

Preflight must report the Ollama version, resolved model name, stable model digest, and a
successful strict structured-output rating. Only then should the baseline snapshot be refreshed
and the development diagnostic evaluation started:

```bash
make freeze-baseline
make evaluate-baseline
```

The snapshot records the digest, and the runner refuses to resume or start when the installed
model digest differs. The evaluation checkpoint is bound to the diagnostic dataset hash,
baseline configuration hash, provider, and model. Predictions are written atomically and remain
ordered by diagnostic sample ID.

Do not run the Phase 5 frozen holdout through this command. The runner independently rejects its
content hash.
