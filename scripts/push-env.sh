#!/usr/bin/env bash
# Copies the keys the VM needs from your local .env into ~/landline/.env on the
# Landline VM, and nothing else. The VM keeps its own POSTGRES_PASSWORD and
# DATABASE_URL (deploy/bootstrap.sh) and its own BOSON_API_KEY, and
# deploy/production.env owns the serving settings, so those are never sent.
# An empty local value never blanks a VM value.
#   scripts/push-env.sh --dry-run          show what would change, by key name
#   scripts/push-env.sh [--yes] [--restart]
#     --yes      apply without asking
#     --restart  restart landline-api afterwards and wait for /health
# LANDLINE_VM (default ubuntu@192.168.104.198) and LOCAL_ENV (default .env in the
# repo root) override the target and the source. Values travel over SSH stdin and
# are never printed. The VM's .env is backed up first; if docker compose or
# python-dotenv can't read the result, or the database lines changed, the backup
# goes back.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VM=${LANDLINE_VM:-ubuntu@192.168.104.198}
LOCAL_ENV=${LOCAL_ENV:-$ROOT/.env}

# Keys the VM takes from your .env.
PUSH="HIGGS_VOICE_RENTER HIGGS_VOICE_LISTING HIGGS_MAX_SESSIONS
  LLM_PROVIDER LLM_MODEL OPENAI_API_KEY OPENROUTER_API_KEY GEMINI_API_KEY
  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN DEMO_AGENT_PHONES
  GEOCODER GOOGLE_MAPS_API_KEY SCRAPER_CONTACT_EMAIL"
# Keys that belong to the VM's database or to deploy/production.env, the VM's own
# BOSON_API_KEY, plus FORCE_BUSINESS_HOURS, a local testing switch. Never sent,
# never changed.
KEEP="POSTGRES_PASSWORD DATABASE_URL BOSON_API_KEY PUBLIC_URL WEB_URL CORS_ORIGINS
  CORS_ORIGIN_REGEX TRANSPORT TWILIO_PHONE_NUMBER FORCE_BUSINESS_HOURS"

dry_run=0 yes=0 restart=0
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=1 ;;
    --yes) yes=1 ;;
    --restart) restart=1 ;;
    *) echo "usage: scripts/push-env.sh [--dry-run] [--yes] [--restart]" >&2; exit 2 ;;
  esac
done

keep=" $(echo $KEEP) "
for k in $PUSH; do
  case $keep in *" $k "*) echo "$k is in both PUSH and KEEP" >&2; exit 1 ;; esac
done
[ -f "$LOCAL_ENV" ] || { echo "$LOCAL_ENV is missing: cp .env.example .env and fill it in" >&2; exit 1; }

vm() { ssh -o BatchMode=yes -o ConnectTimeout=20 "$VM" "$@"; }
sha256() { if command -v sha256sum > /dev/null; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }

# The last KEY= line of the local file, without a trailing unquoted "# comment".
# Prints nothing when the value is empty.
local_line() {
  local line value
  line=$(grep -E "^$1=" "$LOCAL_ENV" | tail -n 1) || return 0
  value=${line#*=}
  case $value in
    \"*|\'*) ;;
    *) value=${value%%[[:space:]]#*} ;;
  esac
  value=${value%"${value##*[![:space:]]}"}
  case $value in ''|\"\"|\'\') return 0 ;; esac
  printf '%s=%s\n' "$1" "$value"
}

# Runs on the VM: one "KEY sha256" line per key, "KEY -" when the key is absent.
plan_script() {
  cat <<'EOF'
set -euo pipefail
cd ~/landline
[ -f .env ] || { echo "~/landline/.env is missing on the VM: run deploy/bootstrap.sh once" >&2; exit 1; }
for k in "$@"; do
  line=$(grep -E "^$k=" .env | tail -n 1) || { echo "$k -"; continue; }
  echo "$k $(printf '%s\n' "$line" | sha256sum | cut -d' ' -f1)"
done
EOF
}

# Runs on the VM: merges ~/landline/.env.incoming into ~/landline/.env.
apply_script() {
  cat <<'EOF'
set -euo pipefail
cd ~/landline
trap 'rm -f .env.incoming .env.merged' EXIT
umask 077

# deploy/remote.sh reads .env with docker compose, the app with python-dotenv.
check() {
  docker compose --env-file "$1" -f deploy/compose.yml config -q > /dev/null 2>&1 ||
    { echo "docker compose can't read $1" >&2; return 1; }
  .venv/bin/python - "$1" <<'PY' || { echo "python-dotenv can't parse $1" >&2; return 1; }
import logging, sys
from dotenv import dotenv_values
bad = []
class Collect(logging.Handler):
    def emit(self, record):
        bad.append(record)
logging.getLogger("dotenv").addHandler(Collect())
dotenv_values(sys.argv[1])
sys.exit(1 if bad else 0)
PY
}
db_lines() { grep -E '^(POSTGRES_PASSWORD|DATABASE_URL)=' "$1" | sha256sum | cut -d' ' -f1; }

[ -f .env ] || { echo "~/landline/.env is missing on the VM" >&2; exit 1; }
check .env || { echo "the VM's .env fails the checks already; left it as it was" >&2; exit 1; }
backup=.env.bak-$(date +%Y%m%d-%H%M%S)
cp -p .env "$backup"
awk -F= 'NR == FNR { line[$1] = $0; order[++n] = $1; next }
  ($1 in line) { print line[$1]; done[$1] = 1; next }
  { print }
  END { for (i = 1; i <= n; i++) if (!(order[i] in done)) print line[order[i]] }' .env.incoming .env > .env.merged
install -m 600 .env.merged .env
if [ "$(db_lines .env)" != "$(db_lines "$backup")" ] || ! check .env; then
  cp -p "$backup" .env
  echo "the new .env failed the checks; restored $backup" >&2
  exit 1
fi
echo "backup ~/landline/$backup"
echo "mode   $(stat -c %a .env)"
echo "keys   $(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d = | tr '\n' ' ')"
EOF
}

echo "== plan ($VM)"
remote=$(plan_script | vm bash -s -- $PUSH)
changes=""
for k in $PUSH; do
  line=$(local_line "$k")
  if [ -z "$line" ]; then
    action="skipped (empty locally)"
  else
    theirs=$(printf '%s\n' "$remote" | awk -v k="$k" '$1 == k { print $2 }')
    if [ -z "$theirs" ] || [ "$theirs" = - ]; then
      action=add
    elif [ "$theirs" = "$(printf '%s\n' "$line" | sha256)" ]; then
      action=unchanged
    else
      action=update
    fi
    case $action in add|update) changes="$changes $k" ;; esac
  fi
  printf '  %-22s %s\n' "$k" "$action"
done
echo "  never sent: $(echo $KEEP)"

if [ -z "$changes" ]; then echo "nothing to change"; exit 0; fi
if [ "$dry_run" = 1 ]; then echo "dry run: the VM was not changed"; exit 0; fi
if [ "$yes" = 0 ]; then
  [ -t 0 ] || { echo "not a terminal: pass --yes to apply" >&2; exit 1; }
  read -r -p "Apply to $VM:~/landline/.env? [y/N] " answer
  case $answer in y|Y|yes) ;; *) echo "nothing changed"; exit 0 ;; esac
fi

echo "== apply"
for k in $changes; do local_line "$k"; done | vm 'umask 077 && cat > ~/landline/.env.incoming'
apply_script | vm bash -s

if [ "$restart" = 1 ]; then
  echo "== restart"
  vm 'sudo systemctl restart landline-api
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 http://127.0.0.1:8000/health && echo && exit 0
  sleep 1
done
sudo journalctl -u landline-api -n 40 --no-pager
exit 1'
else
  echo "the API reads .env at startup: rerun with --restart, or ssh $VM 'sudo systemctl restart landline-api'"
fi
