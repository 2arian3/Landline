#!/usr/bin/env bash
# Runs on the Landline VM after CI has copied the repo to ~/landline
# (.github/workflows/deploy.yml). Idempotent, so it's also the manual deploy:
#   bash ~/landline/deploy/remote.sh [auto|yes|no] [sha]
#     auto  re-scrape only if the database has no active listings (default)
#     yes   re-scrape after deploying; CI passes this when pipeline/ or db/migrations changed
#     no    never
set -euo pipefail
REFRESH=${1:-auto}
SHA=${2:-}
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

[ -f .env ] || { echo "$PWD/.env is missing: run deploy/bootstrap.sh once" >&2; exit 1; }
compose() { docker compose --env-file .env -f deploy/compose.yml "$@"; }

echo "== dependencies"
uv sync --frozen --no-dev --quiet

echo "== database"
compose up -d --build --wait --quiet-pull db
.venv/bin/python scripts/migrate.py

echo "== services"
reload=0
for unit in deploy/systemd/*; do
  dest=/etc/systemd/system/$(basename "$unit")
  if ! sudo cmp -s "$unit" "$dest"; then
    sudo install -m 644 "$unit" "$dest"
    reload=1
  fi
done
if [ "$reload" = 1 ]; then sudo systemctl daemon-reload; fi
sudo systemctl enable --quiet landline-api.service landline-refresh.timer
sudo systemctl start landline-refresh.timer
sudo systemctl restart landline-api.service

echo "== health"
health=""
for _ in $(seq 1 30); do
  health=$(curl -fsS --max-time 2 http://127.0.0.1:8000/health 2>/dev/null) && break
  sleep 1
done
if [ -z "$health" ]; then
  sudo journalctl -u landline-api -n 40 --no-pager
  exit 1
fi
echo "$health"

active=$(compose exec -T db psql -U landline -d landline -tAc "select count(*) from listings where status = 'active'")
if [ "$REFRESH" = yes ] || { [ "$REFRESH" = auto ] && [ "$active" = 0 ]; }; then
  echo "== refresh started in the background ($active active listings now; journalctl -u landline-refresh -f)"
  sudo systemctl start --no-block landline-refresh.service
fi

if [ -n "$SHA" ]; then echo "$SHA" > .deployed-sha; fi
echo "deployed ${SHA:-the local copy}"
