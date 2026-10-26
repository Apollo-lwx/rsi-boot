# Glama stdio image. RSI_PROJECT_ROOT is pinned so list_tools does not call
# roots/list: Glama's scanner does not answer that request, and a hang there
# fails the introspection that a release depends on.
FROM python:3.12-slim-bookworm

WORKDIR /opt/rsi-boot

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RSI_PROJECT_ROOT=/data

USER app
WORKDIR /data

ENTRYPOINT ["rsi"]
CMD ["serve"]
