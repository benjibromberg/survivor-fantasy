---
name: survivor-scoring-analysis
description: >-
  The scoring-config simulator in analyze_scoring.py for survivor-fantasy: running
  it (run_analysis.sh, or sharded with --shard / --merge-shards), the
  rule-constrained two-phase search, the placement check and composite metric, and
  publishing a finished run as a scoring version. Use when tuning scoring configs,
  running, reading or publishing an analysis run, or changing the simulator.
---

# survivor-scoring-analysis

A simulator run from the command line. The webapp imports it only for its
scenario constants and composite helpers (`app/league_metrics.py`), and runs it
as a subprocess (`--metrics-for`), never inside a request. It simulates fantasy
drafts over real seasons (`DEFAULT_SEASONS`, 41-49) with lightweight `SimSurvivor`
/ `SimSeason` objects built from `survivoR.xlsx`. The components, rules and
versions it works with: the `survivor-scoring` skill.

## Running

`run_analysis.sh` activates `venv` or `.venv`, then runs
`analyze_scoring.py --cores 14 --export-json app/static/scoring_analysis.json "$@"`.
That export path is the live page's data file: see "Putting a run on the site"
before a run you might publish. Arguments you pass come last and argparse keeps the
last value, so your `--cores` or `--export-json` override the script's.

```bash
./run_analysis.sh --quick              # 500 configs, 3 drafts per size, light refinement
./run_analysis.sh                      # 25,000 configs
./run_analysis.sh --samples 100000     # custom sample count
./run_analysis.sh --seasons 41,42,43   # 41+ only: older seasons raise an error
```

The hosts for long runs are in the local, uncommitted `CLAUDE.md`. Queue a long
run with a job runner, for example `pueue add --label run-N -- ./run_analysis.sh
--samples 100000`.

- `--cores N`: worker processes (default `cpu_count - 2`).
- `--reexport PATH --export-json PATH`: rebuild the chart JSON from a saved
  checkpoint without re-running.
- A full run or a merge job writes the checkpoint `scoring_results.json` (every
  config with its metrics, best first) to the working directory after Phase 2. It
  and `results/` are gitignored.
- `--help` says `--seasons` defaults to "all 1-49"; the real default is
  `DEFAULT_SEASONS`.
- A job that cannot find `survivoR.xlsx` downloads it, so stage the file before
  starting many jobs.
- `--metrics-for JSON --metrics-out PATH` (repeatable, paired in order)
  evaluates each config over the standard scenarios, writes one record per
  config atomically and stops: no sweep, report or export. Honours `--seasons`
  and `--quick`. The site runs it in the background (`app/league_metrics.py`);
  one config takes about 11 s and 150 MB.

### Sharded runs

One process pool is one machine. To spread Phase 1 across many:

- `--shard K/N` (0 <= K < N) builds the same deterministic config list, evaluates
  every Nth config starting at K with `--cores` workers, writes
  `<shard-dir>/shard-KKK-of-NNN.json` (zero-padded to three digits), and stops:
  no refinement, report, checkpoint or export.
- `--merge-shards N` rebuilds the config list, loads all N slice files from
  `--shard-dir` instead of evaluating, then carries on as a normal run: Phase 2 on
  its own workers, the report, `scoring_results.json`, and `--export-json` if
  given. It fails if a slice is missing, or if the slices' total differs from the
  list it built.
- `--shard-dir` defaults to `results/shards`; shard and merge jobs must agree on
  it. N is your choice.
- Give every job the same `--samples`, `--quick` and `--seasons`: only the total
  count is checked.

On a Slurm cluster: an array job runs `--shard i/N` for i in 0..N-1, then one job
that depends on it runs `--merge-shards N` with `--export-json`.

## The search

- **Phase 1**: `constrained_sample(PARAM_GRID, n)` does stratified random sampling
  over only the part of the grid that keeps `ORDERINGS`. Each ordering chain is
  drawn jointly from its valid combinations, so every sample is usable.
  `DEFAULT_PARAMS` and `LEGACY_PARAMS` (the current default and pre-site configs,
  restricted to the swept keys) are always appended.
- **Phase 2**: neighbourhood refinement. The top 50 configs each have every
  parameter moved up to ±2 grid steps, for 2 rounds (quick: top 5, ±1, 1 round).
  Neighbours that break `ORDERINGS` are skipped.
- **Ranges**: `PARAM_GRID` comments give the reasons. `final_tribal_val` reaches 5
  so the Final Tribal bonus alone can clear the jury bonus, and the idol and
  advantage values have finer steps so the idol chain has four distinct positive
  values to use. `sole_survivor_val` is not swept: `expand_config` defaults it
  to 0 and the report says so.
- **Placement order**: `placement_order(config, season)`, run once per season. On
  structural points only (tribals, jury, merge, placement, Final Tribal, fire; no
  immunity, idol or advantage points), every finalist must score at least as much
  as 4th place, with each of the three finalists tried as the fire winner. Metric
  `placement_order_ok`. 3rd outscoring 2nd (`third_beats_second`) is reported, not
  enforced. A season without a complete final four or a merge is not judged.
- **Penalty**: `composite -= CONSTRAINT_PENALTY * ((1 - placement_order_ok) +
  (1 - orderings_ok))`, with `CONSTRAINT_PENALTY` 100 and `placement_order_ok` the
  share of scenarios that pass. A config failing either check ranks below every
  config that passes both. `composite_unconstrained` keeps the score from before
  the penalty, so a failing config can still be compared on the metrics.
- **Baselines**: `find_config_rank(results, params)` finds a config by its swept
  parameters (`DEFAULT_PARAMS`, `LEGACY_PARAMS`); matching an expanded config
  against `DEFAULT_CONFIG` never succeeds. The report prints the current default
  and the legacy config beside the top 10, with their metrics and composite
  before penalties. The export carries `default_rank`, `default_composite`,
  `legacy_rank` and `legacy_composite`. Both baselines break `ORDERINGS`, so both
  are penalised.

## Composite metric (`evaluate_config`)

- `draft_skill_correlation` (2.0): Spearman's rho between draft quality and final
  rank.
- Longevity (2.0): `_longevity_score(longevity_share)`, the share of points from
  tribals. Linear up to 50%, flat across 50-80%, falling beyond. Flat so an
  immunity point costs nothing against a tribal point inside the band; the old
  linear reward up to 80% always optimised immunity to 0.
- `comeback_rate` (2.0): how often the leader at 50% does not win.
- `suspense` (2.0): share of steps where the eventual winner is not 1st.
- `1 - late_game_gap` at 75% of the season (1.5); `rank_volatility`, normalised
  and capped at 1 (1.5).
- `non_draft_pct` (1.5): points from wildcards and replacements, benefit capped at
  35%, penalised below 10%.
- `midpoint_competitive_pct`, `finale_competitive_pct`, `1 - final_spread` (1.0
  each).
- `early_loser_avg_rank` and `blowout_rate` (-1.0 each; a blowout is 1st scoring
  more than twice last).
- Minus a consistency penalty: the standard deviations of `comeback_rate` and
  `suspense` (1.0 each), `draft_skill_correlation` and `final_spread` (0.5 each).

The constraint penalty above comes last.

## Simulation and speed

- Every player drafts exactly 4 (`MIN_PICKS_PER_PLAYER`); when the pool is too
  small, balanced sub-drafts each draft the full pool. 4-10 players
  (`PLAYER_COUNTS`), 50 drafts per size per season.
- Comparison timelines take the draft closest to median suspense out of 50
  candidates per season.
- `_fast_score_total()` returns a float with no `PointBreakdown`;
  `_build_tribal_table()` precomputes tribal points per tribals-survived count;
  the timeline walks eliminations forward once instead of saving and restoring per
  step; each picked survivor is scored once per step;
  `calculate_leaderboard(return_breakdowns=True)` supplies the longevity and
  non-draft breakdowns.

## Putting a run on the site

The scoring analysis page (`/scoring-analysis`) draws its charts from
`app/static/scoring_analysis.json`, the newest run, and lists each run from
`analysis_runs()` with a link to its data. To publish run N+1 (the procedure in
the `history.py` docstring):

1. Copy the current `app/static/scoring_analysis.json` to
   `app/static/analysis/run-N-<date>.json`, and change run N's `analysis` in
   `SCORING_VERSIONS` to that path. `run_analysis.sh` overwrites the file in
   place, so copy it before the run, or recover it with
   `git show HEAD:app/static/scoring_analysis.json`.
2. Put the new export at `app/static/scoring_analysis.json`.
3. Append a version to `SCORING_VERSIONS` with `analysis` set to
   `"scoring_analysis.json"`, `configs_tested` set to the export's
   `total_configs`, and `config` a literal dict holding every `DEFAULT_CONFIG` key,
   matching the export's `recommended`.
4. Optionally add `app/templates/league_settings/versions/<key>.html` with the
   reasons for the change.
5. `tests/test_scoring_changes_page.py` checks Run 7 against
   `scoring_analysis.json` (`total_configs`, `recommended`), so it fails once the
   file holds a newer run. Move that check to the new version.

Publishing a version changes neither `DEFAULT_CONFIG` nor any season's scoring. A
season takes a version through the admin form or an adopted proposal.
