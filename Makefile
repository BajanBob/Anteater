.PHONY: help install dev test lint fmt type check run detect ui selftest db fp fp-sync build pyz clean

PY ?= python
VENV ?= .venv

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## install runtime deps
	$(PY) -m pip install -e .

dev: ## install everything for development
	$(PY) -m pip install -e ".[dev,alerts]"

test: ## run the test suite (no radio hardware needed)
	$(PY) -m pytest -q -m "not radio and not sdr"

lint: ## ruff
	$(PY) -m ruff check .

fmt: ## ruff format and autofix
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

type: ## mypy
	$(PY) -m mypy anteater termite

check: lint type test ## exactly what CI runs

selftest: ## validate install without hardware
	$(PY) -m anteater selftest --no-radio

db: ## create or upgrade the database schema
	$(PY) -m anteater db upgrade

fp: ## list loaded vendor fingerprints
	$(PY) -m anteater fp list

fp-sync: ## refresh config/oui.csv from the IEEE registry
	$(PY) -m anteater fp sync

run: ## collector (Kismet URL comes from .env / config, override with KISMET=ws://...)
	$(PY) -m anteater collect $(if $(KISMET),--kismet $(KISMET),)

detect: ## detection engines
	$(PY) -m anteater detect --engines all

ui: ## dashboard
	$(PY) -m anteater ui --port 8080

build: ## standalone executable in dist/ (needs the dev extra)
	$(PY) -m PyInstaller --noconfirm --clean build/anteater.spec

pyz: ## single-file zipapp in dist/ (needs deps installed to run)
	$(PY) scripts/build_pyz.py

clean:
	rm -rf build/anteater dist *.egg-info
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
