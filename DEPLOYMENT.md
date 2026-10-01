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

The `data/` volume (mounted at `/app/data`) holds `survivor_fantasy.db`.
Container local disk is otherwise ephemeral; keep durable data in this volume.

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
docker compose cp picks/season45.json survivor-fantasy:/app/picks/
docker compose cp picks/season46.json survivor-fantasy:/app/picks/
docker compose cp picks/season47_snakedraft.json survivor-fantasy:/app/picks/
docker compose cp picks/season49_snakedraft.json survivor-fantasy:/app/picks/
docker compose exec survivor-fantasy python seed.py --picks-dir ./picks
docker compose restart survivor-fantasy
```

`seed.py` drops all tables — export picks first if you have unsaved changes.

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
