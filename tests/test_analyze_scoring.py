"""Tests for analyze_scoring.py — config expansion, leaderboard, metrics, sampling."""

import json
import random
import tempfile

import pytest

from analyze_scoring import (
    CONSTRAINT_PENALTY,
    DEFAULT_PARAMS,
    PARAM_GRID,
    SimPick,
    SimSeason,
    SimSurvivor,
    _build_elim_to_episode,
    _build_tribal_table,
    _compute_sim_stat_overrides,
    _fast_score_total,
    _longevity_score,
    assign_extra_picks,
    build_chart_data,
    calculate_leaderboard,
    compute_ss_streaks,
    constrained_sample,
    evaluate_config,
    expand_config,
    find_config_rank,
    impact_params,
    load_shards,
    parse_shard,
    placement_order,
    rank_users,
    satisfies_orderings,
    shard_configs,
    shard_path,
    snake_draft,
    stratified_random_sample,
    write_shard,
)
from app.scoring.classic import DEFAULT_CONFIG, ClassicScoring

# ── Helpers ────────────────────────────────────────────────────────────────


def make_season(num_players=18, left_at_jury=8, n_finalists=3):
    """Build a minimal SimSeason with survivors."""
    survivors = []
    merge_threshold = num_players - left_at_jury  # 10

    for i in range(1, num_players + 1):
        # Jury: post-merge boots only, not finalists or winner
        made_jury = i > merge_threshold and i <= num_players - n_finalists
        survivors.append(
            SimSurvivor(
                id=i,
                name=f"Player{i}",
                voted_out_order=i,
                made_jury=made_jury,
                elimination_episode=i,
                episode_stats={
                    i: {
                        "ii": 0,
                        "ti": 0,
                        "idol": 0,
                        "idol_play": 0,
                        "adv": 0,
                        "adv_play": 0,
                    }
                    for i in range(1, num_players + 1)
                },
            )
        )
    return SimSeason(
        number=50,
        name="Test Season",
        num_players=num_players,
        left_at_jury=left_at_jury,
        n_finalists=n_finalists,
        survivors=survivors,
    )


def make_picks(survivors, user_ids, picks_per_user=3):
    """Build picks_by_user dict: snake draft across users."""
    picks_by_user = {uid: [] for uid in user_ids}
    available = list(reversed(survivors))  # best survivors first
    idx = 0
    for round_num in range(picks_per_user):
        order = user_ids if round_num % 2 == 0 else list(reversed(user_ids))
        for uid in order:
            if idx < len(available):
                picks_by_user[uid].append(SimPick(available[idx], "draft"))
                idx += 1
    return picks_by_user


# ── expand_config ──────────────────────────────────────────────────────────


class TestExpandConfig:
    def test_placement_ratio_conversion(self):
        config = {"first_val": 10, "placement_ratio": (0.5, 0.25)}
        result = expand_config(config)
        assert result["second_val"] == 5.0
        assert result["third_val"] == 2.5
        assert "placement_ratio" not in result

    def test_placement_ratio_winner_only(self):
        config = {"first_val": 15, "placement_ratio": (0, 0)}
        result = expand_config(config)
        assert result["second_val"] == 0
        assert result["third_val"] == 0

    def test_placement_ratio_snaps_to_half(self):
        # 10 * 0.33 = 3.3 → snaps to 3.5
        config = {"first_val": 10, "placement_ratio": (0.33, 0.17)}
        result = expand_config(config)
        assert result["second_val"] == 3.5
        assert result["third_val"] == 1.5  # 10 * 0.17 = 1.7 → round(3.4)/2 = 1.5

    def test_progressive_mode_removes_flat_keys(self):
        config = {
            "tribal_base": 1.0,
            "tribal_step": 0.5,
            "tribal_val": 0.5,
            "post_merge_tribal_val": 1.0,
        }
        result = expand_config(config)
        assert "tribal_val" not in result
        assert "post_merge_tribal_val" not in result
        assert result["tribal_base"] == 1.0

    def test_flat_mode_keeps_tribal_val(self):
        config = {"tribal_val": 0.5, "post_merge_tribal_val": 1.0}
        result = expand_config(config)
        assert result["tribal_val"] == 0.5
        assert result["post_merge_tribal_val"] == 1.0

    def test_no_placement_ratio_passthrough(self):
        config = {"first_val": 10, "jury_val": 2}
        result = expand_config(config)
        assert result["first_val"] == 10
        assert result["jury_val"] == 2


# ── calculate_leaderboard ─────────────────────────────────────────────────


class TestCalculateLeaderboard:
    def test_basic_scoring(self):
        season = make_season()
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2, 3])
        ss = {1: 0, 2: 0, 3: 0}
        lb = calculate_leaderboard(season, scoring, season.survivors, picks, ss)
        # User 1 got the best survivors (highest voted_out_order), should score highest
        assert lb[1] > lb[2]
        assert lb[2] > lb[3]

    def test_as_of_zero_all_zero(self):
        season = make_season()
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=0
        )
        # At step 0 (pre-game), wildcards are skipped and all survivors have 0 tribals
        assert all(v == 0 for v in lb.values())

    def test_as_of_excludes_future_eliminations(self):
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        # User 1 has the winner (voted_out_order=6), User 2 has first boot (=1)
        picks = {
            1: [SimPick(season.survivors[5], "draft")],  # winner
            2: [SimPick(season.survivors[0], "draft")],  # first boot
        }
        ss = {1: 0, 2: 0}
        # At as_of=1, the winner is still in game (voted_out_order reset to 0)
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=1
        )
        # Both should have similar scores (winner's elim not counted yet)
        assert lb[1] >= 0
        assert lb[2] == 0  # first boot survived 0 tribals

    def test_replacement_skipped_before_merge(self):
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = {
            1: [SimPick(season.survivors[5], "pmr_d")],
        }
        ss = {1: 0}
        merge_thresh = season.merge_threshold  # 3
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=merge_thresh - 1
        )
        assert lb[1] == 0  # replacement not active before merge

    def test_wildcard_skipped_at_step_zero(self):
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = {
            1: [SimPick(season.survivors[5], "wildcard")],
        }
        ss = {1: 0}
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=0
        )
        assert lb[1] == 0

    def test_elim_to_episode_passed_through(self):
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        elim_to_ep = _build_elim_to_episode(season.survivors)
        lb1 = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, elim_to_episode=elim_to_ep
        )
        lb2 = calculate_leaderboard(season, scoring, season.survivors, picks, ss)
        assert lb1 == lb2

    def test_ss_bonus_included_by_default(self):
        season = make_season(num_players=6, left_at_jury=3)
        config = {**DEFAULT_CONFIG, "sole_survivor_val": 2}
        scoring = ClassicScoring(**config)
        picks = {1: [SimPick(season.survivors[5], "draft")]}
        ss_with = {1: 5}
        ss_without = {1: 0}
        lb_with = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss_with
        )
        lb_without = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss_without
        )
        assert lb_with[1] > lb_without[1]
        assert lb_with[1] - lb_without[1] == 10  # 5 streak * 2 val

    def test_ss_bonus_excluded_for_intermediate_steps(self):
        season = make_season(num_players=6, left_at_jury=3)
        config = {**DEFAULT_CONFIG, "sole_survivor_val": 2}
        scoring = ClassicScoring(**config)
        picks = {1: [SimPick(season.survivors[5], "draft")]}
        ss = {1: 5}
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=3
        )
        lb_no_ss = calculate_leaderboard(
            season, scoring, season.survivors, picks, {1: 0}, as_of=3
        )
        assert lb[1] == lb_no_ss[1]  # SS excluded at intermediate steps


# ── stat_overrides ─────────────────────────────────────────────────────────


class TestStatOverrides:
    def test_compute_sim_stat_overrides(self):
        surv = SimSurvivor(
            id=1,
            name="Test",
            voted_out_order=15,
            made_jury=True,
            individual_immunity_wins=3,
            idols_found=1,
            episode_stats={
                5: {
                    "ii": 0,
                    "ti": 1,
                    "idol": 0,
                    "idol_play": 0,
                    "adv": 0,
                    "adv_play": 0,
                },
                10: {
                    "ii": 1,
                    "ti": 2,
                    "idol": 1,
                    "idol_play": 0,
                    "adv": 0,
                    "adv_play": 0,
                },
            },
        )
        overrides = _compute_sim_stat_overrides(surv, merge_episode=10)
        # Post-merge only: current - at_merge
        assert overrides["individual_immunity_wins"] == 2  # 3 - 1
        assert overrides["idols_found"] == 0  # 1 - 1

    def test_returns_none_without_episode_stats(self):
        surv = SimSurvivor(id=1, name="Test", voted_out_order=10, made_jury=True)
        surv.episode_stats = None
        assert _compute_sim_stat_overrides(surv, merge_episode=5) is None

    def test_returns_none_without_merge_episode(self):
        surv = SimSurvivor(id=1, name="Test", voted_out_order=10, made_jury=True)
        assert _compute_sim_stat_overrides(surv, merge_episode=0) is None


# ── stratified_random_sample ──────────────────────────────────────────────


class TestStratifiedRandomSample:
    def test_deterministic_with_seed(self):
        grid = {"a": [1, 2, 3], "b": [10, 20]}
        s1 = stratified_random_sample(grid, 12, seed=42)
        s2 = stratified_random_sample(grid, 12, seed=42)
        assert s1 == s2

    def test_different_seeds_differ(self):
        grid = {"a": [1, 2, 3], "b": [10, 20]}
        s1 = stratified_random_sample(grid, 12, seed=42)
        s2 = stratified_random_sample(grid, 12, seed=99)
        assert s1 != s2

    def test_even_distribution(self):
        grid = {"x": [1, 2, 3, 4]}
        samples = stratified_random_sample(grid, 100, seed=1)
        counts = {}
        for s in samples:
            counts[s["x"]] = counts.get(s["x"], 0) + 1
        # Each value should appear 25 times (100 / 4)
        assert all(c == 25 for c in counts.values())

    def test_correct_length(self):
        samples = stratified_random_sample(PARAM_GRID, 50, seed=1)
        assert len(samples) == 50

    def test_all_keys_present(self):
        samples = stratified_random_sample(PARAM_GRID, 10, seed=1)
        for s in samples:
            for key in PARAM_GRID:
                assert key in s

    def test_values_from_grid(self):
        samples = stratified_random_sample(PARAM_GRID, 20, seed=1)
        for s in samples:
            for key, val in s.items():
                assert val in PARAM_GRID[key], f"{key}={val} not in grid"


# ── Metric formulas ───────────────────────────────────────────────────────


class TestMetrics:
    def _run_evaluate(self, config_overrides=None):
        """Run evaluate_config with a small scenario set and return metrics."""
        season = make_season(num_players=10, left_at_jury=5, n_finalists=3)
        users = [101, 102, 103, 104]
        picks = make_picks(season.survivors, users, picks_per_user=2)
        ss = {uid: 0 for uid in users}
        max_elim = season.num_players
        scenario = (season, season.survivors, picks, ss, max_elim, len(users))

        config = {k: PARAM_GRID[k][0] for k in PARAM_GRID}
        if config_overrides:
            config.update(config_overrides)

        return evaluate_config(config, [scenario])

    def test_composite_is_float(self):
        result = self._run_evaluate()
        assert isinstance(result["composite"], float)

    def test_draft_skill_correlation_range(self):
        result = self._run_evaluate()
        assert "draft_skill_correlation" in result
        assert -1.0 <= result["draft_skill_correlation"] <= 1.0

    def test_longevity_share_range(self):
        result = self._run_evaluate()
        assert "longevity_share" in result
        assert 0 <= result["longevity_share"] <= 1.0

    def test_comeback_rate_range(self):
        result = self._run_evaluate()
        assert "comeback_rate" in result
        assert 0 <= result["comeback_rate"] <= 1.0

    def test_suspense_range(self):
        result = self._run_evaluate()
        assert "suspense" in result
        assert 0 <= result["suspense"] <= 1.0

    def test_high_tribal_config_has_high_longevity(self):
        """Config with high tribal values and zero bonuses should have high longevity_share."""
        result = self._run_evaluate(
            {
                "tribal_base": 2,
                "tribal_step": 1,
                "post_merge_step": 1.5,
                "finale_step": 2,
                "individual_immunity_val": 0,
                "tribal_immunity_val": 0,
                "idol_found_val": 0,
                "advantage_found_val": 0,
                "idol_play_val": 0,
                "advantage_play_val": 0,
                "jury_val": 0,
                "merge_val": 0,
                "final_tribal_val": 0,
                "fire_win_val": 0,
                "first_val": 0,
                "placement_ratio": (0, 0),
            }
        )
        assert "longevity_share" in result
        assert result["longevity_share"] > 0.9


# ── _build_elim_to_episode ────────────────────────────────────────────────


class TestBuildElimToEpisode:
    def test_basic(self):
        survivors = [
            SimSurvivor(
                id=1,
                name="A",
                voted_out_order=1,
                made_jury=False,
                elimination_episode=2,
            ),
            SimSurvivor(
                id=2,
                name="B",
                voted_out_order=2,
                made_jury=False,
                elimination_episode=3,
            ),
            SimSurvivor(id=3, name="C", voted_out_order=0, made_jury=False),
        ]
        result = _build_elim_to_episode(survivors)
        assert result == {1: 2, 2: 3}

    def test_skips_in_game(self):
        survivors = [
            SimSurvivor(id=1, name="A", voted_out_order=0, made_jury=False),
        ]
        assert _build_elim_to_episode(survivors) == {}


# ── rank_users ────────────────────────────────────────────────────────────


class TestRankUsers:
    def test_descending_order(self):
        lb = {1: 100, 2: 50, 3: 200}
        assert rank_users(lb) == [3, 1, 2]

    def test_empty(self):
        assert rank_users({}) == []


# ── Leaderboard as_of edge cases ─────────────────────────────────────────


class TestLeaderboardAsOfEdgeCases:
    def test_as_of_at_max_elim_matches_final(self):
        """as_of=max_elim should give same results as as_of=None (final)."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        lb_final = calculate_leaderboard(season, scoring, season.survivors, picks, ss)
        lb_as_of = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=6, include_ss=True
        )
        assert lb_final == lb_as_of

    def test_as_of_past_max_elim(self):
        """as_of beyond max eliminations should behave like final state."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        lb_final = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=6, include_ss=True
        )
        lb_past = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, as_of=100, include_ss=True
        )
        assert lb_final == lb_past

    def test_monotonic_scores(self):
        """Scores should generally increase or stay flat as as_of increases."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(
            tribal_val=1,
            first_val=0,
            merge_val=0,
            jury_val=0,
            individual_immunity_val=0,
            tribal_immunity_val=0,
            idol_found_val=0,
            advantage_found_val=0,
            idol_play_val=0,
            advantage_play_val=0,
            final_tribal_val=0,
            fire_win_val=0,
        )
        # User 1 picks the winner (survives longest)
        picks = {1: [SimPick(season.survivors[5], "draft")]}
        ss = {1: 0}
        prev_score = 0
        for step in range(1, 7):
            lb = calculate_leaderboard(
                season, scoring, season.survivors, picks, ss, as_of=step
            )
            assert lb[1] >= prev_score, f"Score decreased at step {step}"
            prev_score = lb[1]


# ── Stat override edge cases ─────────────────────────────────────────────


class TestStatOverrideEdgeCases:
    def test_merge_episode_not_in_stats(self):
        """merge_episode not in episode_stats → at_merge defaults to 0."""
        surv = SimSurvivor(
            id=1,
            name="Test",
            voted_out_order=15,
            made_jury=True,
            individual_immunity_wins=3,
            episode_stats={10: {"ii": 2}},  # only ep 10, merge at 5
        )
        overrides = _compute_sim_stat_overrides(surv, merge_episode=5)
        # ep 5 not in stats → merge data is {} → at_merge=0
        assert overrides["individual_immunity_wins"] == 3

    def test_current_less_than_at_merge_clamped(self):
        """max(0, current - at_merge) clamps to 0."""
        surv = SimSurvivor(
            id=1,
            name="Test",
            voted_out_order=15,
            made_jury=True,
            individual_immunity_wins=1,
            episode_stats={5: {"ii": 5}},
        )
        overrides = _compute_sim_stat_overrides(surv, merge_episode=5)
        assert overrides["individual_immunity_wins"] == 0  # max(0, 1-5)

    def test_missing_keys_default_zero(self):
        """Episode data with missing stat keys → those default to 0."""
        surv = SimSurvivor(
            id=1,
            name="Test",
            voted_out_order=15,
            made_jury=True,
            individual_immunity_wins=3,
            idols_found=2,
            episode_stats={5: {"ii": 1}},  # only 'ii', no 'idol'
        )
        overrides = _compute_sim_stat_overrides(surv, merge_episode=5)
        assert overrides["individual_immunity_wins"] == 2  # 3 - 1
        assert overrides["idols_found"] == 2  # 2 - 0 (missing key)


# ── _build_tribal_table ────────────────────────────────────────────────────


class TestBuildTribalTable:
    def test_flat_mode(self):
        """Flat mode: pre_merge_rate=1, post_merge_rate=2, merge at 10."""
        season = make_season(num_players=18, left_at_jury=8)
        config = {
            **DEFAULT_CONFIG,
            "tribal_val": 1,
            "post_merge_tribal_val": 2,
            "tribal_base": None,
        }
        table = _build_tribal_table(config, season)
        # k=0: no tribals
        assert table[0] == (0.0, 0.0)
        # k=5: 5 pre-merge tribals at rate 1 = 5
        assert table[5] == (5.0, 5.0)
        # k=10: 10 pre-merge at 1 = 10
        assert table[10] == (10.0, 10.0)
        # k=12: 10 pre-merge at 1 + 2 post-merge at 2 = 14, pre_merge = 10
        assert table[12] == (14.0, 10.0)

    def test_progressive_mode(self):
        """Progressive mode: base=1, step=0.5, merge at 10."""
        season = make_season(num_players=18, left_at_jury=8)
        config = {
            **DEFAULT_CONFIG,
            "tribal_base": 1,
            "tribal_step": 0.5,
            "post_merge_step": 1,
            "finale_step": 2,
            "finale_size": 5,
        }
        table = _build_tribal_table(config, season)
        assert table[0] == (0.0, 0.0)
        # k=1: base = 1
        assert table[1][0] == 1.0
        # k=2: 1 + 1.5 = 2.5
        assert table[2][0] == 2.5
        # Monotonically increasing
        for k in range(1, 18):
            assert table[k + 1][0] >= table[k][0]

    def test_matches_scoring_system(self):
        """Tribal table totals should match ClassicScoring for all tribals survived."""
        season = make_season(num_players=18, left_at_jury=8)
        config = {
            **DEFAULT_CONFIG,
            "tribal_base": 1.5,
            "tribal_step": 0.25,
            "post_merge_step": 0.5,
            "finale_step": 1.5,
            "finale_size": 5,
        }
        table = _build_tribal_table(config, season)
        scoring = ClassicScoring(**config)
        for k in range(0, 18):
            items = scoring._compute_tribal_points(k, season)
            expected_total = sum(items.values())
            expected_pre = items.get("pre_merge_tribal", 0)
            assert abs(table[k][0] - expected_total) < 1e-9, f"Total mismatch at k={k}"
            assert abs(table[k][1] - expected_pre) < 1e-9, (
                f"Pre-merge mismatch at k={k}"
            )

    def test_length(self):
        season = make_season(num_players=18, left_at_jury=8)
        config = {**DEFAULT_CONFIG, "tribal_base": 1, "tribal_step": 0}
        table = _build_tribal_table(config, season)
        assert len(table) == 19  # 0..18


# ── _fast_score_total ──────────────────────────────────────────────────────


class TestFastScoreTotal:
    def _make_config_and_table(self, **overrides):
        config = {**DEFAULT_CONFIG, **overrides}
        season = make_season(num_players=18, left_at_jury=8)
        table = _build_tribal_table(config, season)
        return config, season, table

    def test_matches_score_pick_draft(self):
        """Fast total for draft picks should match score_pick total."""
        config, season, table = self._make_config_and_table(
            tribal_base=1, tribal_step=0.5, post_merge_step=1, finale_step=2
        )
        scoring = ClassicScoring(**config)
        for s in season.survivors:
            fast = _fast_score_total(s, config, season, table)
            official, _ = scoring.score_pick(s, season, "draft")
            assert abs(fast - official) < 1e-9, (
                f"Mismatch for {s.name}: fast={fast}, official={official}"
            )

    def test_matches_score_pick_with_stats(self):
        """Fast total matches when survivor has performance stats."""
        config, season, table = self._make_config_and_table(
            tribal_base=1, tribal_step=0, post_merge_step=0, finale_step=0
        )
        scoring = ClassicScoring(**config)
        # Give the winner some stats
        winner = season.survivors[-1]  # voted_out_order = 18 = winner
        winner.individual_immunity_wins = 4
        winner.idols_found = 2
        winner.advantages_found = 3
        winner.advantages_played = 3
        winner.won_fire = True
        fast = _fast_score_total(winner, config, season, table)
        official, _ = scoring.score_pick(winner, season, "draft")
        assert abs(fast - official) < 1e-9

    def test_replacement_removes_pre_merge_and_merge(self):
        """is_replacement=True removes pre-merge tribal and merge bonus."""
        config, season, table = self._make_config_and_table(
            tribal_base=1, tribal_step=0, post_merge_step=0, finale_step=0, merge_val=5
        )
        # Survivor past merge (voted_out_order=15, merge at 10)
        surv = season.survivors[14]
        base = _fast_score_total(surv, config, season, table)
        repl = _fast_score_total(surv, config, season, table, is_replacement=True)
        pre_merge_tribal = table[surv.voted_out_order - 1][1]
        assert abs(base - repl - pre_merge_tribal - 5) < 1e-9  # 5 = merge_val

    def test_replacement_with_stat_overrides(self):
        """Replacement with stat_overrides uses override values for performance."""
        config, season, table = self._make_config_and_table(
            tribal_base=1,
            tribal_step=0,
            post_merge_step=0,
            finale_step=0,
            individual_immunity_val=3,
        )
        surv = season.survivors[14]
        surv.individual_immunity_wins = 5
        stat_ov = {
            "individual_immunity_wins": 2,
            "tribal_immunity_wins": 0,
            "idols_found": 0,
            "idols_played": 0,
            "advantages_found": 0,
            "advantages_played": 0,
        }
        repl = _fast_score_total(
            surv, config, season, table, stat_overrides=stat_ov, is_replacement=True
        )
        # Should use 2 immunities (override), not 5 (original)
        surv.individual_immunity_wins = 2
        scoring = ClassicScoring(**config)
        official, bd = scoring.score_pick(surv, season, "draft")
        official -= bd.items.get("pre_merge_tribal", 0) + bd.items.get("merge", 0)
        surv.individual_immunity_wins = 5  # restore
        assert abs(repl - official) < 1e-9

    def test_first_boot_zero(self):
        """First boot (voted_out_order=1) gets 0 tribals survived."""
        config, season, table = self._make_config_and_table(
            tribal_base=1,
            tribal_step=0.5,
            first_val=0,
            merge_val=0,
            jury_val=0,
            final_tribal_val=0,
            fire_win_val=0,
            individual_immunity_val=0,
            tribal_immunity_val=0,
            idol_found_val=0,
            advantage_found_val=0,
            idol_play_val=0,
            advantage_play_val=0,
        )
        surv = season.survivors[0]  # voted_out_order = 1
        fast = _fast_score_total(surv, config, season, table)
        assert fast == 0.0

    def test_flat_mode_matches_score_pick(self):
        """Fast total in flat tribal mode should match score_pick."""
        config = {
            **DEFAULT_CONFIG,
            "tribal_val": 1.5,
            "post_merge_tribal_val": 3,
            "tribal_base": None,
        }
        season = make_season(num_players=18, left_at_jury=8)
        table = _build_tribal_table(config, season)
        scoring = ClassicScoring(**config)
        for s in season.survivors:
            fast = _fast_score_total(s, config, season, table)
            official, _ = scoring.score_pick(s, season, "draft")
            assert abs(fast - official) < 1e-9, (
                f"Flat mode mismatch for {s.name}: fast={fast}, official={official}"
            )

    def test_wildcard_multiplier_applied_correctly(self):
        """Wildcard scoring: fast_base * wildcard_mult should match score_pick."""
        config, season, table = self._make_config_and_table(
            tribal_base=1,
            tribal_step=0.5,
            post_merge_step=1,
            finale_step=2,
            wildcard_multiplier=0.5,
        )
        scoring = ClassicScoring(**config)
        mult = config["wildcard_multiplier"]
        for s in season.survivors:
            fast_base = _fast_score_total(s, config, season, table)
            official, _ = scoring.score_pick(s, season, "wildcard")
            assert abs(fast_base * mult - official) < 1e-9, (
                f"Wildcard mismatch for {s.name}"
            )

    def test_replacement_with_overrides_matches_score_pick(self):
        """Full replacement path: fast + multiplier should match score_pick."""
        config, season, table = self._make_config_and_table(
            tribal_base=1,
            tribal_step=0.5,
            post_merge_step=1,
            finale_step=2,
            wc_replacement_multiplier=0.5,
            draft_replacement_multiplier=1.0,
            individual_immunity_val=3,
        )
        scoring = ClassicScoring(**config)
        # Post-merge survivor with stats
        surv = season.survivors[14]  # voted_out_order=15, past merge=10
        surv.individual_immunity_wins = 4
        surv.episode_stats = {
            i: {
                "ii": min(i, 4),
                "ti": 0,
                "idol": 0,
                "idol_play": 0,
                "adv": 0,
                "adv_play": 0,
            }
            for i in range(1, 19)
        }
        stat_ov = _compute_sim_stat_overrides(surv, merge_episode=10)
        # pmr_w path
        fast_repl = _fast_score_total(
            surv, config, season, table, stat_overrides=stat_ov, is_replacement=True
        )
        fast_modified = fast_repl * config["wc_replacement_multiplier"]
        official, _ = scoring.score_pick(surv, season, "pmr_w", stat_ov)
        assert abs(fast_modified - official) < 1e-9
        # pmr_d path (multiplier = 1.0)
        fast_d = fast_repl * config["draft_replacement_multiplier"]
        official_d, _ = scoring.score_pick(surv, season, "pmr_d", stat_ov)
        assert abs(fast_d - official_d) < 1e-9


# ── Timeline fidelity ──────────────────────────────────────────────────────


class TestTimelineFidelity:
    """Forward-walk timeline should produce same scores as calculate_leaderboard(as_of=N)."""

    def _make_timeline_season(self):
        """Build a season with realistic episode_stats for timeline testing."""
        num_players = 12
        left_at_jury = 5
        survivors = []
        for i in range(1, num_players + 1):
            merge_thresh = num_players - left_at_jury  # 7
            n_finalists = 3
            made_jury = i > merge_thresh and i <= num_players - n_finalists
            # Build cumulative episode_stats
            ep_stats = {}
            for ep in range(1, num_players + 1):
                ep_stats[ep] = {
                    "ii": 1 if (i + ep) % 5 == 0 and ep <= i else 0,
                    "ti": 1 if ep <= 3 and i > 6 else 0,
                    "idol": 1 if i == 10 and ep == 4 else 0,
                    "idol_play": 1 if i == 10 and ep == 7 else 0,
                    "adv": 1 if i == 8 and ep == 6 else 0,
                    "adv_play": 1 if i == 8 and ep == 8 else 0,
                }
                # Make cumulative
                if ep > 1:
                    for k in ep_stats[ep]:
                        ep_stats[ep][k] += ep_stats[ep - 1].get(k, 0)

            # Survivor 10 won fire-making (eliminated at position 9 = fire_elim)
            won_fire = i == 10
            survivors.append(
                SimSurvivor(
                    id=i,
                    name=f"S{i}",
                    voted_out_order=i,
                    made_jury=made_jury,
                    individual_immunity_wins=ep_stats[num_players]["ii"],
                    tribal_immunity_wins=ep_stats[num_players]["ti"],
                    idols_found=ep_stats[num_players]["idol"],
                    idols_played=ep_stats[num_players]["idol_play"],
                    advantages_found=ep_stats[num_players]["adv"],
                    advantages_played=ep_stats[num_players]["adv_play"],
                    won_fire=won_fire,
                    elimination_episode=i,
                    episode_stats=ep_stats,
                )
            )

        season = SimSeason(
            number=99,
            name="Timeline Test",
            num_players=num_players,
            left_at_jury=left_at_jury,
            n_finalists=3,
            survivors=survivors,
        )
        return season

    def test_forward_walk_matches_as_of(self):
        """Forward walk per-step scores should match calculate_leaderboard(as_of=N)."""
        season = self._make_timeline_season()
        config = {
            **DEFAULT_CONFIG,
            "tribal_base": 1,
            "tribal_step": 0.5,
            "post_merge_step": 1,
            "finale_step": 2,
            "finale_size": 5,
        }
        scoring = ClassicScoring(**config)
        fast_config = scoring.config

        # Set up picks: 3 players, draft picks + one wildcard
        picks_by_user = make_picks(season.survivors, [0, 1, 2], picks_per_user=3)
        # Add a wildcard (player 0 gets survivor 5 as wildcard)
        picks_by_user[0].append(SimPick(season.survivors[4], "wildcard"))
        # Add a replacement (player 1 gets survivor 9 as pmr_d, post-merge)
        picks_by_user[1].append(SimPick(season.survivors[8], "pmr_d"))

        ss_streaks = {0: 0, 1: 0, 2: 0}
        max_elim = season.num_players
        elim_to_ep = _build_elim_to_episode(season.survivors)
        merge_thresh = season.merge_threshold
        merge_ep = elim_to_ep.get(merge_thresh, 0)

        # Method 1: calculate_leaderboard(as_of=N) for each step
        as_of_results = {}
        for step in range(1, max_elim + 1):
            lb = calculate_leaderboard(
                season,
                scoring,
                season.survivors,
                picks_by_user,
                ss_streaks,
                as_of=step,
                include_ss=False,
                elim_to_episode=elim_to_ep,
            )
            as_of_results[step] = lb

        # Method 2: Forward walk (same logic as evaluate_config)
        tribal_table = _build_tribal_table(fast_config, season)
        _STAT_FIELDS = (
            "individual_immunity_wins",
            "tribal_immunity_wins",
            "idols_found",
            "idols_played",
            "advantages_found",
            "advantages_played",
        )
        orig_state = {}
        for s in season.survivors:
            orig_state[s.id] = (
                s.voted_out_order,
                s.made_jury,
                getattr(s, "won_fire", False),
                {f: getattr(s, f) for f in _STAT_FIELDS},
            )
            s.voted_out_order = 0
            s.made_jury = False
            s.won_fire = False
            for f in _STAT_FIELDS:
                setattr(s, f, 0)

        n_fin = season.n_finalists
        fire_elim = season.num_players - n_fin

        surv_by_elim = sorted(
            [s for s in season.survivors if orig_state[s.id][0] > 0],
            key=lambda s: orig_state[s.id][0],
        )

        picked_sids = set()
        replacement_sids = set()
        for picks in picks_by_user.values():
            for pick in picks:
                picked_sids.add(pick.survivor.id)
                if pick.pick_type in ("pmr_w", "pmr_d"):
                    replacement_sids.add(pick.survivor.id)
        picked_survivors = [s for s in season.survivors if s.id in picked_sids]
        surv_map = {s.id: s for s in season.survivors}
        wildcard_mult = fast_config.get("wildcard_multiplier", 0.5)
        draft_repl_mult = fast_config.get("draft_replacement_multiplier", 1.0)
        wc_repl_mult = fast_config.get(
            "wc_replacement_multiplier", fast_config.get("replacement_multiplier", 0.5)
        )
        pre_jury = season.num_players - season.left_at_jury

        elim_idx = 0
        ep_key_map = {
            "ii": "individual_immunity_wins",
            "ti": "tribal_immunity_wins",
            "idol": "idols_found",
            "idol_play": "idols_played",
            "adv": "advantages_found",
            "adv_play": "advantages_played",
        }

        for elim in range(1, max_elim + 1):
            while (
                elim_idx < len(surv_by_elim)
                and orig_state[surv_by_elim[elim_idx].id][0] == elim
            ):
                s = surv_by_elim[elim_idx]
                s.voted_out_order = orig_state[s.id][0]
                finalist_threshold = season.num_players - (season.n_finalists)
                if (
                    s.voted_out_order > merge_thresh
                    and s.voted_out_order <= finalist_threshold
                ):
                    s.made_jury = True
                else:
                    s.made_jury = orig_state[s.id][1]
                elim_idx += 1

            # Time-gate won_fire: only credited at or after fire_elim step
            if elim >= fire_elim:
                for s in season.survivors:
                    s.won_fire = orig_state[s.id][2]
            else:
                for s in season.survivors:
                    s.won_fire = False

            target_episode = elim_to_ep.get(elim, 0)
            if target_episode > 0:
                for s in season.survivors:
                    if s.episode_stats:
                        ep_data = s.episode_stats.get(target_episode, {})
                        for ek, attr in ep_key_map.items():
                            setattr(s, attr, ep_data.get(ek, 0))

            base_scores = {}
            for s in picked_survivors:
                base_scores[s.id] = _fast_score_total(
                    s, fast_config, season, tribal_table
                )
            repl_scores = {}
            if elim >= merge_thresh and replacement_sids:
                for sid in replacement_sids:
                    s = surv_map[sid]
                    stat_ov = _compute_sim_stat_overrides(s, merge_ep)
                    if stat_ov:
                        repl_scores[sid] = _fast_score_total(
                            s,
                            fast_config,
                            season,
                            tribal_table,
                            stat_overrides=stat_ov,
                            is_replacement=True,
                        )

            forward_lb = {}
            for user_id, picks in picks_by_user.items():
                total = 0
                for pick in picks:
                    if pick.pick_type == "wildcard" and elim == 0:
                        continue
                    if pick.pick_type in ("pmr_w", "pmr_d") and elim < merge_thresh:
                        continue
                    sid = pick.survivor.id
                    if pick.pick_type in ("pmr_w", "pmr_d"):
                        if sid in repl_scores:
                            base = repl_scores[sid]
                        else:
                            # Always deduct pre-merge tribals
                            base = base_scores.get(sid, 0) - pre_jury
                        mult = (
                            wc_repl_mult
                            if pick.pick_type == "pmr_w"
                            else draft_repl_mult
                        )
                    elif pick.pick_type == "wildcard":
                        base = base_scores.get(sid, 0)
                        mult = wildcard_mult
                    else:
                        base = base_scores.get(sid, 0)
                        mult = 1.0
                    total += base * mult
                forward_lb[user_id] = total

            # Compare forward walk vs calculate_leaderboard(as_of)
            for uid in picks_by_user:
                expected = as_of_results[elim].get(uid, 0)
                actual = forward_lb.get(uid, 0)
                assert abs(expected - actual) < 1e-6, (
                    f"Step {elim}, user {uid}: as_of={expected}, forward={actual}"
                )

        # Restore
        for s in season.survivors:
            if s.id in orig_state:
                s.voted_out_order, s.made_jury, s.won_fire, saved = orig_state[s.id]
                for f, val in saved.items():
                    setattr(s, f, val)


# ── return_breakdowns ──────────────────────────────────────────────────────


class TestReturnBreakdowns:
    def test_breakdowns_returned_when_requested(self):
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        result = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, return_breakdowns=True
        )
        assert isinstance(result, tuple)
        lb, breakdowns = result
        assert set(lb.keys()) == {1, 2}
        assert set(breakdowns.keys()) == {1, 2}

    def test_breakdown_totals_match_leaderboard(self):
        """Sum of modified values in breakdowns should match leaderboard total."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        lb, breakdowns = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, return_breakdowns=True
        )
        for uid in lb:
            pick_total = sum(modified for modified, _, _ in breakdowns[uid])
            # lb includes SS bonus, breakdowns don't
            ss_bonus = scoring.calculate_sole_survivor_bonus(ss.get(uid, 0))
            assert abs(lb[uid] - pick_total - ss_bonus) < 1e-9

    def test_breakdown_pick_types_correct(self):
        """Each breakdown entry should have the right pick type."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = {
            1: [
                SimPick(season.survivors[5], "draft"),
                SimPick(season.survivors[3], "wildcard"),
            ]
        }
        ss = {1: 0}
        _, breakdowns = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, return_breakdowns=True
        )
        types = [pt for _, _, pt in breakdowns[1]]
        assert "draft" in types
        assert "wildcard" in types

    def test_without_breakdowns_returns_dict(self):
        """Without return_breakdowns, returns plain dict (not tuple)."""
        season = make_season(num_players=6, left_at_jury=3)
        scoring = ClassicScoring(**DEFAULT_CONFIG)
        picks = make_picks(season.survivors, [1, 2])
        ss = {1: 0, 2: 0}
        result = calculate_leaderboard(
            season, scoring, season.survivors, picks, ss, return_breakdowns=False
        )
        assert isinstance(result, dict)


# ── assign_extra_picks ─────────────────────────────────────────────────────


class TestAssignExtraPicks:
    def _make_draft_season(self):
        season = make_season(num_players=12, left_at_jury=5)
        rng = random.Random(42)
        picks = snake_draft(season.survivors, 4, rng)
        picks_by_user = {
            pid: [SimPick(s, "draft") for s in survs] for pid, survs in picks.items()
        }
        return season, picks_by_user, rng

    def test_everyone_gets_wildcard(self):
        season, picks_by_user, rng = self._make_draft_season()
        assign_extra_picks(season, picks_by_user, rng)
        for pid in picks_by_user:
            wc = [p for p in picks_by_user[pid] if p.pick_type == "wildcard"]
            assert len(wc) == 1

    def test_wildcard_not_from_own_roster(self):
        season, picks_by_user, rng = self._make_draft_season()
        assign_extra_picks(season, picks_by_user, rng)
        for pid in picks_by_user:
            own_draft_ids = {
                p.survivor.id for p in picks_by_user[pid] if p.pick_type == "draft"
            }
            wc = [p for p in picks_by_user[pid] if p.pick_type == "wildcard"]
            for p in wc:
                assert p.survivor.id not in own_draft_ids

    def test_wildcard_not_first_boot(self):
        season, picks_by_user, rng = self._make_draft_season()
        assign_extra_picks(season, picks_by_user, rng)
        for pid in picks_by_user:
            wc = [p for p in picks_by_user[pid] if p.pick_type == "wildcard"]
            for p in wc:
                assert p.survivor.voted_out_order != 1

    def test_replacement_given_when_draft_eliminated_pre_merge(self):
        """Player whose draft pick was eliminated pre-merge gets pmr_d replacement."""
        season = make_season(num_players=12, left_at_jury=5)
        # merge_threshold is 7
        # Player 0 has a draft pick eliminated at position 3 (pre-merge)
        picks_by_user = {
            0: [
                SimPick(season.survivors[2], "draft"),  # elim 3, pre-merge
                SimPick(season.survivors[10], "draft"),
            ],  # elim 11, post-merge
        }
        rng = random.Random(42)
        assign_extra_picks(season, picks_by_user, rng)
        repl = [p for p in picks_by_user[0] if p.pick_type == "pmr_d"]
        assert len(repl) == 1

    def test_replacement_pmr_w_when_only_wildcard_eliminated(self):
        """If only wildcard eliminated pre-merge, replacement is pmr_w."""
        season = make_season(num_players=12, left_at_jury=5)
        # All draft picks survive, but wildcard is eliminated pre-merge
        picks_by_user = {
            0: [
                SimPick(season.survivors[10], "draft"),  # elim 11, post-merge
                SimPick(season.survivors[2], "wildcard"),
            ],  # elim 3, pre-merge
        }
        rng = random.Random(42)
        assign_extra_picks(season, picks_by_user, rng)
        repl = [p for p in picks_by_user[0] if p.pick_type == "pmr_w"]
        assert len(repl) == 1

    def test_no_replacement_when_no_pre_merge_loss(self):
        """No replacement if all picks (including wildcard) survive past merge."""
        season = make_season(num_players=12, left_at_jury=5)
        # Pre-assign wildcard that also survives past merge (elim 8 > merge=7)
        picks_by_user = {
            0: [
                SimPick(season.survivors[10], "draft"),  # elim 11
                SimPick(season.survivors[9], "draft"),  # elim 10
                SimPick(season.survivors[7], "wildcard"),
            ],  # elim 8, post-merge
        }
        # Only check replacement logic — wildcard already assigned
        merge_thresh = season.merge_threshold
        draft_elim_pre = False
        wc_elim_pre = False
        for p in picks_by_user[0]:
            vo = p.survivor.voted_out_order
            if vo and 0 < vo <= merge_thresh:
                if p.pick_type == "draft":
                    draft_elim_pre = True
                elif p.pick_type == "wildcard":
                    wc_elim_pre = True
        assert not draft_elim_pre
        assert not wc_elim_pre


# ── compute_ss_streaks ─────────────────────────────────────────────────────


class TestComputeSsStreaks:
    def _make_ss_season(self):
        return make_season(num_players=12, left_at_jury=5, n_finalists=3)

    def test_returns_all_players(self):
        season = self._make_ss_season()
        picks = {
            0: [SimPick(season.survivors[11], "draft")],
            1: [SimPick(season.survivors[0], "draft")],
        }
        rng = random.Random(42)
        streaks = compute_ss_streaks(season, picks, rng)
        assert set(streaks.keys()) == {0, 1}

    def test_streak_non_negative(self):
        season = self._make_ss_season()
        picks = {i: [SimPick(season.survivors[i], "draft")] for i in range(4)}
        rng = random.Random(42)
        streaks = compute_ss_streaks(season, picks, rng)
        for streak in streaks.values():
            assert streak >= 0

    def test_max_streak_bounded(self):
        """Streak can't exceed num_players + 1 (all steps including step 0)."""
        season = self._make_ss_season()
        picks = {0: [SimPick(season.survivors[11], "draft")]}
        rng = random.Random(42)
        streaks = compute_ss_streaks(season, picks, rng)
        assert streaks[0] <= season.num_players + 1

    def test_deterministic_with_seed(self):
        season = self._make_ss_season()
        picks = {i: [SimPick(season.survivors[i], "draft")] for i in range(4)}
        s1 = compute_ss_streaks(season, picks, random.Random(42))
        s2 = compute_ss_streaks(season, picks, random.Random(42))
        assert s1 == s2

    def test_no_winner_all_zero(self):
        """If no winner exists (all voted_out_order < num_players), all streaks 0."""
        survivors = [
            SimSurvivor(id=i, name=f"S{i}", voted_out_order=i, made_jury=False)
            for i in range(1, 6)  # max voted_out = 5, but num_players = 10
        ]
        season = SimSeason(
            number=1,
            name="No Winner",
            num_players=10,
            left_at_jury=4,
            n_finalists=3,
            survivors=survivors,
        )
        picks = {0: [SimPick(survivors[0], "draft")]}
        streaks = compute_ss_streaks(season, picks, random.Random(42))
        assert streaks[0] == 0


# ── snake_draft ────────────────────────────────────────────────────────────


class TestSnakeDraft:
    def test_all_survivors_drafted(self):
        season = make_season(num_players=12, left_at_jury=5)
        rng = random.Random(42)
        picks = snake_draft(season.survivors, 4, rng)
        all_drafted = set()
        for survs in picks.values():
            for s in survs:
                all_drafted.add(s.id)
        assert all_drafted == {s.id for s in season.survivors}

    def test_no_duplicates(self):
        season = make_season(num_players=12, left_at_jury=5)
        rng = random.Random(42)
        picks = snake_draft(season.survivors, 4, rng)
        all_ids = [s.id for survs in picks.values() for s in survs]
        assert len(all_ids) == len(set(all_ids))

    def test_balanced_picks(self):
        """Each player should have floor or ceil of n_survivors / n_players."""
        season = make_season(num_players=12, left_at_jury=5)
        rng = random.Random(42)
        picks = snake_draft(season.survivors, 5, rng)
        counts = [len(survs) for survs in picks.values()]
        assert max(counts) - min(counts) <= 1


# ── Checkpoint & Export ────────────────────────────────────────────────────


class TestCheckpointAndExport:
    """Tests for results checkpoint saving and re-export."""

    def _make_results(self):
        """Build minimal results list for testing."""
        configs = [
            {
                "tribal_base": 1,
                "tribal_step": 0.5,
                "post_merge_step": 1,
                "finale_step": 2,
                "first_val": 10,
                "placement_ratio": (0.5, 0.2),
            },
            {
                "tribal_base": None,
                "tribal_step": 0,
                "post_merge_step": 0,
                "finale_step": 0,
                "first_val": 15,
                "placement_ratio": (0.5, 0.2),
            },
        ]
        results = []
        for i, cfg in enumerate(configs):
            metrics = {
                "composite": 6.0 - i * 0.1,
                "draft_skill_correlation": 0.6,
                "longevity_share": 1.1,
                "rank_volatility": 0.2,
                "comeback_rate": 0.25,
                "suspense": 0.24,
                "lead_changes": 0.07,
                "early_loser_avg_rank": 0.58,
                "midpoint_competitive_pct": 0.29,
                "late_game_gap": 0.30,
                "final_spread": 0.72,
                "non_draft_pct": 0.19,
            }
            results.append((cfg, metrics))
        return results

    def test_checkpoint_roundtrip(self):
        """Checkpoint save and reload produces identical results."""
        results = self._make_results()
        checkpoint = [{"config": c, "metrics": m} for c, m in results]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(checkpoint, f)
            path = f.name
        with open(path) as f:
            loaded = json.load(f)
        reloaded = [(r["config"], r["metrics"]) for r in loaded]
        assert len(reloaded) == len(results)
        assert reloaded[0][1]["composite"] == results[0][1]["composite"]

    def test_build_chart_data_handles_none_param(self):
        """build_chart_data doesn't crash when a param value is None."""
        results = self._make_results()
        export = build_chart_data(results, timelines=[])
        assert "recommended" in export
        assert "param_impact" in export

    def test_build_chart_data_finds_the_current_default(self):
        results = self._make_results()
        results.append((dict(DEFAULT_PARAMS), {**results[0][1], "composite": -1.0}))

        export = build_chart_data(results, timelines=[])

        assert export["default_rank"] == len(results)
        assert export["default_composite"] == -1.0

    def test_build_chart_data_structure(self):
        """Export JSON has expected top-level keys."""
        results = self._make_results()
        export = build_chart_data(results, timelines=[])
        for key in [
            "total_configs",
            "param_impact",
            "histogram",
            "top10",
            "recommended",
            "timelines",
        ]:
            assert key in export


# ── Placement order: no finalist below 4th place ───────────────────────────


def make_finale_season():
    """Ten castaways; the three finalists leave on the same final day.

    Orders 1-7 go out on days 1-7 (order 7 is 4th place, a juror who lost
    fire); orders 8-10 are the finalists, all on day 8. So 4th place survived
    6 tribals and each finalist 7, as in a real season.
    """
    season = make_season(num_players=10, left_at_jury=5, n_finalists=3)
    for s in season.survivors:
        s.day_voted_out = s.voted_out_order if s.voted_out_order <= 7 else 8
    return season


class TestPlacementOrder:
    def test_current_default_lets_fourth_outscore_a_finalist(self):
        """Jury 3 beats final tribal 2 plus one more 0.5 tribal."""
        ok, _ = placement_order(dict(DEFAULT_CONFIG), make_finale_season())

        assert ok is False

    def test_a_final_tribal_bonus_above_the_jury_bonus_passes(self):
        config = {**DEFAULT_CONFIG, "final_tribal_val": 4}

        ok, _ = placement_order(config, make_finale_season())

        assert ok is True

    def test_must_hold_whichever_finalist_wins_fire(self):
        """2nd clears 4th on its own; 3rd only does when 3rd wins fire."""
        config = {**DEFAULT_CONFIG, "second_val": 1}

        ok, _ = placement_order(config, make_finale_season())

        assert ok is False

    def test_performance_stats_are_left_out(self):
        season = make_finale_season()
        fourth = next(s for s in season.survivors if s.voted_out_order == 7)
        fourth.idols_played = 5
        for s in season.survivors:
            if s.voted_out_order >= 8:
                s.individual_immunity_wins = 4
        passing = {**DEFAULT_CONFIG, "final_tribal_val": 4, "idol_play_val": 1}
        failing = {**DEFAULT_CONFIG, "individual_immunity_val": 1}

        assert placement_order(passing, season)[0] is True
        assert placement_order(failing, season)[0] is False

    def test_third_beating_second_is_reported_not_required(self):
        fire_lifts_third = {**DEFAULT_CONFIG, "final_tribal_val": 4}
        runner_up_bonus = {**fire_lifts_third, "second_val": 3}

        assert placement_order(fire_lifts_third, make_finale_season()) == (True, True)
        assert placement_order(runner_up_bonus, make_finale_season()) == (True, False)

    def test_a_season_without_a_full_final_four_is_not_judged(self):
        season = make_finale_season()
        for s in season.survivors:
            if s.voted_out_order >= 7:
                s.voted_out_order = 0

        assert placement_order(dict(DEFAULT_CONFIG), season) == (True, False)


class TestPlacementOrderInComposite:
    def _evaluate(self, season=None, **overrides):
        season = season or make_finale_season()
        users = [101, 102, 103, 104]
        picks = make_picks(season.survivors, users, picks_per_user=2)
        scenario = (
            season,
            season.survivors,
            picks,
            {uid: 0 for uid in users},
            season.num_players,
            len(users),
        )
        config = {**ordered_base_config(), **overrides}
        return evaluate_config(config, [scenario])

    def test_failing_config_is_penalised(self):
        """Jury 3 over FTC 0 also breaks FTC > jury: one penalty per broken rule.

        Under the orderings the placement check cannot fail alone: with the
        FTC bonus above the jury bonus a finalist always clears 4th place.
        """
        result = self._evaluate(jury_val=3, final_tribal_val=0)

        assert result["placement_order_ok"] == 0.0
        assert result["orderings_ok"] == 0.0
        assert result["composite"] == pytest.approx(
            result["composite_unconstrained"] - 2 * CONSTRAINT_PENALTY
        )

    def test_passing_config_is_not_penalised(self):
        result = self._evaluate(jury_val=3, final_tribal_val=4)

        assert result["placement_order_ok"] == 1.0
        assert result["composite"] == pytest.approx(result["composite_unconstrained"])

    def test_check_runs_on_real_state_not_the_reset_timeline(self):
        """evaluate_config zeroes castaways to replay the season; the check must not."""
        season = make_finale_season()
        result = self._evaluate(season, jury_val=3, final_tribal_val=0)

        # A check run on the zeroed state would see no final four and pass
        assert result["placement_order_ok"] == 0.0
        assert [s.voted_out_order for s in season.survivors] == list(range(1, 11))


class TestLongevityScore:
    @pytest.mark.parametrize(
        ("share", "score"),
        [(0.3, 0.3), (0.5, 0.5), (0.65, 0.5), (0.8, 0.5), (0.9, 0.3)],
    )
    def test_flat_inside_the_healthy_band(self, share, score):
        """Inside 50-80% a point from immunity costs nothing against a tribal point."""
        assert _longevity_score(share) == pytest.approx(score)


class TestSearchRanges:
    def test_final_tribal_bonus_can_clear_the_jury_bonus(self):
        assert max(PARAM_GRID["final_tribal_val"]) >= max(PARAM_GRID["jury_val"]) + 1

    def test_immunity_values_reach_beyond_one(self):
        assert max(PARAM_GRID["individual_immunity_val"]) >= 1.5
        assert max(PARAM_GRID["tribal_immunity_val"]) >= 1.5


class TestFindConfigRank:
    def test_finds_the_baseline_by_its_swept_parameters(self):
        baseline = {"jury_val": 3, "final_tribal_val": 2}
        results = [
            ({"jury_val": 0, "final_tribal_val": 4}, {"composite": 3.0}),
            (dict(baseline), {"composite": 2.0}),
        ]

        assert find_config_rank(results, baseline) == (2, results[1][1])

    def test_returns_none_when_absent(self):
        results = [({"jury_val": 0}, {"composite": 1.0})]

        assert find_config_rank(results, {"jury_val": 3}) is None


# ── Benji's orderings ──────────────────────────────────────────────────────


def ordered_base_config(**overrides):
    """First grid values, adjusted so every ordering holds."""
    config = {k: PARAM_GRID[k][0] for k in PARAM_GRID}
    config.update(
        jury_val=0,
        final_tribal_val=1,
        idol_play_val=1,
        idol_found_val=0.5,
        advantage_play_val=0.25,
        advantage_found_val=0.1,
        tribal_immunity_val=0.25,
        individual_immunity_val=0.5,
    )
    config.update(overrides)
    return config


class TestOrderings:
    def test_a_config_keeping_every_ordering_passes(self):
        assert satisfies_orderings(ordered_base_config()) is True

    @pytest.mark.parametrize(
        "overrides",
        [
            {"final_tribal_val": 3, "jury_val": 3},  # FTC equal to jury
            {"final_tribal_val": 2, "jury_val": 3},  # FTC below jury
            {"idol_play_val": 0.5, "idol_found_val": 0.5},  # play equal to found
            {"idol_found_val": 0, "idol_play_val": 1},  # finding scores nothing
            {"advantage_play_val": 0.5},  # advantage played equal to idol found
            {"advantage_found_val": 0.25},  # advantage found equal to played
            {"advantage_found_val": 0},  # finding an advantage scores nothing
            {"individual_immunity_val": 0.5, "tribal_immunity_val": 0.5},
            {"tribal_immunity_val": 0, "individual_immunity_val": 1},
        ],
    )
    def test_each_broken_ordering_fails(self, overrides):
        assert satisfies_orderings(ordered_base_config(**overrides)) is False

    def test_the_current_default_breaks_them(self):
        """FTC 2 < jury 3, idol found 0, tribal immunity 0."""
        assert satisfies_orderings(dict(DEFAULT_CONFIG)) is False


class TestConstrainedSample:
    def test_every_sample_keeps_the_orderings(self):
        samples = constrained_sample(PARAM_GRID, 500, seed=1)

        assert len(samples) == 500
        assert all(satisfies_orderings(c) for c in samples)

    def test_samples_carry_exactly_the_grid_parameters(self):
        samples = constrained_sample(PARAM_GRID, 20, seed=1)

        assert all(set(c) == set(PARAM_GRID) for c in samples)

    def test_the_valid_part_of_the_grid_is_covered(self):
        samples = constrained_sample(PARAM_GRID, 2000, seed=1)

        assert {c["final_tribal_val"] for c in samples} == {1, 2, 3, 4, 5}
        assert {c["tribal_immunity_val"] for c in samples} == {0.25, 0.5, 1, 1.5}
        # idol found > advantage played > advantage found > 0 starts at 0.5
        assert {c["idol_found_val"] for c in samples} == {0.5, 0.75, 1, 1.5}
        assert {c["advantage_play_val"] for c in samples} == {0.25, 0.5, 0.75, 1}
        assert {c["advantage_found_val"] for c in samples} == {0.1, 0.25, 0.5}


class TestOrderingsInComposite:
    def _evaluate(self, **overrides):
        season = make_finale_season()
        users = [101, 102, 103, 104]
        picks = make_picks(season.survivors, users, picks_per_user=2)
        scenario = (
            season,
            season.survivors,
            picks,
            {uid: 0 for uid in users},
            season.num_players,
            len(users),
        )
        return evaluate_config(ordered_base_config(**overrides), [scenario])

    def test_a_config_breaking_an_ordering_is_penalised(self):
        result = self._evaluate(tribal_immunity_val=0)

        assert result["orderings_ok"] == 0.0
        assert result["composite"] == pytest.approx(
            result["composite_unconstrained"] - CONSTRAINT_PENALTY
        )

    def test_a_config_keeping_them_is_not(self):
        result = self._evaluate()

        assert result["orderings_ok"] == 1.0
        assert result["composite"] == pytest.approx(result["composite_unconstrained"])


# ── Sharding the sweep across nodes ────────────────────────────────────────


class TestSharding:
    def test_parse_shard(self):
        assert parse_shard("3/20") == (3, 20)

    @pytest.mark.parametrize("bad", ["20/20", "-1/4", "3", "a/b", "0/0"])
    def test_parse_shard_rejects_nonsense(self, bad):
        with pytest.raises(ValueError):
            parse_shard(bad)

    def test_shards_split_the_configs_exactly(self):
        configs = [{"i": i} for i in range(103)]
        shards = [shard_configs(configs, k, 7) for k in range(7)]

        merged = sorted(c["i"] for shard in shards for c in shard)
        assert merged == list(range(103))
        assert max(map(len, shards)) - min(map(len, shards)) <= 1

    def _results(self, n):
        return [
            (
                {"first_val": 5, "placement_ratio": (0.5, 0.25), "i": i},
                {"composite": float(i)},
            )
            for i in range(n)
        ]

    def test_round_trip_restores_tuples(self, tmp_path):
        """JSON turns tuples into lists; refinement looks values up by equality."""
        write_shard(shard_path(tmp_path, 0, 1), self._results(3))

        loaded = load_shards(tmp_path, 1, expected_total=3)

        assert loaded[0][0]["placement_ratio"] == (0.5, 0.25)
        assert isinstance(loaded[0][0]["placement_ratio"], tuple)

    def test_a_missing_shard_fails_naming_it(self, tmp_path):
        write_shard(shard_path(tmp_path, 0, 2), self._results(2))

        with pytest.raises(ValueError, match="shard-001-of-002"):
            load_shards(tmp_path, 2, expected_total=4)

    def test_a_short_total_fails(self, tmp_path):
        write_shard(shard_path(tmp_path, 0, 1), self._results(2))

        with pytest.raises(ValueError, match="expected 3"):
            load_shards(tmp_path, 1, expected_total=3)


class TestImpactParams:
    def test_drops_parameters_the_sweep_never_varies(self):
        rows = [
            ("wildcard_multiplier", "Wildcard", "x"),
            ("sole_survivor_val", "Sole Survivor", "y"),
        ]

        assert impact_params(rows) == [("wildcard_multiplier", "Wildcard", "x")]

    def test_sole_survivor_is_not_swept(self):
        """The sweep scores every config with no Sole Survivor points."""
        assert "sole_survivor_val" not in PARAM_GRID


def test_the_search_uses_the_sites_rules():
    """One definition: the site's Scoring changes page shows the same rules."""
    import analyze_scoring
    from app.scoring import rules

    assert analyze_scoring.ORDERINGS is rules.ORDERINGS
    assert analyze_scoring.satisfies_orderings is rules.satisfies_orderings


def test_expanded_configs_score_no_sole_survivor_points():
    """The chart builders score expanded configs; the league has Sole Survivor off."""
    assert expand_config({"first_val": 5})["sole_survivor_val"] == 0
    assert expand_config({"sole_survivor_val": 2})["sole_survivor_val"] == 2


# ── --metrics-for: one config's metrics, for the site ──────────────────────


def _small_scenarios():
    season = make_season(num_players=10, left_at_jury=5, n_finalists=3)
    users = [101, 102, 103, 104]
    picks = make_picks(season.survivors, users, picks_per_user=2)
    ss = {uid: 0 for uid in users}
    return [(season, season.survivors, picks, ss, season.num_players, len(users))]


class TestMetricsForCli:
    def test_a_config_argument_must_be_a_json_object(self):
        from analyze_scoring import parse_metrics_config

        assert parse_metrics_config('{"jury_val": 2}') == {"jury_val": 2}
        for bad in ("[1, 2]", "jury_val=2", "3"):
            with pytest.raises(ValueError):
                parse_metrics_config(bad)

    def test_the_record_is_evaluate_configs_metrics_with_its_inputs(self):
        from analyze_scoring import config_metrics_record

        scenarios = _small_scenarios()
        config = dict(DEFAULT_CONFIG)

        record = config_metrics_record(config, scenarios, [41, 42], 50)

        assert record["metrics"] == evaluate_config(config, scenarios)
        assert record["config"] == config
        assert record["seasons"] == [41, 42]
        assert record["drafts_per"] == 50
        assert record["n_scenarios"] == 1

    def test_the_record_is_written_whole_and_reads_back(self, tmp_path):
        from analyze_scoring import write_metrics_record

        path = tmp_path / "nested" / "abc.json"
        write_metrics_record(str(path), {"metrics": {"composite": 1.5}})

        assert json.loads(path.read_text()) == {"metrics": {"composite": 1.5}}
        # Nothing left beside it: the temporary file was renamed into place
        assert [p.name for p in path.parent.iterdir()] == ["abc.json"]

    def test_each_config_is_written_to_its_own_path(self, tmp_path, monkeypatch):
        import analyze_scoring

        scenarios = _small_scenarios()
        monkeypatch.setattr(analyze_scoring, "load_all_seasons", lambda numbers: [])
        monkeypatch.setattr(
            analyze_scoring, "generate_scenarios", lambda seasons, drafts_per: scenarios
        )
        first, second = tmp_path / "a.json", tmp_path / "b.json"

        analyze_scoring.run_metrics_jobs(
            [{"jury_val": 0}, {"jury_val": 3}], [str(first), str(second)], [41], 50
        )

        assert json.loads(first.read_text())["config"] == {"jury_val": 0}
        assert json.loads(second.read_text())["config"] == {"jury_val": 3}

    def _main(self, monkeypatch, argv):
        import analyze_scoring

        calls = []
        monkeypatch.setattr(
            analyze_scoring, "run_metrics_jobs", lambda *a: calls.append(a)
        )
        monkeypatch.setattr("sys.argv", ["analyze_scoring.py", *argv])
        analyze_scoring.main()
        return calls

    def test_main_runs_the_jobs_on_the_standard_scenarios(self, monkeypatch, tmp_path):
        from analyze_scoring import DEFAULT_SEASONS, DRAFTS_PER_PLAYER_COUNT

        out = tmp_path / "x.json"
        calls = self._main(
            monkeypatch, ["--metrics-for", '{"jury_val": 2}', "--metrics-out", str(out)]
        )

        assert calls == [
            ([{"jury_val": 2}], [str(out)], DEFAULT_SEASONS, DRAFTS_PER_PLAYER_COUNT)
        ]

    @pytest.mark.parametrize(
        "argv",
        [
            ["--metrics-for", "{}"],
            ["--metrics-out", "x.json"],
            ["--metrics-for", "{}", "--metrics-for", "{}", "--metrics-out", "x.json"],
            ["--metrics-for", "[1]", "--metrics-out", "x.json"],
        ],
    )
    def test_main_refuses_unpaired_or_bad_arguments(self, monkeypatch, argv):
        with pytest.raises(SystemExit) as exit_info:
            self._main(monkeypatch, argv)

        assert exit_info.value.code == 2


class TestCompositeHelpers:
    def test_non_draft_credit_rises_to_35_percent_then_stops(self):
        from analyze_scoring import _non_draft_score

        assert _non_draft_score(0.2) < _non_draft_score(0.3) < _non_draft_score(0.35)
        assert _non_draft_score(0.35) == _non_draft_score(0.6)

    def test_non_draft_below_10_percent_is_penalised(self):
        from analyze_scoring import _non_draft_score

        assert _non_draft_score(0.05) == pytest.approx(0.05 - 0.05 * 2.0)

    def test_volatility_credit_is_capped(self):
        from analyze_scoring import _volatility_score

        assert _volatility_score(1.0) == 0.5
        assert _volatility_score(3.0) == 1.0
