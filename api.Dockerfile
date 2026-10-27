# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU build of torch keeps the image small; swap the index URL for a CUDA build if a GPU is available.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.3.1

COPY pyproject.toml README.md ./
COPY shortfall_bridge ./shortfall_bridge
RUN pip install ".[ml]"

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["shortfall", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
