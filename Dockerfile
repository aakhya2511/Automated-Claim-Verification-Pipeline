FROM python:3.12-slim AS builder

WORKDIR /build
RUN python -m pip install --no-cache-dir "build>=1.2,<2" "uv==0.12.12"
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY app ./app
COPY configs/phase7/optimized_final.yaml ./configs/phase7/optimized_final.yaml
COPY prompts/rater/v1.txt ./prompts/rater/v1.txt
COPY prompts/extractor/v1.txt ./prompts/extractor/v1.txt
COPY data/reference/catalog.jsonl ./data/reference/catalog.jsonl
RUN uv export --frozen --no-dev --no-emit-project --no-annotate \
    --output-file requirements.txt
RUN python -m build --wheel

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACV_ENVIRONMENT=production \
    ACV_LLM__PROVIDER=fake \
    ACV_SERVER__HOST=0.0.0.0 \
    ACV_SERVER__PORT=8000

RUN groupadd --gid 10001 acv && useradd --uid 10001 --gid acv --no-create-home acv
COPY --from=builder /build/dist/*.whl /tmp/dist/
COPY --from=builder /build/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && \
    python -m pip install --no-cache-dir --no-deps /tmp/dist/*.whl && \
    rm -r /tmp/dist /tmp/requirements.txt

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
