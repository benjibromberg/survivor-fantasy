"""A leaderboard entry's points, split by scoring component.

One place for "how many of this player's points came from jury, immunity,
idols...", so pages that total it cannot disagree. Each pick's `breakdown` is
recorded BEFORE its pick-type multiplier (a wildcard's breakdown is its
castaway's full points), so every component is scaled by the pick's
modified / base points here. The scaled components then add up to the pick's
modified points, and a player's components to their total. The one exception
is a pick with no base points, which has nothing to scale: a replacement
scored by flat deduction can still come out negative there.

Pure functions over the plain dicts `_build_leaderboard` returns; the results
hold only plain values, so they are safe to cache past the request.
"""

# Plain words for the breakdown keys, for players reading the league pages.
# Lower case, so they read inside a sentence or after a number. The keys are
# the ones in routes.BREAKDOWN_LABELS, which keeps its short forms for the
# leaderboard charts.
COMPONENT_LABELS = {
    "pre_merge_tribal": "surviving tribals before the merge",
    "post_merge_tribal": "surviving tribals after the merge",
    "finale_tribal": "surviving tribals in the finale",
    "jury": "making the jury",
    "merge": "making the merge",
    "placement": "finishing place",
    "final_tribal": "reaching Final Tribal",
    "individual_immunity": "individual immunity",
    "tribal_immunity": "tribal immunity",
    "idols_found": "idols found",
    "advantages_found": "advantages found",
    "idol_plays": "idols played",
    "advantage_plays": "advantages played",
    "fire_win": "fire-making win",
    "sole_survivor_streak": "Sole Survivor pick",
}

# Below this a change is float noise: it would display as 0.00.
_NOISE = 0.005


def component_label(key):
    return COMPONENT_LABELS.get(key, key.replace("_", " "))


def _scale(pick):
    """The pick-type multiplier as it was applied: modified / base points."""
    base = pick.get("base_points") or 0
    return pick["modified_points"] / base if base else 1


def pick_contributions(entry):
    """Per pick: castaway, pick type label, points, and points by component.

    The components are scaled by the pick's multiplier, so a wildcard's add
    up to half its castaway's points, not all of them.
    """
    out = []
    for pick in entry["picks"]:
        scale = _scale(pick)
        out.append(
            {
                "survivor": pick["survivor"],
                "pick_type": pick["pick_type"],
                "points": pick["modified_points"],
                "components": {
                    key: value * scale
                    for key, value in (pick.get("breakdown") or {}).items()
                },
            }
        )
    return out


def component_totals(entry):
    """{component key: points} for one entry, Sole Survivor streak included."""
    totals = {}
    for pick in pick_contributions(entry):
        for key, value in pick["components"].items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _by_castaway(entry):
    """{component: {castaway: points}}. Summed, since one castaway can be both
    a drafted pick and the Sole Survivor pick."""
    out = {}
    for pick in pick_contributions(entry):
        for key, value in pick["components"].items():
            people = out.setdefault(key, {})
            people[pick["survivor"]] = people.get(pick["survivor"], 0) + value
    return out


def explain_change(before, after, limit=3, castaway_limit=3):
    """Why one player's total differs between two scorings of the same picks.

    `before` and `after` are the same player's leaderboard entry under each
    scoring. Returns {"delta", "reasons", "other", "rest"}: the change in
    total; the `limit` components that moved it most (largest first, each
    with the castaways behind it, largest first); whatever those reasons do
    not account for, so the parts always add up to the change; and the
    components that make up that remainder, so it is not a mystery number.
    """
    old, new = _by_castaway(before), _by_castaway(after)
    reasons = []
    for key in old.keys() | new.keys():
        people_old, people_new = old.get(key, {}), new.get(key, {})
        delta = sum(people_new.values()) - sum(people_old.values())
        if abs(delta) < _NOISE:
            continue
        castaways = []
        for name in people_old.keys() | people_new.keys():
            moved = people_new.get(name, 0) - people_old.get(name, 0)
            if abs(moved) >= _NOISE:
                castaways.append({"name": name, "delta": moved})
        castaways.sort(key=lambda c: (-abs(c["delta"]), c["name"]))
        reasons.append(
            {
                "key": key,
                "label": component_label(key),
                "delta": delta,
                "castaways": castaways[:castaway_limit],
            }
        )
    reasons.sort(key=lambda r: (-abs(r["delta"]), r["key"]))
    reasons, rest = reasons[:limit], reasons[limit:]

    total = after["total_points"] - before["total_points"]
    other = total - sum(r["delta"] for r in reasons)
    return {
        "delta": total,
        "reasons": reasons,
        "other": other if abs(other) >= _NOISE else 0,
        "rest": [
            {"key": r["key"], "label": r["label"], "delta": r["delta"]} for r in rest
        ],
    }
