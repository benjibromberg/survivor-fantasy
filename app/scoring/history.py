"""The league's scoring versions: what each was, where it came from.

A log, so every config here is a frozen copy rather than a reference to
DEFAULT_CONFIG, which changes when a new default ships. The Scoring changes
page uses it to say which version each season played under, and the
scoring analysis page to list the runs behind them.

Add a version when a new analysis run is adopted; never edit an old one.
"""

from .classic import DEFAULT_CONFIG, LEGACY_CONFIG

SCORING_VERSIONS = (
    {
        "key": "legacy",
        "name": "Legacy",
        "date": None,
        "configs_tested": None,
        "analysis": None,
        "note": "The league's scoring before this site: flat tribals, no "
        "simulation behind it.",
        "config": dict(LEGACY_CONFIG),
    },
    {
        "key": "run-6",
        "name": "Run 6",
        "date": "2026-03-13",
        "configs_tested": 103648,
        "analysis": "analysis/run-6-2026-03-13.json",
        "note": "Best of 103,648 simulated configs, with no rules on which "
        "configs were allowed. The default from Season 50.",
        "config": {
            "tribal_val": 0.5,
            "post_merge_tribal_val": 1,
            "tribal_base": 0.5,
            "tribal_step": 0,
            "post_merge_step": 0,
            "finale_step": 0,
            "finale_size": 5,
            "jury_val": 3,
            "merge_val": 0,
            "final_tribal_val": 2,
            "first_val": 5,
            "second_val": 0,
            "third_val": 0,
            "individual_immunity_val": 0,
            "tribal_immunity_val": 0,
            "idol_found_val": 0,
            "advantage_found_val": 0.5,
            "idol_play_val": 1,
            "advantage_play_val": 0,
            "fire_win_val": 2,
            "sole_survivor_val": 1,
            "wildcard_multiplier": 0.5,
            "draft_replacement_multiplier": 1,
            "wc_replacement_multiplier": 0,
            "replacement_deduction": True,
        },
    },
)

# Keys compared when matching a season's config to a version
_COMPARED_KEYS = tuple(DEFAULT_CONFIG)


def _effective(config):
    return {k: config.get(k, DEFAULT_CONFIG[k]) for k in _COMPARED_KEYS}


def describe_config(config):
    """Return (closest version, {key: (version value, config value)} differences).

    The closest version is the one with the fewest differing keys, so a
    season that adopted a run's recommendation with one value changed reads
    as that run plus the change, not as an unknown config.
    """
    effective = _effective(config)
    best, best_diffs = None, None
    for version in SCORING_VERSIONS:
        reference = _effective(version["config"])
        diffs = {
            k: (reference[k], effective[k])
            for k in _COMPARED_KEYS
            if reference[k] != effective[k]
        }
        if best_diffs is None or len(diffs) < len(best_diffs):
            best, best_diffs = version, diffs
    return best, best_diffs


def analysis_runs():
    """Versions that came from an analysis run, newest first."""
    return [v for v in reversed(SCORING_VERSIONS) if v["analysis"]]
