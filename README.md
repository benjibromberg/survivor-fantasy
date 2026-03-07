# Survivor Fantasy

A fantasy league web app for [Survivor](https://en.wikipedia.org/wiki/Survivor_(American_TV_series)). Draft castaways, earn points as they survive tribal councils, win challenges, and find advantages -- then compete with friends on a shared leaderboard.

Built for the modern era of Survivor (seasons 41+) where idols, advantages, and big moves drive the game.

## Features

- **Public leaderboard** with headshot images, tribe colors, and point breakdowns
- **Elimination slider** to rewind the leaderboard to any point in the season
- **Win probability predictions** via exhaustive or sampled permutation simulations
- **Admin panel** (GitHub OAuth) for managing seasons, players, picks, and scoring configs
- **Scoring analysis tool** that tested 3,000+ configurations across 9 modern seasons to find the most fun/fair settings
- **Auto-refresh** from the [survivoR](https://github.com/doehm/survivoR) open-source dataset (daily at 8am EST)
- **Docker + Tailscale** deployment for private hosting on your Tailnet

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python seed.py              # Reset DB + download survivoR data + generate headshot images
python seed.py --no-scrape  # Offline: skip network calls

python run.py               # http://localhost:5050
```

A "Dev Login" link appears in the nav bar for local development (no OAuth needed).

## Environment Variables

Create a `.env` file in the project root:

```env
# Required for admin login in production
GITHUB_CLIENT_ID=your_oauth_app_id
GITHUB_CLIENT_SECRET=your_oauth_app_secret
ADMIN_GITHUB_USERNAME=your_github_username

# Optional
SECRET_KEY=change-me-in-production
DEV_LOGIN=0          # Set to 1 to enable dev login (default: 1 locally, 0 in Docker)
TS_AUTHKEY=tskey-... # Tailscale auth key for Docker deployment
```

## How It Works

### Draft and Pick Types

Each fantasy player has a roster of Survivor castaways built through a snake draft. Wildcards are chosen **after the first episode** (first elimination) from any remaining castaway and can overlap other players' draft picks.

| Type | Scoring |
| ---- | ------- |
| **Draft** | Full points |
| **Wildcard** | Points x wildcard multiplier (default 0.5x) |
| **Replacement (half)** | Replaces eliminated wildcard post-merge. Multiplied points minus pre-merge tribal count |
| **Replacement** | Replaces eliminated draft pick post-merge. Full points minus pre-merge tribal count |

### Sole Survivor Pick

Each player predicts the overall winner each week. Points are awarded based on how many consecutive episodes (ending at the finale) you had the correct winner picked. Longer streaks = more points.

### Scoring Components

All point values are configurable per season via the admin panel. The default config:

| Component | Default | Description |
| --------- | ------- | ----------- |
| Pre-merge tribal | 0.5 | Per tribal council survived (before merge) |
| Post-merge tribal | 1.0 | Per tribal council survived (after merge) |
| Jury | 2 | Bonus for making the jury |
| Merge | 2 | Bonus for reaching the merge |
| Final Tribal Council | 3 | Bonus for reaching FTC |
| 1st / 2nd / 3rd | 15 / 5 / 2 | Placement bonuses |
| Individual immunity | 3 | Per individual immunity win |
| Tribal immunity | 1 | Per tribal immunity win |
| Idol found / played | 2 / 3 | Hidden immunity idol events |
| Advantage found / played | 1 / 2 | Other advantage events |
| Sole Survivor streak | 1 | Per episode in correct winner streak |

## Scoring Analysis

The included `analyze_scoring.py` tool evaluates scoring configurations for fun and fairness by simulating thousands of random snake drafts across modern Survivor seasons (41-49).

```bash
source venv/bin/activate
python analyze_scoring.py --quick                                    # Fast: 500 configs, 3 drafts/size
python analyze_scoring.py --export-json app/static/scoring_analysis.json  # Full: 3000 configs, 50 drafts/size
python analyze_scoring.py --cores 4 --export-json app/static/scoring_analysis.json  # Limit CPU usage
```

Each configuration is scored on:
- **Rank volatility** -- how much player positions shuffle each elimination
- **Comeback rate** -- how often the midpoint leader does NOT win
- **Suspense** -- % of the season where the eventual winner is not in 1st
- **Late-game drama** -- gap between 1st and 2nd at 75% through the season
- **Midpoint competitiveness** -- players within striking distance at the halfway mark
- **Recovery** -- can a player who loses a pick early still compete?

The admin panel has a "Load Recommended Config" button to apply the analysis results with one click.

## Deploy (Docker + Tailscale)

The app runs behind a Tailscale sidecar for private HTTPS on your Tailnet (not internet-facing).

1. Create a [GitHub OAuth App](https://github.com/settings/developers) with callback URL `https://survivor-fantasy.<tailnet>.ts.net/auth/callback`
2. Create `.env` with your credentials (see above)
3. `docker compose up -d`

Your friends on the Tailnet can access it at `https://survivor-fantasy.<tailnet>.ts.net`.

See `docker-compose.yml` and `serve.json` for the full config.

## Data Source

All game data (boot order, tribe assignments, challenge results, advantages, confessionals) comes from the [survivoR](https://github.com/doehm/survivoR) open-source dataset. Player profile links go to [survivorstatsdb.com](https://survivorstatsdb.com). Headshot images are from [fantasysurvivorgame.com](https://www.fantasysurvivorgame.com).

## Project Structure

```
app/
  __init__.py          # App factory, SQLAlchemy, Flask-Login, scheduler
  models.py            # User, Season, Survivor, Pick, SoleSurvivorPick
  routes.py            # Public leaderboard + admin routes
  auth.py              # GitHub OAuth + dev login
  data.py              # survivoR dataset refresh
  predictions.py       # Win probability simulations (cached)
  scheduler.py         # Weekly auto-refresh (Wednesday nights EST)
  scoring/
    base.py            # ScoringSystem base class
    classic.py         # Classic scoring with configurable components
  templates/           # Jinja2 + Pico CSS
  static/
    style.css
    scoring_analysis.json
analyze_scoring.py     # Scoring config optimizer
seed.py                # DB seeding from survivoR + legacy xlsx picks
config.py              # Flask config (env vars)
```
