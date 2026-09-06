ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
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
RUN pip install --no-cache-dir . \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
        /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
    && mkdir -p /var/lib/review-bot/work /var/lib/codex \
    && chown -R reviewbot:reviewbot /app /var/lib/review-bot /var/lib/codex
USER reviewbot
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
