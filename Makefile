.PHONY: install db-up migrate web worker format lint typecheck test verify compose-config build
install:
	python -m pip install -e ".[dev]"
db-up:
	docker compose up -d postgres
migrate:
	python -m alembic upgrade head
web:
	python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
worker:
	python -m app.jobs.worker
format:
	python -m ruff format .
	python -m ruff check --fix .
lint:
	python -m ruff check .
typecheck:
	python -m mypy app
test:
	python -m pytest
compose-config:
	docker compose config --quiet
build:
	docker compose build
verify: lint typecheck test
	python scripts/check_secrets.py
	python -m alembic upgrade head --sql
	docker compose config --quiet
