ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/home/reviewbot/.local/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 reviewbot && useradd --uid 10001 --gid reviewbot --create-home reviewbot
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini review-schema.json ./
COPY prompts ./prompts
COPY scripts ./scripts
RUN pip install --no-cache-dir . && mkdir -p /var/lib/review-bot/work /var/lib/codex && chown -R reviewbot:reviewbot /app /var/lib/review-bot /var/lib/codex
USER reviewbot
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
