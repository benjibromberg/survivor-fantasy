---
name: survivor-deploy
description: >-
  Deploying and operating the survivor-fantasy app: the Proxmox host, Docker plus Cloudflare Tunnel, the update and re-seed procedures, what lives on the data volume, CI/CD workflows and the Snyk setup. Use when deploying, re-seeding, adding a season to production, changing CI, or running a security scan.
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
- **Security** (`.github/workflows/security.yml`): Three Snyk jobs (SAST, SCA, container) on PR, push to main, daily 8am ET. SAST/SCA block on high/critical via SARIF `error`-level parsing; container is advisory-only. All action refs pinned to commit SHAs.
- **Dependabot** (`.github/dependabot.yml`): Weekly updates for pip, GitHub Actions, Docker. Conventional commit prefixes.
- **Branch protection**: `snyk-code` and `snyk-sca` required checks on `main`. Admin bypass enabled.
- **Preview deployments** (#35): Coolify on Proxmox (under evaluation — security constraints documented in issue).
## Security (Snyk)

Snyk MCP is configured globally for "Secure at Inception" scanning. Run scans before shipping code changes.

**CI workflow gotchas (`security.yml`):**
- **`--severity-threshold` filters SARIF output**, not just exit code. Don't use it if you want all findings visible in Code Scanning. The workflow uses `continue-on-error` + `jq` SARIF parsing instead.
- **Snyk container SARIF has invalid `security-severity` values** (`"undefined"`, `"null"` strings). Must `sed`-sanitize before `upload-sarif`. See github/codeql-action#2187.
- **`snyk/actions/*` are Docker actions** — they run their own Python. Host-side `setup-python` + `pip install` is redundant for SCA scans.
- **GitHub Actions `permissions:` block** defaults unspecified permissions to `none`. Always include `contents: read` alongside `security-events: write`.
- **Fork PRs won't be scanned** — `SNYK_TOKEN` is withheld from fork `pull_request` events. Checks show red (safe default), manual review required.

- **`snyk_code_scan`**: Run on new/modified Python code. Use `path` = absolute project path.
- **`snyk_sca_scan`**: Run when `requirements.txt` changes. Use `command=python3` and `skip_unresolved=true` if venv is not active.
- **Fix → rescan → repeat**: If issues are found, fix them using Snyk's context, then rescan to verify. Repeat until clean.
- **No `hashlib.md5`**: Use `hashlib.sha256` for all hashing, even non-cryptographic uses like cache keys. Snyk flags MD5 regardless of context.
- **CLI path arguments**: `seed.py` and `analyze_scoring.py` accept file paths from command-line args. Always use `os.path.realpath()` and validate paths are within expected directories before opening. Snyk will still flag the taint flow from argparse → open() — this is a residual finding inherent to CLI tools that accept file paths.
- **Keep dependencies current**: Check `snyk_sca_scan` when updating `requirements.txt`. Pin exact versions.
- **IaC scan does not apply**: No Terraform/K8s/CloudFormation files in this repo.
