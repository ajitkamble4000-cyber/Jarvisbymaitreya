# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Optional extra trust anchors (see extra-ca/README.md). Appended to the base image's system bundle;
# pip is pointed at that bundle so it trusts the same roots as the rest of the system.
COPY extra-ca/ /usr/local/share/extra-ca/
RUN cat /usr/local/share/extra-ca/*.crt >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN useradd --create-home --uid 10001 jarvis && mkdir -p /app/data && chown -R jarvis:jarvis /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir .

USER jarvis
EXPOSE 8000

# Migrations run before the server starts. Production should run them as a separate job.
# Forwarded headers are trusted only from the reverse proxy listed in FORWARDED_ALLOW_IPS
# (loopback by default). Trusting '*' would let any client set its own X-Forwarded-For and
# defeat the IP-keyed rate limits and the audit IP.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
