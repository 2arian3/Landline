#!/usr/bin/env bash
# One-time setup of the Landline VM (Ubuntu 22.04 with Docker). Safe to re-run:
# an existing .env is kept. After this, deploy/remote.sh does the rest.
#   SCRAPER_CONTACT_EMAIL=you@example.com bash ~/landline/deploy/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."
UV_VERSION=0.11.16

if [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_NO_MODIFY_PATH=1 sh
fi

if [ ! -f .env ]; then
  umask 077
  pw=$(openssl rand -hex 24)
  cat > .env <<EOF
# Secrets for the Landline VM only. Never commit this file.
# Non-secret settings live in deploy/production.env.
POSTGRES_PASSWORD=$pw
DATABASE_URL=postgresql://landline:$pw@127.0.0.1:5432/landline
SCRAPER_CONTACT_EMAIL=${SCRAPER_CONTACT_EMAIL:-}
EOF
  echo "wrote $PWD/.env"
fi
grep -q '^SCRAPER_CONTACT_EMAIL=.\+' .env || echo "note: set SCRAPER_CONTACT_EMAIL in $PWD/.env before the first refresh"
