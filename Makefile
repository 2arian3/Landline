.PHONY: setup db-up db-down db-migrate data data-report dev web test lint eval contracts-snapshot

export PYTHONPATH := server:.
LOCAL_DB := postgresql://landline:landline@localhost:54329/landline

# Hooks, Python deps and web deps. Run once after cloning.
setup:
	git config core.hooksPath .githooks
	chmod +x .githooks/*
	uv sync
	cd web && npm install

# Local Postgres 17 + PostGIS + pgvector on :54329 (the shared dev DB is Supabase).
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

test:
	uv run pytest -q
	cd web && npm run typecheck

lint:
	uv run ruff check server pipeline scripts

eval:
	@echo "added in E-01: writes evals/reports/latest.md"

contracts-snapshot:
	@echo "added in X-01: regenerates the contract JSON Schema snapshot"
