import itertools
import hashlib
import json
import logging
import os
import random
import time

import pandas as pd

from .data import SURVIVOR_DATA_FILE, get_idol_ids
from .models import Season, Survivor, Pick, User, SoleSurvivorPick, calculate_ss_streak
from .scoring import get_scoring_system, SCORING_STAT_KEYS

logger = logging.getLogger(__name__)

# If remaining players exceed this, use random sampling instead of exhaustive permutations
MAX_EXHAUSTIVE = 8
SAMPLE_SIZE = 50_000

# In-memory cache: {cache_key: (timestamp, frozen, projected, total_scenarios, exhaustive, rates)}
_cache = {}
CACHE_TTL = 300  # 5 minutes

# Cached historical rates (computed once from survivoR.xlsx, survives across requests)
_rates_cache = None


def _cache_key(season, scoring_config):
    """Build a cache key from season state + scoring config."""
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    state = {
        'season_id': season.id,
        'vo': {s.id: s.voted_out_order for s in survivors},
        'config': scoring_config,
    }
    raw = json.dumps(state, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


NEW_ERA_START = 41

def _compute_historical_rates():
    """Compute per-tribal-survived rates from new-era (41+) survivoR data.

    All rates (immunity, idols, advantages) use new-era seasons only,
    since this app exclusively supports new-era Survivor.

    Results are cached in memory (dataset doesn't change within a session).
    """
    global _rates_cache
    if _rates_cache is not None:
        return _rates_cache

    if not os.path.exists(SURVIVOR_DATA_FILE):
        logger.warning('survivoR.xlsx not found — using empty rates')
        _rates_cache = {
            'ii_per_pm_tribal': 0, 'idol_per_tribal': 0,
            'adv_per_tribal': 0, 'adv_play_per_tribal': 0,
            'n_seasons': 0,
            'career_ii_rates': {}, 'idol_play_rate': 0.5, 'idol_ids': set(),
        }
        return _rates_cache

    # Load survivoR data, filtered to new-era US seasons only
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    us_cast = castaways[
        (castaways['version'] == 'US') &
        (castaways['season'] >= NEW_ERA_START)
    ].copy()

    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    us_cr = challenge_results[
        (challenge_results['version'] == 'US') &
        (challenge_results['season'] >= NEW_ERA_START)
    ]

    # Individual immunity wins per castaway per season
    ii_wins = (us_cr[us_cr['won_individual_immunity'] == 1]
               .groupby(['season', 'castaway_id']).size()
               .reset_index(name='ii_wins'))

    season_sizes = us_cast.groupby('season')['castaway_id'].nunique()

    # --- Single pass over new-era seasons: immunity + idol/advantage rates ---
    all_post_merge_tribals = 0
    all_ii = 0
    all_tribals = 0
    all_idols = 0
    all_adv = 0
    all_adv_played = 0
    n_seasons = 0
    merge_thresholds = {}

    # Idol/advantage data
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    us_am = advantage_movement[
        (advantage_movement['version'] == 'US') &
        (advantage_movement['season'] >= NEW_ERA_START)
    ]

    idol_found = us_am[us_am['event'] == 'Found'].copy()
    try:
        adv_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details')
        idol_ids = get_idol_ids(adv_details[adv_details['version'] == 'US'])
    except Exception:
        idol_ids = set()

    idol_found_counts = (idol_found[idol_found['advantage_id'].isin(idol_ids)]
                         .groupby(['season', 'castaway_id']).size()
                         .reset_index(name='count'))
    all_found_counts = (idol_found
                        .groupby(['season', 'castaway_id']).size()
                        .reset_index(name='count'))
    adv_played = us_am[us_am['event'] == 'Played']
    played_counts = (adv_played
                     .groupby(['season', 'castaway_id']).size()
                     .reset_index(name='count'))

    for season_num in sorted(us_cast['season'].unique()):
        s_cast = us_cast[us_cast['season'] == season_num]
        n_players = int(season_sizes.get(season_num, 0))
        if n_players == 0:
            continue

        # Compute merge threshold from jury/finalist/winner columns
        n_jury = int(s_cast['jury'].sum()) if 'jury' in s_cast.columns else 0
        n_finalists = int(s_cast['finalist'].sum()) if 'finalist' in s_cast.columns else 0
        left_at_merge = n_jury + n_finalists + (1 if s_cast['winner'].sum() > 0 else 0)
        if left_at_merge == 0:
            left_at_merge = max(1, n_players // 2)
        merge_threshold = n_players - left_at_merge
        merge_thresholds[season_num] = merge_threshold

        for _, row in s_cast.iterrows():
            order = row.get('order')
            if pd.isna(order) or int(order) <= 0:
                continue
            tribals = int(order) - 1
            post_merge = max(0, tribals - merge_threshold)
            all_post_merge_tribals += post_merge
            all_tribals += tribals

            cid = row['castaway_id']
            # Immunity
            ii_row = ii_wins[(ii_wins['season'] == season_num) &
                             (ii_wins['castaway_id'] == cid)]
            all_ii += int(ii_row['ii_wins'].sum()) if len(ii_row) > 0 else 0

            # Idols/advantages
            idol_r = idol_found_counts[
                (idol_found_counts['season'] == season_num) &
                (idol_found_counts['castaway_id'] == cid)]
            all_idols += int(idol_r['count'].sum()) if len(idol_r) > 0 else 0

            adv_r = all_found_counts[
                (all_found_counts['season'] == season_num) &
                (all_found_counts['castaway_id'] == cid)]
            all_adv += int(adv_r['count'].sum()) if len(adv_r) > 0 else 0

            play_r = played_counts[
                (played_counts['season'] == season_num) &
                (played_counts['castaway_id'] == cid)]
            all_adv_played += int(play_r['count'].sum()) if len(play_r) > 0 else 0

        n_seasons += 1

    # --- Per-castaway career immunity win rates (new-era appearances only) ---
    us_valid = us_cast[us_cast['order'].notna() & (us_cast['order'] > 0)].copy()
    us_valid['tribals'] = us_valid['order'].astype(int) - 1
    us_valid['merge_threshold'] = us_valid['season'].map(merge_thresholds)
    us_valid = us_valid.dropna(subset=['merge_threshold'])
    us_valid['post_merge_tribals'] = (
        us_valid['tribals'] - us_valid['merge_threshold']).clip(lower=0)

    career_pm = us_valid.groupby('castaway_id')['post_merge_tribals'].sum()
    career_ii_total = ii_wins.groupby('castaway_id')['ii_wins'].sum()

    career_ii_rates = {}
    for cid in career_pm.index:
        pm = career_pm.get(cid, 0)
        if pm > 0:
            ii_count = career_ii_total.get(cid, 0) if cid in career_ii_total.index else 0
            career_ii_rates[cid] = ii_count / pm

    # --- Idol play rate: fraction of found idols that were played ---
    total_idol_found = int(idol_found_counts['count'].sum()) if len(idol_found_counts) > 0 else 0
    idol_played_events = us_am[
        (us_am['event'] == 'Played') & (us_am['advantage_id'].isin(idol_ids))]
    total_idol_played = len(idol_played_events)
    idol_play_rate = (total_idol_played / total_idol_found
                      if total_idol_found > 0 else 0.5)

    _rates_cache = {
        'ii_per_pm_tribal': all_ii / all_post_merge_tribals if all_post_merge_tribals else 0,
        'idol_per_tribal': all_idols / all_tribals if all_tribals else 0,
        'adv_per_tribal': all_adv / all_tribals if all_tribals else 0,
        'adv_play_per_tribal': all_adv_played / all_tribals if all_tribals else 0,
        'n_seasons': n_seasons,
        'career_ii_rates': career_ii_rates,
        'idol_play_rate': idol_play_rate,
        'idol_ids': idol_ids,
    }

    logger.info(
        'Historical rates computed from %d new-era seasons: '
        'ii=%.4f, idol=%.4f, adv=%.4f, adv_play=%.4f, '
        'career_ii_rates=%d castaways, idol_play_rate=%.2f',
        n_seasons, _rates_cache['ii_per_pm_tribal'],
        _rates_cache['idol_per_tribal'], _rates_cache['adv_per_tribal'],
        _rates_cache['adv_play_per_tribal'],
        len(career_ii_rates), idol_play_rate)

    return _rates_cache


def _score_all_users(scoring, users_with_picks, user_picks, surv_by_id,
                     season, current_elim_count, merge_threshold,
                     at_merge_stats, user_ss_picks):
    """Score all users and return the winner's user_id.

    at_merge_stats: dict {survivor_id: {attr: at_merge_val}} for replacement picks.
        Precomputed once to avoid repeated JSON parsing in the hot loop.
    """
    best_id = None
    best_pts = float('-inf')
    for user in users_with_picks:
        total = 0
        for pick in user_picks[user.id]:
            # Wildcard timing: skip if no eliminations yet
            if pick.pick_type == 'wildcard' and current_elim_count == 0:
                continue
            # Replacement timing: skip if merge hasn't happened
            if pick.pick_type in ('pmr_w', 'pmr_d') and current_elim_count < merge_threshold:
                continue

            survivor = surv_by_id[pick.survivor_id]
            stat_overrides = None
            if pick.pick_type in ('pmr_w', 'pmr_d') and at_merge_stats:
                merge_vals = at_merge_stats.get(survivor.id)
                if merge_vals:
                    stat_overrides = {
                        attr: max(0, (getattr(survivor, attr) or 0) - merge_val)
                        for attr, merge_val in merge_vals.items()
                    }
            modified, _ = scoring.score_pick(
                survivor, season, pick.pick_type, stat_overrides)
            total += modified

        # Sole survivor streak bonus
        ss_streak = calculate_ss_streak(user_ss_picks.get(user.id, []), season)
        total += scoring.calculate_sole_survivor_bonus(ss_streak)

        if total > best_pts:
            best_pts = total
            best_id = user.id
    return best_id


def _get_season_idol_holdings(season, remaining, idol_ids):
    """Get currently-held idols for remaining castaways from survivoR.xlsx.

    Returns dict mapping survivor DB id -> number of idols currently held.
    """
    if not idol_ids or not os.path.exists(SURVIVOR_DATA_FILE):
        return {}

    remaining_by_cid = {s.castaway_id: s.id for s in remaining if s.castaway_id}
    if not remaining_by_cid:
        return {}

    try:
        am = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
        season_am = am[(am['version'] == 'US') & (am['season'] == season.number)]
    except Exception:
        return {}

    holdings = {}
    for castaway_id, surv_id in remaining_by_cid.items():
        cast_idol_events = season_am[
            (season_am['castaway_id'] == castaway_id) &
            (season_am['advantage_id'].isin(idol_ids))
        ]
        found = len(cast_idol_events[cast_idol_events['event'] == 'Found'])
        played = len(cast_idol_events[cast_idol_events['event'] == 'Played'])
        held = max(0, found - played)
        if held > 0:
            holdings[surv_id] = held

    if holdings:
        logger.info('Idol holdings for season %d: %s',
                     season.number, holdings)
    return holdings


def _apply_idol_protection(ordering, idol_holdings, idol_play_rate):
    """Modify elimination ordering for idol protection in projected mode.

    Idol holders have a chance of playing their idol when they would be
    eliminated, surviving one additional tribal. Each idol protects once.
    """
    if not idol_holdings:
        return ordering

    result = list(ordering)
    protected = set()

    for i in range(len(result) - 1):
        surv = result[i]
        if surv.id in protected:
            continue
        n_idols = idol_holdings.get(surv.id, 0)
        if n_idols > 0 and random.random() < idol_play_rate:
            # Idol played successfully — swap with next person
            result[i], result[i + 1] = result[i + 1], result[i]
            protected.add(surv.id)

    return result


def calculate_win_probabilities(season):
    """Calculate each fantasy player's probability of winning the season.

    Computes two modes:
    - Frozen: uses current stats as-is, only simulates elimination order
    - Projected: adds expected future stats (immunity wins, idols, advantages)
      based on historical per-tribal rates from completed seasons

    Returns (frozen_results, projected_results, total_scenarios, exhaustive, rates).
    """
    config = season.get_scoring_config()
    key = _cache_key(season, config)

    # Check cache
    if key in _cache:
        ts, cached_frozen, cached_proj, cached_total, cached_exhaustive, cached_rates = _cache[key]
        if time.time() - ts < CACHE_TTL:
            frozen = {}
            projected = {}
            for uid, data in cached_frozen.items():
                user = User.query.get(uid)
                if user:
                    frozen[uid] = {**data, 'user': user}
            for uid, data in cached_proj.items():
                user = User.query.get(uid)
                if user:
                    projected[uid] = {**data, 'user': user}
            return frozen, projected, cached_total, cached_exhaustive, cached_rates

    scoring = get_scoring_system(season.scoring_system, config)
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    remaining = [s for s in survivors if s.voted_out_order == 0]
    eliminated = [s for s in survivors if s.voted_out_order > 0]

    empty = ({}, {}, 0, True, {})
    if not remaining:
        return empty

    current_max_vo = max((s.voted_out_order for s in eliminated), default=0)

    users_with_picks = (
        User.query.join(Pick).filter(Pick.season_id == season.id)
        .distinct().all()
    )
    if not users_with_picks:
        return empty

    # Pre-load picks
    user_picks = {}
    for user in users_with_picks:
        user_picks[user.id] = Pick.query.filter_by(
            user_id=user.id, season_id=season.id).all()

    surv_by_id = {s.id: s for s in survivors}

    # Historical rates for projected mode (cached from survivoR.xlsx)
    rates = _compute_historical_rates()

    # Per-player immunity rates (career history, falling back to base rate)
    career_rates = rates.get('career_ii_rates', {})
    base_ii_rate = rates['ii_per_pm_tribal']
    surv_ii_rates = {}
    for surv in remaining:
        if surv.castaway_id and surv.castaway_id in career_rates:
            surv_ii_rates[surv.id] = career_rates[surv.castaway_id]
        else:
            surv_ii_rates[surv.id] = base_ii_rate

    # Idol holdings for remaining castaways (for idol protection modeling)
    idol_holdings = _get_season_idol_holdings(
        season, remaining, rates.get('idol_ids', set()))
    idol_play_rate = rates.get('idol_play_rate', 0.5)

    # Determine permutations or sample
    n_remaining = len(remaining)
    exhaustive = n_remaining <= MAX_EXHAUSTIVE
    if exhaustive:
        orderings = list(itertools.permutations(remaining))
    else:
        orderings = []
        remaining_list = list(remaining)
        for _ in range(SAMPLE_SIZE):
            shuffled = remaining_list[:]
            random.shuffle(shuffled)
            orderings.append(tuple(shuffled))

    total_scenarios = len(orderings)
    frozen_counts = {u.id: 0 for u in users_with_picks}
    projected_counts = {u.id: 0 for u in users_with_picks}

    # Save original state
    original_vo = {s.id: s.voted_out_order for s in survivors}
    original_jury = {s.id: s.made_jury for s in survivors}
    original_ii = {s.id: s.individual_immunity_wins for s in remaining}
    original_idols = {s.id: s.idols_found for s in remaining}
    original_idols_played = {s.id: s.idols_played for s in remaining}
    original_adv = {s.id: s.advantages_found for s in remaining}
    original_adv_played = {s.id: s.advantages_played for s in remaining}

    merge_threshold = season.merge_threshold
    jury_threshold = merge_threshold
    current_post_merge = max(0, current_max_vo - merge_threshold)

    # Precompute at-merge stat values for replacement pick scoring
    # (avoids repeated JSON parsing in the hot loop)
    merge_episode = None
    for s in survivors:
        if s.voted_out_order and s.voted_out_order == merge_threshold and s.elimination_episode:
            merge_episode = s.elimination_episode
            break

    at_merge_stats = {}
    if merge_episode:
        # Collect all survivor IDs that have replacement picks
        replacement_surv_ids = set()
        for user in users_with_picks:
            for pick in user_picks[user.id]:
                if pick.pick_type in ('pmr_w', 'pmr_d'):
                    replacement_surv_ids.add(pick.survivor_id)
        for sid in replacement_surv_ids:
            surv = surv_by_id[sid]
            ep_stats = surv.get_episode_stats()
            merge_data = ep_stats.get(str(merge_episode), {})
            at_merge_stats[sid] = {
                attr: int(merge_data.get(ep_key, 0))
                for ep_key, attr in SCORING_STAT_KEYS.items()
            }

    # Pre-fetch sole survivor picks for SS bonus
    user_ss_picks = {}
    for user in users_with_picks:
        user_ss_picks[user.id] = SoleSurvivorPick.query.filter_by(
            user_id=user.id, season_id=season.id).all()

    for ordering in orderings:
        # --- Frozen mode: original ordering, current stats ---
        for i, surv in enumerate(ordering):
            surv.voted_out_order = current_max_vo + i + 1
        n_fin = season.n_finalists
        finalist_threshold = season.num_players - n_fin
        for s in survivors:
            if s.voted_out_order > jury_threshold:
                # Finalists and winner are NOT jury members
                s.made_jury = s.voted_out_order <= finalist_threshold

        winner_frozen = _score_all_users(
            scoring, users_with_picks, user_picks, surv_by_id, season,
            current_max_vo, merge_threshold, at_merge_stats, user_ss_picks)
        if winner_frozen is not None:
            frozen_counts[winner_frozen] += 1

        # --- Projected mode: idol-protected ordering + per-player stats ---
        # Apply idol protection (may reorder eliminations)
        proj_ordering = _apply_idol_protection(
            ordering, idol_holdings, idol_play_rate)

        # Set projected elimination order
        for i, surv in enumerate(proj_ordering):
            surv.voted_out_order = current_max_vo + i + 1
        # Re-compute jury for remaining (ordering may differ from frozen)
        for surv in remaining:
            surv.made_jury = (surv.voted_out_order > jury_threshold
                              and surv.voted_out_order <= finalist_threshold)

        # Adjust stats with per-player immunity rates
        for surv in remaining:
            sim_tribals = surv.voted_out_order - 1
            additional_tribals = max(0, sim_tribals - current_max_vo)
            sim_post_merge = max(0, sim_tribals - merge_threshold)
            additional_post_merge = max(0, sim_post_merge - current_post_merge)

            surv.individual_immunity_wins = (
                (original_ii[surv.id] or 0)
                + additional_post_merge * surv_ii_rates[surv.id]
            )
            surv.idols_found = (
                (original_idols[surv.id] or 0)
                + additional_tribals * rates['idol_per_tribal']
            )
            surv.idols_played = (
                (original_idols_played[surv.id] or 0)
                + additional_tribals * rates['idol_per_tribal']
                * rates.get('idol_play_rate', 0.5)
            )
            surv.advantages_found = (
                (original_adv[surv.id] or 0)
                + additional_tribals * rates['adv_per_tribal']
            )
            surv.advantages_played = (
                (original_adv_played[surv.id] or 0)
                + additional_tribals * rates['adv_play_per_tribal']
            )

        winner_proj = _score_all_users(
            scoring, users_with_picks, user_picks, surv_by_id, season,
            current_max_vo, merge_threshold, at_merge_stats, user_ss_picks)
        if winner_proj is not None:
            projected_counts[winner_proj] += 1

        # Restore all state
        for s in survivors:
            s.voted_out_order = original_vo[s.id]
            s.made_jury = original_jury[s.id]
        for surv in remaining:
            surv.individual_immunity_wins = original_ii[surv.id]
            surv.idols_found = original_idols[surv.id]
            surv.idols_played = original_idols_played[surv.id]
            surv.advantages_found = original_adv[surv.id]
            surv.advantages_played = original_adv_played[surv.id]

    # Build results
    frozen_results = {}
    projected_results = {}
    for user in users_with_picks:
        frozen_results[user.id] = {
            'user': user,
            'scenarios_won': frozen_counts[user.id],
            'win_pct': frozen_counts[user.id] / total_scenarios * 100 if total_scenarios else 0,
        }
        projected_results[user.id] = {
            'user': user,
            'scenarios_won': projected_counts[user.id],
            'win_pct': projected_counts[user.id] / total_scenarios * 100 if total_scenarios else 0,
        }

    frozen_results = dict(sorted(
        frozen_results.items(), key=lambda x: x[1]['win_pct'], reverse=True))
    projected_results = dict(sorted(
        projected_results.items(), key=lambda x: x[1]['win_pct'], reverse=True))

    # Cache (without user objects)
    cache_frozen = {uid: {k: v for k, v in d.items() if k != 'user'}
                    for uid, d in frozen_results.items()}
    cache_proj = {uid: {k: v for k, v in d.items() if k != 'user'}
                  for uid, d in projected_results.items()}
    _cache[key] = (time.time(), cache_frozen, cache_proj,
                   total_scenarios, exhaustive, rates)

    return frozen_results, projected_results, total_scenarios, exhaustive, rates


def clear_cache():
    """Clear the prediction cache (call after data refresh)."""
    global _rates_cache
    _cache.clear()
    _rates_cache = None
