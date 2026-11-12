"""A leaderboard entry's points, split by scoring component.

One place for "how many of this player's points came from jury, immunity,
idols...", so pages that total it cannot disagree. Each pick's `breakdown` is
recorded BEFORE its pick-type multiplier (a wildcard's breakdown is its
castaway's full points), so every component is scaled by the pick's
modified / base points here. The scaled components then add up to the pick's
modified points, and a player's components to their total. The one exception
is a pick with no base points, which has nothing to scale: a replacement
scored by flat deduction can still come out negative there.

Components roll up into the league's own categories (COMPONENT_GROUPS):
"surviving tribals" rather than three tribal keys. A change is explained by
group, because a scoring change spreads across sibling keys (flat versus
progressive tribals moves all three), and ranking raw keys then lists three
pieces of one reason while the rest piles into a remainder.

An explained change is rounded to hundredths here, once, so what a player
reads adds up: the listed reasons plus "everything else" make the change, and
the change is the shown new total minus the shown old total.

Pure functions over the plain dicts `_build_leaderboard` returns; the results
hold only plain values, so they are safe to cache past the request.
"""

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

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

# The league owner's categories: group key -> label and the breakdown keys in
# it. Every key in routes.BREAKDOWN_LABELS sits in exactly one group (tested).
COMPONENT_GROUPS = {
    "tribals": {
        "label": "surviving tribals",
        "keys": ("pre_merge_tribal", "post_merge_tribal", "finale_tribal"),
    },
    "jury": {"label": "making the jury", "keys": ("jury",)},
    "final_tribal_placement": {
        "label": "Final Tribal and placement",
        "keys": ("final_tribal", "placement"),
    },
    "immunity": {
        "label": "immunity wins",
        "keys": ("individual_immunity", "tribal_immunity"),
    },
    "idols": {"label": "idols", "keys": ("idols_found", "idol_plays")},
    "advantages": {
        "label": "advantages",
        "keys": ("advantages_found", "advantage_plays"),
    },
    "fire_win": {"label": "fire-making win", "keys": ("fire_win",)},
    "merge": {"label": "making the merge", "keys": ("merge",)},
    "sole_survivor": {
        "label": "Sole Survivor pick",
        "keys": ("sole_survivor_streak",),
    },
}

_GROUP_OF = {
    key: group for group, spec in COMPONENT_GROUPS.items() for key in spec["keys"]
}

# Below this a change is float noise: it would display as 0.00.
_NOISE = 0.005


def component_label(key):
    return COMPONENT_LABELS.get(key, key.replace("_", " "))


def group_of(key):
    """The group a breakdown key belongs to. A key no group lists (a new
    component nobody has filed yet) stands as a group of its own rather than
    vanishing from the totals."""
    return _GROUP_OF.get(key, key)


def group_label(group):
    spec = COMPONENT_GROUPS.get(group)
    return spec["label"] if spec else component_label(group)


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


def group_totals(entry):
    """{group key: points} for one entry: component_totals rolled up by group."""
    totals = {}
    for key, value in component_totals(entry).items():
        group = group_of(key)
        totals[group] = totals.get(group, 0) + value
    return totals


def _by_castaway(entry):
    """{group: {castaway: points}}. Summed, since one castaway can be both a
    drafted pick and the Sole Survivor pick, and a group holds several keys."""
    out = {}
    for pick in pick_contributions(entry):
        for key, value in pick["components"].items():
            people = out.setdefault(group_of(key), {})
            people[pick["survivor"]] = people.get(pick["survivor"], 0) + value
    return out


def shown_cents(points):
    """`points` in whole hundredths, rounded the way the pages print it.

    The templates print points with '%.2f', which rounds the float's exact
    value half to even. Decimal does the same, so a change worked out from
    these always matches the two totals on the page.
    """
    exact = Decimal(points).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return int(exact * 100)


def _split_cents(values, target):
    """Round `values` to hundredths so they sum to exactly `target` hundredths.

    Largest remainder: floor every value, then hand the hundredths still owed
    to the values with the largest fractions. Rounding each on its own does
    not keep the sum (1.875 and -0.8 round to 1.88 and -0.8, which is 1.08,
    against a change shown as 1.07). Float noise is cleared first, so -0.8
    counts as -80 hundredths and not as -80.000000000000004.
    """
    if not values:
        return []
    exact = [Decimal(repr(round(v, 9))) * 100 for v in values]
    cents = [int(e.to_integral_value(rounding=ROUND_FLOOR)) for e in exact]
    owed = target - sum(cents)
    by_fraction = sorted(
        range(len(values)), key=lambda i: exact[i] - cents[i], reverse=True
    )
    step = 1 if owed > 0 else -1
    if owed < 0:
        by_fraction.reverse()
    for n in range(abs(owed)):
        cents[by_fraction[n % len(values)]] += step
    return [c / 100 for c in cents]


def explain_change(before, after, limit=3, castaway_limit=3):
    """Why one player's total differs between two scorings of the same picks.

    `before` and `after` are the same player's leaderboard entry under each
    scoring. Returns {"delta", "reasons", "other", "rest"}: the change in
    total; the `limit` component groups that moved it most, by their net
    change (largest first, each with the castaways behind it, largest
    first); whatever those reasons do not account for, so the parts always
    add up to the change; and the groups that make up that remainder, so it
    is not a mystery number.

    The change, the reasons and the remainder come back rounded to
    hundredths (_split_cents), and they add up exactly: the reasons plus the
    remainder equal the change, and the change equals the two totals as
    printed (shown_cents). The remainder is 0 when it rounds to nothing. Ranks
    and labels come from the unrounded amounts. Castaway amounts are a
    capped list, not a breakdown, and are left as they are.
    """
    old, new = _by_castaway(before), _by_castaway(after)
    reasons = []
    for group in old.keys() | new.keys():
        people_old, people_new = old.get(group, {}), new.get(group, {})
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
                "key": group,
                "label": group_label(group),
                "delta": delta,
                "castaways": castaways[:castaway_limit],
            }
        )
    reasons.sort(key=lambda r: (-abs(r["delta"]), r["key"]))
    reasons, rest = reasons[:limit], reasons[limit:]

    # The remainder before rounding: everything the listed reasons miss,
    # which is the rest groups plus any points no component accounts for.
    other = after["total_points"] - before["total_points"]
    other -= sum(r["delta"] for r in reasons)
    change = shown_cents(after["total_points"]) - shown_cents(before["total_points"])
    *listed, other_shown = _split_cents([r["delta"] for r in reasons] + [other], change)
    for reason, shown in zip(reasons, listed, strict=True):
        reason["delta"] = shown

    rest_raw = [r["delta"] for r in rest]
    if rest and abs(sum(rest_raw) - other) < _NOISE:
        # The rest groups are the whole remainder, so they add up to it too
        rest_shown = _split_cents(rest_raw, round(other_shown * 100))
    else:
        rest_shown = [round(v, 2) for v in rest_raw]
    return {
        "delta": change / 100,
        "reasons": reasons,
        "other": other_shown,
        "rest": [
            {"key": r["key"], "label": r["label"], "delta": shown}
            for r, shown in zip(rest, rest_shown, strict=True)
        ],
    }
