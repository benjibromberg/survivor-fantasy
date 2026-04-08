# Contributing

Thanks for your interest in contributing to the Survivor Fantasy League app. This guide covers how to set up a development environment, run tests, and submit changes.

## Prerequisites

- Python 3.11+
- Git

## Local Development Setup

1. **Clone and create a virtual environment:**

   ```bash
   git clone https://github.com/benjibromberg/survivor-fantasy.git
   cd survivor-fantasy
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Set up environment variables:**

   Copy `.env.example` to `.env` and fill in the values:

   ```bash
   cp .env.example .env
   ```

   For local development, the key settings are:

   - `DEV_LOGIN=1` — enables dev login without GitHub OAuth
   - `ADMIN_GITHUB_USERNAME` — must match a player name in your pick files (e.g., `benji`)

3. **Seed the database:**

   ```bash
   python seed.py                       # Downloads survivoR data + generates images
   python seed.py --no-scrape           # Offline: skip network calls
   python seed.py --picks-dir ./picks   # Also load pick assignments from JSON files
   ```

   > **Warning:** `seed.py` drops all tables. Export any picks from an existing database before re-seeding.

4. **Run the app:**

   ```bash
   python run.py   # Starts on 0.0.0.0:5050
   ```

## Running Tests

```bash
python -m pytest
```

There are 180+ tests covering scoring, highlights, and the scoring analysis simulation. All tests must pass before submitting a PR.

## Branch Conventions

Always work on a feature branch — never commit directly to `main`.

Branch naming follows `<type>/<short-description>`:

- `feat/season-planner`
- `fix/hydration-error`
- `refactor/scoring-engine`
- `docs/contributing-guide`
- `test/highlight-edge-cases`
- `ci/snyk-actions`

## Commit Conventions

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<optional scope>): <description>
```

Common types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `style`, `perf`, `ci`, `build`

Keep the subject line under 72 characters. Use the body for "why", not "what."

## Pull Request Process

1. Create a feature branch off `main`
2. Make atomic, logical commits
3. Rebase on latest `main` before opening a PR
4. Ensure all tests pass (`python -m pytest`)
5. Open a PR to `main` with a clear description of what and why

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `app/` | Flask application (routes, models, auth, scoring, templates) |
| `app/scoring/` | Extensible scoring system — subclass `ScoringSystem` in `base.py` |
| `app/templates/` | Jinja2 templates with Pico CSS v2 dark theme |
| `app/static/` | CSS, JS, fonts, images |
| `tests/` | pytest test suite |
| `picks/` | Pick assignment JSON files (gitignored for privacy) |

## Key Concepts

- **New-era only:** The app exclusively supports Survivor seasons 41+. All data fields (`n_cast`, `n_jury`, `n_finalists`) are required from the [survivoR](https://github.com/doehm/survivoR) dataset.
- **Scoring:** Extend by subclassing `ScoringSystem` in `app/scoring/base.py`. Implement `calculate_survivor_points()` and register in `app/scoring/__init__.py`.
- **Pick types:** `draft` (full points), `wildcard` (half points), `pmr_d`/`pmr_w` (post-merge replacements).
- **Data source:** All game data comes from the survivoR open-source dataset.

## Design System

Read `DESIGN.md` before making any visual or UI changes. All font choices, colors, spacing, and aesthetic direction are defined there.

## Deployment

See `DEPLOYMENT.md` for the Docker + Tailscale sidecar setup on Proxmox.
