---
name: survivor-scoring-analysis
description: >-
  The scoring-config simulator in analyze_scoring.py for survivor-fantasy: how to run it (run_analysis.sh, pueue on the WSL box), the two-phase optimisation, its performance tricks and the composite metric weights. Use when tuning scoring configs, reading an analysis run, or changing the simulator.
---

# survivor-scoring-analysis

The standalone scoring simulator. Nothing in the webapp imports it, so this is only needed when tuning or running the analysis.

The machine this runs on is named in `CLAUDE.md`, which is not committed.

## Scoring Analysis (`analyze_scoring.py`)

Standalone script that simulates fantasy drafts across real historical seasons (41-49) to evaluate scoring configs. Uses `SimSurvivor`/`SimSeason` lightweight objects loaded from survivoR.xlsx with per-episode cumulative stats.

**Running:** Always use `run_analysis.sh` (not `analyze_scoring.py` directly). It activates the venv, sets `--cores 14`, and exports chart data to the JSON file.

```bash
# On the WSL box (its host is in CLAUDE.md), queue via pueue:
pueue add --label "run-N-scoring" -- bash -c 'cd ~/survivor-fantasy && ./run_analysis.sh --samples 100000 2>&1 | tee scoring_runN.log'

# Quick test run (500 configs, 3 drafts/size)
./run_analysis.sh --quick

# Custom sample count
./run_analysis.sh --samples 50000

# Specific seasons only
./run_analysis.sh --seasons 41,42,43
```

**Two-phase optimization:**

1. **Phase 1**: Stratified random sampling of configs (default 25k, configurable via `--samples`). Each config evaluated across all draft scenarios using multiprocessing.
2. **Phase 2**: Iterative neighborhood refinement — top 50 configs perturbed ±2 param steps, evaluated, re-sorted, then refined again (2 rounds). Discovers fine-tuned optima near the best Phase 1 results.

**Performance optimizations:**

- `_fast_score_total()`: Returns float directly (no PointBreakdown/dict creation). ~2.5x faster than `score_pick`.
- `_build_tribal_table()`: Precomputes tribal points for each tribals_survived count (0..num_players).
- Forward-only timeline: Walks eliminations forward using `episode_stats`, avoids save/modify/restore per step.
- Per-survivor dedup: Scores each unique survivor once per timeline step, then applies pick-type multipliers.
- `return_breakdowns` mode: `calculate_leaderboard` returns per-pick breakdowns, reused for longevity share and non-draft impact metrics.

**Key metrics (composite weights):**

- `draft_skill_correlation` (2.0) — Spearman's rho: does drafting higher-lasting survivors correlate with winning?
- `longevity_share` (2.0) — % of total points from tribal survival
- `comeback_rate` (2.0) — can players recover from early losses?
- `suspense` (2.0) — % of season the eventual winner is NOT in 1st
- `late_game_gap` (1.5) — closeness at 75% of season
- `rank_volatility` (1.5) — do standings shift?
- `non_draft_pct` (1.5) — wildcards/SS picks contribute but don't dominate
- `midpoint_competitive_pct` (1.0) — players within striking distance at midpoint
- `finale_competitive_pct` (1.0) — players within 20% of leader entering the finale
- `final_spread` (1.0) — closeness of final result
- `early_loser_avg_rank` (-1.0) — penalize configs where losing one pick is fatal
- `blowout_rate` (-1.0) — penalize configs where 1st place scores >2x last place

**Draft simulation:** Every player always drafts exactly 4 castaways. When there are too many players for one pool, balanced sub-drafts split the group. Comparison timelines use median-suspense selection (50 candidates per season) to avoid cherry-picking outliers.
