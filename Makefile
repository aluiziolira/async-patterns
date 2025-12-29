.PHONY: help install dev test lint format type-check clean benchmark run-tests

help:
	@echo "Available targets:"
	@echo "  install       - Install project dependencies"
	@echo "  dev           - Install with development dependencies"
	@echo "  test          - Run all tests with coverage"
	@echo "  lint          - Run ruff linter and formatter check"
	@echo "  format        - Auto-format code with ruff"
	@echo "  type-check    - Run mypy type checker (strict mode)"
	@echo "  clean         - Remove cache files and build artifacts"
	@echo "  benchmark     - Run performance benchmarks"

install:
	poetry install

dev:
	poetry install --with dev

test:
	poetry run pytest tests/ --cov=async_patterns --cov-report=term-missing --cov-report=html

run-tests:
	poetry run pytest tests/

lint:
	poetry run ruff check src/ tests/
	poetry run ruff format --check src/ tests/

format:
	poetry run ruff format src/ tests/
	poetry run ruff check --fix src/ tests/

type-check:
	poetry run mypy src/ --strict

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage

benchmark:
	poetry run python -m benchmarks.runner
