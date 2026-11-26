#!/usr/bin/env bash
# Full local check: lint, Python tests with coverage, frontend syntax + unit tests.
# Usage: scripts/test.sh            (creates .venv on first run)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "==> Creating virtualenv"
  python3 -m venv .venv
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements-dev.txt
fi

echo "==> Lint (ruff)"
"$PY" -m ruff check carscan tests

echo "==> Python tests (pytest + coverage)"
"$PY" -m pytest --cov --cov-report=term --cov-fail-under=85 -q

echo "==> JavaScript syntax"
scripts/check_js.sh

if command -v node >/dev/null 2>&1; then
  echo "==> JavaScript unit tests (node --test)"
  node --test tests/js/*.test.mjs
fi

echo "==> All checks passed"
