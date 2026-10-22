.PHONY: setup db-migrate data data-report dev web test eval contracts-snapshot

# Install the git hooks now; later tasks extend this with Python and web deps.
setup:
	git config core.hooksPath .githooks
	chmod +x .githooks/*
	@echo "hooks installed (.githooks)"
	@[ -f pyproject.toml ] && uv sync || echo "pyproject.toml not created yet (task X-01)"
	@[ -f web/package.json ] && (cd web && npm install) || echo "web/ not scaffolded yet (task U-01)"

db-migrate:
	@echo "added in X-01: applies db/migrations/*.sql to \$$DATABASE_URL"

data:
	@echo "added in D-02..D-08: fetch -> parse -> geocode -> enrich -> extract -> light -> dedupe -> load"

data-report:
	@echo "added in D-08"

dev:
	@echo "added in X-01: API on :8000 with TRANSPORT=stub"

web:
	@echo "added in U-01: web on :3000"

test:
	@echo "added in X-01: python + web tests, no network"

eval:
	@echo "added in E-01: writes evals/reports/latest.md"

contracts-snapshot:
	@echo "added in X-01: regenerates the contract JSON Schema snapshot"
