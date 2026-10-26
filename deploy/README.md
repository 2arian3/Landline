# deploy/

The API and its database run on one Arbutus VM. The public gateway's nginx proxies to it, and the web app runs on Vercel. Every push to the `release` branch that passes CI redeploys the VM. `main` is where work lands. `release` is what runs. Task U-07.

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
| `production.env` | Non-secret settings: `WEB_URL`, `CORS_ORIGINS`, `CORS_ORIGIN_REGEX`, `PUBLIC_URL`, `TRANSPORT`, `TWILIO_PHONE_NUMBER` (the prod number), `HIGGS_MAX_SESSIONS` and `CALL_CONCURRENCY` (Higgs lines). Committed; ships on deploy |
| `systemd/` | `landline-api.service`, `landline-refresh.service` + `.timer` |
| `remote.sh` | Runs on the VM on every deploy: `uv sync`, database up, migrations, install units, restart the API, health check, optional re-scrape |
| `bootstrap.sh` | One-time VM setup: installs uv and writes `~/landline/.env` with a generated database password |
| `rsync-exclude.txt` | What a deploy doesn't copy, and so keeps on the VM: `.env`, `.venv`, scraped data |
| `nginx/landline.conf` | The gateway site. Installed by hand, never by CI |
| `nginx/install.sh` | Installs that site and its certificate on the gateway. Backs up `/etc/nginx` first; rolls back if `nginx -t` fails |

Secrets live only on the VM, in `~/landline/.env` (mode 600): `POSTGRES_PASSWORD`, `DATABASE_URL` and `SCRAPER_CONTACT_EMAIL`. Later the Boson and Twilio keys go there too. systemd loads `production.env`, and its values take precedence over `.env`.

## CI/CD

`.github/workflows/deploy.yml` runs when **CI** succeeds on a push to `release`. It can also be started by hand from the Actions tab, with a re-scrape choice. A manual run only deploys when it is started on `release`. Pushes to `main` run CI and nothing else.

To ship, Arian puts a commit of his on top of a green `main`, then fast-forwards `release` to it. Vercel deploys only when Arian authored the commit at the tip of `release` (see **Commit author** below), and a merge commit made on GitHub is authored by whoever clicked Merge, so `main`'s tip is usually someone else's:

```bash
git switch main && git pull --ff-only
git commit --allow-empty -m "Ship <main's short sha> to release"   # skip if main's tip is already Arian's
git push origin main
# once CI is green on that commit:
git push origin main:release
```

The ship commit goes on `main` too, so `release` stays on `main`'s line. Git refuses the push if `release` has diverged from `main`. So as long as everyone ships this way, `release` only moves forward along `main`. To roll back, push an older commit of `main` with `--force-with-lease`:

```bash
git push --force-with-lease origin <sha>:release
```

The deploy then reinstalls that version. Migrations are forward-only, so a rollback past a migration keeps the newer schema.


1. It connects to the VM through the gateway, using the gateway as an SSH jump host. The VM can't be reached any other way. The connection uses `.github/actions/landline-ssh` with secrets `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`.
2. `rsync --delete` copies the commit to a staging copy, `~/landline.next`.
3. `deploy/remote.sh` runs from there. It pauses the refresh timer and applies the commit's migrations. Only if they succeed does it copy the code into `~/landline`, sync dependencies and restart the API, so a failed migration leaves the running tree as it was. If the commit changed `pipeline/` or `db/migrations/`, or the database has no listings, it also starts a re-scrape in the background.
4. It checks `https://landline.134.87.8.230.sslip.io/health` and the CORS preflight through the gateway.

`.github/workflows/refresh-listings.yml` (manual only) runs the VM's refresh job now. It then attaches the log and the scrape as an artifact. The 6-hour schedule is the VM's own timer.

**The deploy key** is a dedicated ed25519 key, used only by CI.
- On the gateway it is restricted to `restrict,port-forwarding,permitopen="192.168.104.198:22",command="/bin/false"`. It can open the tunnel to the VM's SSH port and nothing else: no shell and no other destination.
- On the VM it is accepted only from the gateway's private address (`from="192.168.104.213"`).
- To rotate it, generate a new key, replace both `authorized_keys` lines (each tagged `landline-deploy`), and run `gh secret set DEPLOY_SSH_KEY < key`.

## Web on Vercel

`.github/workflows/deploy-web.yml` deploys `web/` to Vercel production when a push to `release` changes `web/`. It can also be run by hand on `release`.
- The workflow typechecks and builds the app on the runner (`vercel build`), then uploads the prebuilt output (`vercel deploy --prebuilt --prod`).
- A failed build deploys nothing.
- On the first run it creates and links the Vercel project.
- Keep the Git repository **disconnected** in the Vercel dashboard. `web/vercel.json` applies only to these CLI deploys from `web/`; a dashboard Git integration would read the repo root and deploy on its own.

The build sets `NEXT_PUBLIC_API_URL` and `API_URL` to `https://landline.134.87.8.230.sslip.io`, so the browser talks to the API directly. That keeps the event stream off Vercel's proxy. The production origin must therefore be allowed by CORS: set it as `WEB_URL` in `production.env` and ship it to `release` along with the first web deploy.

| Setting | Kind | Value |
|---|---|---|
| `VERCEL_TOKEN` | secret | A Vercel access token (vercel.com → Account Settings → Tokens), scoped to the account or team that owns the project. Set it with `gh secret set VERCEL_TOKEN` so it never appears in chat or git |
| `VERCEL_PROJECT` | variable | Project name; default `landline`, giving `https://landline.vercel.app` if that name is free |
| `VERCEL_SCOPE` | variable | The team slug when the project lives under a team. Unset for a personal account |
| `WEB_URL` | variable | The production URL. It is checked after each deploy; the same origin goes in `production.env` |

The project lives under the Vercel team **AAMEL**. `landline.vercel.app` belongs to someone else, so the production domain is **https://landline-mu.vercel.app** (also the repository variable `WEB_URL` and `WEB_URL` in `production.env`). Each deploy prints its domains in the run summary.

Vercel's default Standard Protection puts every URL except the production domain behind a Vercel login, including per-deploy URLs and `landline-aamel.vercel.app`. Share only the production domain; judges need no Vercel login there.

**Commit author.** Vercel deploys only when the commit at the tip of `release` is authored by Arian (`boukani.arian@gmail.com`, the email of his Vercel account, set in his clone with `git config user.email`). A tip authored by anyone else, a GitHub merge commit included, fails: on Fri 23 Oct, `26e797c` (Danial's merge of #27) failed with "Your deployment failed. Please retry later.", while every tip of Arian's before it deployed. So every ship ends with a commit of Arian's (above).

`deploy-web.yml` runs on its own only when the push to `release` changes `web/`. When a push carries no `web/` change (say, the web changes went out on an earlier push whose deploy failed), run it by hand once the tip is Arian's: `gh workflow run deploy-web.yml --ref release`. A failed deploy prints Vercel's reason in the "Explain a failed deploy" step.

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
| **Before a demo** | `sudo systemctl stop landline-refresh.timer`: a refresh restarts the API, which resets in-memory demo sessions. Deploys leave a stopped timer stopped and skip their own re-scrape. `start` it again afterwards; a slot missed while it was stopped then runs at once |
| psql | `cd ~/landline && docker compose --env-file .env -f deploy/compose.yml exec db psql -U landline` |
| Manual deploy of what's on disk | `bash ~/landline/deploy/remote.sh no` |
| After a failed deploy | The live code is unchanged if a migration failed. The refresh timer stays stopped until a deploy succeeds; fix forward, or `sudo systemctl start landline-refresh.timer` |

Production's data on your laptop, for anyone on the team: put `ADMIN_TOKEN` (password manager) in your `.env`, then

```bash
make db-up
```

```bash
make db-pull
```

It downloads a consistent copy through the API (`GET /api/admin/export`, over HTTPS, no SSH or VPN) and loads it into the local database on :54329, replacing what was there. Production is only read. It refuses any database not on your machine, and production itself even through a tunnel. `make db-pull ARGS=--audio` copies the call recordings too.

The live database from your laptop, through a tunnel (needs your key on the gateway and the VM; the VM's Postgres listens only on localhost):

```bash
ssh -N -J ubuntu@134.87.8.230 -L 5433:127.0.0.1:5432 ubuntu@192.168.104.198
```

Then connect to `postgresql://landline:<password from ~/landline/.env>@localhost:5433/landline`.

## CORS

The production web app calls the API directly (`NEXT_PUBLIC_API_URL`, set by `deploy-web.yml`), so its origin must be listed. Without `NEXT_PUBLIC_API_URL`, the app calls its own `/api` proxy (`next.config.ts`, `API_URL`), which is same-origin and needs no CORS. Both values are set in `deploy-web.yml`'s job `env` and baked in at build time on the runner. Change them there, not in the Vercel dashboard, which the build doesn't read for keys the job already sets. A new web origin goes in `CORS_ORIGINS`, or as `WEB_URL`, in `production.env`; push it to `release`. `localhost:3000` is always allowed. nginx adds no CORS headers of its own. The API answers preflights and leaves out `Access-Control-Allow-Origin` for any origin not listed.

## SevenTask (paused)

The VM used to run SevenTask, and it is stopped while Landline runs there:
- the containers `seventask-api` and `seventask-sql` are stopped
- `seventask-admin-api.service` and the VM's nginx are disabled

Nothing was deleted. To bring it back, first stop Landline (`sudo systemctl disable --now landline-api landline-refresh.timer`), then run:

```bash
bash ~/seventask-paused/restore.sh
```

The state before pausing is in `~/seventask-paused/state-before.txt`.
