from .classic import ClassicScoring

SCORING_SYSTEMS = [ClassicScoring()]

_registry = {s.name: type(s) for s in SCORING_SYSTEMS}


def get_scoring_system(name, config=None):
    cls = _registry.get(name, ClassicScoring)
    if config:
        return cls(**config)
    return cls()


# Episode-stat key -> model attribute for scoring-relevant stats
SCORING_STAT_KEYS = {
    'ii': 'individual_immunity_wins',
    'ti': 'tribal_immunity_wins',
    'idol': 'idols_found',
    'idol_play': 'idols_played',
    'adv': 'advantages_found',
    'adv_play': 'advantages_played',
}


def compute_stat_overrides(survivor, merge_episode):
    """Compute post-merge-only stat overrides for replacement pick scoring.

    Returns a dict {attr: post_merge_value} or None if merge_episode unavailable.
    """
    if not merge_episode:
        return None
    ep_stats = survivor.get_episode_stats()
    merge_data = ep_stats.get(str(merge_episode), {})
    overrides = {}
    for ep_key, attr in SCORING_STAT_KEYS.items():
        current_val = getattr(survivor, attr) or 0
        at_merge = int(merge_data.get(ep_key, 0))
        overrides[attr] = max(0, current_val - at_merge)
    return overrides
