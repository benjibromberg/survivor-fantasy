# Deployment Guide

This app runs on a home server (e.g. Proxmox) using Docker, with a
**Cloudflare Tunnel** sidecar for private HTTPS access and **Cloudflare Access**
(email one-time PIN) for authentication — no port forwarding, no public
exposure, and no network client for your league to install. Access is granted by
adding an email to an allowlist; members get a 6-digit code by email.

Rationale and the alternatives considered: `docs/cloudflare-private-access.md`.

## Architecture

```
League member ──▶ https://www.benjis-survivor-fantasy.party
                    │  Cloudflare edge: Access enforces email OTP against your allowlist
                    ▼
                  Cloudflare Tunnel  (outbound-only; nothing exposed on the host)
                    ▼
[Proxmox Host] Docker
  ├── cloudflared        (Tunnel connector; token-based, remotely managed)
  └── survivor-fantasy   (Flask app via gunicorn on :5050)
```

`cloudflared` makes an outbound connection to Cloudflare and serves the app
through the tunnel's public hostname. Cloudflare Access sits in front of that
hostname and only forwards a request to the tunnel after the visitor passes your
OTP policy. The app's admin login reads the email Access forwards (see
**Admin login** below).

## Prerequisites

- A machine running Docker (Proxmox LXC, VM, or bare metal).
- A free [Cloudflare account](https://dash.cloudflare.com/) with **Zero Trust**
  enabled, and a domain on Cloudflare DNS (here: `benjis-survivor-fantasy.party`,
  via Cloudflare Registrar).

## Setup

The Cloudflare dashboard steps are scripted in the **`cloudflare-access-setup.sh`
wizard** at the repo root — run it and it walks you through each click and
captures the tunnel token. The steps it covers:

### 1. Create the Tunnel and get its token
Zero Trust → **Networks → Tunnels → Create a tunnel** (e.g. `survivor-fantasy`).
Choose **Docker** and copy the **token** (the `eyJ...` value). Store it as
`CF_TUNNEL_TOKEN` in the server `.env`.

> **Secret storage:** the canonical copy lives in the macOS Keychain as
> `SURVIVOR_FANTASY_TUNNEL_TOKEN` (`keychain-secret set SURVIVOR_FANTASY_TUNNEL_TOKEN`).
> It is written to the server `.env` as the `CF_TUNNEL_TOKEN` line (the name
> `docker-compose.yml` interpolates). The Keychain name is descriptive to avoid
> clashing with other tunnels; the `.env` var name is fixed by compose.

### 2. Add a public hostname route
On the tunnel's **Published application routes**, add:
- **Hostname**: `www.benjis-survivor-fantasy.party`
- **Service**: `http://survivor-fantasy:5050`

(`survivor-fantasy` is the app's compose service name; `cloudflared` reaches it
over the compose network.)

### 3. Add the One-Time PIN identity provider
Zero Trust → **Integrations → Identity providers → Add → One-time PIN**. (New
Zero Trust orgs don't add OTP automatically.)

### 4. Create the Access application + policy
Zero Trust → **Access controls → Applications → Add → Self-hosted**:
- **Application hostname**: `www.benjis-survivor-fantasy.party`
- **Policy**: Action **Allow**, rule **Emails** (or **Emails ending in**) listing
  your league. Authentication method: **One-time PIN**.

### 5. Configure environment
Create `.env` in the project root (see `.env.example`):

```env
# Admin login: the Access email treated as admin
ADMIN_EMAIL=you@example.com

# Cloudflare Tunnel token (from step 1)
CF_TUNNEL_TOKEN=eyJ...

# Required in production (DEV_LOGIN=0). App errors on startup if missing.
SECRET_KEY=generate-a-random-string-here

# Optional data directory for the Docker volume mount
# APPDATA_DIR=/srv/survivor-fantasy
```

### 6. Deploy

```bash
git clone https://github.com/benjibromberg/survivor-fantasy.git
cd survivor-fantasy
# create .env (above)

# Seed the database, then move it to the data volume
pip install -r requirements.txt
python seed.py --picks-dir ./picks
mkdir -p data && mv survivor_fantasy.db data/

docker compose up -d
```

The app will be live at `https://www.benjis-survivor-fantasy.party` once the
tunnel connects (a minute or two). The first visit prompts for an email; approved
addresses receive a one-time code.

### 7. Verify

```bash
docker compose ps
docker compose logs cloudflared       # should show "Registered tunnel connection"
docker compose logs survivor-fantasy
```

## Admin login

The whole site is gated by Access, so every visitor is an authenticated league
member. **Admin** is whoever's Access email matches `ADMIN_EMAIL`: visit
`/login` (the "Login" button) and the app promotes that email to admin. Access
forwards the verified email in the `Cf-Access-Authenticated-User-Email` header;
because the origin is reachable only through the tunnel behind Access, the app
trusts that header. For defence-in-depth you can additionally validate the
`Cf-Access-Jwt-Assertion` JWT against your team's public keys.

`DEV_LOGIN=1` still works for local development (no Access in front); the
Dockerfile sets `DEV_LOGIN=0` in the image.

## How It Works

- **`cloudflared`** runs the remotely-managed tunnel from the `CF_TUNNEL_TOKEN`
  (`tunnel --no-autoupdate run`). Its route and the Access policy live in the
  Cloudflare dashboard, not in the repo.
- **`survivor-fantasy`** is the Flask app (gunicorn, 2 workers, `0.0.0.0:5050`),
  reachable to `cloudflared` over the compose network as
  `http://survivor-fantasy:5050`.

## Data Persistence

The `data/` volume (mounted at `/app/data`) holds everything the app writes:

- `survivor_fantasy.db`: the database.
- `survivoR.xlsx`: the downloaded survivoR dataset.
- `picks/season{N}.json`: pick exports, one file per season that has picks
  (picks, Sole Survivor picks, and the season's scoring config).

Container local disk is otherwise ephemeral; keep durable data in this volume.

The app runs as the non-root `appuser` (UID 1000 in the current image), and the
rest of `/app` is owned by root, so `data/` is the only place it can write. If
the host directory belongs to another user, hand it over:

```bash
sudo chown -R 1000:1000 data
docker compose restart survivor-fantasy
```

The pick exports are the backup of everything entered through the admin panel.
They are rewritten around each daily refresh, around each admin **Refresh**, by
the admin **Export Picks** / **Export All Picks** buttons, and by `seed.py`
before it drops tables. A failed export is logged as a warning (scheduler and
refresh) or shown as an error (export buttons), so if `data/picks/` is missing
or stale, check `docker compose logs survivor-fantasy` for
`Pick export ... failed`.

## Updating

```bash
cd /path/to/survivor-fantasy
docker compose down
git pull
docker compose build
docker compose up -d
```

### Re-seeding (if the schema changed)

```bash
docker compose exec survivor-fantasy python seed.py --picks-dir /app/data/picks
docker compose restart survivor-fantasy
```

`seed.py` drops every table and rebuilds from survivoR plus the pick files. In
order, it:

1. Exports the picks of every season that has any to
   `/app/data/picks/season{N}.json` (`data/picks/` on the host).
2. Stops with an error and a non-zero exit, before dropping anything, if that
   export fails while the database holds picks, or if `--picks-dir` is not a
   directory.
3. Drops and recreates the tables, then builds the default seasons plus every
   season that has a pick file in `--picks-dir`, and loads one pick file per
   season (discovery rules: `picks/README.md`). When a season has both
   `season{N}.json` and a suffixed file such as `season{N}_draft.json`, the
   freshly exported `season{N}.json` wins.

So pointing `--picks-dir` at the export directory restores the picks that were
in the database just before the drop. No files need copying into the container.

Things to know before running it:

- Without `--picks-dir` the picks are exported but not loaded back. Run the
  command again with `--picks-dir` to restore them.
- Read the output for `WARNING` lines. A pick file whose season was not built
  is skipped, not loaded. That happens when `--seasons` leaves the season out
  (it builds exactly the seasons listed) or when the survivoR dataset has no
  data for it yet. The skipped file stays in `data/picks/`.
- The export never deletes files. A leftover file for a season whose picks you
  have since removed (or a hand-placed file for such a season) is loaded again.
  Delete any `data/picks/season*.json` you do not want restored first.
- Only seasons with picks are exported, and only their picks, Sole Survivor
  picks, and scoring config come back. A custom scoring config on a season with
  no picks is lost, players with no picks are not recreated, and only the
  highest-numbered season built is marked active (pass `--active=N` to choose
  another), so check the admin panel afterwards.
- To load a draft from a file for a season that has no picks in the database
  yet, put the file in `data/picks/` on the host and run the same command. For
  a season that already has picks, the export in step 1 overwrites
  `season{N}.json` and outranks a suffixed file, so edit those picks in the
  admin panel instead.

## Auto-Refresh

An APScheduler job refreshes survivoR data daily at 8am EST, inside the Flask
process. No cron setup needed.

## Proxmox-Specific Notes

- Install Docker in the LXC (or use a VM with Docker).
- The LXC needs outbound network access for `cloudflared` and data refresh; it
  needs **no** inbound port forwarding.
- Allocate at least 1GB RAM and 2 CPU cores.

## Migrating off Tailscale

The previous deployment used a Tailscale sidecar (`serve.json`, `TS_AUTHKEY`).
To cut over with zero downtime, run the Cloudflare tunnel alongside Tailscale
first (the app can serve both), verify the new URL + OTP, then remove the
Tailscale sidecar and auth key.
