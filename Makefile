.PHONY: setup dev migrate test lint import logs

setup:
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	cp -n .env.example .env || true
	docker compose up -d db redis
	@echo "Waiting for database to be ready..."
	sleep 3
	.venv/bin/alembic upgrade head

dev:
	docker compose up -d db redis
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

migrate:
	.venv/bin/alembic upgrade head

test:
	.venv/bin/pytest tests/ -v --tb=short

lint:
	.venv/bin/ruff check app/ tests/
	.venv/bin/ruff format --check app/ tests/
	.venv/bin/mypy app/

import:
	.venv/bin/python -m scripts.import_pois $(REGION)

logs:
	docker compose logs -f --tail=100
