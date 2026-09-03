# 개발 가이드

## 요구 사항

- Python 3.12 이상
- Git
- Docker 및 Docker Compose
- Codex CLI

## 로컬 실행

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.jobs.worker
```

개발 중 외부 Webhook을 시험하려면 임시 HTTPS 터널을 열고 출력된 URL을 `PUBLIC_BASE_URL`로 사용합니다.

```bash
cloudflared tunnel --url http://localhost:8000
```

Quick Tunnel URL은 바뀔 수 있으므로 GitHub App의 Webhook, Callback, Setup URL도 함께 갱신해야 합니다.

## 검증

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy app
python -m pytest
python scripts/check_secrets.py
python -m alembic upgrade head --sql
docker compose config --quiet
docker compose build
```

같은 작업은 Makefile의 `format`, `lint`, `typecheck`, `test`, `verify`,
`compose-config`, `build` target으로도 실행할 수 있습니다.
