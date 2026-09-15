PYTHON ?= python

.PHONY: install lint format typecheck test test-postgres migrate coverage run config-check snapshot-check docker-build docker-up docker-down ci

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy src

test-postgres:
	$(PYTHON) scripts/test_postgres.py

migrate:
	$(PYTHON) -m alembic upgrade head

test:
	$(PYTHON) -m pytest

coverage:
	$(PYTHON) -m pytest --cov=godzilla --cov-report=term-missing --cov-fail-under=85

run:
	$(PYTHON) -m godzilla run --config-dir config

config-check:
	$(PYTHON) -m godzilla config-check --config-dir config

snapshot-check:
	$(PYTHON) -m godzilla snapshot-check --config-dir config

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

ci:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src
	$(PYTHON) scripts/test_postgres.py
