# Developer entry points. Everything also works with plain docker compose / npm / pytest.
.PHONY: env up down logs bootstrap backend-install backend-check backend-test frontend-install frontend-check check

COMPOSE := docker compose
PY := backend/.venv/bin

env:            ## create .env with random secrets (never overwrites)
	python3 scripts/gen-env.py

up: env         ## build and start the full stack
	$(COMPOSE) up --build -d
	@echo "UI: http://localhost:8080  API docs: http://localhost:8000/api/v1/docs"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api worker

backend-install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

backend-check:  ## lint + format check + strict type check
	cd backend && .venv/bin/ruff check src tests migrations && .venv/bin/ruff format --check src tests migrations && .venv/bin/mypy

backend-test:   ## needs TEST_DATABASE_URL and TEST_REDIS_URL for integration tests
	cd backend && .venv/bin/pytest

frontend-install:
	cd frontend && npm ci

frontend-check:
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build

check: backend-check backend-test frontend-check
