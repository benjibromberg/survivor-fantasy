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

# Put your league's pick files in the data volume (format: picks/README.md)
mkdir -p data/picks
cp /path/to/your/season*.json data/picks/

# Seed the database straight into the data volume
pip install -r requirements.txt
DATABASE_URL="sqlite:///$PWD/data/survivor_fantasy.db" python seed.py --picks-dir ./data/picks

docker compose up -d
```

Pick files are not in the repo, so on a first deployment they have to be placed
by hand as above. `data/` must be writable by the container user; see
**Data Persistence**.

Seed into `data/` directly rather than seeding in the repo root and moving
`survivor_fantasy.db` afterwards. SQLite runs in WAL mode here, so a freshly
seeded database can sit almost entirely in `survivor_fantasy.db-wal`, and moving
the `.db` file alone leaves the data behind.

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

## Player login

League members can log in as themselves to see their team and name it for the
active season. A member's Access email has to be linked to their player first:

1. As admin, open **Admin → Players** for a season and enter the member's email
   in the **Login Email** column. It must be the same address that is on the
   Access allowlist.
2. The member clicks **Login**. The app matches their Access email to the linked
   player and shows a **My Team** link in the nav.

An email that is not linked to a player cannot log in, and no account is created
for it. Players get no admin pages; `ADMIN_EMAIL` stays the only admin, and the
admin can link that same address to their own player row to log in as both. The
admin can also set any player's team name from the Players page.

Linked emails and team names are part of the pick export (see **Data
Persistence**), so a re-seed with `--picks-dir` restores them.

Locally, `/dev-login?user=<username>` logs in as a player without Access.

### Wildcard self-service

By default the admin enters every wildcard under **Manage Picks**. To let
players pick their own, open the season's admin page and enter when Episode 2
starts (Eastern time) under **Wildcard Picks**. From then on:

- A player with draft picks chooses their wildcard on **My Team**: any castaway
  still in the game who is not already on their own team. They can change it as
  often as they like.
- Picks lock 15 minutes before Episode 2 for everyone. After that no player
  can set or change a wildcard; enter any that are missing under Manage Picks.
- Wildcards stay off the leaderboard, charts and win odds, and score no points,
  until every player with draft picks has one. Each player sees their own on
  My Team, and the admin sees all of them under Manage Picks. The season admin
  page shows who is still to pick.

The admin can change any wildcard at any time under Manage Picks, including
after the lock. Clearing the Episode 2 time switches self-service off and shows
every wildcard again. The Episode 2 time is exported with the season's picks,
so a re-seed with `--picks-dir` restores it and wildcards stay hidden.

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
  (picks, Sole Survivor picks, the season's scoring config, the players' team
  names for that season, and its Episode 2 start time).
- `picks/players.json`: the login email linked to each player. It holds
  personal email addresses, so treat the `picks/` directory as private.

- `headshots/<season>/<hash>.webp`: castaway headshots mirrored from
  fantasysurvivorgame.com, resized to 160 px WebP and served at
  `/headshots/...` with immutable caching. Filled by the next data refresh or
  admin **Fetch images**; existing remote `image_url` values are replaced
  one time. Safe to delete (re-fetched on demand). Override the location with
  `HEADSHOTS_DIR`.

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
   `/app/data/picks/season{N}.json` (`data/picks/` on the host), and the linked
   login emails to `players.json` beside them.
2. Stops with an error and a non-zero exit, before dropping anything, if that
   export fails while the database holds picks or linked emails, or if
   `--picks-dir` is not a directory.
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
- Only seasons with picks are exported. Their picks, Sole Survivor picks,
  scoring config, team names and Episode 2 time come back, and so does every
  linked login email (`players.json`). A custom scoring config, team name or
  Episode 2 time on a season with no picks is lost. Players with no picks are
  not recreated unless they have a linked email. Only the highest-numbered
  season built is marked active (pass `--active=N` to choose another), so
  check the admin panel afterwards.
- Do not re-seed to add a season or to load a draft. Use `add_season.py`
  (**Adding a season** below), which leaves the rest of the database alone. For
  a season that already has picks, edit them in the admin panel.

## Auto-Refresh

An APScheduler job refreshes survivoR data daily at 8am EST, inside the Flask
process. No cron setup needed. It refreshes the **active** season only, so a new
season gets no data updates until it is activated.

## Adding a season

A new season does not need a re-seed. `add_season.py` adds one season to the
existing database and touches nothing else.

```bash
# 1. Create the season once survivoR has its cast. It is created inactive, so
#    the homepage keeps showing the current season.
docker compose exec survivor-fantasy python add_season.py 52

# 2. After the draft, put the pick file in data/picks/ on the host
#    (format: picks/README.md), then load it and switch the active season.
docker compose exec survivor-fantasy \
  python add_season.py 52 --picks /app/data/picks/season52.json --activate
```

Each run first backs the database up to `data/backups/`. What the steps do:

- **Create:** downloads the latest survivoR dataset, creates the season and its
  castaways, and fetches headshots. If survivoR has no data for the season yet,
  nothing is created.
- **`--picks`:** loads a draft into a season that has no picks yet. The whole
  file is checked first: every castaway name has to match the season's
  castaways exactly, and every pick type has to be a known code. If anything is
  off, nothing is written and the problems are listed. Players in the file who
  do not exist yet are created.
- **`--activate`:** makes the season the only active one, the same as the toggle
  on the admin seasons page.

The same steps work from the admin panel (create the season, enter each
player's picks, then activate), which is the way to change picks afterwards. To
print the exact castaway names for a pick file, open the season's admin page.

Picks loaded this way are in the database, and the next export rewrites
`data/picks/season{N}.json` from it.

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
