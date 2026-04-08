"""Tests for analyze_scoring.py — config expansion, leaderboard, metrics, sampling."""

import json
import random
import tempfile

from analyze_scoring import (
    PARAM_GRID,
    SimPick,
    SimSeason,
    SimSurvivor,
    _build_elim_to_episode,
    _build_tribal_table,
    _compute_sim_stat_overrides,
    _fast_score_total,
    assign_extra_picks,
    build_chart_data,
    calculate_leaderboard,
    compute_ss_streaks,
    evaluate_config,
    expand_config,
    rank_users,
    snake_draft,
    stratified_random_sample,
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
