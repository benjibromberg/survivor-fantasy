# Survivor Fantasy

A fantasy league web app for [Survivor](https://en.wikipedia.org/wiki/Survivor_(American_TV_series)). Draft castaways, earn points as they survive tribal councils, win challenges, and find advantages -- then compete with friends on a shared leaderboard.

Built for the modern era of Survivor (seasons 41+) where idols, advantages, and big moves drive the game.

## Features

- **Public leaderboard** with headshot images, tribe colors, point breakdowns, game stats, and castaway bios
- **Episode timeline** to navigate the leaderboard to any point in the season (with merge/finale milestones)
- **Season progression chart** showing cumulative point trends over time
- **Win probability predictions** with frozen and projected modes (per-player career immunity rates, idol protection modeling, historical rate projections)
- **Admin panel** (GitHub OAuth) for managing seasons, players, picks, and scoring configs — auto-fetches survivoR data on season creation, active toggle, delete support
- **Scoring analysis tool** that tested 25,000+ configurations across 9 modern seasons to find the most fun/fair settings
- **Auto-refresh** from the [survivoR](https://github.com/doehm/survivoR) open-source dataset (daily at 8am EST)
- **Finale celebration** with champion banner, ember animations, lit/snuffed torches, and Sole Survivor highlight
- **Character Journey Cards** with auto-generated narrative highlights (immunity wins, idol finds, votes survived, tribe swaps, merge) as compact badges + toggleable journey timeline on each pick pill
- **Winner badges** showing past season wins on player names across all leaderboards
- **Survivor-themed design** with Survivant logo font, Cinzel headings, Bebas Neue stats, tiki torch SVGs, and Tribal Council color palette
- **Auto-generated sidebar TOC** with scroll-based active section highlighting
- **Docker + Tailscale** deployment for private hosting on your Tailnet

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python seed.py                          # Download survivoR data + generate images
python seed.py --no-scrape              # Offline: skip network calls
python seed.py --picks-dir ./picks      # Also load pick assignments from JSON

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
SECRET_KEY=your-random-secret          # Required in production (app errors on startup if missing)
DEV_LOGIN=0          # Set to 1 to enable dev login (default: 1 locally, 0 in Docker)
TS_AUTHKEY=tskey-... # Tailscale auth key for Docker deployment
```

## How It Works

### Draft and Pick Types

Each fantasy player has a roster of Survivor castaways built through a snake draft. Wildcards are chosen **after the first episode** (first elimination) from any remaining castaway.

| Type | Scoring | When Picked |
| ---- | ------- | ----------- |
| **Draft** | Full points | Before the season (snake draft, 4 picks each) |
| **Wildcard** | Half points (0.5x) | After first elimination |
| **Replacement** | 0 pts (roster only) | After merge, if a pick was eliminated pre-merge |

If there are more players than the cast can support, the group is split into balanced sub-drafts so everyone still gets 4 picks. Replacement picks appear on your roster but don't earn points — they keep you engaged after losing a pick early.

### Sole Survivor Pick

Each player predicts the overall winner. Points are awarded based on how many consecutive episodes (ending at the finale) you had the correct winner picked. Longer streaks = more points.

### Scoring Components

All point values are configurable per season via the admin panel. The default config (optimized via scoring analysis):

| Component | Default | Description |
| --------- | ------- | ----------- |
| Progressive tribal | 0.5 base | Each tribal survived earns points, increasing in the finale phase (+0.5/tribal) |
| Jury | 3 | Bonus for making the jury |
| 1st / 2nd / 3rd | 5 / 1.5 / 1 | Placement bonuses |
| Idol / advantage found | 0.5 / 0.5 | Finding hidden immunity idols or advantages |
| Sole Survivor streak | 1 | Per episode in correct winner streak |

## Scoring Analysis

The included `analyze_scoring.py` tool evaluates scoring configurations for fun and fairness by simulating thousands of random snake drafts across modern Survivor seasons (41-49).

```bash
source venv/bin/activate
python analyze_scoring.py --quick                                    # Fast: 500 configs, 3 drafts/size
python analyze_scoring.py --export-json app/static/scoring_analysis.json  # Full: 25000 configs, 50 drafts/size
python analyze_scoring.py --samples 50000 --cores 14 --export-json ...   # Custom sample count + CPU cores
```

Uses two-phase optimization: Phase 1 broad sweep via stratified random sampling, then Phase 2 iterative neighborhood refinement (top 50 configs, ±2 param steps, 2 rounds).

Each configuration is scored on 12 metrics including:
- **Draft skill correlation** -- does drafting higher-lasting survivors correlate with winning?
- **Comeback rate** -- how often the midpoint leader does NOT win
- **Suspense** -- % of the season where the eventual winner is not in 1st
- **Blowout rate** -- how often 1st place scores more than double last place
- **Finale competitiveness** -- players within striking distance entering the finale
- **Rank volatility**, **late-game drama**, **midpoint competitiveness**, **recovery**, **longevity share**, **final spread**, **non-draft impact**

The admin panel has a "Load Recommended Config" button to apply the analysis results with one click.

## Deploy (Docker + Tailscale)

The app runs behind a Tailscale sidecar for private HTTPS on your Tailnet (not internet-facing). This follows the same pattern as the [Tailscale self-hosting guide for audiobookshelf](https://github.com/tailscale-dev/video-code-snippets/tree/main/2025/2025-06-self-hosting-part2/audiobookshelf).

1. Create a [GitHub OAuth App](https://github.com/settings/developers) with callback URL `https://survivor-fantasy.<tailnet>.ts.net/auth/callback`
2. Create `.env` with your credentials (see above)
3. `docker compose up -d`

Your friends on the Tailnet can access it at `https://survivor-fantasy.<tailnet>.ts.net`.

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for a detailed walkthrough including Proxmox setup.

## Data Source

All game data (boot order, tribe assignments, challenge results, advantages, confessionals) comes from the [survivoR](https://github.com/doehm/survivoR) open-source dataset. Player profile links go to [survivorstatsdb.com](https://survivorstatsdb.com). Headshot images are from [fantasysurvivorgame.com](https://www.fantasysurvivorgame.com).

## Project Structure

```
app/
  __init__.py          # App factory, SQLAlchemy, Flask-Login, scheduler
  models.py            # User, Season, Survivor, Pick, SoleSurvivorPick
  routes.py            # Public leaderboard, timeline, scoring compare, rules + admin routes
  auth.py              # GitHub OAuth + dev login
  data.py              # survivoR dataset refresh (stats, tribes, episodes, bios, images)
  highlights.py        # Character Journey Cards (auto-generated narrative events + badges)
  predictions.py       # Win probability simulations (frozen + projected modes, cached)
  scheduler.py         # Daily auto-refresh (8am EST)
  scoring/
    base.py            # ScoringSystem base class, PointBreakdown
    classic.py         # Classic scoring with configurable components
  templates/           # Jinja2 + Pico CSS v2
  static/
    style.css          # Tribal Council theme, font imports, Pico overrides
    fonts/survivant.ttf  # Official Survivor logo font
    scoring_analysis.json
analyze_scoring.py     # Scoring config optimizer
seed.py                # DB seeding from survivoR + JSON pick files
config.py              # Flask config (env vars)
Dockerfile             # Python 3.11-slim + gunicorn
docker-compose.yml     # App + Tailscale sidecar
serve.json             # Tailscale Serve config (HTTPS proxy to :5050)
DEPLOYMENT.md          # Full deployment guide for Proxmox + Docker + Tailscale
```
