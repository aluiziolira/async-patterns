.PHONY: help install test test-quick lint format clean benchmark benchmark-full benchmark-resilience

help:
	@echo "Available targets:"
	@echo "  install             - Install project dependencies"
	@echo "  test                - Run all tests with coverage"
	@echo "  test-quick          - Run tests without coverage"
	@echo "  lint                - Run ruff + mypy strict"
	@echo "  format              - Auto-format code with ruff"
	@echo "  clean               - Remove cache files"
	@echo "  benchmark           - Performance benchmark (5000 requests)"
	@echo "  benchmark-full      - Extended benchmark (60s + persistence)"
	@echo "  benchmark-resilience - Circuit breaker + retry testing"


install:
	poetry install --with dev

test:
	poetry run pytest tests/ -n auto --cov=async_patterns --cov-report=term-missing --cov-report=html

test-quick:
	poetry run pytest tests/ -n auto

lint:
	poetry run ruff check src/ tests/
	poetry run ruff format --check src/ tests/
	poetry run mypy src/ --strict

format:
	poetry run ruff format src/ tests/
	poetry run ruff check --fix src/ tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage

benchmark:
	poetry run python -m benchmarks.runner --request-count 5000 --pooling

benchmark-full:
	poetry run python -m benchmarks.runner --duration-sec 60 \
		--concurrency 200 --workers 32 --jsonl-out benchmark_results.jsonl

benchmark-resilience:
	poetry run python -m benchmarks.runner \
		--mock-error-rate 1.0 --run-resilience --request-count 50
