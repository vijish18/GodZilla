PYTHON ?= python

.PHONY: install lint format typecheck test coverage run config-check docker-build docker-up docker-down ci

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m pytest -m unit

coverage:
	$(PYTHON) -m pytest -m unit --cov=godzilla --cov-report=term-missing --cov-fail-under=85

run:
	$(PYTHON) -m godzilla run --config-dir config

config-check:
	$(PYTHON) -m godzilla config-check --config-dir config

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
	$(PYTHON) -m pytest -m unit --cov=godzilla --cov-report=term-missing --cov-fail-under=85
