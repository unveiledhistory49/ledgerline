# LedgerLine API
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps from pyproject. Copy order maximises layer cache reuse.
# README.md is required because pyproject declares `readme = "README.md"`.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# No secrets are baked in: runtime config comes from environment variables.
RUN pip install --upgrade pip \
    && pip install .

# Run as non-root.
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4); sys.exit(0)" || exit 1

CMD ["uvicorn", "ledgerline.api:app", "--host", "0.0.0.0", "--port", "8000"]
