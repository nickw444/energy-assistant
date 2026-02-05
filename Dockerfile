FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_CACHE_DIR=/app/.cache/uv \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

RUN useradd -r -u 10001 -g nogroup app \
    && mkdir -p /app/.cache/uv \
    && chown -R app:nogroup /app
USER app

EXPOSE 6070

CMD ["energy-assistant", "--config", "/config/config.yaml"]
