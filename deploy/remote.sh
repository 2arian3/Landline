#!/usr/bin/env bash
# Deploys a copy of the repo on the Landline VM. CI copies each commit to
# ~/landline.next and runs this script from there (.github/workflows/deploy.yml):
#   1. apply the commit's migrations, using the live venv (migrate.py only needs psycopg)
#   2. only if they succeed, copy the code into ~/landline, sync dependencies, restart
# so a migration that fails leaves the running tree exactly as it was.
# Run from ~/landline itself it skips the copy, which makes it the manual deploy:
#   bash ~/landline/deploy/remote.sh [auto|yes|no] [sha]
#     auto  re-scrape only if the database has no active listings (default)
#     yes   re-scrape after deploying; CI passes this when pipeline/ or db/migrations changed
#     no    never
set -euo pipefail
REFRESH=${1:-auto}
SHA=${2:-}
SRC=$(cd "$(dirname "$0")/.." && pwd)
LIVE=$HOME/landline
export PATH="$HOME/.local/bin:$PATH"

[ -f "$LIVE/.env" ] || { echo "$LIVE/.env is missing: run deploy/bootstrap.sh once" >&2; exit 1; }
compose() { docker compose --env-file "$LIVE/.env" -f "$SRC/deploy/compose.yml" "$@"; }

# A refresh restarts the API onto whatever is in ~/landline, so keep it off while
# that changes. Remember its state: someone may have stopped it on purpose (before
# a demo), and a deploy must not turn it back on. A failed deploy leaves it off.
timer_was_active=0
systemctl is-active --quiet landline-refresh.timer && timer_was_active=1
first_install=0
systemctl is-enabled --quiet landline-refresh.timer 2> /dev/null || first_install=1
sudo systemctl stop landline-refresh.timer 2> /dev/null || true
trap 'echo "deploy failed; the refresh timer stays stopped until a deploy succeeds" >&2' ERR

echo "== database"
compose up -d --build --wait --quiet-pull db
if [ -x "$LIVE/.venv/bin/python" ]; then
  py=$LIVE/.venv/bin/python
else
  (cd "$SRC" && uv sync --frozen --no-dev --quiet)
  py=$SRC/.venv/bin/python
fi
db_url=$(grep -m1 '^DATABASE_URL=' "$LIVE/.env" | cut -d= -f2-)
DATABASE_URL=$db_url "$py" "$SRC/scripts/migrate.py"

if [ "$SRC" != "$LIVE" ]; then
  echo "== code"
  rsync -a --delete --exclude-from="$SRC/deploy/rsync-exclude.txt" "$SRC/" "$LIVE/"
fi
cd "$LIVE"

echo "== dependencies"
uv sync --frozen --no-dev --quiet

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
sudo systemctl enable --quiet landline-api.service
sudo systemctl restart landline-api.service

echo "== health"
health=""
for _ in $(seq 1 30); do
  health=$(curl -fsS --max-time 2 http://127.0.0.1:8000/health 2> /dev/null) && break
  sleep 1
done
if [ -z "$health" ]; then
  sudo journalctl -u landline-api -n 40 --no-pager
  exit 1
fi
echo "$health"

if [ "$first_install" = 1 ]; then
  sudo systemctl enable --now landline-refresh.timer
elif [ "$timer_was_active" = 1 ]; then
  sudo systemctl start landline-refresh.timer
fi

active=$(compose exec -T db psql -U landline -d landline -tAc "select count(*) from listings where status = 'active'")
if [ "$first_install" = 0 ] && [ "$timer_was_active" = 0 ]; then
  echo "== refresh timer left stopped, as it was before this deploy; no re-scrape (sudo systemctl start landline-refresh.timer)"
elif [ "$REFRESH" = yes ] || { [ "$REFRESH" = auto ] && [ "$active" = 0 ]; }; then
  echo "== refresh started in the background ($active active listings now; journalctl -u landline-refresh -f)"
  sudo systemctl start --no-block landline-refresh.service
fi

if [ -n "$SHA" ]; then echo "$SHA" > .deployed-sha; fi
echo "deployed ${SHA:-the local copy}"
