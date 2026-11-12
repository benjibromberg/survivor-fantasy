"""Points by scoring component (app/breakdowns.py), on plain leaderboard entries.

Entries are built in the shape _build_leaderboard returns: each pick's
`breakdown` is its castaway's points BEFORE the pick-type multiplier, and
`modified_points` is after it.
"""

import pytest

from app.breakdowns import (
    COMPONENT_GROUPS,
    COMPONENT_LABELS,
    component_totals,
    explain_change,
    group_totals,
    pick_contributions,
)


def _pick(name, pick_type, breakdown, multiplier=1.0):
    base = sum(breakdown.values())
    return {
        "survivor": name,
        "pick_type": pick_type,
        "pick_type_raw": pick_type.lower(),
        "base_points": base,
        "modified_points": base * multiplier,
        "breakdown": dict(breakdown),
    }


def _ss(name, bonus):
    """A Sole Survivor pick, as _build_leaderboard appends it."""
    return {
        "survivor": name,
        "pick_type": "SS Pick (3 ep streak)",
        "pick_type_raw": "sole_survivor",
        "base_points": bonus,
        "modified_points": bonus,
        "breakdown": {"sole_survivor_streak": bonus},
    }


def _entry(*picks):
    return {
        "picks": list(picks),
        "total_points": sum(p["modified_points"] for p in picks),
    }


DRAFT = {"pre_merge_tribal": 4.0, "jury": 3.0, "tribal_immunity": 1.0}
WILD = {"pre_merge_tribal": 3.0, "idol_plays": 2.0}


class TestComponentTotals:
    def test_a_draft_counts_in_full(self):
        totals = component_totals(_entry(_pick("Kyle", "Draft", DRAFT)))

        assert totals == DRAFT
        assert sum(totals.values()) == pytest.approx(8.0)

    def test_a_wildcard_is_scaled_by_its_multiplier(self):
        totals = component_totals(_entry(_pick("Kenzie", "Wildcard", WILD, 0.5)))

        assert totals == {"pre_merge_tribal": 1.5, "idol_plays": 1.0}
        # Not the castaway's full points: the wildcard earned half of them.
        assert sum(totals.values()) != pytest.approx(sum(WILD.values()))
        assert sum(totals.values()) == pytest.approx(2.5)

    def test_picks_add_together_and_match_the_total(self):
        entry = _entry(
            _pick("Kyle", "Draft", DRAFT),
            _pick("Kenzie", "Wildcard", WILD, 0.5),
            _ss("Kyle", 6.0),
        )

        totals = component_totals(entry)

        assert totals["pre_merge_tribal"] == pytest.approx(5.5)
        assert sum(totals.values()) == pytest.approx(entry["total_points"])

    def test_the_sole_survivor_streak_is_included(self):
        totals = component_totals(_entry(_pick("Kyle", "Draft", DRAFT), _ss("Kyle", 6)))

        assert totals["sole_survivor_streak"] == 6

    def test_a_pick_with_no_base_points_does_not_divide_by_zero(self):
        entry = _entry(_pick("Andy", "Draft", {}), _ss("Andy", 0))

        assert component_totals(entry) == {"sole_survivor_streak": 0}


class TestPickContributions:
    def test_each_pick_carries_its_castaway_type_points_and_scaled_parts(self):
        contributions = pick_contributions(
            _entry(
                _pick("Kyle", "Draft", DRAFT), _pick("Kenzie", "Wildcard", WILD, 0.5)
            )
        )

        assert contributions == [
            {
                "survivor": "Kyle",
                "pick_type": "Draft",
                "points": 8.0,
                "components": DRAFT,
            },
            {
                "survivor": "Kenzie",
                "pick_type": "Wildcard",
                "points": 2.5,
                "components": {"pre_merge_tribal": 1.5, "idol_plays": 1.0},
            },
        ]

    def test_holds_only_plain_values(self):
        """The Scoring changes page caches these past the request."""
        pick = _pick("Kyle", "Draft", DRAFT)
        pick["survivor_obj"] = object()  # the ORM row _build_leaderboard adds

        (contribution,) = pick_contributions(_entry(pick))

        assert "survivor_obj" not in contribution
        assert all(
            isinstance(v, (str, int, float, dict)) for v in contribution.values()
        )


class TestExplainChange:
    def test_names_the_groups_that_moved_most_and_who_moved_them(self):
        before = _entry(
            _pick("Kyle", "Draft", {"jury": 3.0, "final_tribal": 2.0}),
            _pick("Kenzie", "Draft", {"fire_win": 2.0, "final_tribal": 2.0}),
        )
        after = _entry(
            _pick("Kyle", "Draft", {"jury": 3.0, "final_tribal": 5.0}),
            _pick("Kenzie", "Draft", {"fire_win": 1.0, "final_tribal": 5.0}),
        )

        change = explain_change(before, after)

        assert change["delta"] == pytest.approx(5.0)
        first, second = change["reasons"]
        assert first["key"] == "final_tribal_placement"
        assert first["label"] == "Final Tribal and placement"
        assert first["delta"] == pytest.approx(6.0)
        assert [c["name"] for c in first["castaways"]] == ["Kenzie", "Kyle"]
        assert second["key"] == "fire_win"
        assert second["label"] == "fire-making win"
        assert second["delta"] == pytest.approx(-1.0)
        assert second["castaways"] == [{"name": "Kenzie", "delta": -1.0}]
        assert change["other"] == 0

    def test_leaves_out_groups_that_did_not_move(self):
        before = _entry(_pick("Kyle", "Draft", {"jury": 3.0, "placement": 5.0}))
        after = _entry(_pick("Kyle", "Draft", {"jury": 3.0, "placement": 7.0}))

        change = explain_change(before, after)

        assert [r["key"] for r in change["reasons"]] == ["final_tribal_placement"]

    def test_sibling_keys_that_move_together_are_one_reason(self):
        before = _entry(_pick("Kyle", "Draft", {"final_tribal": 2.0}))
        after = _entry(_pick("Kyle", "Draft", {"final_tribal": 5.0, "placement": 5.0}))

        (reason,) = explain_change(before, after)["reasons"]

        assert reason["key"] == "final_tribal_placement"
        assert reason["delta"] == pytest.approx(8.0)
        assert reason["castaways"] == [{"name": "Kyle", "delta": 8.0}]

    def test_keys_in_one_group_moving_opposite_ways_net_out(self):
        """Tribals lose 4 before the merge and gain 3.5 after it: a net -0.5,
        which ranks below the jury's +1 although each tribal key moved more."""
        before = _entry(
            _pick(
                "Kyle",
                "Draft",
                {"pre_merge_tribal": 8.0, "post_merge_tribal": 2.0, "jury": 3.0},
            )
        )
        after = _entry(
            _pick(
                "Kyle",
                "Draft",
                {"pre_merge_tribal": 4.0, "post_merge_tribal": 5.5, "jury": 4.0},
            )
        )

        change = explain_change(before, after)

        assert [r["key"] for r in change["reasons"]] == ["jury", "tribals"]
        tribals = change["reasons"][1]
        assert tribals["label"] == "surviving tribals"
        assert tribals["delta"] == pytest.approx(-0.5)
        assert tribals["castaways"] == [{"name": "Kyle", "delta": -0.5}]

    def test_a_wildcard_moves_its_player_by_half(self):
        before = _entry(_pick("Kenzie", "Wildcard", {"idol_plays": 1.0}, 0.5))
        after = _entry(_pick("Kenzie", "Wildcard", {"idol_plays": 2.0}, 0.5))

        (reason,) = explain_change(before, after)["reasons"]

        assert reason["delta"] == pytest.approx(0.5)

    def test_what_the_listed_reasons_miss_is_reported_so_parts_add_up(self):
        old = {"jury": 1.0, "merge": 1.0, "placement": 1.0, "fire_win": 1.0}
        new = {"jury": 5.0, "merge": 4.0, "placement": 3.0, "fire_win": 1.5}
        before = _entry(_pick("Kyle", "Draft", old))
        after = _entry(_pick("Kyle", "Draft", new))

        change = explain_change(before, after, limit=3)

        assert [r["key"] for r in change["reasons"]] == [
            "jury",
            "merge",
            "final_tribal_placement",
        ]
        assert change["other"] == pytest.approx(0.5)
        listed = sum(r["delta"] for r in change["reasons"])
        assert listed + change["other"] == pytest.approx(change["delta"])

    def test_the_remainder_names_the_groups_in_it(self):
        old = {"jury": 1.0, "merge": 1.0, "placement": 1.0, "fire_win": 1.0}
        new = {"jury": 5.0, "merge": 4.0, "placement": 3.0, "fire_win": 1.5}
        before = _entry(_pick("Kyle", "Draft", old))
        after = _entry(_pick("Kyle", "Draft", new))

        change = explain_change(before, after, limit=2)

        assert change["rest"] == [
            {
                "key": "final_tribal_placement",
                "label": "Final Tribal and placement",
                "delta": 2.0,
            },
            {"key": "fire_win", "label": "fire-making win", "delta": 0.5},
        ]
        assert sum(r["delta"] for r in change["rest"]) == pytest.approx(change["other"])

    def test_no_change_has_no_reasons(self):
        entry = _entry(_pick("Kyle", "Draft", DRAFT))

        assert explain_change(entry, entry) == {
            "delta": 0,
            "reasons": [],
            "other": 0,
            "rest": [],
        }


class TestGroupTotals:
    def test_components_roll_up_into_their_groups(self):
        entry = _entry(
            _pick("Kyle", "Draft", DRAFT),
            _pick("Kenzie", "Wildcard", WILD, 0.5),
            _ss("Kyle", 6.0),
        )

        totals = group_totals(entry)

        assert totals == pytest.approx(
            {
                "tribals": 5.5,
                "jury": 3.0,
                "immunity": 1.0,
                "idols": 1.0,
                "sole_survivor": 6.0,
            }
        )
        assert sum(totals.values()) == pytest.approx(entry["total_points"])

    def test_a_key_no_group_lists_keeps_its_points(self):
        totals = group_totals(_entry(_pick("Kyle", "Draft", {"jury": 3, "new": 2})))

        assert totals == {"jury": 3, "new": 2}


def test_every_breakdown_key_has_a_plain_label_and_no_label_is_spare():
    from app.routes import BREAKDOWN_LABELS

    assert set(COMPONENT_LABELS) == set(BREAKDOWN_LABELS)


def test_every_breakdown_key_is_in_exactly_one_group():
    from app.routes import BREAKDOWN_LABELS

    listed = [key for spec in COMPONENT_GROUPS.values() for key in spec["keys"]]
    for key in BREAKDOWN_LABELS:
        assert listed.count(key) == 1, key


def test_no_group_lists_a_key_that_is_not_a_breakdown_key():
    from app.routes import BREAKDOWN_LABELS

    for group, spec in COMPONENT_GROUPS.items():
        assert spec["keys"], group
        for key in spec["keys"]:
            assert key in BREAKDOWN_LABELS, (group, key)
