ARG PYTHON_VERSION=3.13.7
ARG NODE_VERSION=22.19.0
ARG CODEX_CLI_VERSION=0.151.0

FROM node:${NODE_VERSION}-bookworm-slim AS codex
ARG CODEX_CLI_VERSION
RUN npm install --global --include=optional "@openai/codex@${CODEX_CLI_VERSION}" \
    && codex --version \
    && npm cache clean --force

FROM python:${PYTHON_VERSION}-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/home/reviewbot/.local/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 reviewbot && useradd --uid 10001 --gid reviewbot --create-home reviewbot
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && codex --version
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini review-schema.json ./
COPY prompts ./prompts
RUN pip install --no-cache-dir . && mkdir -p /var/lib/review-bot/work /var/lib/codex && chown -R reviewbot:reviewbot /app /var/lib/review-bot /var/lib/codex
USER reviewbot
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
