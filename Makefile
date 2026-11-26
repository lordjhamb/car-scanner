.PHONY: install dev test lint run demo clean
install:        ## create .venv and install everything
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/pip install -q -e .
test:           ## lint + all tests
	scripts/test.sh
lint:
	.venv/bin/python -m ruff check carscan tests
run:            ## start the app on http://127.0.0.1:8077
	.venv/bin/python -m carscan
