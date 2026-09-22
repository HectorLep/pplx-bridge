# Pplx Bridge: FastAPI + Playwright Chromium (Perplexity web)
# La imagen base ya trae los navegadores y la version de Playwright 1.44.0.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PPLX_HOST=0.0.0.0 \
    PPLX_PORT=8000 \
    PPLX_HEADLESS=1 \
    PPLX_USER_DATA_DIR=/data/profile \
    PPLX_PROJECT_ROOT=/app \
    PPLX_NO_SANDBOX=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "playwright==1.44.0"

COPY . .

RUN mkdir -p /data/profile

EXPOSE 8000

CMD ["python", "-m", "core_bridge.cli", "start"]
