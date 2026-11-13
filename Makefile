PY ?= python3

.PHONY: install lint test check

install:
	$(PY) -m pip install -e '.[dev]'

lint:
	ruff check .
	ruff format --check .
	mypy
	$(PY) scripts/check.py

test:
	$(PY) -m pytest -q

check: lint test
