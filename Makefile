.PHONY: test lint

test:
	uv run tests/test_docbase.py

lint:
	uv run --with ruff ruff check docbase.py tests
