.PHONY: install dev test lint typecheck fmt serve seed clean e2e lock audit

install:
	pip install -e ".[dev,test]"

dev:
	pip install -e ".[dev,test]" && pre-commit install 2>/dev/null || true

test:
	python -m pytest tests/ -q

test-verbose:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

fmt:
	ruff format src tests scripts
	ruff check --fix src tests scripts

typecheck:
	mypy src/ledgerline

serve:
	python -m ledgerline.cli serve --db sqlite:///./ledgerline.db

seed:
	python -m ledgerline.cli seed --db sqlite:///./ledgerline.db

e2e:
	bash scripts/e2e.sh

lock:
	pip-compile --output-file=requirements.lock pyproject.toml
	pip-compile --output-file=requirements-dev.lock --extra=dev --extra=test pyproject.toml

audit:
	pip-audit -r requirements.lock --desc

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
