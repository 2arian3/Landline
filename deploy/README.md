# deploy/

The API and its database run on one Arbutus VM. The public gateway's nginx proxies to it, and the web app runs on Vercel. Every push to `main` that passes CI redeploys the VM. Task U-07.

**Public URL:** https://landline.134.87.8.230.sslip.io (`/health`, `/docs`, `/api/*`)

```
browser / Vercel / Twilio
        │ https, wss
        ▼
gateway 134.87.8.230 (public)          nginx: conf.d/landline.conf, Let's Encrypt
        │ http, private network
        ▼
Landline VM 192.168.104.198            no public address
  landline-api.service      uvicorn :8000, one process
  landline-refresh.timer    scrape → check → load, every 6 h
  docker: landline-db-1     Postgres 17 + PostGIS + pgvector on 127.0.0.1:5432
```

## Files

| File | What it is |
|---|---|
| `compose.yml` | Production Postgres (same image as `db/local`), bound to localhost, volume `landline_pgdata` |
| `production.env` | Non-secret settings: `WEB_URL`, `CORS_ORIGINS`, `CORS_ORIGIN_REGEX`, `PUBLIC_URL`, `TRANSPORT`. Committed; ships on deploy |
| `systemd/` | `landline-api.service`, `landline-refresh.service` + `.timer` |
| `remote.sh` | Runs on the VM on every deploy: `uv sync`, database up, migrations, install units, restart the API, health check, optional re-scrape |
| `bootstrap.sh` | One-time VM setup: installs uv and writes `~/landline/.env` with a generated database password |
| `rsync-exclude.txt` | What a deploy doesn't copy, and so keeps on the VM: `.env`, `.venv`, scraped data |
| `nginx/landline.conf` | The gateway site. Installed by hand, never by CI |
| `nginx/install.sh` | Installs that site and its certificate on the gateway. Backs up `/etc/nginx` first; rolls back if `nginx -t` fails |

Secrets live only on the VM, in `~/landline/.env` (mode 600): `POSTGRES_PASSWORD`, `DATABASE_URL` and `SCRAPER_CONTACT_EMAIL`. Later the Boson and Twilio keys go there too. systemd loads `production.env`, and its values take precedence over `.env`.

## CI/CD

`.github/workflows/deploy.yml` runs when **CI** succeeds on a push to `main`. It can also be started by hand from the Actions tab, with a re-scrape choice.

1. It connects to the VM through the gateway, using the gateway as an SSH jump host. The VM can't be reached any other way. The connection uses `.github/actions/landline-ssh` with secrets `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`.
2. `rsync --delete` copies the commit to `~/landline`.
3. `deploy/remote.sh` does the rest. If the commit changed `pipeline/` or `db/migrations/`, or the database has no listings, it also starts a re-scrape in the background.
4. It checks `https://landline.134.87.8.230.sslip.io/health` and the CORS preflight through the gateway.

`.github/workflows/refresh-listings.yml` (manual only) runs the VM's refresh job now. It then attaches the log and the scrape as an artifact. The 6-hour schedule is the VM's own timer.

**The deploy key** is a dedicated ed25519 key, used only by CI.
- On the gateway it is restricted to `restrict,port-forwarding,permitopen="192.168.104.198:22",command="/bin/false"`. It can open the tunnel to the VM's SSH port and nothing else: no shell and no other destination.
- On the VM it is accepted only from the gateway's private address (`from="192.168.104.213"`).
- To rotate it, generate a new key, replace both `authorized_keys` lines (each tagged `landline-deploy`), and run `gh secret set DEPLOY_SSH_KEY < key`.

## The gateway is shared

Other services (CAIOS) are exposed on 134.87.8.230. Landline adds exactly three things there:
- `/etc/nginx/conf.d/landline.conf`
- the webroot `/var/www/landline-acme`
- the certificate under `/etc/letsencrypt/live/landline.134.87.8.230.sslip.io/`, renewed by `certbot.timer`, which reloads nginx

`caios.conf` and `nginx.conf` are not edited. Every name in `landline.conf` starts with `landline_` (`$landline_connection`, `upstream landline_api`), so nothing collides with them. Before changing `landline.conf`, keep it that way. Then reinstall with:

```bash
scp deploy/nginx/landline.conf deploy/nginx/install.sh ubuntu@134.87.8.230:landline-nginx/
```

```bash
ssh ubuntu@134.87.8.230 'bash landline-nginx/install.sh'
```

## Operating it

Connect through the gateway with your own key (`-J` is the jump host):

```bash
ssh -J ubuntu@134.87.8.230 ubuntu@192.168.104.198
```

On the VM:

| Task | Command |
|---|---|
| API logs | `journalctl -u landline-api -f` |
| Restart the API | `sudo systemctl restart landline-api` |
| Refresh now / its log | `sudo systemctl start landline-refresh` / `journalctl -u landline-refresh -n 80` |
| Next refresh | `systemctl list-timers landline-refresh.timer` |
| **Before judging** | `sudo systemctl stop landline-refresh.timer`: a refresh restarts the API, which resets in-memory demo sessions. `start` it again afterwards |
| psql | `cd ~/landline && docker compose --env-file .env -f deploy/compose.yml exec db psql -U landline` |
| Manual deploy of what's on disk | `bash ~/landline/deploy/remote.sh no` |

Database from your laptop, through a tunnel (the VM's Postgres listens only on localhost):

```bash
ssh -N -J ubuntu@134.87.8.230 -L 5433:127.0.0.1:5432 ubuntu@192.168.104.198
```

Then connect to `postgresql://landline:<password from ~/landline/.env>@localhost:5433/landline`.

## CORS

The web app normally calls the API through its own `/api` proxy (`next.config.ts`, `API_URL`). That is same-origin, so it needs no CORS. When the browser should call the API directly, set `NEXT_PUBLIC_API_URL=https://landline.134.87.8.230.sslip.io` on Vercel. Do this, for example, to keep the event stream off Vercel's proxy. Then add the web app's origin to `CORS_ORIGINS`, or set it as `WEB_URL`, in `production.env`, and push. `localhost:3000` is always allowed. nginx adds no CORS headers of its own. The API answers preflights and leaves out `Access-Control-Allow-Origin` for any origin not listed.

## SevenTask (paused)

The VM used to run SevenTask, and it is stopped for the hackathon:
- the containers `seventask-api` and `seventask-sql` are stopped
- `seventask-admin-api.service` and the VM's nginx are disabled

Nothing was deleted. To bring it back, first stop Landline (`sudo systemctl disable --now landline-api landline-refresh.timer`), then run:

```bash
bash ~/seventask-paused/restore.sh
```

The state before pausing is in `~/seventask-paused/state-before.txt`.
