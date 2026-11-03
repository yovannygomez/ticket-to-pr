.PHONY: install lint test up down worker receiver poller

install:
	python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

test:
	.venv/bin/pytest -q

up:
	docker compose up -d && docker build -q -t ticket-to-pr-sandbox:dev sandbox

down:
	docker compose stop

worker:
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/python -m pipeline.worker

receiver:
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/uvicorn pipeline.receiver:app --port 8000

poller:
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/python -m pipeline.poller
