"""Tests for app/scoring — ClassicScoring, PointBreakdown, pick modifiers."""
import json
import pytest
from app.scoring.base import PointBreakdown
from app.scoring.classic import ClassicScoring, DEFAULT_CONFIG, LEGACY_CONFIG
from app.scoring import get_scoring_system, compute_stat_overrides, SCORING_STAT_KEYS
from analyze_scoring import SimSurvivor, SimSeason


# ── Helpers ────────────────────────────────────────────────────────────────

def make_season(num_players=18, left_at_jury=8, n_finalists=3):
    """Build a SimSeason. merge_threshold = num_players - left_at_jury."""
    survivors = []
    merge_threshold = num_players - left_at_jury
    for i in range(1, num_players + 1):
        made_jury = i > merge_threshold and i < num_players
        survivors.append(SimSurvivor(
            id=i, name=f'P{i}', voted_out_order=i, made_jury=made_jury,
        ))
    return SimSeason(
        number=50, name='Test', num_players=num_players,
        left_at_jury=left_at_jury, n_finalists=n_finalists,
        survivors=survivors,
    )


def make_survivor(**kwargs):
    """Build a single SimSurvivor with defaults."""
    defaults = dict(id=1, name='Test', voted_out_order=0, made_jury=False,
                    individual_immunity_wins=0, tribal_immunity_wins=0,
                    idols_found=0, advantages_found=0, advantages_played=0,
                    won_fire=False)
    defaults.update(kwargs)
    return SimSurvivor(**defaults)


# ── PointBreakdown ─────────────────────────────────────────────────────────

class TestPointBreakdown:
    def test_total_sums_items(self):
        bd = PointBreakdown(items={'a': 3, 'b': 5.5, 'c': 1.5})
        assert bd.total == 10.0

    def test_total_empty(self):
        assert PointBreakdown().total == 0

    def test_total_updates_with_items(self):
        bd = PointBreakdown()
        bd.items['x'] = 7
        assert bd.total == 7
        bd.items['y'] = 3
        assert bd.total == 10


# ── Flat tribal points ────────────────────────────────────────────────────

class TestFlatTribalPoints:
    def test_pre_merge_only(self):
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(tribal_val=1, post_merge_tribal_val=2)
        # Eliminated 5th (4 tribals survived, all pre-merge)
        surv = make_survivor(voted_out_order=5)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items.get('pre_merge_tribal') == 4
        assert 'post_merge_tribal' not in bd.items

    def test_post_merge(self):
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(tribal_val=1, post_merge_tribal_val=2)
        # Eliminated 13th (12 tribals: 10 pre + 2 post)
        surv = make_survivor(voted_out_order=13)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['pre_merge_tribal'] == 10
        assert bd.items['post_merge_tribal'] == 4  # 2 post-merge tribals * 2

    def test_first_boot_zero_tribals(self):
        season = make_season()
        scoring = ClassicScoring(tribal_val=1)
        surv = make_survivor(voted_out_order=1)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items.get('pre_merge_tribal', 0) == 0

    def test_in_game_uses_current_tribal_count(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(tribal_val=0.5, post_merge_tribal_val=1)
        # Still in game (voted_out_order=0), season has had 5 eliminations
        surv = make_survivor(voted_out_order=0)
        # current_tribal_count = max voted_out_order among survivors = 18
        bd = scoring.calculate_survivor_points(surv, season)
        # All 18 tribals: 10 pre-merge * 0.5 + 8 post-merge * 1 = 13
        assert bd.items['pre_merge_tribal'] == 5.0
        assert bd.items['post_merge_tribal'] == 8.0


# ── Progressive tribal points ─────────────────────────────────────────────

class TestProgressiveTribalPoints:
    def test_linear_model_pre_merge_only(self):
        """base=1, step=0.5 → tribals worth 1, 1.5, 2, 2.5, 3"""
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(tribal_base=1, tribal_step=0.5)
        surv = make_survivor(voted_out_order=6)  # 5 tribals survived
        bd = scoring.calculate_survivor_points(surv, season)
        # sum = 1 + 1.5 + 2 + 2.5 + 3 = 10
        assert bd.items['pre_merge_tribal'] == 10.0

    def test_linear_model_formula(self):
        """Verify closed-form: K*base + step*K*(K-1)/2"""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(tribal_base=0.5, tribal_step=0.5)
        surv = make_survivor(voted_out_order=4)  # 3 tribals
        bd = scoring.calculate_survivor_points(surv, season)
        # 3*0.5 + 0.5*3*2/2 = 1.5 + 1.5 = 3.0
        assert bd.items['pre_merge_tribal'] == 3.0

    def test_piecewise_three_phases(self):
        """base=1, step=0, pm_step=1, f_step=2 with 18 players, finale_size=5"""
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(
            tribal_base=1, tribal_step=0, post_merge_step=1,
            finale_step=2, finale_size=5,
        )
        # finale_threshold = max(10, 18-5) = 13
        # Winner: voted_out_order=18 → 17 tribals
        # pre: 10, post: 3 (13-10), finale: 4 (17-13)
        surv = make_survivor(voted_out_order=18)
        bd = scoring.calculate_survivor_points(surv, season)

        # Pre-merge: 10 tribals * 1 = 10 (step=0, all same value)
        assert bd.items['pre_merge_tribal'] == 10.0

        # Post-merge: last_pre=1, 3 tribals: sum of (1+1*1) + (1+2*1) + (1+3*1) = 2+3+4 = 9
        assert bd.items['post_merge_tribal'] == 9.0

        # Finale: last_post = 1 + 3*1 = 4, 4 tribals: (4+1*2)+(4+2*2)+(4+3*2)+(4+4*2) = 6+8+10+12 = 36
        assert bd.items['finale_tribal'] == 36.0

    def test_progressive_zero_step_equals_flat(self):
        """With step=0, progressive should match flat base per tribal."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring_prog = ClassicScoring(
            tribal_base=1.5, tribal_step=0, post_merge_step=0, finale_step=0,
        )
        scoring_flat = ClassicScoring(tribal_val=1.5, post_merge_tribal_val=1.5)
        surv = make_survivor(voted_out_order=15)  # 14 tribals
        bd_prog = scoring_prog.calculate_survivor_points(surv, season)
        bd_flat = scoring_flat.calculate_survivor_points(surv, season)
        prog_tribal = sum(v for k, v in bd_prog.items.items()
                          if 'tribal' in k and k != 'final_tribal')
        flat_tribal = sum(v for k, v in bd_flat.items.items()
                          if 'tribal' in k and k != 'final_tribal')
        assert prog_tribal == flat_tribal

    def test_progressive_dispatches_on_tribal_base(self):
        """tribal_base=None → flat mode, tribal_base set → progressive."""
        season = make_season()
        flat = ClassicScoring(tribal_val=1)
        prog = ClassicScoring(tribal_base=1, tribal_step=0)
        bd_flat = flat._compute_tribal_points(4, season)
        bd_prog = prog._compute_tribal_points(4, season)
        assert bd_flat == bd_prog  # both give 4 tribals * 1 = 4

    def test_first_boot_progressive_zero(self):
        season = make_season()
        scoring = ClassicScoring(tribal_base=1, tribal_step=0.5)
        surv = make_survivor(voted_out_order=1)
        bd = scoring.calculate_survivor_points(surv, season)
        tribal_pts = sum(v for k, v in bd.items.items()
                         if 'tribal' in k and k != 'final_tribal')
        assert tribal_pts == 0


# ── Milestones and placement ──────────────────────────────────────────────

class TestMilestones:
    def test_winner_gets_first_val_and_ftc(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(first_val=15, final_tribal_val=3)
        surv = make_survivor(voted_out_order=18)  # winner
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['placement'] == 15
        assert bd.items['final_tribal'] == 3

    def test_second_place(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(second_val=5, final_tribal_val=3)
        surv = make_survivor(voted_out_order=17)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['placement'] == 5
        assert bd.items['final_tribal'] == 3

    def test_third_place(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(third_val=2, final_tribal_val=3)
        surv = make_survivor(voted_out_order=16)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['placement'] == 2
        assert bd.items['final_tribal'] == 3

    def test_no_placement_for_mid_game_elim(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(first_val=15, second_val=5, third_val=2)
        surv = make_survivor(voted_out_order=10)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'placement' not in bd.items

    def test_jury_bonus(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(jury_val=2)
        surv = make_survivor(voted_out_order=12, made_jury=True)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['jury'] == 2

    def test_winner_no_jury_bonus(self):
        """Winner has made_jury=False in survivoR data."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(jury_val=2)
        surv = make_survivor(voted_out_order=18, made_jury=False)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'jury' not in bd.items

    def test_merge_bonus(self):
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(merge_val=2)
        surv = make_survivor(voted_out_order=12)  # past merge
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['merge'] == 2

    def test_no_merge_bonus_pre_merge(self):
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(merge_val=2)
        surv = make_survivor(voted_out_order=5)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'merge' not in bd.items

    def test_zero_val_disables_component(self):
        season = make_season()
        scoring = ClassicScoring(jury_val=0, merge_val=0, first_val=0)
        surv = make_survivor(voted_out_order=18, made_jury=True)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'jury' not in bd.items
        assert 'merge' not in bd.items
        assert 'placement' not in bd.items


# ── Performance bonuses ───────────────────────────────────────────────────

class TestPerformanceBonuses:
    def test_individual_immunity(self):
        season = make_season()
        scoring = ClassicScoring(individual_immunity_val=3)
        surv = make_survivor(voted_out_order=15, individual_immunity_wins=2)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['individual_immunity'] == 6

    def test_tribal_immunity(self):
        season = make_season()
        scoring = ClassicScoring(tribal_immunity_val=1)
        surv = make_survivor(voted_out_order=10, tribal_immunity_wins=4)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['tribal_immunity'] == 4

    def test_idols_found(self):
        season = make_season()
        scoring = ClassicScoring(idol_found_val=2)
        surv = make_survivor(voted_out_order=15, idols_found=1)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['idols_found'] == 2

    def test_advantages_found_excludes_idols(self):
        """advantages_found includes idols; scoring subtracts idols_found."""
        season = make_season()
        scoring = ClassicScoring(advantage_found_val=1)
        surv = make_survivor(voted_out_order=15, advantages_found=3, idols_found=1)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['advantages_found'] == 2  # 3 - 1 idol

    def test_idol_plays(self):
        season = make_season()
        scoring = ClassicScoring(idol_play_val=3)
        surv = make_survivor(voted_out_order=15, idols_found=2)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['idol_plays'] == 6  # 2 * 3

    def test_advantage_plays_excludes_idol_plays(self):
        season = make_season()
        scoring = ClassicScoring(advantage_play_val=2)
        surv = make_survivor(voted_out_order=15, advantages_played=3, idols_found=1)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['advantage_plays'] == 4  # (3-1) * 2

    def test_fire_win(self):
        season = make_season()
        scoring = ClassicScoring(fire_win_val=3)
        surv = make_survivor(voted_out_order=17, won_fire=True)
        bd = scoring.calculate_survivor_points(surv, season)
        assert bd.items['fire_win'] == 3

    def test_no_fire_win_when_false(self):
        season = make_season()
        scoring = ClassicScoring(fire_win_val=3)
        surv = make_survivor(voted_out_order=17, won_fire=False)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'fire_win' not in bd.items


# ── score_pick (pick type modifiers) ──────────────────────────────────────

class TestScorePick:
    def test_draft_full_points(self):
        season = make_season()
        scoring = ClassicScoring(tribal_val=1, first_val=0, merge_val=0, jury_val=0)
        surv = make_survivor(voted_out_order=5)
        pts, bd = scoring.score_pick(surv, season, 'draft')
        assert pts == bd.total

    def test_wildcard_half_points(self):
        season = make_season()
        scoring = ClassicScoring(tribal_val=1, wildcard_multiplier=0.5,
                                 first_val=0, merge_val=0, jury_val=0)
        surv = make_survivor(voted_out_order=5)
        pts, bd = scoring.score_pick(surv, season, 'wildcard')
        assert pts == bd.total * 0.5

    def test_wildcard_custom_multiplier(self):
        season = make_season()
        scoring = ClassicScoring(tribal_val=1, wildcard_multiplier=0.75,
                                 first_val=0, merge_val=0, jury_val=0)
        surv = make_survivor(voted_out_order=5)
        pts, _ = scoring.score_pick(surv, season, 'wildcard')
        expected = 4 * 0.75  # 4 tribals * 1pt * 0.75
        assert pts == expected

    def test_replacement_with_stat_overrides(self):
        """pmr_d with stat_overrides: uses overridden stats, strips pre-merge tribal."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(
            tribal_val=1, post_merge_tribal_val=2,
            individual_immunity_val=3,
            first_val=0, merge_val=0, jury_val=0,
        )
        # Survivor with 15 voted_out_order (14 tribals total)
        surv = make_survivor(voted_out_order=15, individual_immunity_wins=3)
        # Override: only 1 immunity win after merge
        overrides = {'individual_immunity_wins': 1, 'tribal_immunity_wins': 0,
                     'idols_found': 0, 'advantages_found': 0, 'advantages_played': 0}
        pts, bd = scoring.score_pick(surv, season, 'pmr_d', stat_overrides=overrides)
        # pre_merge_tribal stripped, post_merge_tribal stays, immunity=1*3
        assert 'pre_merge_tribal' not in bd.items
        assert 'merge' not in bd.items
        # Survivor stats restored after scoring
        assert surv.individual_immunity_wins == 3

    def test_pmr_w_applies_replacement_multiplier(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(
            tribal_val=1, replacement_multiplier=0.5,
            first_val=0, merge_val=0, jury_val=0,
        )
        surv = make_survivor(voted_out_order=15)
        overrides = {'individual_immunity_wins': 0, 'tribal_immunity_wins': 0,
                     'idols_found': 0, 'advantages_found': 0, 'advantages_played': 0}
        pts_w, _ = scoring.score_pick(surv, season, 'pmr_w', stat_overrides=overrides)
        pts_d, _ = scoring.score_pick(surv, season, 'pmr_d', stat_overrides=overrides)
        assert pts_w == pts_d * 0.5

    def test_replacement_fallback_without_overrides(self):
        """Without stat_overrides, falls back to flat deduction."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(
            tribal_val=1, replacement_deduction=True,
            first_val=0, merge_val=0, jury_val=0,
        )
        surv = make_survivor(voted_out_order=15)
        pts, bd = scoring.score_pick(surv, season, 'pmr_d')
        # Flat deduction: total - pre_jury (10)
        assert pts == bd.total - 10


# ── apply_pick_modifier ───────────────────────────────────────────────────

class TestApplyPickModifier:
    def test_draft_unchanged(self):
        scoring = ClassicScoring()
        assert scoring.apply_pick_modifier(100, 'draft', 18, 8) == 100

    def test_wildcard_halved(self):
        scoring = ClassicScoring(wildcard_multiplier=0.5)
        assert scoring.apply_pick_modifier(100, 'wildcard', 18, 8) == 50

    def test_pmr_d_deducts_pre_jury(self):
        scoring = ClassicScoring(replacement_deduction=True)
        # pre_jury = 18 - 8 = 10
        assert scoring.apply_pick_modifier(100, 'pmr_d', 18, 8) == 90

    def test_pmr_w_deducts_and_halves(self):
        scoring = ClassicScoring(replacement_multiplier=0.5, replacement_deduction=True)
        # (100 - 10) * 0.5 = 45
        assert scoring.apply_pick_modifier(100, 'pmr_w', 18, 8) == 45

    def test_no_deduction_when_disabled(self):
        scoring = ClassicScoring(replacement_deduction=False)
        assert scoring.apply_pick_modifier(100, 'pmr_d', 18, 8) == 100

    def test_pmr_w_no_deduction(self):
        scoring = ClassicScoring(replacement_multiplier=0.5, replacement_deduction=False)
        assert scoring.apply_pick_modifier(100, 'pmr_w', 18, 8) == 50


# ── Sole Survivor bonus ──────────────────────────────────────────────────

class TestSoleSurvivorBonus:
    def test_streak_calculation(self):
        scoring = ClassicScoring(sole_survivor_val=1)
        assert scoring.calculate_sole_survivor_bonus(10) == 10

    def test_zero_val_no_bonus(self):
        scoring = ClassicScoring(sole_survivor_val=0)
        assert scoring.calculate_sole_survivor_bonus(10) == 0

    def test_zero_streak(self):
        scoring = ClassicScoring(sole_survivor_val=2)
        assert scoring.calculate_sole_survivor_bonus(0) == 0


# ── Legacy config ─────────────────────────────────────────────────────────

class TestLegacyConfig:
    def test_legacy_flat_tribals(self):
        """Legacy: 1pt per tribal regardless of phase."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(**LEGACY_CONFIG)
        # Winner: 17 tribals * 1 = 17
        surv = make_survivor(voted_out_order=18, made_jury=False)
        bd = scoring.calculate_survivor_points(surv, season)
        tribal_pts = bd.items.get('pre_merge_tribal', 0) + bd.items.get('post_merge_tribal', 0)
        assert tribal_pts == 17

    def test_legacy_winner_total(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(**LEGACY_CONFIG)
        surv = make_survivor(voted_out_order=18, made_jury=False)
        bd = scoring.calculate_survivor_points(surv, season)
        # 17 tribals + 1 merge + 7 first = 25
        assert bd.total == 25

    def test_legacy_no_bonus_categories(self):
        """Legacy config has all bonus vals at 0."""
        season = make_season()
        scoring = ClassicScoring(**LEGACY_CONFIG)
        surv = make_survivor(voted_out_order=15, individual_immunity_wins=3,
                             idols_found=2, made_jury=True)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'individual_immunity' not in bd.items
        assert 'idols_found' not in bd.items
        assert 'jury' not in bd.items


# ── Progressive tribal edge cases ─────────────────────────────────────────

class TestProgressiveEdgeCases:
    def test_all_zero_coefficients(self):
        """base=0, all steps=0 → no tribal points at all."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(tribal_base=0, tribal_step=0,
                                 post_merge_step=0, finale_step=0)
        surv = make_survivor(voted_out_order=15)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'pre_merge_tribal' not in bd.items
        assert 'post_merge_tribal' not in bd.items
        assert 'finale_tribal' not in bd.items

    def test_finale_size_larger_than_num_players(self):
        """finale_size > num_players → finale_threshold = merge_threshold,
        post-merge phase has 0 width, all post-merge tribals go to finale."""
        season = make_season(num_players=10, left_at_jury=5)  # merge at 5
        scoring = ClassicScoring(tribal_base=1, tribal_step=0,
                                 post_merge_step=0.5, finale_step=10,
                                 finale_size=20)
        # finale_threshold = max(5, 10-20) = 5 = merge_threshold
        # post_count = min(max(0, 9-5), max(0, 5-5)) = min(4, 0) = 0
        # All 4 post-merge tribals go to finale phase
        surv = make_survivor(voted_out_order=10)  # winner, 9 tribals
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'post_merge_tribal' not in bd.items
        assert 'finale_tribal' in bd.items

    def test_survivor_eliminated_exactly_at_merge(self):
        """Eliminated at merge threshold → all pre-merge tribals, no post-merge."""
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(tribal_base=1, tribal_step=0.5,
                                 post_merge_step=1)
        surv = make_survivor(voted_out_order=10)  # tribals = 9
        bd = scoring.calculate_survivor_points(surv, season)
        # 9 pre-merge tribals, but merge_threshold=10 so pre_count=min(9,10)=9
        assert 'pre_merge_tribal' in bd.items
        assert 'post_merge_tribal' not in bd.items

    def test_survivor_eliminated_at_finale_threshold(self):
        """Eliminated exactly at finale boundary → pre + post-merge, no finale."""
        season = make_season(num_players=18, left_at_jury=8)  # merge at 10
        scoring = ClassicScoring(tribal_base=1, tribal_step=0,
                                 post_merge_step=0.5, finale_step=2,
                                 finale_size=5)
        # finale_threshold = max(10, 18-5) = 13
        surv = make_survivor(voted_out_order=13)  # 12 tribals
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'pre_merge_tribal' in bd.items
        assert 'post_merge_tribal' in bd.items
        assert 'finale_tribal' not in bd.items


# ── Placement edge cases ─────────────────────────────────────────────────

class TestPlacementEdgeCases:
    def test_fourth_place_no_placement_or_ftc(self):
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(first_val=15, second_val=5, third_val=2,
                                 final_tribal_val=3)
        surv = make_survivor(voted_out_order=15)  # 4th place (18-3=15)
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'placement' not in bd.items
        assert 'final_tribal' not in bd.items

    def test_ftc_without_placement(self):
        """final_tribal_val set but placement vals at 0 → FTC bonus still awarded."""
        season = make_season(num_players=18, left_at_jury=8)
        scoring = ClassicScoring(first_val=0, second_val=0, third_val=0,
                                 final_tribal_val=3)
        surv = make_survivor(voted_out_order=18)  # winner
        bd = scoring.calculate_survivor_points(surv, season)
        assert 'placement' not in bd.items
        assert bd.items['final_tribal'] == 3


# ── get_scoring_system ────────────────────────────────────────────────────

class TestGetScoringSystem:
    def test_returns_classic_by_name(self):
        scoring = get_scoring_system('Classic')
        assert isinstance(scoring, ClassicScoring)

    def test_unknown_name_falls_back_to_classic(self):
        scoring = get_scoring_system('NonexistentSystem')
        assert isinstance(scoring, ClassicScoring)

    def test_config_applied(self):
        scoring = get_scoring_system('Classic', config={'first_val': 99})
        assert scoring.config['first_val'] == 99

    def test_no_config_uses_defaults(self):
        scoring = get_scoring_system('Classic')
        assert scoring.config['first_val'] == DEFAULT_CONFIG['first_val']


# ── compute_stat_overrides ────────────────────────────────────────────────

class _MockSurvivor:
    """Minimal mock with get_episode_stats() for testing compute_stat_overrides."""
    def __init__(self, stats, episode_stats_json=None):
        for k, v in stats.items():
            setattr(self, k, v)
        self._episode_stats = episode_stats_json

    def get_episode_stats(self):
        if self._episode_stats:
            return json.loads(self._episode_stats)
        return {}


class TestComputeStatOverrides:
    def test_basic_post_merge_delta(self):
        stats = {
            'individual_immunity_wins': 4, 'tribal_immunity_wins': 3,
            'idols_found': 2, 'advantages_found': 1, 'advantages_played': 1,
        }
        ep_stats = {'5': {'ii': 1, 'ti': 2, 'idol': 1, 'adv': 0, 'adv_play': 0}}
        surv = _MockSurvivor(stats, json.dumps(ep_stats))
        overrides = compute_stat_overrides(surv, merge_episode=5)
        assert overrides['individual_immunity_wins'] == 3  # 4 - 1
        assert overrides['tribal_immunity_wins'] == 1  # 3 - 2
        assert overrides['idols_found'] == 1  # 2 - 1
        assert overrides['advantages_found'] == 1  # 1 - 0
        assert overrides['advantages_played'] == 1  # 1 - 0

    def test_returns_none_without_merge_episode(self):
        surv = _MockSurvivor({'individual_immunity_wins': 1})
        assert compute_stat_overrides(surv, merge_episode=0) is None
        assert compute_stat_overrides(surv, merge_episode=None) is None

    def test_merge_episode_not_in_stats(self):
        """merge_episode exists but isn't in episode_stats → at_merge defaults to 0."""
        stats = {'individual_immunity_wins': 3, 'tribal_immunity_wins': 0,
                 'idols_found': 0, 'advantages_found': 0, 'advantages_played': 0}
        ep_stats = {'10': {'ii': 2}}  # merge at ep 5, but only ep 10 in stats
        surv = _MockSurvivor(stats, json.dumps(ep_stats))
        overrides = compute_stat_overrides(surv, merge_episode=5)
        # merge ep 5 not in stats → all at_merge = 0 → overrides = current vals
        assert overrides['individual_immunity_wins'] == 3

    def test_current_less_than_at_merge_clamped(self):
        """If current < at_merge (data inconsistency), clamp to 0."""
        stats = {'individual_immunity_wins': 1, 'tribal_immunity_wins': 0,
                 'idols_found': 0, 'advantages_found': 0, 'advantages_played': 0}
        ep_stats = {'5': {'ii': 3}}  # at_merge=3 but current=1 (data error)
        surv = _MockSurvivor(stats, json.dumps(ep_stats))
        overrides = compute_stat_overrides(surv, merge_episode=5)
        assert overrides['individual_immunity_wins'] == 0  # max(0, 1-3)

    def test_missing_keys_in_episode_data(self):
        """Episode data missing some keys → those default to 0."""
        stats = {'individual_immunity_wins': 5, 'tribal_immunity_wins': 2,
                 'idols_found': 0, 'advantages_found': 0, 'advantages_played': 0}
        ep_stats = {'5': {'ii': 2}}  # only 'ii', missing 'ti' etc.
        surv = _MockSurvivor(stats, json.dumps(ep_stats))
        overrides = compute_stat_overrides(surv, merge_episode=5)
        assert overrides['individual_immunity_wins'] == 3  # 5 - 2
        assert overrides['tribal_immunity_wins'] == 2  # 2 - 0 (missing key)

    def test_all_scoring_stat_keys_present(self):
        """Overrides dict contains all keys from SCORING_STAT_KEYS."""
        stats = {attr: 0 for attr in SCORING_STAT_KEYS.values()}
        ep_stats = {'5': {}}
        surv = _MockSurvivor(stats, json.dumps(ep_stats))
        overrides = compute_stat_overrides(surv, merge_episode=5)
        for attr in SCORING_STAT_KEYS.values():
            assert attr in overrides
