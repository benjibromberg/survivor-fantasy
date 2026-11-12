"""The league's scoring rules: orderings any scoring config must keep.

Set by Benji on 2026-11-11. The scoring search in analyze_scoring.py only
considers configs that keep them, and the Scoring changes page shows each
one with the season's values.

Each rule is a chain of config keys whose values must strictly decrease,
the last also exceeding `floor` unless that is None:

- reaching Final Tribal is worth more than making the jury;
- an individual immunity win more than a tribal one, a tribal one more than
  nothing;
- an idol played more than an idol found, more than an advantage played,
  more than an advantage found, more than nothing.
"""

ORDERINGS = (
    (("final_tribal_val", "jury_val"), None),
    (("individual_immunity_val", "tribal_immunity_val"), 0),
    (
        (
            "idol_play_val",
            "idol_found_val",
            "advantage_play_val",
            "advantage_found_val",
        ),
        0,
    ),
)


def chain_holds(values, floor):
    """True if `values` strictly decrease and the last exceeds `floor`."""
    if any(a <= b for a, b in zip(values, values[1:], strict=False)):
        return False
    return floor is None or values[-1] > floor


def satisfies_orderings(config):
    """True if `config` keeps every rule in ORDERINGS."""
    return all(
        chain_holds([config[k] for k in chain], floor) for chain, floor in ORDERINGS
    )


def rule_readout(config, labels):
    """Each rule with this config's values, for display.

    `labels` maps a config key to its display name (CONFIG_LABELS gives
    (name, description) pairs). Returns a list of
    {"steps": [(name, value), ...], "floor": floor, "holds": bool}.
    """
    readout = []
    for chain, floor in ORDERINGS:
        values = [config.get(k, 0) for k in chain]
        readout.append(
            {
                "steps": [
                    (labels[k][0], v) for k, v in zip(chain, values, strict=True)
                ],
                "floor": floor,
                "holds": chain_holds(values, floor),
            }
        )
    return readout
