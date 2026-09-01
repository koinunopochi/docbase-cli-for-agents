.PHONY: test lint

test:
	uv run python -m unittest discover -s tests -p 'test_*.py'

lint:
	uv run --with ruff ruff check docbase.py tests
