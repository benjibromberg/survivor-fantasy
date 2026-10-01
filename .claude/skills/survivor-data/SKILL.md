---
name: survivor-data
description: >-
  survivoR.xlsx dataset reference for the survivor-fantasy app: which sheets and columns feed which fields, the US/new-era filters, castaway_id semantics, and what seed.py does. Use when reading or refreshing upstream data, adding a season, debugging a stat that looks wrong, or filing an issue upstream against doehm/survivoR.
---

# survivor-data

How this app reads the survivoR dataset, and how seeding works.

The *gotchas* about what these fields mean (Vote History columns, `elimination_episode`, merge detection, `max_episode`) deliberately stay in CLAUDE.md, because they cause bugs when absent and CLAUDE.md loads unconditionally. This file is the lookup table.

## survivoR dataset (`survivoR.xlsx`)

Downloaded from [doehm/survivoR](https://github.com/doehm/survivoR) GitHub repo. Multi-sheet Excel file with comprehensive Survivor data. Key sheets used by this app:

**Upstream validation:** R is available on this Mac (v4.4.3). Install the package (`install.packages("survivoR")`) to verify data against the R data frames directly — the xlsx is a derived export. When filing issues on doehm/survivoR, reference R data frame names (`boot_order`, `castaways`, etc.) as primary, xlsx as secondary. The repo uses testthat Edition 3 with emoji-prefixed test names and dplyr pipeline style.

| Sheet | Key Columns | Used For |
|---|---|---|
| **Castaways** | `season`, `castaway_id`, `full_name`, `short_name`, `order`, `result`, `jury`, `finalist`, `winner`, `n_cast`, `n_jury`, `n_finalists` | Season/survivor creation, elimination order, jury status |
| **Season Summary** | `season`, `season_name`, `n_cast`, `n_jury`, `n_finalists` | Season metadata (name, player count, jury size) |
| **Tribe Mapping** | `season`, `castaway_id`, `tribe`, `tribe_colour`, `episode`, `tribe_status` | Tribe assignments, merge detection (`tribe_status='Merged'`), tribe_status tracking |
| **Challenge Results** | `season`, `castaway_id`, `won_individual_immunity` | Individual/tribal immunity win counts |
| **Confessionals** | `season`, `castaway_id`, `episode`, `confessional_count`, `confessional_time` | Per-episode confessional stats |
| **Vote History** | `season`, `castaway_id` (voter), `vote_id` (voted for), `voted_out_id` (who left), `episode`, `vote_event` | Votes received, votes cast, correct votes (see "Vote History columns" above) |
| **Advantage Movement** | `season`, `castaway_id`, `advantage_id`, `event` (Found/Played) | Idol/advantage found/played counts, current idol holdings |
| **Advantage Details** | `advantage_id`, `advantage_type` | Distinguishing idols from non-idol advantages (Hidden Immunity Idol vs other types) |
| **Castaway Details** | `castaway_id`, `occupation`, `personality_type` | Bio data (MBTI, occupation) |

**Common patterns:**
- Filter to US version: `df[df['version'] == 'US']`
- Filter to new-era: `df[df['season'] >= 41]`
- `castaway_id` (e.g., `US0009`) is the persistent cross-season identifier for returning players
- `order` column in Castaways = elimination order (same as `voted_out_order` in our DB)
- In-progress seasons may have `NaN` for `n_jury`, `n_finalists` — stored as `None` in our DB
## Seed script (`seed.py`)

Loads seasons from the [survivoR](https://github.com/doehm/survivoR) open-source dataset (castaway data, tribes, stats, confessionals, vote history, challenge results, advantages). Optionally loads pick assignments from JSON files via `--picks-dir`. Uses `_build_nickname_map()` to resolve returning player names (e.g., Oscar → Ozzy).

It **drops every table**, so it is for local setup and rare rebuilds, not for adding a season. Without `--seasons` it builds `DEFAULT_SEASONS` plus every season that has a pick file in `--picks-dir`; pick files are discovered by name (`season*.json`, with `season{N}.json` winning over suffixed variants), and a file whose season was not built is skipped with a `WARNING`. Before dropping, it exports the current picks and exits non-zero if that export fails while picks exist. Pick file format and discovery rules: `picks/README.md`. The pick loader matches castaway names by prefix and only warns on a miss, so validate names against the season's castaways before trusting a load.
