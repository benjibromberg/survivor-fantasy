---
name: survivor-data
description: >-
  survivoR.xlsx dataset reference for the survivor-fantasy app: which sheets and columns feed which fields, the US/new-era filters, castaway_id semantics, and what seed.py does. Use when reading or refreshing upstream data, adding a season, debugging a stat that looks wrong, or filing an issue upstream against doehm/survivoR.
---

# survivor-data

How this app reads the survivoR dataset, and how seeding works.

The *gotchas* about what these fields mean (Vote History columns, `elimination_episode`, merge detection, `max_episode`) deliberately stay in CLAUDE.md, because they cause bugs when absent and CLAUDE.md loads unconditionally. This file is the lookup table.

## survivoR dataset (`survivoR.xlsx`)

Built by `download_survivor_data()` (`app/data.py`) from the [doehm/survivoR](https://github.com/doehm/survivoR) GitHub repo, not downloaded as an xlsx. survivoR regenerates `dev/json/<table>.json` with every episode but its `dev/xlsx/survivoR.xlsx` export only now and then (in October 2026 the xlsx stayed at US51 episode 2 while the JSON had episode 4), so the app fetches the JSON table behind each sheet it reads and writes them into `survivoR.xlsx` under the same sheet names. Every reader still uses `pd.read_excel`.

- **Reading a new sheet** means adding it to `SURVIVOR_TABLES` in `app/data.py` (sheet name to `dev/json` table name). `tests/test_survivor_json.py` scans every `read_excel` call and fails until you do.
- **What the JSON cannot carry:** floats have at most 4 decimal places (`score_overall` 0.803225 in the xlsx is 0.8032), and a column that is empty in every row is absent from the sheet instead of empty. No column the app reads is empty across a whole table. Otherwise the sheets read back with the same columns, dtypes and values as survivoR's xlsx; row order differs, and nothing here depends on it.
- **The file is swapped in whole** (`_write_atomically`): every table is fetched and checked first, so a failed refresh keeps yesterday's file. A value the workbook cannot hold (an impossible date such as 2026-02-30, a control character, a nested list or object) fails the check, naming its table, row and column. Text starting with `=` is written as text, not a formula.
- **One commit per refresh:** `survivor_head_commit()` asks GitHub's API for the head of survivoR's `master` once, and every table is fetched at that SHA, never at `master` (raw.githubusercontent.com caches each file for five minutes, so a refresh just after an upstream push could otherwise mix commits). `download_survivor_data()` returns the SHA. If the lookup fails (GitHub allows 60 requests an hour per address without a token, then answers 403), every table comes from `master` as before, the log says the refresh is not pinned to one commit, and the function returns `SURVIVOR_BRANCH` instead of a SHA. A 5xx, 429, timeout or dropped connection is retried twice, after 2 s and 4 s, or after the reply's `Retry-After` if longer (over 30 s fails at once); any other 4xx fails at once.

Key sheets used by this app:

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
