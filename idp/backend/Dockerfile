# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml ./
COPY src ./src
# Optional corporate CA for TLS-intercepting proxies (see README "Building behind a proxy").
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FORWARDED_ALLOW_IPS="127.0.0.1"
RUN groupadd --system idp && useradd --system --gid idp --home-dir /app --shell /usr/sbin/nologin idp
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /app/var/storage && chown -R idp:idp /app/var
USER idp
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2).status == 200 else 1)"
# --proxy-headers + FORWARDED_ALLOW_IPS make the real client IP (rate limits, audit) visible
# behind a trusted reverse proxy. Set FORWARDED_ALLOW_IPS to your proxy's address.
CMD ["sh", "-c", "exec uvicorn idp.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\" --no-access-log"]
