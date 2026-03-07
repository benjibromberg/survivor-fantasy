"""Analyze scoring configurations across all US Survivor seasons with simulated drafts.

Loads castaway data from survivoR.xlsx and simulates random snake drafts with 4-10
fantasy players per season. Evaluates scoring configs on fun/fairness metrics:
- Recovery: Can a player who loses a pick early still win?
- Volatility: Do standings shift throughout the season?
- Competitiveness: Are multiple players in contention at the midpoint?
- Late-game drama: Is the leader at 75% still beatable?
- Pick-type impact: Do wildcards and sole survivor picks matter?

Usage:
    python analyze_scoring.py                    # full sweep (2000 configs, 49 seasons)
    python analyze_scoring.py --quick            # fast iteration (500 configs)
    python analyze_scoring.py --export-json PATH # export chart data for web page
"""
import argparse
import json
import multiprocessing as mp
import random
import time
from collections import defaultdict

import pandas as pd

from app.scoring.classic import ClassicScoring, DEFAULT_CONFIG

SURVIVOR_XLSX = 'survivoR.xlsx'
DEFAULT_SEASONS = list(range(41, 50))  # modern Survivor (idols, advantages, new era)
PLAYER_COUNTS = [4, 5, 6, 7, 8, 9, 10]
DRAFTS_PER_PLAYER_COUNT = 50  # random draft orders per size per season
MIN_PICKS_PER_PLAYER = 4
TIMELINE_SEASONS = [42, 45, 47, 49]  # representative modern seasons


# ── Parameter grid (5 values per param) ─────────────────────────────────────

PARAM_GRID = {
    'tribal_val': [0.25, 0.5, 0.75, 1.0, 1.5],
    'post_merge_tribal_val': [0.5, 1, 1.5, 2, 3],
    'jury_val': [1, 2, 3, 4, 5],
    'merge_val': [1, 2, 3, 4, 5],
    'final_tribal_val': [1, 2, 3, 5, 8],
    'first_val': [5, 8, 10, 15, 20],
    'placement_ratio': [
        (0.50, 0.25),   # steep drop (e.g. 10:5:2.5)
        (0.40, 0.20),   # classic steep (e.g. 10:4:2)
        (0.60, 0.30),   # moderate drop
        (0.70, 0.40),   # gentle (e.g. 10:7:4)
        (0.33, 0.17),   # winner takes most (e.g. 12:4:2)
    ],
    'individual_immunity_val': [1, 2, 3, 4, 5],
    'tribal_immunity_val': [0.5, 1, 1.5, 2, 3],
    'idol_found_val': [1, 2, 3, 4, 5],
    'advantage_found_val': [0.5, 1, 1.5, 2, 3],
    'idol_play_val': [1, 2, 3, 4, 5],
    'advantage_play_val': [0.5, 1, 1.5, 2, 3],
    'wildcard_multiplier': [0, 0.25, 0.5, 0.75, 1.0],
    'replacement_multiplier': [0, 0.25, 0.5, 0.75, 1.0],
    'replacement_deduction': [True, False],
    'sole_survivor_val': [0, 0.25, 0.5, 1, 2],
}


def expand_config(config):
    """Expand placement_ratio into second_val and third_val (snapped to 0.25 increments)."""
    expanded = dict(config)
    ratio = expanded.pop('placement_ratio', None)
    if ratio:
        first = expanded.get('first_val', 10)
        expanded['second_val'] = round(first * ratio[0] * 4) / 4
        expanded['third_val'] = round(first * ratio[1] * 4) / 4
    return expanded


# ── Lightweight data objects (no DB dependency) ─────────────────────────────

class SimSurvivor:
    __slots__ = ('id', 'name', 'voted_out_order', 'made_jury',
                 'individual_immunity_wins', 'tribal_immunity_wins',
                 'idols_found', 'advantages_found', 'advantages_played')

    def __init__(self, id, name, voted_out_order, made_jury,
                 individual_immunity_wins=0, tribal_immunity_wins=0,
                 idols_found=0, advantages_found=0, advantages_played=0):
        self.id = id
        self.name = name
        self.voted_out_order = voted_out_order
        self.made_jury = made_jury
        self.individual_immunity_wins = individual_immunity_wins
        self.tribal_immunity_wins = tribal_immunity_wins
        self.idols_found = idols_found
        self.advantages_found = advantages_found
        self.advantages_played = advantages_played


class SimSeason:
    def __init__(self, number, name, num_players, left_at_jury, survivors):
        self.number = number
        self.name = name
        self.num_players = num_players
        self.left_at_jury = left_at_jury
        self.survivors = survivors

    @property
    def current_tribal_count(self):
        voted = [s.voted_out_order for s in self.survivors if s.voted_out_order]
        return max(voted) if voted else 0


class SimPick:
    __slots__ = ('survivor', 'survivor_id', 'pick_type', 'user')

    def __init__(self, survivor, pick_type='draft'):
        self.survivor = survivor
        self.survivor_id = survivor.id
        self.pick_type = pick_type
        self.user = None  # not needed for analysis


# ── Data loading ────────────────────────────────────────────────────────────

def load_all_seasons(season_numbers=None):
    """Load season data from survivoR.xlsx. Returns list of SimSeason."""
    if season_numbers is None:
        season_numbers = ALL_SEASONS

    castaways = pd.read_excel(SURVIVOR_XLSX, 'Castaways')
    season_summary = pd.read_excel(SURVIVOR_XLSX, 'Season Summary')
    challenge_results = pd.read_excel(SURVIVOR_XLSX, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_XLSX, 'Advantage Movement')
    advantage_details = pd.read_excel(SURVIVOR_XLSX, 'Advantage Details')

    us_cast = castaways[castaways['version'] == 'US']
    us_ss = season_summary[season_summary['version'] == 'US']
    us_cr = challenge_results[challenge_results['version'] == 'US']
    us_am = advantage_movement[advantage_movement['version'] == 'US']

    # Pre-compute stats grouped by (season, castaway_id)
    indiv_imm = us_cr[us_cr['won_individual_immunity'] == 1].groupby(
        ['season', 'castaway_id']).size()
    tribal_imm = us_cr[us_cr['won_tribal_immunity'] == 1].groupby(
        ['season', 'castaway_id']).size()

    idol_ids = set()
    if not advantage_details.empty:
        idol_rows = advantage_details[
            advantage_details['advantage_type'].str.contains('Idol', na=False)]
        idol_ids = set(idol_rows['advantage_id'])

    if not us_am.empty:
        idols_found_df = us_am[
            (us_am['event'].str.contains('Found', na=False)) &
            (us_am['advantage_id'].isin(idol_ids))
        ].groupby(['season', 'castaway_id']).size()
        adv_found_df = us_am[
            us_am['event'].str.contains('Found', na=False)
        ].groupby(['season', 'castaway_id']).size()
        adv_played_df = us_am[
            us_am['event'] == 'Played'
        ].groupby(['season', 'castaway_id']).size()
    else:
        idols_found_df = adv_found_df = adv_played_df = pd.Series(dtype=int)

    seasons = []
    for snum in season_numbers:
        cast = us_cast[us_cast['season'] == snum]
        if cast.empty:
            continue
        ss_row = us_ss[us_ss['season'] == snum]
        if ss_row.empty:
            continue
        ss = ss_row.iloc[0]

        n_cast = int(ss['n_cast']) if pd.notna(ss['n_cast']) else len(cast)
        n_jury = int(ss['n_jury']) if pd.notna(ss['n_jury']) else 7
        n_finalists = int(ss['n_finalists']) if pd.notna(ss['n_finalists']) else 2
        left_at_jury = n_jury + n_finalists

        season_name = str(ss['season_name']) if pd.notna(ss['season_name']) else f'Season {snum}'

        survs = []
        for _, row in cast.iterrows():
            order = int(row['order']) if pd.notna(row['order']) else 0
            # Jury status
            made_jury = False
            if 'jury_status' in row.index and pd.notna(row['jury_status']):
                made_jury = (str(row['jury_status']).strip().lower() == 'jury')
            elif 'jury' in row.index and pd.notna(row['jury']):
                made_jury = bool(row['jury'])

            cid = row['castaway_id'] if pd.notna(row.get('castaway_id')) else None
            ii = int(indiv_imm.get((snum, cid), 0)) if cid else 0
            ti = int(tribal_imm.get((snum, cid), 0)) if cid else 0
            idf = int(idols_found_df.get((snum, cid), 0)) if cid else 0
            af = int(adv_found_df.get((snum, cid), 0)) if cid else 0
            ap = int(adv_played_df.get((snum, cid), 0)) if cid else 0

            survs.append(SimSurvivor(
                id=len(survs), name=row['castaway'],
                voted_out_order=order, made_jury=made_jury,
                individual_immunity_wins=ii, tribal_immunity_wins=ti,
                idols_found=idf, advantages_found=af, advantages_played=ap,
            ))

        max_order = max((s.voted_out_order for s in survs), default=0)
        if max_order == 0:
            continue

        seasons.append(SimSeason(
            number=snum, name=season_name,
            num_players=n_cast, left_at_jury=left_at_jury,
            survivors=survs,
        ))

    return seasons


# ── Draft simulation ────────────────────────────────────────────────────────

def snake_draft(survivors, n_players, rng):
    """Snake draft survivors among n_players. Returns {pid: [SimSurvivor]}."""
    available = list(survivors)
    rng.shuffle(available)
    picks = {i: [] for i in range(n_players)}
    round_num = 0
    while available:
        order = range(n_players) if round_num % 2 == 0 else range(n_players - 1, -1, -1)
        for p in order:
            if not available:
                break
            picks[p].append(available.pop())
        round_num += 1
    return picks


def simulate_draft(survivors, n_players, min_picks=MIN_PICKS_PER_PLAYER, rng=None):
    """Draft with sub-drafts if needed to ensure min_picks per player."""
    picks_each = len(survivors) // n_players
    if picks_each >= min_picks:
        return snake_draft(survivors, n_players, rng)

    # Sub-drafts: split players into groups small enough for min_picks each
    max_per_group = max(2, len(survivors) // min_picks)
    all_picks = {}
    pid = 0
    remaining = n_players
    while remaining > 0:
        group = min(max_per_group, remaining)
        sub = snake_draft(survivors, group, rng)
        for local_id in range(group):
            all_picks[pid] = sub[local_id]
            pid += 1
        remaining -= group
    return all_picks


def generate_scenarios(seasons, player_counts=None, drafts_per=None, seed=42):
    """Pre-generate many draft scenarios for each season.

    For each season × player count × draft repetition, simulates a snake draft
    with a different random order. Each player also gets one wildcard picked
    after the first elimination (first boot excluded).

    Returns list of (season, survivors, picks_by_user, ss_streaks, max_elim, n_players).
    """
    if player_counts is None:
        player_counts = PLAYER_COUNTS
    if drafts_per is None:
        drafts_per = DRAFTS_PER_PLAYER_COUNT
    rng = random.Random(seed)
    scenarios = []

    for season in seasons:
        max_elim = max(s.voted_out_order for s in season.survivors if s.voted_out_order)
        winner = next(
            (s for s in season.survivors if s.voted_out_order == season.num_players), None)
        num_episodes = max_elim + 1

        for n_players in player_counts:
            for _ in range(drafts_per):
                draft = simulate_draft(season.survivors, n_players, rng=rng)

                # Convert draft picks to SimPick objects
                picks_by_user = {}
                for pid, survs in draft.items():
                    picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]

                # Wildcard: each player picks one survivor who survived ep 1.
                # Wildcards CAN overlap with other players' draft picks.
                # Only exclude survivors already on THIS player's roster.
                wc_eligible = [s for s in season.survivors
                               if s.voted_out_order != 1]
                for pid in picks_by_user:
                    own_ids = {p.survivor_id for p in picks_by_user[pid]}
                    pool = [s for s in wc_eligible if s.id not in own_ids]
                    if pool:
                        picks_by_user[pid].append(
                            SimPick(rng.choice(pool), 'wildcard'))

                # SS: each player's longest-lasting drafted castaway
                ss_streaks = {}
                for pid, picks in picks_by_user.items():
                    draft_picks = [p for p in picks if p.pick_type == 'draft']
                    if draft_picks:
                        best = max(draft_picks,
                                   key=lambda p: p.survivor.voted_out_order or 999)
                        if winner and best.survivor.voted_out_order == season.num_players:
                            ss_streaks[pid] = num_episodes
                        else:
                            ss_streaks[pid] = 0

                scenarios.append((
                    season, season.survivors, picks_by_user, ss_streaks,
                    max_elim, n_players))

    return scenarios


# ── Scoring ─────────────────────────────────────────────────────────────────

PERFORMANCE_KEYS = ('individual_immunity', 'tribal_immunity', 'idols_found',
                    'advantages_found', 'idol_plays', 'advantage_plays')


def calculate_leaderboard(season, scoring, survivors, picks_by_user,
                          ss_streaks, as_of=None, include_ss=None):
    """Calculate leaderboard at a given elimination point.

    Masks eliminations beyond as_of. Prorates performance bonuses for masked survivors.
    SS bonus excluded for intermediate steps by default.
    """
    if include_ss is None:
        include_ss = (as_of is None)

    originals = {}
    if as_of is not None:
        for s in survivors:
            originals[s.id] = (s.voted_out_order, s.made_jury)
            if s.voted_out_order and s.voted_out_order > as_of:
                s.voted_out_order = 0
                s.made_jury = False

    results = {}
    for user_id, picks in picks_by_user.items():
        total = 0
        for pick in picks:
            breakdown = scoring.calculate_survivor_points(pick.survivor, season)
            # Prorate performance for survivors not yet eliminated at this step
            if as_of is not None and pick.survivor_id in originals:
                orig_elim = originals[pick.survivor_id][0]
                if orig_elim and orig_elim > as_of:
                    fraction = as_of / orig_elim
                    for key in PERFORMANCE_KEYS:
                        if key in breakdown.items:
                            breakdown.items[key] *= fraction
            modified = scoring.apply_pick_modifier(
                breakdown.total, pick.pick_type,
                season.num_players, season.left_at_jury)
            total += modified
        if include_ss:
            streak = ss_streaks.get(user_id, 0)
            total += scoring.calculate_sole_survivor_bonus(streak)
        results[user_id] = total

    for s in survivors:
        if s.id in originals:
            s.voted_out_order, s.made_jury = originals[s.id]

    return results


def rank_users(results):
    return sorted(results.keys(), key=lambda uid: results[uid], reverse=True)


# ── Sampling ────────────────────────────────────────────────────────────────

def latin_hypercube_sample(param_grid, n_samples, seed=42):
    """Stratified random sampling for even coverage of parameter space."""
    rng = random.Random(seed)
    keys = list(param_grid.keys())
    value_sequences = {}
    for key in keys:
        values = param_grid[key]
        n_vals = len(values)
        seq = (values * ((n_samples // n_vals) + 1))[:n_samples]
        rng.shuffle(seq)
        value_sequences[key] = seq
    return [{key: value_sequences[key][i] for key in keys} for i in range(n_samples)]


# ── Evaluation ──────────────────────────────────────────────────────────────

def evaluate_config(config, scenarios):
    """Evaluate a scoring config across all pre-generated draft scenarios."""
    expanded = expand_config(config)
    scoring = ClassicScoring(**expanded)
    metrics = defaultdict(list)

    for season, survivors, picks_by_user, ss_streaks, max_elim, n_players in scenarios:
        # Recovery: average final rank of players who lost a pick in first 3 elims
        early_losers = set()
        for user_id, picks in picks_by_user.items():
            for pick in picks:
                if pick.survivor.voted_out_order and 1 <= pick.survivor.voted_out_order <= 3:
                    early_losers.add(user_id)

        final_lb = calculate_leaderboard(
            season, scoring, survivors, picks_by_user, ss_streaks)
        final_ranking = rank_users(final_lb)
        winner_id = final_ranking[0] if final_ranking else None

        if early_losers and winner_id:
            for uid in early_losers:
                if uid in final_ranking:
                    rank = final_ranking.index(uid) + 1
                    metrics['early_loser_avg_rank'].append(rank / n_players)

        # Build full timeline of rankings at every elimination step
        all_rankings = []  # list of {uid: rank} at each step
        leaders = []
        for elim in range(1, max_elim + 1):
            lb = calculate_leaderboard(
                season, scoring, survivors, picks_by_user, ss_streaks, as_of=elim)
            if lb:
                ranking = rank_users(lb)
                uid_to_rank = {uid: i for i, uid in enumerate(ranking)}
                all_rankings.append(uid_to_rank)
                leaders.append(ranking[0])

        # Lead changes (kept for backwards compatibility but less weight)
        lead_changes = sum(
            1 for i in range(1, len(leaders)) if leaders[i] != leaders[i - 1])
        metrics['lead_changes'].append(lead_changes / max(len(leaders) - 1, 1))

        # Rank volatility: average absolute rank change per player per step
        if len(all_rankings) >= 2:
            total_rank_changes = 0
            n_transitions = 0
            for i in range(1, len(all_rankings)):
                prev, curr = all_rankings[i - 1], all_rankings[i]
                for uid in prev:
                    if uid in curr:
                        total_rank_changes += abs(prev[uid] - curr[uid])
                        n_transitions += 1
            if n_transitions > 0:
                metrics['rank_volatility'].append(total_rank_changes / n_transitions)

        # Comeback rate: how often does the leader at 50% NOT win?
        mid_idx = len(leaders) // 2
        if mid_idx > 0 and leaders and winner_id is not None:
            mid_leader = leaders[mid_idx - 1]  # leader at ~50%
            metrics['comeback_rate'].append(0.0 if mid_leader == winner_id else 1.0)

        # Suspense: % of steps where the eventual winner is NOT in 1st place
        if leaders and winner_id is not None:
            not_leading = sum(1 for l in leaders if l != winner_id)
            metrics['suspense'].append(not_leading / len(leaders))

        # Competitiveness at midpoint
        mid = max_elim // 2
        mid_lb = calculate_leaderboard(
            season, scoring, survivors, picks_by_user, ss_streaks, as_of=mid)
        if mid_lb:
            mid_scores = sorted(mid_lb.values(), reverse=True)
            if mid_scores[0] > 0:
                threshold = mid_scores[0] * 0.8
                competitive = sum(1 for s in mid_scores if s >= threshold)
                metrics['midpoint_competitive_pct'].append(competitive / n_players)

        # Late-game drama: gap between 1st and 2nd at 75%
        late = int(max_elim * 0.75)
        late_lb = calculate_leaderboard(
            season, scoring, survivors, picks_by_user, ss_streaks, as_of=late)
        if late_lb:
            late_scores = sorted(late_lb.values(), reverse=True)
            if len(late_scores) >= 2 and late_scores[0] > 0:
                gap = (late_scores[0] - late_scores[1]) / late_scores[0]
                metrics['late_game_gap'].append(gap)

        # Final spread
        final_scores = sorted(final_lb.values(), reverse=True)
        if final_scores[0] > 0:
            spread = (final_scores[0] - final_scores[-1]) / final_scores[0]
            metrics['final_spread'].append(spread)

        # Non-draft impact
        for user_id, picks in picks_by_user.items():
            draft_pts = non_draft_pts = 0
            for pick in picks:
                breakdown = scoring.calculate_survivor_points(pick.survivor, season)
                modified = scoring.apply_pick_modifier(
                    breakdown.total, pick.pick_type,
                    season.num_players, season.left_at_jury)
                if pick.pick_type == 'draft':
                    draft_pts += modified
                else:
                    non_draft_pts += modified
            streak = ss_streaks.get(user_id, 0)
            non_draft_pts += scoring.calculate_sole_survivor_bonus(streak)
            total = draft_pts + non_draft_pts
            if total > 0:
                metrics['non_draft_pct'].append(non_draft_pts / total)

    # Average all metrics
    result = {}
    for key, values in metrics.items():
        result[key] = sum(values) / len(values) if values else 0

    # Composite score — heavily weighted toward drama and volatility
    # Normalize rank_volatility: typical range 0-1 positions per step,
    # divide by (n_players-1) to get 0-1 scale (approximation)
    norm_volatility = min(result.get('rank_volatility', 0) / 2.0, 1.0)

    result['composite'] = (
        result.get('early_loser_avg_rank', 1) * -0.5     # recovery (lower = better)
        + norm_volatility * 3.0                            # rank shuffling (higher = better)
        + result.get('comeback_rate', 0) * 2.5            # comebacks (higher = better)
        + result.get('suspense', 0) * 2.0                 # winner not always leading (higher = better)
        + result.get('midpoint_competitive_pct', 0) * 1.0 # bunched at midpoint
        + (1 - result.get('late_game_gap', 1)) * 1.5      # close at 75% (higher = better)
        + (1 - result.get('final_spread', 1)) * 0.5       # final closeness
        + min(result.get('non_draft_pct', 0), 0.4) * 0.5  # wildcard/SS matter
    )
    return result


# ── Parallel evaluation ─────────────────────────────────────────────────────

_worker_scenarios = None


def _init_worker(scenarios):
    global _worker_scenarios
    _worker_scenarios = scenarios


def _evaluate_worker(config):
    return (config, evaluate_config(config, _worker_scenarios))


# ── Output helpers ──────────────────────────────────────────────────────────

def print_metrics(metrics):
    print(f'  Rank volatility:      {metrics.get("rank_volatility", 0):.3f}'
          f'  (avg rank change per player per step)')
    print(f'  Comeback rate:        {metrics.get("comeback_rate", 0):.0%}'
          f'  (midpoint leader loses)')
    print(f'  Suspense:             {metrics.get("suspense", 0):.0%}'
          f'  (steps where winner not leading)')
    print(f'  Lead changes:         {metrics.get("lead_changes", 0):.2f}'
          f'  (per step)')
    print(f'  Early loser rank:     {metrics.get("early_loser_avg_rank", 0):.2f}'
          f'  (0-1, lower = better recovery)')
    print(f'  Midpoint competitive: {metrics.get("midpoint_competitive_pct", 0):.0%}'
          f'  (within 20% of lead)')
    print(f'  Late-game gap:        {metrics.get("late_game_gap", 0):.0%}'
          f'  (1st-2nd gap at 75%)')
    print(f'  Final spread:         {metrics.get("final_spread", 0):.0%}'
          f'  (1st-last gap)')
    print(f'  Non-draft impact:     {metrics.get("non_draft_pct", 0):.0%}'
          f'  (pts from wildcard/SS)')


def print_config_result(rank, config, metrics, label=None):
    if label:
        print(f'\n#{rank} — {label} — Composite: {metrics["composite"]:.3f}')
    else:
        print(f'\n#{rank} — Composite: {metrics["composite"]:.3f}')
    display = expand_config(config)
    diffs = {k: v for k, v in display.items() if v != DEFAULT_CONFIG.get(k)}
    if diffs:
        print(f'  Differs from default: {json.dumps(diffs)}')
    else:
        print(f'  (matches default config)')
    print_metrics(metrics)


# ── Timelines ───────────────────────────────────────────────────────────────

def build_season_timelines(seasons, recommended_config, seed=99):
    """Build leaderboard-over-time for representative seasons with 6-player drafts."""
    scoring = ClassicScoring(**recommended_config)
    rng = random.Random(seed)
    timelines = []

    for season in seasons:
        max_elim = max(s.voted_out_order for s in season.survivors if s.voted_out_order)
        winner = next(
            (s for s in season.survivors if s.voted_out_order == season.num_players), None)
        num_episodes = max_elim + 1

        # 6-player draft for timeline display
        draft = simulate_draft(season.survivors, 6, rng=rng)
        picks_by_user = {}
        for pid, survs in draft.items():
            picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]

        # Wildcard: pick from survivors who survived ep 1 (can overlap other rosters)
        wc_eligible = [s for s in season.survivors if s.voted_out_order != 1]
        for pid in picks_by_user:
            own_ids = {p.survivor_id for p in picks_by_user[pid]}
            pool = [s for s in wc_eligible if s.id not in own_ids]
            if pool:
                picks_by_user[pid].append(SimPick(rng.choice(pool), 'wildcard'))

        ss_streaks = {}
        for pid, picks in picks_by_user.items():
            draft_picks = [p for p in picks if p.pick_type == 'draft']
            if draft_picks:
                best = max(draft_picks, key=lambda p: p.survivor.voted_out_order or 999)
                if winner and best.survivor.voted_out_order == season.num_players:
                    ss_streaks[pid] = num_episodes
                else:
                    ss_streaks[pid] = 0

        steps = list(range(0, max_elim + 1))
        series = {uid: [] for uid in picks_by_user}
        for step in steps:
            lb = calculate_leaderboard(
                season, scoring, season.survivors, picks_by_user, ss_streaks,
                as_of=step, include_ss=(step == max_elim))
            for uid in picks_by_user:
                series[uid].append(round(lb.get(uid, 0), 1))

        datasets = []
        for uid in sorted(picks_by_user.keys(),
                          key=lambda u: series[u][-1], reverse=True):
            datasets.append({
                'name': f'Player {uid + 1}',
                'points': series[uid],
            })

        timelines.append({
            'season': f'Season {season.number}',
            'labels': [f'Elim {s}' if s > 0 else 'Start' for s in steps],
            'datasets': datasets,
        })

    return timelines


# ── Chart data ──────────────────────────────────────────────────────────────

def build_chart_data(results, timelines=None):
    """Build JSON for the web analysis page."""
    # Per-parameter impact
    param_impact = {}
    for param in PARAM_GRID:
        groups = defaultdict(list)
        for config, metrics in results:
            val = config.get(param, DEFAULT_CONFIG.get(param))
            if param == 'placement_ratio' and isinstance(val, (list, tuple)):
                label = f'{int(val[0]*100)}%:{int(val[1]*100)}%'
            else:
                label = str(val)
            groups[label].append(metrics['composite'])

        if param == 'placement_ratio':
            sorted_keys = sorted(groups.keys())
        else:
            sorted_keys = sorted(groups.keys(),
                                 key=lambda x: (x == 'True', x == 'False',
                                                float(x) if x not in ('True', 'False') else 0))
        param_impact[param] = {
            'values': sorted_keys,
            'avg': [round(sum(groups[k]) / len(groups[k]), 3) for k in sorted_keys],
            'std': [round((sum((s - sum(groups[k]) / len(groups[k])) ** 2
                               for s in groups[k]) / len(groups[k])) ** 0.5, 3)
                    for k in sorted_keys],
            'n': [len(groups[k]) for k in sorted_keys],
        }

    # Histogram
    all_composites = [m['composite'] for _, m in results]
    histogram_bins = 20
    min_c, max_c = min(all_composites), max(all_composites)
    bin_width = (max_c - min_c) / histogram_bins
    hist_labels = [round(min_c + i * bin_width, 2) for i in range(histogram_bins)]
    hist_counts = [0] * histogram_bins
    for c in all_composites:
        idx = min(int((c - min_c) / bin_width), histogram_bins - 1)
        hist_counts[idx] += 1

    # Top 10
    top10 = []
    for config, metrics in results[:10]:
        expanded = expand_config(config)
        diffs = {k: v for k, v in expanded.items() if v != DEFAULT_CONFIG.get(k)}
        top10.append({
            'diffs': diffs,
            'composite': round(metrics['composite'], 3),
            'metrics': {k: round(v, 3) for k, v in metrics.items()},
        })

    # Default rank
    default_rank = None
    for i, (config, metrics) in enumerate(results):
        expanded = expand_config(config)
        if all(expanded.get(k) == DEFAULT_CONFIG.get(k) for k in DEFAULT_CONFIG):
            default_rank = i + 1
            break

    return {
        'total_configs': len(results),
        'param_impact': param_impact,
        'histogram': {'labels': hist_labels, 'counts': hist_counts},
        'top10': top10,
        'default_rank': default_rank,
        'recommended': expand_config(results[0][0]),
        'timelines': timelines or [],
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Analyze scoring configs across all US Survivor seasons')
    parser.add_argument('--quick', action='store_true',
                        help='Fast iteration (500 configs, 3 drafts/size vs 3000 configs, 50 drafts/size)')
    parser.add_argument('--seasons', type=str, default=None,
                        help='Comma-separated season numbers (default: all 1-49)')
    parser.add_argument('--export-json', type=str, default=None,
                        help='Export chart JSON to this path')
    parser.add_argument('--cores', type=int, default=None,
                        help='Number of CPU cores to use (default: cpu_count - 2)')
    args = parser.parse_args()

    n_samples = 500 if args.quick else 3000
    drafts_per = 3 if args.quick else DRAFTS_PER_PLAYER_COUNT
    if args.seasons:
        season_numbers = [int(s.strip()) for s in args.seasons.split(',')]
    else:
        season_numbers = DEFAULT_SEASONS

    t0 = time.time()
    print(f'Loading survivor data for {len(season_numbers)} seasons from {SURVIVOR_XLSX}...')
    seasons = load_all_seasons(season_numbers)
    print(f'Loaded {len(seasons)} complete seasons in {time.time() - t0:.1f}s')

    print(f'Generating draft scenarios ({len(PLAYER_COUNTS)} sizes x '
          f'{drafts_per} drafts x {len(seasons)} seasons)...')
    scenarios = generate_scenarios(seasons, drafts_per=drafts_per)
    print(f'Generated {len(scenarios)} draft scenarios '
          f'(avg {sum(s[4] for s in scenarios) / len(scenarios):.0f} eliminations)')

    t0 = time.time()

    # ── Parameter sweep ──────────────────────────────────────────────
    print(f'\n{"="*80}')
    print(f'PARAMETER SWEEP ({n_samples} configs via stratified sampling, '
          f'{len(scenarios)} scenarios each)')
    print(f'{"="*80}')

    configs = latin_hypercube_sample(PARAM_GRID, n_samples)
    # Always include default config
    configs.append({k: DEFAULT_CONFIG.get(k) for k in PARAM_GRID
                    if k in DEFAULT_CONFIG})

    n_workers = args.cores if args.cores else max(1, mp.cpu_count() - 2)
    n_workers = max(1, min(n_workers, mp.cpu_count()))
    print(f'Evaluating {len(configs)} configs across {n_workers} CPU cores '
          f'({mp.cpu_count()} available)...')

    results = []
    with mp.Pool(n_workers, initializer=_init_worker, initargs=(scenarios,)) as pool:
        for i, result in enumerate(pool.imap_unordered(_evaluate_worker, configs)):
            results.append(result)
            done = i + 1
            if done % 200 == 0 or done == len(configs):
                elapsed = time.time() - t0
                rate = done / elapsed
                eta = (len(configs) - done) / rate
                print(f'  {done}/{len(configs)} evaluated'
                      f'  ({rate:.1f}/sec, ~{eta:.0f}s remaining)')

    results.sort(key=lambda x: x[1]['composite'], reverse=True)

    print(f'\nTOP 10 CONFIGURATIONS:')
    for i, (config, metrics) in enumerate(results[:10]):
        print_config_result(i + 1, config, metrics)

    # Default rank
    for i, (config, metrics) in enumerate(results):
        expanded = expand_config(config)
        if all(expanded.get(k) == DEFAULT_CONFIG.get(k) for k in DEFAULT_CONFIG):
            print(f'\n{"="*80}')
            print(f'CURRENT DEFAULT — Rank #{i+1} of {len(results)}')
            print_metrics(metrics)
            print(f'  Composite: {metrics["composite"]:.3f}')
            break

    # ── Pick type impact ─────────────────────────────────────────────
    print(f'\n\n{"="*80}')
    print('PICK TYPE IMPACT ANALYSIS')
    print(f'{"="*80}')

    for param, label, unit in [
        ('wildcard_multiplier', 'Wildcard multiplier',
         'e.g. 0.5 = wildcards earn half points'),
        ('replacement_multiplier', 'Replacement multiplier',
         'e.g. 0.5 = replacements earn half points'),
        ('replacement_deduction', 'Replacement pre-merge deduction',
         'subtract pre-merge tribals from replacement scores'),
        ('sole_survivor_val', 'Sole Survivor bonus',
         'points per episode in streak (0 = disabled)'),
    ]:
        groups = defaultdict(list)
        for config, metrics in results:
            groups[config.get(param, DEFAULT_CONFIG.get(param))].append(
                metrics['composite'])
        print(f'\n  {label} ({unit}):')
        sorted_keys = sorted(
            groups.keys(), key=lambda x: (isinstance(x, bool), x))
        best_val = max(sorted_keys, key=lambda v: sum(groups[v]) / len(groups[v]))
        for val in sorted_keys:
            scores = groups[val]
            avg = sum(scores) / len(scores)
            std = (sum((s - avg) ** 2 for s in scores) / len(scores)) ** 0.5
            marker = ' <-- best' if val == best_val else ''
            print(f'    When set to {str(val):>8}:  '
                  f'avg fun/fairness score = {avg:.3f}'
                  f' +/- {std:.3f}  ({len(scores)} configs tested){marker}')

    # ── Recommended config ───────────────────────────────────────────
    best_config, best_metrics = results[0]
    best_expanded = expand_config(best_config)
    print(f'\n\n{"="*80}')
    print('RECOMMENDED CONFIG (highest composite score)')
    print(f'{"="*80}')
    print(json.dumps(best_expanded, indent=2))
    print_metrics(best_metrics)
    print(f'  Composite: {best_metrics["composite"]:.3f}')

    # ── Export JSON ──────────────────────────────────────────────────
    if args.export_json:
        print(f'\nBuilding timelines for seasons {TIMELINE_SEASONS}...')
        timeline_seasons = [s for s in seasons if s.number in TIMELINE_SEASONS]
        timelines = build_season_timelines(timeline_seasons, best_expanded)
        export = build_chart_data(results, timelines)
        with open(args.export_json, 'w') as f:
            json.dump(export, f, indent=2)
        print(f'Exported chart data to {args.export_json}')

    elapsed = time.time() - t0
    print(f'\nTotal: {len(results)} configs x {len(scenarios)} scenarios in {elapsed:.1f}s')


if __name__ == '__main__':
    main()
