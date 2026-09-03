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

## Windows Docker Desktop의 GitHub App Private Key

PEM 파일을 저장소나 `.env`에 넣지 않습니다. Windows Docker Desktop에서는 실제
PEM의 호스트 경로만 `.env`에 지정하고 Compose secret이 컨테이너 안의 읽기 전용
경로로 연결합니다.

```dotenv
GITHUB_PRIVATE_KEY_HOST_PATH=C:/Users/user/.inryeok-bot/github-app.pem
GITHUB_PRIVATE_KEY_PATH=/run/secrets/github-app.pem
```

첫 경로는 문서 예시일 뿐이며, 실제 사용자 경로는 각자의 `.env`에서 설정합니다.
`github_app_private_key` secret은 web과 worker에만 `/run/secrets/github-app.pem`으로
마운트됩니다.

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
