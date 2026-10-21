FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOR_CATALOG_PATH=/app/catalog.json

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --home-dir /app bor \
    && mkdir -p /data && chown bor:bor /data

COPY bor/ ./bor/
COPY catalog.json ./catalog.json
USER bor
ENTRYPOINT ["python", "-m", "bor.app"]
