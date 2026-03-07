import itertools
import hashlib
import json
import random
import time

from .models import Survivor, Pick, User
from .scoring import get_scoring_system

# If remaining players exceed this, use random sampling instead of exhaustive permutations
MAX_EXHAUSTIVE = 8
SAMPLE_SIZE = 50_000

# In-memory cache: {cache_key: (timestamp, results, total_scenarios, exhaustive)}
_cache = {}
CACHE_TTL = 300  # 5 minutes


def _cache_key(season, scoring_config):
    """Build a cache key from season state + scoring config."""
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    # Key includes: season id, each survivor's voted_out_order, scoring config
    state = {
        'season_id': season.id,
        'vo': {s.id: s.voted_out_order for s in survivors},
        'config': scoring_config,
    }
    raw = json.dumps(state, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def calculate_win_probabilities(season):
    """Calculate each fantasy player's probability of winning the season.

    Results are cached for 5 minutes based on season state + scoring config.
    Returns (results_dict, total_scenarios, exhaustive).
    """
    config = season.get_scoring_config()
    key = _cache_key(season, config)

    # Check cache
    if key in _cache:
        ts, cached_results, cached_total, cached_exhaustive = _cache[key]
        if time.time() - ts < CACHE_TTL:
            # Rehydrate user objects for the cached results
            results = {}
            for uid, data in cached_results.items():
                user = User.query.get(uid)
                if user:
                    results[uid] = {**data, 'user': user}
            return results, cached_total, cached_exhaustive

    scoring = get_scoring_system(season.scoring_system, config)
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    remaining = [s for s in survivors if s.voted_out_order == 0]
    eliminated = [s for s in survivors if s.voted_out_order > 0]

    if not remaining:
        return {}, 0, True

    current_max_vo = max((s.voted_out_order for s in eliminated), default=0)

    users_with_picks = (
        User.query.join(Pick).filter(Pick.season_id == season.id)
        .distinct().all()
    )
    if not users_with_picks:
        return {}, 0, True

    # Pre-load picks
    user_picks = {}
    for user in users_with_picks:
        user_picks[user.id] = Pick.query.filter_by(
            user_id=user.id, season_id=season.id).all()

    surv_by_id = {s.id: s for s in survivors}

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
    win_counts = {u.id: 0 for u in users_with_picks}

    original_vo = {s.id: s.voted_out_order for s in survivors}
    original_jury = {s.id: s.made_jury for s in survivors}

    jury_threshold = season.num_players - season.left_at_jury

    for ordering in orderings:
        for i, surv in enumerate(ordering):
            surv.voted_out_order = current_max_vo + i + 1

        for s in survivors:
            if s.voted_out_order > jury_threshold:
                s.made_jury = True

        best_user_id = None
        best_points = float('-inf')

        for user in users_with_picks:
            total = 0
            for pick in user_picks[user.id]:
                survivor = surv_by_id[pick.survivor_id]
                breakdown = scoring.calculate_survivor_points(survivor, season)
                modified = scoring.apply_pick_modifier(
                    breakdown.total, pick.pick_type,
                    season.num_players, season.left_at_jury
                )
                total += modified

            if total > best_points:
                best_points = total
                best_user_id = user.id

        if best_user_id is not None:
            win_counts[best_user_id] += 1

        for s in survivors:
            s.voted_out_order = original_vo[s.id]
            s.made_jury = original_jury[s.id]

    # Build results
    results = {}
    for user in users_with_picks:
        results[user.id] = {
            'user': user,
            'scenarios_won': win_counts[user.id],
            'win_pct': win_counts[user.id] / total_scenarios * 100 if total_scenarios else 0,
        }

    results = dict(sorted(results.items(), key=lambda x: x[1]['win_pct'], reverse=True))

    # Cache (store without user objects — they can't be pickled across requests)
    cacheable = {uid: {k: v for k, v in d.items() if k != 'user'} for uid, d in results.items()}
    _cache[key] = (time.time(), cacheable, total_scenarios, exhaustive)

    return results, total_scenarios, exhaustive


def clear_cache():
    """Clear the prediction cache (call after data refresh)."""
    _cache.clear()
