"""Analyze scoring configurations across all US Survivor seasons with simulated drafts.

Loads castaway data from survivoR.xlsx and simulates random snake drafts with 4-10
fantasy players per season. Evaluates scoring configs on fun/fairness metrics:
- Recovery: Can a player who loses a pick early still win?
- Volatility: Do standings shift throughout the season?
- Competitiveness: Are multiple players in contention at the midpoint?
- Late-game drama: Is the leader at 75% still beatable?
- Pick-type impact: Do wildcards and sole survivor picks matter?

Usage:
    python analyze_scoring.py                    # full sweep (25000 configs, 9 modern seasons)
    python analyze_scoring.py --quick            # fast iteration (500 configs)
    python analyze_scoring.py --samples 50000    # custom sample count
    python analyze_scoring.py --export-json PATH # export chart data for web page
"""
import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data import SURVIVOR_DATA_FILE, download_survivor_data, get_idol_ids, get_fire_winners
from app.scoring.classic import ClassicScoring, DEFAULT_CONFIG, LEGACY_CONFIG

DEFAULT_SEASONS = list(range(41, 50))  # modern Survivor (idols, advantages, new era)
PLAYER_COUNTS = [4, 5, 6, 7, 8, 9, 10]
DRAFTS_PER_PLAYER_COUNT = 50  # random draft orders per size per season
MIN_PICKS_PER_PLAYER = 4
TIMELINE_SEASONS = [42, 45, 47, 49]  # representative modern seasons


# ── Parameter grid (5 values per param) ─────────────────────────────────────

PARAM_GRID = {
    # Progressive tribal values
    'tribal_base': [0.5, 1, 1.5, 2],
    'tribal_step': [0, 0.25, 0.5, 1],
    'post_merge_step': [0, 0.5, 1, 1.5, 2],
    'finale_step': [0, 0.5, 1, 1.5, 2, 3],
    # Milestone bonuses (test at 0 — likely redundant with progressive tribals)
    'jury_val': [0, 1, 2, 3],
    'merge_val': [0, 1, 2, 3],
    'final_tribal_val': [0, 1, 2, 3],
    'fire_win_val': [0, 1, 2, 3],
    # Placement
    'first_val': [5, 8, 10, 15, 20],
    'placement_ratio': [
        (0, 0),          # winner only — no 2nd/3rd bonus
        (0.33, 0.17),    # winner takes most (e.g. 12:4:2)
        (0.50, 0.25),    # steep drop (e.g. 10:5:2.5)
        (0.60, 0.30),    # moderate drop
        (0.70, 0.40),    # gentle (e.g. 10:7:4)
    ],
    # Bonus categories (test at low values — likely redundant with longevity)
    'individual_immunity_val': [0, 0.5, 1],
    'tribal_immunity_val': [0, 0.5, 1],
    'idol_found_val': [0, 0.5, 1],
    'advantage_found_val': [0, 0.5, 1],
    'idol_play_val': [0, 0.5, 1],
    'advantage_play_val': [0, 0.5, 1],
    # Pick type modifiers
    'wildcard_multiplier': [0, 0.5],
    'draft_replacement_multiplier': [0, 0.5, 1],
    'wc_replacement_multiplier': [0, 1],
    # replacement_deduction always True — replacements never get retroactive points
    # Sole Survivor streak
    # sole_survivor_val excluded from optimization — tuned separately as a
    # fun bonus that shouldn't distort scoring balance.  Locked at 0 during
    # the sweep so it can't inflate comeback/suspense/non-draft metrics.
}


def expand_config(config):
    """Expand shorthand params into full config keys.

    - placement_ratio -> second_val, third_val (snapped to 0.5 increments)
    - When tribal_base is set, remove tribal_val/post_merge_tribal_val so
      ClassicScoring uses progressive mode.
    """
    expanded = dict(config)
    ratio = expanded.pop('placement_ratio', None)
    if ratio:
        first = expanded.get('first_val', 10)
        expanded['second_val'] = round(first * ratio[0] * 2) / 2
        expanded['third_val'] = round(first * ratio[1] * 2) / 2
    # Progressive tribal mode: clear flat keys so ClassicScoring detects tribal_base
    if 'tribal_base' in expanded and expanded['tribal_base'] is not None:
        expanded.pop('tribal_val', None)
        expanded.pop('post_merge_tribal_val', None)
    return expanded


# ── Lightweight data objects (no DB dependency) ─────────────────────────────

class SimSurvivor:
    __slots__ = ('id', 'name', 'voted_out_order', 'made_jury',
                 'individual_immunity_wins', 'tribal_immunity_wins',
                 'idols_found', 'idols_played', 'advantages_found', 'advantages_played',
                 'won_fire', 'elimination_episode', 'episode_stats')

    def __init__(self, id, name, voted_out_order, made_jury,
                 individual_immunity_wins=0, tribal_immunity_wins=0,
                 idols_found=0, idols_played=0, advantages_found=0, advantages_played=0,
                 won_fire=False, elimination_episode=None, episode_stats=None):
        self.id = id
        self.name = name
        self.voted_out_order = voted_out_order
        self.made_jury = made_jury
        self.individual_immunity_wins = individual_immunity_wins
        self.tribal_immunity_wins = tribal_immunity_wins
        self.idols_found = idols_found
        self.idols_played = idols_played
        self.advantages_found = advantages_found
        self.advantages_played = advantages_played
        self.won_fire = won_fire
        self.elimination_episode = elimination_episode
        self.episode_stats = episode_stats or {}

    def get_episode_stats(self):
        return self.episode_stats


class SimSeason:
    def __init__(self, number, name, num_players, left_at_jury, n_finalists, survivors):
        self.number = number
        self.name = name
        self.num_players = num_players
        self.left_at_jury = left_at_jury
        self.n_finalists = n_finalists
        self.survivors = survivors

    @property
    def merge_threshold(self):
        if self.left_at_jury is None:
            return None
        return self.num_players - self.left_at_jury

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
        season_numbers = DEFAULT_SEASONS
    invalid = [s for s in season_numbers if s < 41]
    if invalid:
        raise ValueError(f'Only new-era seasons (41+) are supported. Invalid: {invalid}')

    if not os.path.exists(SURVIVOR_DATA_FILE):
        print(f'{SURVIVOR_DATA_FILE} not found, downloading...')
        download_survivor_data()

    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, 'Season Summary')
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details')

    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, 'Vote History')

    us_cast = castaways[castaways['version'] == 'US']
    us_ss = season_summary[season_summary['version'] == 'US']
    us_cr = challenge_results[challenge_results['version'] == 'US']
    us_am = advantage_movement[advantage_movement['version'] == 'US']
    us_vh = vote_history[vote_history['version'] == 'US']

    # Fire challenge winners by (season, castaway_id)
    fire_rows = get_fire_winners(us_vh)
    fire_winner_set = set(zip(fire_rows['season'], fire_rows['castaway_id']))

    # Pre-compute stats grouped by (season, castaway_id)
    indiv_imm = us_cr[us_cr['won_individual_immunity'] == 1].groupby(
        ['season', 'castaway_id']).size()
    tribal_imm = us_cr[us_cr['won_tribal_immunity'] == 1].groupby(
        ['season', 'castaway_id']).size()

    # Per-episode incremental stats for immunity (global, no idol_id issue)
    def _ep_counts(df, filter_fn=None):
        """Return {(season, castaway_id): {episode: count}}."""
        if df.empty:
            return {}
        filtered = filter_fn(df) if filter_fn else df
        if filtered.empty:
            return {}
        grouped = filtered.groupby(['season', 'castaway_id', 'episode']).size()
        result = {}
        for (s, cid, ep), count in grouped.items():
            result.setdefault((s, cid), {})[int(ep)] = int(count)
        return result

    ii_by_ep = _ep_counts(us_cr, lambda df: df[df['won_individual_immunity'] == 1])
    ti_by_ep = _ep_counts(us_cr, lambda df: df[df['won_tribal_immunity'] == 1])

    seasons = []
    for snum in season_numbers:
        cast = us_cast[us_cast['season'] == snum]
        if cast.empty:
            continue
        ss_row = us_ss[us_ss['season'] == snum]
        if ss_row.empty:
            continue
        ss = ss_row.iloc[0]

        if pd.isna(ss['n_cast']) or pd.isna(ss['n_jury']) or pd.isna(ss['n_finalists']):
            raise ValueError(
                f'Season {snum}: survivoR data missing required fields '
                f'(n_cast, n_jury, n_finalists). Only new-era seasons (41+) are supported.')
        n_cast = int(ss['n_cast'])
        n_jury = int(ss['n_jury'])
        n_finalists = int(ss['n_finalists'])
        left_at_jury = n_jury + n_finalists

        season_name = str(ss['season_name']) if pd.notna(ss['season_name']) else f'Season {snum}'

        # Find max episode for this season (for building cumulative stats)
        s_episodes = set()
        s_cr_season = us_cr[us_cr['season'] == snum]
        s_am_season = us_am[us_am['season'] == snum]
        if not s_cr_season.empty:
            s_episodes.update(s_cr_season['episode'].dropna().astype(int))
        if not s_am_season.empty:
            s_episodes.update(s_am_season['episode'].dropna().astype(int))
        max_episode = max(s_episodes) if s_episodes else 0

        # Per-season idol IDs (advantage_id is per-season, not global)
        s_idol_ids = get_idol_ids(advantage_details, season_number=snum)

        # Per-season advantage stats (idols vs non-idol separated)
        if not s_am_season.empty:
            idols_found_s = s_am_season[
                s_am_season['event'].str.contains('Found', na=False) &
                s_am_season['advantage_id'].isin(s_idol_ids)
            ].groupby('castaway_id').size()
            idols_played_s = s_am_season[
                (s_am_season['event'] == 'Played') &
                s_am_season['advantage_id'].isin(s_idol_ids)
            ].groupby('castaway_id').size()
            adv_found_s = s_am_season[
                s_am_season['event'].str.contains('Found', na=False) &
                ~s_am_season['advantage_id'].isin(s_idol_ids)
            ].groupby('castaway_id').size()
            adv_played_s = s_am_season[
                (s_am_season['event'] == 'Played') &
                ~s_am_season['advantage_id'].isin(s_idol_ids)
            ].groupby('castaway_id').size()
        else:
            idols_found_s = idols_played_s = adv_found_s = adv_played_s = pd.Series(dtype=int)

        # Per-episode advantage stats (per-season idol filtering)
        def _season_ep_counts(df, filter_fn=None):
            if df.empty:
                return {}
            filtered = filter_fn(df) if filter_fn else df
            if filtered.empty:
                return {}
            grouped = filtered.groupby(['castaway_id', 'episode']).size()
            result = {}
            for (cid, ep), count in grouped.items():
                result.setdefault(cid, {})[int(ep)] = int(count)
            return result

        s_idol_found_by_ep = _season_ep_counts(
            s_am_season, lambda df: df[df['event'].str.contains('Found', na=False) &
                                        df['advantage_id'].isin(s_idol_ids)]
        ) if not s_am_season.empty else {}
        s_idol_play_by_ep = _season_ep_counts(
            s_am_season, lambda df: df[(df['event'] == 'Played') &
                                        df['advantage_id'].isin(s_idol_ids)]
        ) if not s_am_season.empty else {}
        s_adv_found_by_ep = _season_ep_counts(
            s_am_season, lambda df: df[df['event'].str.contains('Found', na=False) &
                                        ~df['advantage_id'].isin(s_idol_ids)]
        ) if not s_am_season.empty else {}
        s_adv_played_by_ep = _season_ep_counts(
            s_am_season, lambda df: df[(df['event'] == 'Played') &
                                        ~df['advantage_id'].isin(s_idol_ids)]
        ) if not s_am_season.empty else {}

        survs = []
        for _, row in cast.iterrows():
            order = int(row['order']) if pd.notna(row['order']) else 0
            # Jury status — prefer the boolean 'jury' column over 'jury_status'
            # ('jury_status' contains strings like '1st jury member', not just 'jury')
            made_jury = False
            if 'jury' in row.index and pd.notna(row['jury']):
                made_jury = bool(row['jury'])

            cid = row['castaway_id'] if pd.notna(row.get('castaway_id')) else None
            ii = int(indiv_imm.get((snum, cid), 0)) if cid else 0
            ti = int(tribal_imm.get((snum, cid), 0)) if cid else 0
            idf = int(idols_found_s.get(cid, 0)) if cid else 0
            ipd = int(idols_played_s.get(cid, 0)) if cid else 0
            af = int(adv_found_s.get(cid, 0)) if cid else 0
            ap = int(adv_played_s.get(cid, 0)) if cid else 0

            elim_ep = int(row['episode']) if pd.notna(row.get('episode')) else None

            # Build per-episode cumulative stats for scoring-relevant fields
            ep_stats = {}
            if cid and max_episode > 0:
                running = {'ii': 0, 'ti': 0, 'idol': 0, 'idol_play': 0, 'adv': 0, 'adv_play': 0}
                for ep in range(1, max_episode + 1):
                    running['ii'] += ii_by_ep.get((snum, cid), {}).get(ep, 0)
                    running['ti'] += ti_by_ep.get((snum, cid), {}).get(ep, 0)
                    running['idol'] += s_idol_found_by_ep.get(cid, {}).get(ep, 0)
                    running['idol_play'] += s_idol_play_by_ep.get(cid, {}).get(ep, 0)
                    running['adv'] += s_adv_found_by_ep.get(cid, {}).get(ep, 0)
                    running['adv_play'] += s_adv_played_by_ep.get(cid, {}).get(ep, 0)
                    ep_stats[ep] = dict(running)

            survs.append(SimSurvivor(
                id=len(survs), name=row['castaway'],
                voted_out_order=order, made_jury=made_jury,
                individual_immunity_wins=ii, tribal_immunity_wins=ti,
                idols_found=idf, idols_played=ipd,
                advantages_found=af, advantages_played=ap,
                won_fire=(snum, cid) in fire_winner_set if cid else False,
                elimination_episode=elim_ep, episode_stats=ep_stats,
            ))

        max_order = max((s.voted_out_order for s in survs), default=0)
        if max_order == 0:
            continue

        seasons.append(SimSeason(
            number=snum, name=season_name,
            num_players=n_cast, left_at_jury=left_at_jury,
            n_finalists=n_finalists, survivors=survs,
        ))

    return seasons


# ── Draft simulation ────────────────────────────────────────────────────────

def snake_draft(survivors, n_players, rng, max_picks=None):
    """Snake draft survivors among n_players. Returns {pid: [SimSurvivor]}.

    If max_picks is set, each player takes at most that many picks (remaining
    survivors go undrafted).
    """
    available = list(survivors)
    rng.shuffle(available)
    picks = {i: [] for i in range(n_players)}
    round_num = 0
    while available:
        order = range(n_players) if round_num % 2 == 0 else range(n_players - 1, -1, -1)
        for p in order:
            if not available:
                break
            if max_picks and len(picks[p]) >= max_picks:
                continue
            picks[p].append(available.pop())
        # Stop if everyone is full
        if max_picks and all(len(picks[p]) >= max_picks for p in picks):
            break
        round_num += 1
    return picks


def simulate_draft(survivors, n_players, min_picks=MIN_PICKS_PER_PLAYER, rng=None):
    """Draft with sub-drafts if needed to ensure min_picks per player.

    Every player always drafts exactly min_picks survivors.  When there are
    too many players for one pool (n_players * min_picks > len(survivors)),
    players are split into balanced sub-groups that each draft the full
    survivor pool independently (survivors can be shared across groups).
    """
    if n_players * min_picks <= len(survivors):
        return snake_draft(survivors, n_players, rng, max_picks=min_picks)

    # Sub-drafts: balanced groups so each group can supply min_picks per member
    max_per_group = len(survivors) // min_picks
    n_groups = -(-n_players // max_per_group)  # ceil division
    base_size = n_players // n_groups
    remainder = n_players % n_groups
    groups = [base_size + (1 if i < remainder else 0) for i in range(n_groups)]

    all_picks = {}
    pid = 0
    for group_size in groups:
        sub = snake_draft(survivors, group_size, rng, max_picks=min_picks)
        for local_id in range(group_size):
            all_picks[pid] = sub[local_id]
            pid += 1
    return all_picks


def assign_extra_picks(season, picks_by_user, rng):
    """Add wildcard and replacement picks to a draft.

    Follows league rules:
    - Wildcard: one per player, random survivor who survived ep 1 (not on own roster).
    - Replacement (assigned after merge): one per player IF any pick was eliminated
      pre-merge. Type depends on which pick was lost:
      - pmr_d (full pts) if a draft pick was eliminated pre-merge.
      - pmr_w (half pts) if only the wildcard was eliminated pre-merge.
      Replacement is a random survivor still alive at the merge, not on own roster.
    """
    merge_thresh = season.merge_threshold

    # Wildcard: survivors who weren't first boot
    wc_eligible = [s for s in season.survivors if s.voted_out_order != 1]
    for pid in picks_by_user:
        own_ids = {p.survivor_id for p in picks_by_user[pid]}
        pool = [s for s in wc_eligible if s.id not in own_ids]
        if pool:
            picks_by_user[pid].append(SimPick(rng.choice(pool), 'wildcard'))

    # Replacement: only if a pick was eliminated pre-merge
    post_merge_alive = [s for s in season.survivors
                        if s.voted_out_order == 0
                        or s.voted_out_order > merge_thresh]
    for pid in picks_by_user:
        draft_elim_pre = False
        wc_elim_pre = False
        for p in picks_by_user[pid]:
            vo = p.survivor.voted_out_order
            if vo and 0 < vo <= merge_thresh:
                if p.pick_type == 'draft':
                    draft_elim_pre = True
                elif p.pick_type == 'wildcard':
                    wc_elim_pre = True

        if not draft_elim_pre and not wc_elim_pre:
            continue  # not eligible

        pick_type = 'pmr_d' if draft_elim_pre else 'pmr_w'
        own_ids = {p.survivor_id for p in picks_by_user[pid]}
        pool = [s for s in post_merge_alive if s.id not in own_ids]
        if pool:
            picks_by_user[pid].append(SimPick(rng.choice(pool), pick_type))


def compute_ss_streaks(season, picks_by_user, rng):
    """Simulate sole survivor picks and compute streaks.

    Models realistic SS pick behavior:
    - Initial pick: 60% chance from own draft roster, 40% any survivor.
    - When pick is eliminated pre-finale: always switch to a random remaining
      survivor (with roster bias).
    - Finale eliminations: no switches — picks are locked since multiple
      eliminations happen in the same episode. Finale size derived from
      survivoR n_finalists data (e.g., final 4 for old-school 2-finalist
      seasons, final 5 for modern 3-finalist seasons).
    - Streak: consecutive steps from the finale backwards where pick == winner.

    Uses elimination steps as a proxy for episodes (1 step = 1 elimination).
    """
    max_elim = max((s.voted_out_order for s in season.survivors if s.voted_out_order), default=0)
    winner = next(
        (s for s in season.survivors if s.voted_out_order == season.num_players), None)

    if not winner:
        return {pid: 0 for pid in picks_by_user}

    all_survivors = list(season.survivors)
    # Finale covers finalists + 2 eliminations (fire-making/final vote) in one episode
    # Uses actual n_finalists from survivoR data (2 for old-school, 3 for modern)
    finale_players = season.n_finalists + 2
    finale_step = max(1, season.num_players - finale_players)

    def pick_ss(roster_ids, pool, rng):
        """Pick an SS target, biased toward own roster."""
        roster_pool = [s for s in pool if s.id in roster_ids]
        if roster_pool and rng.random() < 0.6:
            return rng.choice(roster_pool)
        return rng.choice(pool) if pool else None

    ss_streaks = {}
    for pid, picks in picks_by_user.items():
        roster_ids = {p.survivor_id for p in picks if p.pick_type == 'draft'}

        # Initial pick (step 0 = pre-game)
        current_pick = pick_ss(roster_ids, all_survivors, rng)
        pick_at_step = {0: current_pick}

        for step in range(1, max_elim + 1):
            # Picks lock during the finale — no mid-episode switches
            if step >= finale_step:
                pick_at_step[step] = current_pick
                continue

            # If current pick was just eliminated, switch
            if current_pick and current_pick.voted_out_order == step:
                alive = [s for s in all_survivors
                         if s.voted_out_order == 0 or s.voted_out_order > step]
                new_pick = pick_ss(roster_ids, alive, rng)
                if new_pick:
                    current_pick = new_pick
            pick_at_step[step] = current_pick

        # Streak: consecutive steps from end where pick == winner
        streak = 0
        for step in range(max_elim, -1, -1):
            if pick_at_step.get(step) is winner:
                streak += 1
            else:
                break

        ss_streaks[pid] = streak

    return ss_streaks


def generate_scenarios(seasons, player_counts=None, drafts_per=None, seed=42):
    """Pre-generate many draft scenarios for each season.

    For each season × player count × draft repetition, simulates a snake draft
    with a different random order. Each player also gets one wildcard and one
    replacement pick.

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

        for n_players in player_counts:
            for _ in range(drafts_per):
                draft = simulate_draft(season.survivors, n_players, rng=rng)

                picks_by_user = {}
                for pid, survs in draft.items():
                    picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]

                assign_extra_picks(season, picks_by_user, rng)
                ss_streaks = compute_ss_streaks(season, picks_by_user, rng)

                scenarios.append((
                    season, season.survivors, picks_by_user, ss_streaks,
                    max_elim, n_players))

    return scenarios


# ── Scoring ─────────────────────────────────────────────────────────────────

_STAT_FIELDS = ('individual_immunity_wins', 'tribal_immunity_wins',
                 'idols_found', 'idols_played', 'advantages_found', 'advantages_played')
_EP_KEY_MAP = {
    'ii': 'individual_immunity_wins', 'ti': 'tribal_immunity_wins',
    'idol': 'idols_found', 'idol_play': 'idols_played',
    'adv': 'advantages_found', 'adv_play': 'advantages_played',
}


def _build_tribal_table(config, season):
    """Precompute tribal points (total, pre_merge) for each tribals_survived count.

    Returns a list indexed by tribals_survived (0..num_players).
    Each entry is (total_tribal_points, pre_merge_component).
    """
    merge_threshold = season.merge_threshold
    use_progressive = config.get('tribal_base') is not None

    table = [(0.0, 0.0)]  # k=0: no tribals survived

    for k in range(1, season.num_players + 1):
        if use_progressive:
            base = config['tribal_base']
            step = config.get('tribal_step', 0)
            pm_step = config.get('post_merge_step', 0)
            f_step = config.get('finale_step', 0)
            finale_size = config.get('finale_size', 5)
            finale_threshold = max(merge_threshold, season.num_players - finale_size)

            pre_count = min(k, merge_threshold)
            post_count = min(max(0, k - merge_threshold),
                             max(0, finale_threshold - merge_threshold))
            finale_count = max(0, k - finale_threshold)

            pre_merge = (pre_count * base + step * pre_count * (pre_count - 1) / 2
                         ) if pre_count > 0 else 0.0

            if post_count > 0:
                last_pre = base + (merge_threshold - 1) * step if merge_threshold > 0 else base
                post_merge = post_count * last_pre + pm_step * post_count * (post_count + 1) / 2
            else:
                post_merge = 0.0

            if finale_count > 0:
                last_pre = base + (merge_threshold - 1) * step if merge_threshold > 0 else base
                pm_count_full = max(0, finale_threshold - merge_threshold)
                last_post = last_pre + pm_count_full * pm_step
                finale_total = finale_count * last_post + f_step * finale_count * (finale_count + 1) / 2
            else:
                finale_total = 0.0

            total = pre_merge + post_merge + finale_total
        else:
            pre_merge_rate = config.get('tribal_val', 0)
            post_merge_rate = config.get('post_merge_tribal_val', pre_merge_rate)
            pre_count = min(k, merge_threshold)
            post_count = max(0, k - merge_threshold)
            pre_merge = pre_count * pre_merge_rate
            total = pre_merge + post_count * post_merge_rate

        table.append((total, pre_merge))

    return table


def _fast_score_total(survivor, config, season, tribal_table,
                      stat_overrides=None, is_replacement=False):
    """Compute modified points directly as a float (no PointBreakdown object).

    ~2x faster than score_pick by avoiding dict/object creation.
    For timeline scoring where breakdowns aren't needed.
    """
    elim_order = survivor.voted_out_order or 0
    if elim_order > 0:
        tribals = elim_order - 1
    else:
        tribals = season.current_tribal_count

    tribal_total, pre_merge_tribal = tribal_table[tribals]
    total = tribal_total

    merge_threshold = season.merge_threshold
    past_merge = elim_order > merge_threshold if elim_order else tribals > merge_threshold

    merge_bonus = 0.0
    if config['jury_val'] and survivor.made_jury:
        total += config['jury_val']
    if config['merge_val'] and past_merge:
        merge_bonus = config['merge_val']
        total += merge_bonus

    # Placement
    if elim_order > 0:
        n = season.num_players
        if elim_order == n:
            total += config['first_val']
            if config['final_tribal_val']:
                total += config['final_tribal_val']
        elif elim_order == n - 1:
            total += config['second_val']
            if config['final_tribal_val']:
                total += config['final_tribal_val']
        elif elim_order == n - 2:
            total += config['third_val']
            if config['final_tribal_val']:
                total += config['final_tribal_val']

    # Performance (use stat_overrides if provided)
    if stat_overrides:
        ii = stat_overrides.get('individual_immunity_wins', 0)
        ti = stat_overrides.get('tribal_immunity_wins', 0)
        idols = stat_overrides.get('idols_found', 0)
        idol_plays = stat_overrides.get('idols_played', 0)
        adv = stat_overrides.get('advantages_found', 0)
        adv_play = stat_overrides.get('advantages_played', 0)
    else:
        ii = survivor.individual_immunity_wins or 0
        ti = survivor.tribal_immunity_wins or 0
        idols = survivor.idols_found or 0
        idol_plays = survivor.idols_played or 0
        adv = survivor.advantages_found or 0
        adv_play = survivor.advantages_played or 0

    if config['individual_immunity_val'] and ii:
        total += ii * config['individual_immunity_val']
    if config['tribal_immunity_val'] and ti:
        total += ti * config['tribal_immunity_val']
    if config['idol_found_val'] and idols:
        total += idols * config['idol_found_val']
    if config['advantage_found_val'] and adv:
        total += adv * config['advantage_found_val']
    if config['idol_play_val'] and idol_plays:
        total += idol_plays * config['idol_play_val']
    if config['advantage_play_val'] and adv_play:
        total += adv_play * config['advantage_play_val']

    if config.get('fire_win_val') and survivor.won_fire:
        total += config['fire_win_val']

    # For replacement picks: remove pre-merge tribal and merge bonus
    if is_replacement:
        total -= pre_merge_tribal
        total -= merge_bonus

    return total


def _compute_sim_stat_overrides(survivor, merge_episode):
    """Compute post-merge-only stat overrides for a SimSurvivor replacement pick.

    Same logic as app/scoring compute_stat_overrides but works with SimSurvivor's
    episode_stats dict (int keys) instead of DB model's JSON (string keys).
    """
    if not merge_episode or not survivor.episode_stats:
        return None
    at_merge = survivor.episode_stats.get(merge_episode, {})
    overrides = {}
    for ep_key, attr in _EP_KEY_MAP.items():
        current_val = getattr(survivor, attr) or 0
        merge_val = at_merge.get(ep_key, 0)
        overrides[attr] = max(0, current_val - merge_val)
    return overrides


def _build_elim_to_episode(survivors):
    """Build elimination step -> episode mapping from survivor data."""
    return {s.voted_out_order: s.elimination_episode
            for s in survivors
            if s.voted_out_order and s.voted_out_order > 0 and s.elimination_episode}


def calculate_leaderboard(season, scoring, survivors, picks_by_user,
                          ss_streaks, as_of=None, include_ss=None,
                          elim_to_episode=None, return_breakdowns=False):
    """Calculate leaderboard at a given elimination point.

    Uses per-episode cumulative stats when available to set survivor attributes
    to their values at the target episode (same approach as routes.py _apply_as_of).
    Applies wildcard/replacement timing (skip wildcards at step 0, replacements before merge).
    SS bonus excluded for intermediate steps by default.

    When return_breakdowns=True, returns (results, breakdowns_by_user) where
    breakdowns_by_user = {user_id: [(modified, breakdown, pick_type), ...]}.
    """
    if include_ss is None:
        include_ss = (as_of is None)

    merge_thresh = season.merge_threshold

    if elim_to_episode is None:
        elim_to_episode = _build_elim_to_episode(survivors)
    merge_episode = elim_to_episode.get(merge_thresh, 0)

    originals = {}
    if as_of is not None:
        target_episode = elim_to_episode.get(as_of, 0) if as_of > 0 else 0

        n_fin = season.n_finalists
        fire_elim = season.num_players - n_fin
        finalist_threshold = season.num_players - n_fin
        for s in survivors:
            originals[s.id] = (
                s.voted_out_order, s.made_jury, s.won_fire,
                {f: getattr(s, f) for f in _STAT_FIELDS},
            )
            if s.voted_out_order and s.voted_out_order > as_of:
                s.voted_out_order = 0
                s.made_jury = False
            elif (s.voted_out_order > 0
                  and s.voted_out_order > merge_thresh
                  and s.voted_out_order <= finalist_threshold):
                # Force jury for post-merge boots; exclude finalists and winner
                s.made_jury = True

            # Fire-making hasn't happened yet at this point
            if as_of < fire_elim:
                s.won_fire = False

            # Set stats to their values at the target episode
            if target_episode > 0 and s.episode_stats:
                ep_data = s.episode_stats.get(target_episode, {})
                for ep_key, attr in _EP_KEY_MAP.items():
                    setattr(s, attr, ep_data.get(ep_key, 0))
            elif as_of == 0:
                for f in _STAT_FIELDS:
                    setattr(s, f, 0)

    results = {}
    all_breakdowns = {} if return_breakdowns else None
    for user_id, picks in picks_by_user.items():
        total = 0
        user_bds = [] if return_breakdowns else None
        for pick in picks:
            # Wildcard timing: skip if no eliminations yet
            if pick.pick_type == 'wildcard' and as_of is not None and as_of == 0:
                continue
            # Replacement timing: skip if merge hasn't happened
            if pick.pick_type in ('pmr_w', 'pmr_d') and as_of is not None and as_of < merge_thresh:
                continue

            # Compute stat_overrides for replacement picks
            stat_overrides = None
            if pick.pick_type in ('pmr_w', 'pmr_d'):
                stat_overrides = _compute_sim_stat_overrides(
                    pick.survivor, merge_episode)

            modified, breakdown = scoring.score_pick(
                pick.survivor, season, pick.pick_type, stat_overrides)
            total += modified
            if return_breakdowns:
                user_bds.append((modified, breakdown, pick.pick_type))
        if include_ss:
            streak = ss_streaks.get(user_id, 0)
            total += scoring.calculate_sole_survivor_bonus(streak)
        results[user_id] = total
        if return_breakdowns:
            all_breakdowns[user_id] = user_bds

    # Restore original values
    for s in survivors:
        if s.id in originals:
            s.voted_out_order, s.made_jury, s.won_fire, saved_stats = originals[s.id]
            for f, val in saved_stats.items():
                setattr(s, f, val)

    if return_breakdowns:
        return results, all_breakdowns
    return results


def rank_users(results):
    return sorted(results.keys(), key=lambda uid: results[uid], reverse=True)


# ── Sampling ────────────────────────────────────────────────────────────────

def stratified_random_sample(param_grid, n_samples, seed=42):
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
    fast_config = scoring.config  # has all DEFAULT_CONFIG keys merged
    metrics = defaultdict(list)

    for season, survivors, picks_by_user, ss_streaks, max_elim, n_players in scenarios:
        # Precompute elim→episode mapping once per scenario
        elim_to_ep = _build_elim_to_episode(survivors)
        merge_ep = elim_to_ep.get(season.merge_threshold, 0)

        # Recovery: average final rank of players who lost a pick in first 3 elims
        early_losers = set()
        for user_id, picks in picks_by_user.items():
            for pick in picks:
                if pick.survivor.voted_out_order and 1 <= pick.survivor.voted_out_order <= 3:
                    early_losers.add(user_id)

        # Build full timeline via forward-only pass (no per-step save/restore)
        # Save original state once, walk forward through eliminations
        orig_state = {}
        for s in survivors:
            orig_state[s.id] = (
                s.voted_out_order, s.made_jury, s.won_fire,
                {f: getattr(s, f) for f in _STAT_FIELDS},
            )
            # Reset to pre-game state
            s.voted_out_order = 0
            s.made_jury = False
            s.won_fire = False
            for f in _STAT_FIELDS:
                setattr(s, f, 0)

        # Sort survivors by elimination order for forward walk
        surv_by_elim = sorted(
            [s for s in survivors if orig_state[s.id][0] and orig_state[s.id][0] > 0],
            key=lambda s: orig_state[s.id][0])
        merge_thresh = season.merge_threshold
        merge_ep = elim_to_ep.get(merge_thresh, 0)

        # Precompute tribal points table and identify unique picked survivors
        tribal_table = _build_tribal_table(fast_config, season)
        picked_sids = set()
        replacement_sids = set()
        for picks in picks_by_user.values():
            for pick in picks:
                picked_sids.add(pick.survivor.id)
                if pick.pick_type in ('pmr_w', 'pmr_d'):
                    replacement_sids.add(pick.survivor.id)
        picked_survivors = [s for s in survivors if s.id in picked_sids]
        surv_map = {s.id: s for s in survivors}

        wildcard_mult = fast_config.get('wildcard_multiplier', 0.5)
        draft_repl_mult = fast_config.get('draft_replacement_multiplier', 1.0)
        wc_repl_mult = fast_config.get('wc_replacement_multiplier', fast_config.get('replacement_multiplier', 0.5))
        pre_jury = season.num_players - season.left_at_jury

        timeline_lbs = {}
        all_rankings = []
        leaders = []
        elim_idx = 0

        n_fin = season.n_finalists
        fire_elim = season.num_players - n_fin
        finalist_threshold = season.num_players - n_fin

        for elim in range(1, max_elim + 1):
            # Reveal this elimination: set the survivor's real voted_out_order
            while elim_idx < len(surv_by_elim) and orig_state[surv_by_elim[elim_idx].id][0] == elim:
                s = surv_by_elim[elim_idx]
                s.voted_out_order = orig_state[s.id][0]
                # Force jury for post-merge boots; exclude finalists and winner
                if (s.voted_out_order > merge_thresh
                        and s.voted_out_order <= finalist_threshold):
                    s.made_jury = True
                else:
                    s.made_jury = orig_state[s.id][1]
                elim_idx += 1

            # Restore won_fire once fire-making challenge has occurred
            if elim >= fire_elim:
                for s in survivors:
                    s.won_fire = orig_state[s.id][2]

            # Update all survivors' episode stats to this step's episode
            target_episode = elim_to_ep.get(elim, 0)
            if target_episode > 0:
                for s in survivors:
                    if s.episode_stats:
                        ep_data = s.episode_stats.get(target_episode, {})
                        for ep_key, attr in _EP_KEY_MAP.items():
                            setattr(s, attr, ep_data.get(ep_key, 0))

            # Score each unique picked survivor once (fast path, no PointBreakdown)
            base_scores = {}
            for s in picked_survivors:
                base_scores[s.id] = _fast_score_total(
                    s, fast_config, season, tribal_table)

            # Replacement survivors: score with stat_overrides (post-merge only)
            repl_scores = {}
            if elim >= merge_thresh and replacement_sids:
                for sid in replacement_sids:
                    s = surv_map[sid]
                    stat_ov = _compute_sim_stat_overrides(s, merge_ep)
                    if stat_ov:
                        repl_scores[sid] = _fast_score_total(
                            s, fast_config, season, tribal_table,
                            stat_overrides=stat_ov, is_replacement=True)

            # Build leaderboard via table lookups (no scoring calls)
            lb = {}
            for user_id, picks in picks_by_user.items():
                total = 0
                for pick in picks:
                    if pick.pick_type == 'wildcard' and elim == 0:
                        continue
                    if pick.pick_type in ('pmr_w', 'pmr_d') and elim < merge_thresh:
                        continue
                    sid = pick.survivor.id
                    if pick.pick_type in ('pmr_w', 'pmr_d'):
                        if sid in repl_scores:
                            base = repl_scores[sid]
                        else:
                            # Fallback: always deduct pre-merge tribals
                            base = base_scores.get(sid, 0) - pre_jury
                        mult = wc_repl_mult if pick.pick_type == 'pmr_w' else draft_repl_mult
                    elif pick.pick_type == 'wildcard':
                        base = base_scores.get(sid, 0)
                        mult = wildcard_mult
                    else:
                        base = base_scores.get(sid, 0)
                        mult = 1.0
                    total += base * mult
                lb[user_id] = total

            timeline_lbs[elim] = lb
            if lb:
                ranking = rank_users(lb)
                uid_to_rank = {uid: i for i, uid in enumerate(ranking)}
                all_rankings.append(uid_to_rank)
                leaders.append(ranking[0])

        # Restore original state before final leaderboard scoring
        for s in survivors:
            if s.id in orig_state:
                s.voted_out_order, s.made_jury, s.won_fire, saved_stats = orig_state[s.id]
                for f, val in saved_stats.items():
                    setattr(s, f, val)

        # Final leaderboard WITHOUT SS bonus — SS is tuned separately and must
        # not inflate comeback/suspense/draft_skill/final_spread metrics.
        zero_ss = {uid: 0 for uid in picks_by_user}
        final_lb, final_breakdowns = calculate_leaderboard(
            season, scoring, survivors, picks_by_user, zero_ss,
            elim_to_episode=elim_to_ep, return_breakdowns=True)
        final_ranking = rank_users(final_lb)
        winner_id = final_ranking[0] if final_ranking else None

        if early_losers and winner_id:
            for uid in early_losers:
                if uid in final_ranking:
                    rank = final_ranking.index(uid) + 1
                    metrics['early_loser_avg_rank'].append(rank / n_players)

        # Lead changes
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
            mid_leader = leaders[mid_idx]  # leader at ~50%
            metrics['comeback_rate'].append(0.0 if mid_leader == winner_id else 1.0)

        # Suspense: % of steps where the eventual winner is NOT in 1st place
        if leaders and winner_id is not None:
            not_leading = sum(1 for l in leaders if l != winner_id)
            metrics['suspense'].append(not_leading / len(leaders))

        # Competitiveness at midpoint (reuse timeline)
        mid = max_elim // 2
        mid_lb = timeline_lbs.get(mid, {})
        if mid_lb:
            mid_scores = sorted(mid_lb.values(), reverse=True)
            if mid_scores[0] > 0:
                threshold = mid_scores[0] * 0.8
                competitive = sum(1 for s in mid_scores if s >= threshold)
                metrics['midpoint_competitive_pct'].append(competitive / n_players)

        # Late-game drama: gap between 1st and 2nd at 75% (reuse timeline)
        late = int(max_elim * 0.75)
        late_lb = timeline_lbs.get(late, {})
        if late_lb:
            late_scores = sorted(late_lb.values(), reverse=True)
            if len(late_scores) >= 2 and late_scores[0] > 0:
                gap = (late_scores[0] - late_scores[1]) / late_scores[0]
                metrics['late_game_gap'].append(gap)

        # Competitiveness entering the finale (finale_size players remain)
        finale_size = fast_config.get('finale_size', 5)
        finale_elim = max(merge_thresh, season.num_players - finale_size)
        finale_lb = timeline_lbs.get(finale_elim, {})
        if finale_lb:
            finale_scores = sorted(finale_lb.values(), reverse=True)
            if finale_scores[0] > 0:
                threshold = finale_scores[0] * 0.8
                competitive = sum(1 for s in finale_scores if s >= threshold)
                metrics['finale_competitive_pct'].append(competitive / n_players)

        # Final spread
        final_scores = sorted(final_lb.values(), reverse=True)
        if final_scores[0] > 0:
            spread = (final_scores[0] - final_scores[-1]) / final_scores[0]
            metrics['final_spread'].append(spread)
            # Blowout: 1st place scored more than 2x last place
            metrics['blowout_rate'].append(
                1.0 if final_scores[0] > 2 * final_scores[-1] else 0.0)

        # Non-draft impact and longevity share (SS excluded — tuned separately)
        for user_id, pick_results in final_breakdowns.items():
            draft_pts = non_draft_pts = 0
            tribal_pts = 0
            for modified, breakdown, pick_type in pick_results:
                if pick_type == 'draft':
                    draft_pts += modified
                else:
                    non_draft_pts += modified
                # Apply same multiplier to tribal components so numerator
                # and denominator are on the same scale.
                raw_total = breakdown.total
                mult = (modified / raw_total) if raw_total else 1.0
                for k in ('pre_merge_tribal', 'post_merge_tribal', 'finale_tribal'):
                    tribal_pts += breakdown.items.get(k, 0) * mult
            total = draft_pts + non_draft_pts
            if total > 0:
                metrics['non_draft_pct'].append(non_draft_pts / total)
                metrics['longevity_share'].append(tribal_pts / total)

        # Draft skill correlation: do players who drafted better survivors
        # (higher average voted_out_order) finish higher?
        draft_quality = {}  # user_id -> avg voted_out_order of draft picks
        for user_id, picks in picks_by_user.items():
            draft_picks = [p for p in picks if p.pick_type == 'draft']
            valid_draft = [p for p in draft_picks
                           if p.survivor.voted_out_order]
            if valid_draft:
                avg_order = sum(
                    p.survivor.voted_out_order for p in valid_draft
                ) / len(valid_draft)
                draft_quality[user_id] = avg_order
        if len(draft_quality) >= 3 and final_ranking:
            # Spearman-style: correlation between draft quality rank and final rank
            quality_rank = sorted(draft_quality, key=lambda u: draft_quality[u], reverse=True)
            q_rank_map = {uid: i for i, uid in enumerate(quality_rank)}
            f_rank_map = {uid: i for i, uid in enumerate(final_ranking) if uid in q_rank_map}
            n_r = len(f_rank_map)
            if n_r >= 3:
                d_sq_sum = sum(
                    (q_rank_map[uid] - f_rank_map[uid]) ** 2 for uid in f_rank_map
                )
                # Spearman's rho: 1 - 6*sum(d^2) / (n*(n^2-1))
                rho = 1 - 6 * d_sq_sum / (n_r * (n_r ** 2 - 1))
                metrics['draft_skill_correlation'].append(rho)

    # Average and std dev for all metrics
    result = {}
    for key, values in metrics.items():
        if values:
            mean = sum(values) / len(values)
            result[key] = mean
            result[f'{key}_std'] = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        else:
            result[key] = 0
            result[f'{key}_std'] = 0

    # Composite score — balance draft skill, engagement mechanics, and game health
    norm_volatility = min(result.get('rank_volatility', 0) / 2.0, 1.0)
    longevity = result.get('longevity_share', 0)
    non_draft = result.get('non_draft_pct', 0)

    # Longevity: reward 50-80%, penalize outside that range
    # Prevents degenerate "tribals are everything" configs
    if longevity <= 0.8:
        longevity_score = longevity  # linear reward up to 0.8
    else:
        longevity_score = 0.8 - (longevity - 0.8) * 2.0  # penalize excess

    # Non-draft floor: wildcards/SS should contribute at least ~15%
    # Prevents optimizer from zeroing out engagement mechanics
    non_draft_score = min(non_draft, 0.35)  # cap benefit at 35%
    if non_draft < 0.10:
        non_draft_score -= (0.10 - non_draft) * 2.0  # penalize below 10%

    # Consistency penalty: penalize configs with high variance across drafts.
    # A config that reliably produces good games is better than one that
    # averages well but swings wildly depending on the draft.
    consistency_penalty = (
        result.get('comeback_rate_std', 0) * 1.0
        + result.get('suspense_std', 0) * 1.0
        + result.get('draft_skill_correlation_std', 0) * 0.5
        + result.get('final_spread_std', 0) * 0.5
    )

    result['composite'] = (
        result.get('draft_skill_correlation', 0) * 2.0    # good drafting wins (higher = better)
        + longevity_score * 2.0                             # tribals important but not everything
        + result.get('comeback_rate', 0) * 2.0             # comebacks possible
        + result.get('suspense', 0) * 2.0                  # winner not always leading
        + (1 - result.get('late_game_gap', 1)) * 1.5       # close at 75%
        + norm_volatility * 1.5                             # standings shift
        + result.get('midpoint_competitive_pct', 0) * 1.0  # bunched at midpoint
        + result.get('finale_competitive_pct', 0) * 1.0    # still in contention at finale
        + result.get('early_loser_avg_rank', 1) * -1.0     # recovery (lower = better)
        + (1 - result.get('final_spread', 1)) * 1.0        # final closeness
        - result.get('blowout_rate', 0) * 1.0              # penalize frequent blowouts
        + non_draft_score * 1.5                             # wildcards/SS picks matter
        - consistency_penalty                                # prefer reliable outcomes
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
    print(f'  Draft skill corr:     {metrics.get("draft_skill_correlation", 0):.3f}'
          f'  (good drafting → winning, weight=2.0)')
    print(f'  Longevity share:      {metrics.get("longevity_share", 0):.0%}'
          f'  (% pts from tribals, weight=2.0)')
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
    print(f'  Blowout rate:         {metrics.get("blowout_rate", 0):.0%}'
          f'  (1st > 2x last)')
    print(f'  Non-draft impact:     {metrics.get("non_draft_pct", 0):.0%}'
          f'  (pts from wildcard/SS)')
    # Consistency (std devs)
    cb_std = metrics.get('comeback_rate_std', 0)
    su_std = metrics.get('suspense_std', 0)
    ds_std = metrics.get('draft_skill_correlation_std', 0)
    fs_std = metrics.get('final_spread_std', 0)
    penalty = cb_std * 1.0 + su_std * 1.0 + ds_std * 0.5 + fs_std * 0.5
    print(f'  Consistency penalty:  {penalty:.3f}'
          f'  (comeback σ={cb_std:.2f}, suspense σ={su_std:.2f},'
          f' draft_skill σ={ds_std:.2f}, spread σ={fs_std:.2f})')


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

        # 6-player draft with wildcard + replacement picks
        draft = simulate_draft(season.survivors, 6, rng=rng)
        picks_by_user = {}
        for pid, survs in draft.items():
            picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]

        assign_extra_picks(season, picks_by_user, rng)
        ss_streaks = compute_ss_streaks(season, picks_by_user, rng)

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
            'labels': ['Sole Survivor' if s == season.num_players else
                       (f'Elim {s}' if s > 0 else 'Start') for s in steps],
            'datasets': datasets,
        })

    return timelines


def _score_timeline(season, scoring, picks_by_user, ss_streaks, max_elim):
    """Score a draft across all elimination steps. Returns {uid: [scores...]}."""
    steps = list(range(0, max_elim + 1))
    series = {uid: [] for uid in picks_by_user}
    for step in steps:
        lb = calculate_leaderboard(
            season, scoring, season.survivors, picks_by_user, ss_streaks,
            as_of=step, include_ss=(step == max_elim))
        for uid in picks_by_user:
            series[uid].append(round(lb.get(uid, 0), 1))
    return series


def build_comparison_timelines(seasons, recommended_config, n_candidates=50,
                               seed=42):
    """Build side-by-side legacy vs recommended timelines for the same drafts.

    Tries n_candidates random drafts per season and picks the one whose
    suspense (% of steps where the eventual winner is NOT leading) is closest
    to the median across all candidates.  This avoids cherry-picking a
    runaway-leader outlier or an unrealistically close game.
    """
    scoring_rec = ClassicScoring(**recommended_config)
    scoring_leg = ClassicScoring(**LEGACY_CONFIG)
    rng = random.Random(seed)
    comparisons = []

    for season in seasons:
        max_elim = max(s.voted_out_order for s in season.survivors
                       if s.voted_out_order)

        # Generate many candidate drafts and measure suspense for each
        candidates = []
        for _ in range(n_candidates):
            draft = simulate_draft(season.survivors, 6, rng=rng)
            picks_by_user = {}
            for pid, survs in draft.items():
                picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]
            assign_extra_picks(season, picks_by_user, rng)
            ss_streaks = compute_ss_streaks(season, picks_by_user, rng)

            series_rec = _score_timeline(
                season, scoring_rec, picks_by_user, ss_streaks, max_elim)

            # Quick suspense: % of steps where leader != final winner
            final_scores = {uid: series_rec[uid][-1] for uid in series_rec}
            winner_uid = max(final_scores, key=final_scores.get)
            leaders = []
            for step_idx in range(len(series_rec[winner_uid])):
                step_scores = {uid: series_rec[uid][step_idx]
                               for uid in series_rec}
                leaders.append(max(step_scores, key=step_scores.get))
            not_leading = sum(1 for l in leaders if l != winner_uid)
            suspense = not_leading / max(len(leaders), 1)

            candidates.append((suspense, picks_by_user, ss_streaks))

        # Pick the draft closest to median suspense
        suspense_values = sorted(c[0] for c in candidates)
        median_suspense = suspense_values[len(suspense_values) // 2]
        best = min(candidates, key=lambda c: abs(c[0] - median_suspense))
        _, picks_by_user, ss_streaks = best

        steps = list(range(0, max_elim + 1))
        labels = ['Sole Survivor' if s == season.num_players else
                  (f'Elim {s}' if s > 0 else 'Start') for s in steps]

        series_rec = _score_timeline(
            season, scoring_rec, picks_by_user, ss_streaks, max_elim)
        series_leg = _score_timeline(
            season, scoring_leg, picks_by_user, ss_streaks, max_elim)

        # Sort by recommended final score
        sorted_uids = sorted(picks_by_user.keys(),
                             key=lambda u: series_rec[u][-1], reverse=True)

        def make_datasets(series):
            return [{'name': f'Player {uid + 1}', 'points': series[uid]}
                    for uid in sorted_uids]

        comparisons.append({
            'season': f'Season {season.number}',
            'labels': labels,
            'recommended': {'datasets': make_datasets(series_rec)},
            'legacy': {'datasets': make_datasets(series_leg)},
        })

    return comparisons


def build_percentile_bands(seasons, recommended_config, n_drafts=50, seed=42):
    """Build 25th/50th/75th percentile bands for 1st-2nd gap across many drafts."""
    scoring = ClassicScoring(**recommended_config)
    rng = random.Random(seed)
    bands = []

    for season in seasons:
        max_elim = max(s.voted_out_order for s in season.survivors if s.voted_out_order)
        steps = list(range(0, max_elim + 1))

        # Collect gap at each step across many drafts
        gap_by_step = {step: [] for step in steps}

        for _ in range(n_drafts):
            draft = simulate_draft(season.survivors, 6, rng=rng)
            picks_by_user = {}
            for pid, survs in draft.items():
                picks_by_user[pid] = [SimPick(s, 'draft') for s in survs]
            assign_extra_picks(season, picks_by_user, rng)
            ss_streaks = compute_ss_streaks(season, picks_by_user, rng)

            for step in steps:
                lb = calculate_leaderboard(
                    season, scoring, season.survivors, picks_by_user, ss_streaks,
                    as_of=step, include_ss=(step == max_elim))
                scores = sorted(lb.values(), reverse=True)
                if len(scores) >= 2 and scores[0] > 0:
                    gap = (scores[0] - scores[1]) / scores[0]
                else:
                    gap = 0.0
                gap_by_step[step].append(gap)

        # Compute percentiles
        p25, p50, p75 = [], [], []
        for step in steps:
            vals = gap_by_step[step]
            p25.append(round(float(np.percentile(vals, 25)), 3))
            p50.append(round(float(np.percentile(vals, 50)), 3))
            p75.append(round(float(np.percentile(vals, 75)), 3))

        bands.append({
            'season': f'Season {season.number}',
            'labels': ['Sole Survivor' if s == season.num_players else
                       (f'Elim {s}' if s > 0 else 'Start') for s in steps],
            'p25': p25,
            'median': p50,
            'p75': p75,
            'n_drafts': n_drafts,
        })

    return bands


def build_season_health_stats(recommended_config, scenarios):
    """Compute aggregate game health metrics per season for a given config."""
    expanded = expand_config(recommended_config)
    # Run evaluate_config on just this config's scenarios, grouped by season
    season_metrics = defaultdict(lambda: defaultdict(list))

    scoring = ClassicScoring(**expanded)
    fast_config = scoring.config

    for season, survivors, picks_by_user, ss_streaks, max_elim, n_players in scenarios:
        snum = season.number
        elim_to_ep = _build_elim_to_episode(survivors)

        # Quick forward walk to collect per-step leaders and rankings
        orig_state = {}
        for s in survivors:
            orig_state[s.id] = (
                s.voted_out_order, s.made_jury, s.won_fire,
                {f: getattr(s, f) for f in _STAT_FIELDS},
            )
            s.voted_out_order = 0
            s.made_jury = False
            s.won_fire = False
            for f in _STAT_FIELDS:
                setattr(s, f, 0)

        surv_by_elim = sorted(
            [s for s in survivors if orig_state[s.id][0] and orig_state[s.id][0] > 0],
            key=lambda s: orig_state[s.id][0])
        merge_thresh = season.merge_threshold
        tribal_table = _build_tribal_table(fast_config, season)
        picked_sids = set()
        replacement_sids = set()
        for picks in picks_by_user.values():
            for pick in picks:
                picked_sids.add(pick.survivor.id)
                if pick.pick_type in ('pmr_w', 'pmr_d'):
                    replacement_sids.add(pick.survivor.id)
        picked_survivors = [s for s in survivors if s.id in picked_sids]
        surv_map = {s.id: s for s in survivors}

        wildcard_mult = fast_config.get('wildcard_multiplier', 0.5)
        draft_repl_mult = fast_config.get('draft_replacement_multiplier', 1.0)
        wc_repl_mult = fast_config.get('wc_replacement_multiplier', fast_config.get('replacement_multiplier', 0.5))
        pre_jury = season.num_players - season.left_at_jury
        merge_ep = elim_to_ep.get(merge_thresh, 0)

        n_fin = season.n_finalists
        fire_elim = season.num_players - n_fin
        finalist_threshold = season.num_players - n_fin

        leaders = []
        all_rankings = []
        timeline_lbs = {}
        elim_idx = 0

        for elim in range(1, max_elim + 1):
            while elim_idx < len(surv_by_elim) and orig_state[surv_by_elim[elim_idx].id][0] == elim:
                s = surv_by_elim[elim_idx]
                s.voted_out_order = orig_state[s.id][0]
                if s.voted_out_order > merge_thresh and s.voted_out_order <= finalist_threshold:
                    s.made_jury = True
                else:
                    s.made_jury = orig_state[s.id][1]
                elim_idx += 1

            if elim >= fire_elim:
                for s in survivors:
                    s.won_fire = orig_state[s.id][2]

            target_episode = elim_to_ep.get(elim, 0)
            if target_episode > 0:
                for s in survivors:
                    if s.episode_stats:
                        ep_data = s.episode_stats.get(target_episode, {})
                        for ep_key, attr in _EP_KEY_MAP.items():
                            setattr(s, attr, ep_data.get(ep_key, 0))

            base_scores = {}
            for s in picked_survivors:
                base_scores[s.id] = _fast_score_total(s, fast_config, season, tribal_table)

            repl_scores = {}
            if elim >= merge_thresh and replacement_sids:
                for sid in replacement_sids:
                    s = surv_map[sid]
                    stat_ov = _compute_sim_stat_overrides(s, merge_ep)
                    if stat_ov:
                        repl_scores[sid] = _fast_score_total(
                            s, fast_config, season, tribal_table,
                            stat_overrides=stat_ov, is_replacement=True)

            lb = {}
            for user_id, picks in picks_by_user.items():
                total = 0
                for pick in picks:
                    if pick.pick_type == 'wildcard' and elim == 0:
                        continue
                    if pick.pick_type in ('pmr_w', 'pmr_d') and elim < merge_thresh:
                        continue
                    sid = pick.survivor.id
                    if pick.pick_type in ('pmr_w', 'pmr_d'):
                        if sid in repl_scores:
                            base = repl_scores[sid]
                        else:
                            # Always deduct pre-merge tribals
                            base = base_scores.get(sid, 0) - pre_jury
                        mult = wc_repl_mult if pick.pick_type == 'pmr_w' else draft_repl_mult
                    elif pick.pick_type == 'wildcard':
                        base = base_scores.get(sid, 0)
                        mult = wildcard_mult
                    else:
                        base = base_scores.get(sid, 0)
                        mult = 1.0
                    total += base * mult
                lb[user_id] = total

            timeline_lbs[elim] = lb
            if lb:
                ranking = rank_users(lb)
                all_rankings.append({uid: i for i, uid in enumerate(ranking)})
                leaders.append(ranking[0])

        # Restore
        for s in survivors:
            if s.id in orig_state:
                s.voted_out_order, s.made_jury, s.won_fire, saved = orig_state[s.id]
                for f, val in saved.items():
                    setattr(s, f, val)

        # Compute scenario metrics
        zero_ss = {uid: 0 for uid in picks_by_user}
        final_lb, _ = calculate_leaderboard(
            season, scoring, survivors, picks_by_user, zero_ss,
            elim_to_episode=elim_to_ep, return_breakdowns=True)
        final_ranking = rank_users(final_lb)
        winner_id = final_ranking[0] if final_ranking else None

        lead_changes = sum(1 for i in range(1, len(leaders)) if leaders[i] != leaders[i - 1])
        season_metrics[snum]['lead_changes'].append(
            lead_changes / max(len(leaders) - 1, 1))

        if len(all_rankings) >= 2:
            total_rc = 0
            n_tr = 0
            for i in range(1, len(all_rankings)):
                prev, curr = all_rankings[i - 1], all_rankings[i]
                for uid in prev:
                    if uid in curr:
                        total_rc += abs(prev[uid] - curr[uid])
                        n_tr += 1
            if n_tr > 0:
                season_metrics[snum]['rank_volatility'].append(total_rc / n_tr)

        mid_idx = len(leaders) // 2
        if mid_idx > 0 and leaders and winner_id is not None:
            season_metrics[snum]['comeback_rate'].append(
                0.0 if leaders[mid_idx] == winner_id else 1.0)

        if leaders and winner_id is not None:
            not_leading = sum(1 for l in leaders if l != winner_id)
            season_metrics[snum]['suspense'].append(not_leading / len(leaders))

        mid_lb = timeline_lbs.get(max_elim // 2, {})
        if mid_lb:
            mid_scores = sorted(mid_lb.values(), reverse=True)
            if mid_scores[0] > 0:
                threshold = mid_scores[0] * 0.8
                competitive = sum(1 for s in mid_scores if s >= threshold)
                season_metrics[snum]['midpoint_competitive_pct'].append(
                    competitive / n_players)

        final_scores = sorted(final_lb.values(), reverse=True)
        if len(final_scores) >= 2 and final_scores[0] > 0:
            season_metrics[snum]['final_spread'].append(
                (final_scores[0] - final_scores[-1]) / final_scores[0])
            season_metrics[snum]['blowout_rate'].append(
                1.0 if final_scores[0] > 2 * final_scores[-1] else 0.0)

        # Competitiveness entering the finale
        finale_size = fast_config.get('finale_size', 5)
        finale_elim = max(season.merge_threshold, season.num_players - finale_size)
        finale_lb = timeline_lbs.get(finale_elim, {})
        if finale_lb:
            finale_scores = sorted(finale_lb.values(), reverse=True)
            if finale_scores[0] > 0:
                threshold = finale_scores[0] * 0.8
                competitive = sum(1 for s in finale_scores if s >= threshold)
                season_metrics[snum]['finale_competitive_pct'].append(
                    competitive / n_players)

    # Average and CI bands (1σ, 2σ, 3σ standard errors) per season
    result = {}
    for snum, mdict in sorted(season_metrics.items()):
        entry = {'n_drafts': len(mdict.get('lead_changes', []))}
        for key, vals in mdict.items():
            if vals:
                mean = sum(vals) / len(vals)
                std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
                se = std / len(vals) ** 0.5
                entry[f'avg_{key}'] = round(mean, 3)
                entry[f'se_{key}'] = round(se, 4)
            else:
                entry[f'avg_{key}'] = 0
                entry[f'se_{key}'] = 0
        result[str(snum)] = entry
    return result


# ── Chart data ──────────────────────────────────────────────────────────────

def build_chart_data(results, timelines=None, comparison_timelines=None,
                     percentile_bands=None, season_health=None,
                     legacy_health=None, n_scenarios=None):
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
                                                float(x) if x not in ('True', 'False', 'None') else -1e9))
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

    # Default rank + composite
    default_rank = None
    default_composite = None
    for i, (config, metrics) in enumerate(results):
        expanded = expand_config(config)
        if all(expanded.get(k) == DEFAULT_CONFIG.get(k) for k in DEFAULT_CONFIG):
            default_rank = i + 1
            default_composite = round(metrics['composite'], 3)
            break

    # Legacy rank + composite
    legacy_rank = None
    legacy_composite = None
    for i, (config, metrics) in enumerate(results):
        expanded = expand_config(config)
        if all(expanded.get(k) == LEGACY_CONFIG.get(k) for k in LEGACY_CONFIG
               if k in expanded):
            legacy_rank = i + 1
            legacy_composite = round(metrics['composite'], 3)
            break

    return {
        'total_configs': len(results),
        'n_scenarios': n_scenarios or 0,
        'param_impact': param_impact,
        'histogram': {'labels': hist_labels, 'counts': hist_counts},
        'top10': top10,
        'default_rank': default_rank,
        'default_composite': default_composite,
        'legacy_rank': legacy_rank,
        'legacy_composite': legacy_composite,
        'recommended': expand_config(results[0][0]),
        'timelines': timelines or [],
        'comparison_timelines': comparison_timelines or [],
        'percentile_bands': percentile_bands or [],
        'season_health': season_health or {},
        'legacy_health': legacy_health or {},
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Analyze scoring configs across all US Survivor seasons')
    parser.add_argument('--quick', action='store_true',
                        help='Fast iteration (500 configs, 3 drafts/size vs 25000 configs, 50 drafts/size)')
    parser.add_argument('--samples', type=int, default=None,
                        help='Number of configs to sample (overrides --quick default)')
    parser.add_argument('--seasons', type=str, default=None,
                        help='Comma-separated season numbers (default: all 1-49)')
    parser.add_argument('--export-json', type=str, default=None,
                        help='Export chart JSON to this path')
    parser.add_argument('--cores', type=int, default=None,
                        help='Number of CPU cores to use (default: cpu_count - 2)')
    parser.add_argument('--reexport', type=str, default=None,
                        help='Re-export from a saved checkpoint (skip optimization)')
    args = parser.parse_args()

    # Re-export mode: load saved results and jump to export
    if args.reexport:
        if not args.export_json:
            print('Error: --reexport requires --export-json')
            sys.exit(1)
        print(f'Loading results from {args.reexport}...')
        with open(args.reexport) as f:
            checkpoint = json.load(f)
        results = [(r['config'], r['metrics']) for r in checkpoint]
        print(f'Loaded {len(results)} configs')

        if args.seasons:
            season_numbers = [int(s.strip()) for s in args.seasons.split(',')]
        else:
            season_numbers = DEFAULT_SEASONS
        seasons = load_all_seasons(season_numbers)

        best_expanded = expand_config(results[0][0])
        timeline_seasons = [s for s in seasons if s.number in TIMELINE_SEASONS]

        print(f'Building visualizations for seasons {TIMELINE_SEASONS}...')
        timelines = build_season_timelines(timeline_seasons, best_expanded)
        comparisons = build_comparison_timelines(timeline_seasons, best_expanded)
        print('Building percentile bands (50 drafts per season)...')
        pct_bands = build_percentile_bands(timeline_seasons, best_expanded)

        print('Computing per-season health stats...')
        drafts_per = 3 if args.quick else DRAFTS_PER_PLAYER_COUNT
        scenarios = generate_scenarios(seasons, drafts_per=drafts_per)
        health = build_season_health_stats(results[0][0], scenarios)
        legacy_cfg = {k: LEGACY_CONFIG.get(k, DEFAULT_CONFIG.get(k))
                      for k in PARAM_GRID if k in LEGACY_CONFIG or k in DEFAULT_CONFIG}
        leg_health = build_season_health_stats(legacy_cfg, scenarios)

        export = build_chart_data(
            results, timelines,
            comparison_timelines=comparisons,
            percentile_bands=pct_bands,
            season_health=health,
            legacy_health=leg_health,
            n_scenarios=len(scenarios))
        with open(args.export_json, 'w') as f:
            json.dump(export, f, indent=2)
        print(f'Exported chart data to {args.export_json}')
        return

    n_samples = args.samples or (500 if args.quick else 25000)
    drafts_per = 3 if args.quick else DRAFTS_PER_PLAYER_COUNT
    if args.seasons:
        season_numbers = [int(s.strip()) for s in args.seasons.split(',')]
    else:
        season_numbers = DEFAULT_SEASONS

    t0 = time.time()
    print(f'Loading survivor data for {len(season_numbers)} seasons from {SURVIVOR_DATA_FILE}...')
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

    configs = stratified_random_sample(PARAM_GRID, n_samples)
    # Always include default config
    configs.append({k: DEFAULT_CONFIG.get(k) for k in PARAM_GRID
                    if k in DEFAULT_CONFIG})
    # Always include legacy (pre-site) config
    configs.append({k: LEGACY_CONFIG.get(k, DEFAULT_CONFIG.get(k))
                    for k in PARAM_GRID if k in LEGACY_CONFIG or k in DEFAULT_CONFIG})

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
    print(f'Phase 1 completed in {time.time() - t0:.1f}s')

    # ── Phase 2: Iterative neighborhood refinement ──────────────────
    # Take top configs, perturb each param ±2 steps, evaluate neighbors.
    # Repeat for 2 rounds — each round re-sorts and refines the new top.
    top_n = 50 if not args.quick else 5
    n_rounds = 2 if not args.quick else 1
    step_range = 2 if not args.quick else 1
    print(f'\n{"="*80}')
    print(f'PHASE 2: NEIGHBORHOOD REFINEMENT '
          f'(top {top_n}, ±{step_range} steps, {n_rounds} rounds)')
    print(f'{"="*80}')

    seen = {tuple(sorted(c.items())) for c, _ in results}
    prev_top10 = {tuple(sorted(c.items())) for c, _ in results[:10]}

    for round_num in range(1, n_rounds + 1):
        neighbor_configs = []
        for config, _ in results[:top_n]:
            for key in PARAM_GRID:
                values = PARAM_GRID[key]
                current = config.get(key)
                try:
                    idx = values.index(current)
                except ValueError:
                    continue
                for offset in range(-step_range, step_range + 1):
                    if offset == 0:
                        continue
                    neighbor_idx = idx + offset
                    if 0 <= neighbor_idx < len(values):
                        neighbor = {**config, key: values[neighbor_idx]}
                        sig = tuple(sorted(neighbor.items()))
                        if sig not in seen:
                            seen.add(sig)
                            neighbor_configs.append(neighbor)

        if not neighbor_configs:
            print(f'  Round {round_num}: no new neighbors to evaluate')
            continue

        print(f'  Round {round_num}: evaluating {len(neighbor_configs)} '
              f'neighbor configs...')
        t_refine = time.time()
        with mp.Pool(n_workers, initializer=_init_worker,
                      initargs=(scenarios,)) as pool:
            for i, result in enumerate(
                    pool.imap_unordered(_evaluate_worker, neighbor_configs)):
                results.append(result)
                done = i + 1
                if done % 200 == 0 or done == len(neighbor_configs):
                    elapsed = time.time() - t_refine
                    rate = done / elapsed
                    eta = (len(neighbor_configs) - done) / rate
                    print(f'    {done}/{len(neighbor_configs)} evaluated'
                          f'  ({rate:.1f}/sec, ~{eta:.0f}s remaining)')
        results.sort(key=lambda x: x[1]['composite'], reverse=True)
        new_in_top10 = sum(1 for c, _ in results[:10]
                           if tuple(sorted(c.items())) not in prev_top10)
        print(f'  Round {round_num}: {new_in_top10} new configs in top 10')
        prev_top10 = {tuple(sorted(c.items())) for c, _ in results[:10]}

    print(f'\nTotal: {len(results)} configs evaluated')

    # Save results checkpoint so we can re-export without re-running
    checkpoint_path = 'scoring_results.json'
    checkpoint = [{'config': config, 'metrics': metrics}
                  for config, metrics in results]
    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint, f)
    print(f'Saved results checkpoint to {checkpoint_path}')

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

    # Legacy rank
    for i, (config, metrics) in enumerate(results):
        expanded = expand_config(config)
        if all(expanded.get(k) == LEGACY_CONFIG.get(k) for k in LEGACY_CONFIG
               if k in expanded):
            print(f'\n{"="*80}')
            print(f'LEGACY (PRE-SITE) — Rank #{i+1} of {len(results)}')
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
        ('draft_replacement_multiplier', 'Draft replacement multiplier',
         'e.g. 0.5 = draft replacements earn half points'),
        ('wc_replacement_multiplier', 'Wildcard replacement multiplier',
         'e.g. 0.5 = wildcard replacements earn half points'),
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
        timeline_seasons = [s for s in seasons if s.number in TIMELINE_SEASONS]

        print(f'\nBuilding visualizations for seasons {TIMELINE_SEASONS}...')
        timelines = build_season_timelines(timeline_seasons, best_expanded)
        comparisons = build_comparison_timelines(timeline_seasons, best_expanded)
        print('Building percentile bands (50 drafts per season)...')
        pct_bands = build_percentile_bands(timeline_seasons, best_expanded)

        print('Computing per-season health stats...')
        health = build_season_health_stats(best_config, scenarios)
        legacy_cfg = {k: LEGACY_CONFIG.get(k, DEFAULT_CONFIG.get(k))
                      for k in PARAM_GRID if k in LEGACY_CONFIG or k in DEFAULT_CONFIG}
        leg_health = build_season_health_stats(legacy_cfg, scenarios)

        export = build_chart_data(
            results, timelines,
            comparison_timelines=comparisons,
            percentile_bands=pct_bands,
            season_health=health,
            legacy_health=leg_health,
            n_scenarios=len(scenarios))
        with open(args.export_json, 'w') as f:
            json.dump(export, f, indent=2)
        print(f'Exported chart data to {args.export_json}')

    elapsed = time.time() - t0
    print(f'\nTotal: {len(results)} configs x {len(scenarios)} scenarios in {elapsed:.1f}s')


if __name__ == '__main__':
    main()
