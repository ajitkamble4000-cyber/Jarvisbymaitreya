# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 jarvis && mkdir -p /app/data && chown -R jarvis:jarvis /app

COPY --chown=jarvis:jarvis pyproject.toml ./
COPY --chown=jarvis:jarvis app ./app
COPY --chown=jarvis:jarvis alembic ./alembic
COPY --chown=jarvis:jarvis alembic.ini ./
# A managed or corporate proxy CA may be supplied as a BuildKit secret. It is
# trusted only by this networked step and never copied into an image layer.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then \
        PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir .; \
    else \
        pip install --no-cache-dir .; \
    fi

USER jarvis
EXPOSE 8000

# Migrations run before the server starts. Production should run them as a separate job.
# Forwarded headers are trusted only from the reverse proxy listed in FORWARDED_ALLOW_IPS
# (loopback by default). Trusting '*' would let any client set its own X-Forwarded-For and
# defeat the IP-keyed rate limits and the audit IP.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
