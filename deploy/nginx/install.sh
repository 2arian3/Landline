#!/usr/bin/env bash
# Installs the Landline site on the public gateway (134.87.8.230). Run it there,
# as a user with sudo; CI never touches the gateway. It adds only
# /etc/nginx/conf.d/landline.conf, a Let's Encrypt certificate for the name and
# its webroot. No other nginx file is edited, and a config that fails `nginx -t`
# is rolled back before anything reloads.
#   scp deploy/nginx/landline.conf deploy/nginx/install.sh gateway:landline-nginx/
#   ssh gateway 'bash landline-nginx/install.sh'
set -euo pipefail
NAME=landline.134.87.8.230.sslip.io
CONF=/etc/nginx/conf.d/landline.conf
WEBROOT=/var/www/landline-acme
HERE=$(cd "$(dirname "$0")" && pwd)

backup=/root/nginx-before-landline-$(date +%Y%m%d%H%M%S).tgz
sudo tar -czf "$backup" -C /etc nginx
echo "backed up /etc/nginx to $backup"

# Puts $1 in place as landline.conf and reloads, or restores what was there.
apply() {
  local prev
  prev=$(mktemp)
  if sudo test -f "$CONF"; then sudo cp "$CONF" "$prev"; else rm -f "$prev"; fi
  sudo install -m 644 "$1" "$CONF"
  if ! sudo nginx -t -q; then
    if [ -f "$prev" ]; then sudo install -m 644 "$prev" "$CONF"; else sudo rm -f "$CONF"; fi
    echo "nginx -t failed; landline.conf rolled back, nothing reloaded" >&2
    exit 1
  fi
  rm -f "$prev"
  sudo systemctl reload nginx
}

command -v certbot >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq certbot; }
sudo install -d -m 755 "$WEBROOT"

if ! sudo test -f "/etc/letsencrypt/live/$NAME/fullchain.pem"; then
  # First run: answer only the ACME challenge until the certificate exists.
  tmp=$(mktemp)
  cat > "$tmp" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $NAME;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 404; }
}
EOF
  apply "$tmp"
  rm -f "$tmp"
  sudo certbot certonly --webroot -w "$WEBROOT" -d "$NAME" --non-interactive --agree-tos \
    --register-unsafely-without-email --deploy-hook "systemctl reload nginx"
fi

apply "$HERE/landline.conf"
echo "https://$NAME is live; certbot.timer renews it and reloads nginx"
