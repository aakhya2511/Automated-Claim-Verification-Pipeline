.DEFAULT_GOAL := help
SHELL := /bin/bash

PY_VERSION := 3.12
VENV := .venv
PYTHON := $(VENV)/bin/python
UV := $(shell command -v uv 2>/dev/null)

.PHONY: help setup reference-data generate-eval validate-eval generate-dev validate-dev ollama-preflight baseline-smoke freeze-baseline evaluate-baseline test lint fmt typecheck check run build installed-wheel-smoke docker-build docker-run docker-smoke release-integrity smoke-llm clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/python: pyproject.toml uv.lock
ifdef UV
	uv sync --frozen --python $(PY_VERSION) --extra dev
else
	python$(PY_VERSION) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
endif

setup: $(VENV)/bin/python ## Create the venv and install locked development dependencies
	@echo "environment ready: $$($(PYTHON) --version)"

reference-data: $(VENV)/bin/python ## Regenerate the deterministic reference catalog
	$(PYTHON) scripts/generate_reference_data.py --out data/reference/catalog.jsonl

generate-eval: $(VENV)/bin/python ## Reproduce the frozen 500-sample evaluation benchmark
	$(PYTHON) -m app.evaluation.generate \
		--output data/evaluation/v1/benchmark_500.jsonl --samples 500 --seed 20260909

validate-eval: $(VENV)/bin/python ## Validate the frozen benchmark and manifest
	$(PYTHON) -m app.evaluation.validate data/evaluation/v1/benchmark_500.jsonl

generate-dev: $(VENV)/bin/python ## Reproduce the separate 400-sample diagnostic corpus
	$(PYTHON) -m app.evaluation.generate_dev \
		--output data/evaluation/dev/v1/diagnostic.jsonl --samples 400 --seed 20260910

validate-dev: $(VENV)/bin/python ## Validate diagnostic integrity and zero holdout overlap
	$(PYTHON) -m app.evaluation.validate_dev data/evaluation/dev/v1/diagnostic.jsonl

ollama-preflight: $(VENV)/bin/python ## Verify local Ollama, model digest, and structured output
	$(PYTHON) -m app.evaluation.preflight_ollama

baseline-smoke: $(VENV)/bin/python ## Exercise dedicated non-corpus Phase 6 baseline requests
	$(PYTHON) -m app.evaluation.smoke_baseline

freeze-baseline: $(VENV)/bin/python ## Snapshot redacted baseline configuration and source identity
	$(PYTHON) -m app.evaluation.freeze_baseline \
		--output experiments/baseline/v1/config.json

evaluate-baseline: $(VENV)/bin/python ## Run the real-provider development baseline
	$(PYTHON) -m app.evaluation.run \
		--dataset data/evaluation/dev/v1/diagnostic.jsonl \
		--config experiments/baseline/v1/config.json \
		--output artifacts/baseline/v1

test: ## Run the test suite (no network, no model calls)
	$(PYTHON) -m pytest

lint: ## Lint with ruff
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt: ## Auto-format and auto-fix
	$(PYTHON) -m ruff format app tests scripts
	$(PYTHON) -m ruff check --fix app tests scripts

typecheck: ## Static type check with mypy
	$(PYTHON) -m mypy --strict app

check: lint typecheck test ## Everything CI runs

run: ## Start the API with reload
	$(PYTHON) -m uvicorn app.main:app --reload \
		--host $${ACV_SERVER__HOST:-127.0.0.1} --port $${ACV_SERVER__PORT:-8000}

build: ## Build source and wheel distributions
	$(PYTHON) -m build

installed-wheel-smoke: ## Build/install the wheel in isolation and smoke the offline API
	$(PYTHON) scripts/installed_wheel_smoke.py

docker-build: ## Build the production-style container image
	docker build --tag automated-claim-verification:local .

docker-run: ## Run the image in offline mode on port 8000
	docker run --rm --init --publish 8000:8000 \
		--env ACV_LLM__PROVIDER=fake automated-claim-verification:local

docker-smoke: docker-build ## Smoke liveness and deterministic verification in Docker
	$(PYTHON) scripts/docker_smoke.py --image automated-claim-verification:local

release-integrity: ## Validate frozen public hashes without rerunning benchmarks
	$(PYTHON) scripts/check_release_integrity.py

smoke-llm: ## Run opt-in live semantic-rater smoke cases (skips without credentials)
	$(PYTHON) scripts/smoke_llm.py

clean: ## Remove caches and the virtualenv
	rm -rf $(VENV) build dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
