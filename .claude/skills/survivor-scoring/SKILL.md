---
name: survivor-scoring
description: >-
  How survivor-fantasy scores a season and changes its scoring: the ScoringSystem
  base class, the Classic components and their flat and progressive tribal
  arithmetic, the league rules a config must keep, the scoring versions log, and
  putting a scoring change to a league vote. Use when changing how points are
  calculated, adding a component, reading or changing a season's scoring_config,
  adding a scoring version, or working on scoring proposals and the Scoring changes
  page.
---

# survivor-scoring

Two rules that bite stay in CLAUDE.md: tribal counting goes through
`Season.compute_tribals_survived`, and `PointBreakdown.total` is computed, never
assigned. This skill is the arithmetic and the machinery around it.

## Adding a scoring system (`app/scoring/`)

1. Subclass `ScoringSystem` in `base.py`: implement `name`, `description` and
   `calculate_survivor_points(survivor, season) -> PointBreakdown`.
2. Optionally override `apply_pick_modifier()` or `score_pick()` for pick types.
3. Register it in `SCORING_SYSTEMS` in `__init__.py`. `get_scoring_system(name,
   config)` falls back to Classic for an unknown name.

## Where a season's scoring comes from

- `Season.scoring_config` (JSON) is merged key by key over `DEFAULT_CONFIG`
  (`ClassicScoring(**config)`). A season created in the admin stores `{}`, so it
  scores under `DEFAULT_CONFIG` until a config is saved or a proposal adopted.
- `DEFAULT_CONFIG` in `classic.py` is the Run 6 config, with Sole Survivor on
  (`sole_survivor_val` 1). Run 7 is recorded only in `SCORING_VERSIONS`.
- Admin season page: **Save Settings** applies the form at once; ticking
  **Propose** sends it to a league vote instead. A "Load <name>" button per
  `SCORING_VERSIONS` entry fills the form. Neither path checks the league rules.
- `sole_survivor_val` 0 hides the season's Sole Survivor page and hub entry.

## Classic components (`classic.py`)

Every value is points, and 0 disables the component.

- Tribal survival, flat or progressive (next section).
- `jury_val` when `survivor.made_jury` (finalists are not jurors).
- `merge_val` for outlasting the merge.
- `first_val` / `second_val` / `third_val` by placement, plus `final_tribal_val`
  to each of the three finalists.
- `individual_immunity_val`, `tribal_immunity_val` per win.
- `idol_found_val`, `idol_play_val`, `advantage_found_val`, `advantage_play_val`
  per idol or advantage.
- `fire_win_val` for winning the final-four fire challenge.
- `sole_survivor_val`, Sole Survivor picks only:
  `calculate_sole_survivor_bonus(streak)` = streak × value, the streak being the
  consecutive episodes ending at the finale with the eventual winner picked.
- Pick types: `wildcard_multiplier`, `draft_replacement_multiplier` (`pmr_d`),
  `wc_replacement_multiplier` (`pmr_w`), `replacement_deduction`.

A new key goes in `DEFAULT_CONFIG` (the admin save iterates it) and
`CONFIG_LABELS` (the admin form, the Scoring changes diffs and `rule_readout` take
labels from it). The simulator has its own copy of this arithmetic
(`_build_tribal_table`, `_fast_score_total`, `_structural_points` in
`analyze_scoring.py`): change both. `tests/test_analyze_scoring.py` compares the
copy with `score_pick`.

## Tribal modes

- **Progressive** when `tribal_base` is set (not `None`). `tribal_val` and
  `post_merge_tribal_val` are then ignored. `DEFAULT_CONFIG` and every analysis
  version use this mode.
  - Pre-merge: tribal N = `tribal_base + (N-1) * tribal_step`.
  - Post-merge: continues from the last pre-merge value, growing by
    `post_merge_step` per tribal.
  - Finale: continues from the last post-merge value, growing by `finale_step`.
    It starts when `finale_size` players remain.
  - Each phase is a closed-form arithmetic sum, with no loops.
- **Flat** when `tribal_base` is `None` (`LEGACY_CONFIG`): `tribal_val` per
  pre-merge tribal, `post_merge_tribal_val` per post-merge one.
- With `merge_threshold` `None` (merge not reached), every tribal counts as
  pre-merge.

## League rules (`rules.py`)

`ORDERINGS` holds chains of config keys whose values must strictly decrease, the
last also above a floor unless the floor is `None`:

- `final_tribal_val` > `jury_val`
- `individual_immunity_val` > `tribal_immunity_val` > 0
- `idol_play_val` > `idol_found_val` > `advantage_play_val` >
  `advantage_found_val` > 0

`satisfies_orderings(config)` is the yes/no check. `analyze_scoring.py` imports it
and `ORDERINGS`, so the search and the site share one definition.
`rule_readout(config, CONFIG_LABELS)` returns each chain's labelled values, its
floor and `holds`; the Scoring changes page shows it as Holds or Broken for the
open proposal, else for the season's config. Nothing enforces the rules on save or
propose. `DEFAULT_CONFIG` breaks all three, and a test asserts that it does.

## Scoring versions (`history.py`)

`SCORING_VERSIONS` is an append-only log: Legacy, Run 6, Run 7. Each entry has
`key`, `name`, `date`, `configs_tested`, `analysis` (a path under `app/static/`,
or `None`), `note` and `config`, a frozen literal dict, never a reference to
`DEFAULT_CONFIG`. Never edit an old entry except to move its `analysis` path.

- `describe_config(config)` returns `(closest version, {key: (version value,
  config value)})`: the version with the fewest differing keys, compared over
  `DEFAULT_CONFIG`'s keys with missing ones filled from `DEFAULT_CONFIG`. A season
  that took Run 7 and changed one value reads as Run 7 plus that change.
- `analysis_runs()` returns the versions with an `analysis` path, newest first,
  for the scoring analysis page.
- An optional `app/templates/league_settings/versions/<key>.html` supplies the
  "Why it changed" prose on Scoring changes.
- Adding a version after an analysis run: the `survivor-scoring-analysis` skill,
  "Putting a run on the site".

## Proposals and the league vote (`app/proposals.py`)

`ScoringProposal` (`config`, `closes_at` in naive UTC, `status` open, adopted or
withdrawn, `replaced_config`) and `ScoringVote` live in `models.py`. Proposals are
never deleted, and each vote belongs to one proposal.

- **Open**: `propose(season, config, closes_at)`, with `closes_at` in the future.
  A season has at most one open proposal: proposing again withdraws the open one,
  and the new one starts with no votes.
- **Vote**: `cast_vote()`. Any signed-in user with a pick in the season may vote
  until `closes_at`, and voting again replaces their vote. `CHOICES`: `now`
  (switch this season), `next` (start next season), `keep`. The optional comment
  is cut to `COMMENT_MAX_LENGTH` (500). Every vote shows with its voter's name.
- **Tally**: `results(proposal, season, viewer)` gives counts per choice, how many
  of the eligible players voted, the viewer's own vote and why they cannot vote.
- **Adopt / withdraw**: admin buttons calling `adopt(season)` / `withdraw(season)`.
  Neither waits for `closes_at`, and the tally is advisory: `adopt` always applies
  the config to this season, with nothing automatic behind `next`. It copies the
  season's old `scoring_config` into `replaced_config` first.
- **History as stretches**: `_season_periods()` in `routes.py` makes each adopted
  proposal one stretch on its `replaced_config`, ending on its adoption date with
  its tally, followed by the current config. Adopting mid-season adds to a
  season's history instead of rewriting it. `_scoring_history()` folds
  neighbouring seasons on the same unchanged version into one row.
