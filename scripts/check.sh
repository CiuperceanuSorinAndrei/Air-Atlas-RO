#!/usr/bin/env bash
set -euo pipefail
uv sync --locked
uv run --locked ruff check scripts tests
uv run --locked ruff format --check scripts tests
uv run --locked pytest
uv run --locked python scripts/import_eea.py --check src/data/observations.json
npm ci --ignore-scripts
npm run lint
npm test
npm audit --audit-level=moderate
npm run build
