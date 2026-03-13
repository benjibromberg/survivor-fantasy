# Deployment Guide

This app is designed to run on a home server (e.g., Proxmox) using Docker with a Tailscale sidecar for private HTTPS access on your Tailnet — no port forwarding or public exposure required.

This setup follows the same pattern as the [Tailscale self-hosting guide for audiobookshelf](https://github.com/tailscale-dev/video-code-snippets/tree/main/2025/2025-06-self-hosting-part2/audiobookshelf), demonstrated in [this video](https://www.youtube.com/watch?v=guHoZ68N3XM).

## Architecture

```
Internet (blocked)
       |
[Proxmox Host]
  └── Docker
        ├── survivor-fantasy-ts   (Tailscale sidecar)
        │     └── Tailscale Serve: HTTPS :443 → proxy to 127.0.0.1:5050
        └── survivor-fantasy      (Flask app via gunicorn)
              └── Listens on :5050 (network_mode: service:survivor-fantasy-ts)
```

The Flask app shares the Tailscale container's network stack via `network_mode: service:survivor-fantasy-ts`. Tailscale Serve handles TLS termination and proxies HTTPS traffic to the app on port 5050. The app is only accessible to devices on your Tailnet.

## Prerequisites

- A machine running Docker (Proxmox LXC, bare metal, VM, etc.)
- A [Tailscale account](https://tailscale.com/) with an auth key
- A [GitHub OAuth App](https://github.com/settings/developers) for admin login

## Setup

### 1. Generate a Tailscale auth key

Go to [Tailscale Admin Console → Settings → Keys](https://login.tailscale.com/admin/settings/keys) and create a new auth key. For a persistent deployment, use a reusable key with no expiry, and tag it appropriately (e.g., `tag:server`).

### 2. Create a GitHub OAuth App

Go to [GitHub → Settings → Developer settings → OAuth Apps](https://github.com/settings/developers) and create a new app:

- **Homepage URL**: `https://survivor-fantasy.<your-tailnet>.ts.net`
- **Authorization callback URL**: `https://survivor-fantasy.<your-tailnet>.ts.net/auth/callback`

Note your Client ID and generate a Client Secret.

### 3. Configure environment

Create a `.env` file in the project root:

```env
# Tailscale
TS_AUTHKEY=tskey-auth-...

# GitHub OAuth (for admin login)
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret
ADMIN_GITHUB_USERNAME=your_github_username

# Flask
SECRET_KEY=generate-a-random-string-here

# Data directory (optional, defaults to current directory)
# APPDATA_DIR=/srv/survivor-fantasy
```

### 4. Deploy

```bash
# Clone the repo
git clone https://github.com/benjibromberg/survivor-fantasy.git
cd survivor-fantasy

# Create .env (see above)

# Seed the database
pip install -r requirements.txt
python seed.py
# Move the DB to the data volume mount
mkdir -p data
mv survivor_fantasy.db data/

# Start the containers
docker compose up -d
```

The app will be available at `https://survivor-fantasy.<your-tailnet>.ts.net` within a minute or two of the Tailscale container joining your Tailnet.

### 5. Verify

```bash
# Check container status
docker compose ps

# Check Tailscale status
docker compose exec survivor-fantasy-ts tailscale status

# Check app logs
docker compose logs survivor-fantasy
```

## How It Works

### Docker Compose (`docker-compose.yml`)

Two services:

1. **`survivor-fantasy-ts`** — The Tailscale sidecar container. Joins your Tailnet, gets a hostname (`survivor-fantasy`), and runs Tailscale Serve to accept HTTPS and proxy to the app.

2. **`survivor-fantasy`** — The Flask app built from the `Dockerfile`. Uses `network_mode: service:survivor-fantasy-ts` to share the Tailscale container's network, so it's reachable at `127.0.0.1:5050` from within the sidecar's network namespace.

### Tailscale Serve (`serve.json`)

```json
{
  "TCP": { "443": { "HTTPS": true } },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": { "/": { "Proxy": "http://127.0.0.1:5050" } }
    }
  },
  "AllowFunnel": { "${TS_CERT_DOMAIN}:443": false }
}
```

- Listens on port 443 with automatic HTTPS (Tailscale manages the TLS cert)
- Proxies all requests to the Flask/gunicorn server on port 5050
- `AllowFunnel: false` ensures the app is Tailnet-only (not publicly accessible)

### Dockerfile

- Python 3.11-slim base image
- Installs dependencies + gunicorn
- Runs with 2 gunicorn workers on port 5050
- `DEV_LOGIN=0` disables the dev login shortcut in production

## Data Persistence

The `data/` volume (mounted at `/app/data`) contains:

- `survivor_fantasy.db` — SQLite database with all seasons, picks, and user data

Tailscale state is persisted in `ts-state/` and `ts-config/` volumes so the node doesn't need to re-authenticate on container restart.

## Updating

```bash
cd /path/to/survivor-fantasy
docker compose down
git pull
docker compose build
docker compose up -d
```

The SQLite database is preserved across rebuilds since it lives in the mounted `data/` volume.

### Re-seeding the database

If the schema has changed (new columns, etc.), you'll need to re-seed:

```bash
# Copy pick JSON files into the running container (they aren't in the Docker image
# because they're added after build — *.xlsx is in .dockerignore but JSONs need
# to be present at /app/picks/ inside the container)
docker compose cp picks/season45.json survivor-fantasy:/app/picks/
docker compose cp picks/season46.json survivor-fantasy:/app/picks/
docker compose cp picks/season47_snakedraft.json survivor-fantasy:/app/picks/
docker compose cp picks/season49_snakedraft.json survivor-fantasy:/app/picks/

# Re-seed (downloads fresh survivoR data + generates images)
docker compose exec survivor-fantasy python seed.py --picks-dir ./picks

# Restart to pick up the new DB
docker compose restart survivor-fantasy
```

If you don't need images (faster), add `--no-scrape`. If pick files are already inside the container from a previous build, skip the `cp` steps.

## Auto-Refresh

The app includes an APScheduler job that automatically refreshes game data from the survivoR dataset daily at 8am EST. No cron setup needed — it runs inside the Flask app process.

## Proxmox-Specific Notes

If running in a Proxmox LXC container:

- Ensure Docker is installed in the LXC (or use a VM with Docker pre-installed)
- The LXC needs network access for Tailscale to connect and for data refresh
- No special Proxmox configuration is needed — the Tailscale sidecar handles all networking
- Consider allocating at least 1GB RAM and 2 CPU cores for comfortable operation
