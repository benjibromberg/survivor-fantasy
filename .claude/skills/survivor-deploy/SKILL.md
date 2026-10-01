---
name: survivor-deploy
description: >-
  Deploying and operating the survivor-fantasy app: the Proxmox host, Docker plus Cloudflare Tunnel, the update and re-seed procedures, what lives on the data volume, and CI/CD workflows. Use when deploying, re-seeding, adding a season to production, or changing CI.
---

# survivor-deploy

Operating the deployed app. `DEPLOYMENT.md` in the repo is the source of truth for setup and procedures; this file is the agent-side summary plus the things that are not written down there.

Host names and paths are deliberately not here. They live in `CLAUDE.md`, which is not committed; this file uses placeholders for them.

## Deployment

Docker + Cloudflare Tunnel sidecar (`docker-compose.yml`), with Cloudflare Access in front. `DEPLOYMENT.md` is the source of truth for setup, updating, re-seeding and what lives on the data volume; read it instead of restating steps here.

**Deployment host**: a Proxmox box reached over the tailnet. The SSH target
and the repo path on it are in CLAUDE.md, which is not committed.

**Docker permissions:** The container runs as `appuser` (UID 1000). `/app/data` is the only place it can write, so everything the app writes lives there (derived from `DATABASE_URL`): the SQLite DB, `survivoR.xlsx`, `picks/` exports, `headshots/`, and `backups/`. The volume must be owned by UID 1000 on the host. The rest of `/app` is a root-owned image layer; code that writes to a path relative to the working directory works locally and fails in the container.

**Update process** (no re-seed):

```bash
ssh <proxmox-host>          # target in CLAUDE.md
cd <repo-path-on-host>
git pull --ff-only
docker compose build
docker compose up -d
```

- **Added columns do not need a re-seed.** `_add_missing_columns()` adds them at startup. A new stat column stays empty until each season is refreshed, and the daily job only refreshes the active season, so refresh older seasons once after such a deploy.
- **Never re-seed production to add a season or picks.** `seed.py` drops every table. Use `add_season.py <N> [--picks FILE] [--activate]` (backs up, creates the season inactive, validates the pick file before loading), or the admin panel (create, enter picks, activate). See "Adding a season" in `DEPLOYMENT.md`. Picks edited in the admin panel exist only in the DB until the next export.
- **Schema changes and two workers:** gunicorn runs 2 workers without `--preload`, and both run the startup schema sync. Before `up -d` on a deploy that adds columns, tables or indexes, run the sync once in a single process: `docker compose run --rm survivor-fantasy python -c "from app import create_app; create_app()"`.
- **Re-seeding (rare):** `docker compose exec survivor-fantasy python seed.py --picks-dir /app/data/picks`, then restart. It exports picks to `/app/data/picks` first and aborts before dropping anything if that export fails. `DEPLOYMENT.md` lists what a re-seed does not restore.
- **Back up before any production write.** Use the SQLite online backup API into `data/backups/`. The DB runs in WAL mode, so copying or moving the `.db` file alone can lose data.
- **Verifying production:** the public URL is behind Access, so check pages from inside the container (`http://localhost:5050`), and say which layer was verified.
## CI/CD

- **CI** (`.github/workflows/ci.yml`): `pytest` + `ruff` on PR/push.
- **Dependabot** (`.github/dependabot.yml`): Weekly updates for pip, GitHub Actions, Docker. Conventional commit prefixes.
- **Branch protection**: `pytest`, `ruff` and `docker-build` required checks on `main`. Admin bypass enabled.
- **Preview deployments** (#35): Coolify on Proxmox (under evaluation — security constraints documented in issue).
## Security practices

These outlived the scanner that first flagged them and are kept on their own merits.

- **Validate CLI path arguments**: `seed.py` and `analyze_scoring.py` take file paths from `argv`. Resolve with `os.path.realpath()` and check the result is inside the directory you expect before opening it.
- **GitHub Actions `permissions:` blocks** default every unspecified permission to `none`, so a workflow that needs `contents: read` must say so even when it also asks for something else.
- **Prefer `hashlib.sha256`** over `md5`, including for non-cryptographic uses such as cache keys. There is no upside to md5 here and it reads as a finding to every future reviewer and tool.
