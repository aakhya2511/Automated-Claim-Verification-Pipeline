# Deployment

The reference deployment is an internal service behind an authenticated gateway or private
network boundary. Python 3.12 is required.

## Locked local setup

```bash
uv sync --frozen --extra dev
uv run pytest
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Update dependencies deliberately with `uv lock --upgrade`, review the lock diff, then rerun all
quality gates and the installed-wheel smoke. `make setup` never regenerates data.

## Wheel

```bash
python -m build
python -m venv /tmp/acv-runtime
/tmp/acv-runtime/bin/pip install dist/automated_claim_verification-0.1.0-py3-none-any.whl
cd /tmp
ACV_LLM__PROVIDER=fake /tmp/acv-runtime/bin/uvicorn app.main:app
```

The wheel includes the frozen final profile, V1 prompts, and synthetic demo catalog. Set
`ACV_DATA__REFERENCE_CATALOG_PATH`, `ACV_DATA__CONFIGS_DIR`, or `ACV_DATA__PROMPTS_DIR` to use
deployment-controlled assets. Startup validates and indexes these resources before serving.

## Docker

```bash
make docker-build
make docker-run
make docker-smoke
```

The multi-stage image installs the wheel into Python 3.12 slim, runs as UID/GID 10001, contains
no credential, uses exec-form Uvicorn for signal handling, and has a dependency-free liveness
check. Fake/offline is the image default. Mount or bake governed catalog data in a downstream
image and configure its explicit path.

`/health` answers process liveness. `/ready` confirms the catalog is loaded and reports provider
configuration without making a billable or slow probe. `/metrics` exposes Prometheus text.

## Providers

Ollama and OpenAI are optional. OpenAI needs `ACV_LLM__API_KEY` supplied at runtime through the
deployment secret manager; never put it in an image, `.env` committed to Git, or a command-line
argument. Set provider/model/base URL and run a one-time deployment preflight where appropriate.
See [ollama.md](ollama.md) for container networking.
