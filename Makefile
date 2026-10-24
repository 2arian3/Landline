.PHONY: setup db-up db-down db-migrate data data-report dev web test lint eval contracts-snapshot

# Windows Python splits PYTHONPATH on ";", everything else on ":".
PATHSEP := $(if $(filter Windows_NT,$(OS)),;,:)
export PYTHONPATH := server$(PATHSEP).
LOCAL_DB := postgresql://landline:landline@localhost:54329/landline

# Hooks, Python deps and web deps. Run once after cloning.
setup:
	git config core.hooksPath .githooks
	chmod +x .githooks/*
	uv sync
	cd web && npm install

# Local Postgres 17 + PostGIS + pgvector on :54329 (production runs the same image on the VM).
db-up:
	docker compose up -d --build db

db-down:
	docker compose down

# Applies db/migrations/*.sql to $DATABASE_URL (or the local DB if unset).
db-migrate:
	DATABASE_URL=$${DATABASE_URL:-$(LOCAL_DB)} uv run python scripts/migrate.py

# Scrape liv.rent politely, check the result, load it, refresh the UI fixture.
data:
	DATABASE_URL=$${DATABASE_URL:-$(LOCAL_DB)} uv run python -m pipeline run --source livrent \
	  --min-listings 500 --load --export-fixture web/lib/fixtures/listings.json --fixture-limit 400

data-report:
	uv run python -m pipeline report data/stages/normalize/livrent-latest.jsonl

# API on :8000. Uses $DATABASE_URL, else the latest scrape, else the fixture.
dev:
	cd server && uv run uvicorn landline.app:app --reload --port 8000

# Web on :3000, proxying /api to :8000. Add ?dev=1 for the demo controls.
web:
	cd web && npm run dev

# The DB tests use a throwaway schema in $TEST_DATABASE_URL (the local DB by default) and skip without one.
test:
	TEST_DATABASE_URL=$${TEST_DATABASE_URL:-$(LOCAL_DB)} uv run pytest -q
	cd web && npm run typecheck && npm test

lint:
	uv run ruff check server pipeline scripts

eval:
	uv run python -m evals.run_concierge
	uv run python -m evals.run_calls

# Rewrites server/tests/snapshots/contracts.json after a contract change; commit it with the change.
contracts-snapshot:
	uv run python -m landline.contracts.snapshot
