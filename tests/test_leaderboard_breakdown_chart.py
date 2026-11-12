"""The leaderboard's Point Breakdown chart adds up to the standings.

Each pick's `breakdown` is its castaway's points BEFORE the pick-type
multiplier. The chart used to sum it raw, so a wildcard counted at full value
and a replacement without its deduction, and a team's stacked bar ran past
its points (season 46 on real picks: one bar at 50 against 38 points).
"""

import importlib
import json
import re
import sys

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def season(client):
    """A finished eight-castaway season. Pat holds a draft (the winner), a
    wildcard (3rd place), a wildcard replacement (2nd place, worth 0 by
    default) and the winner as Sole Survivor from episode 1. Sam holds a
    draft and a draft replacement, whose raw breakdown was already right:
    it is scored from post-merge stats at full value."""
    c, db = client
    from app.models import Pick, Season, SoleSurvivorPick, Survivor, User
    from app.scoring.classic import DEFAULT_CONFIG

    s = Season(
        number=46,
        name="Season 46",
        num_players=8,
        num_episodes=8,
        left_at_jury=5,
        n_finalists=3,
        merge_episode_num=4,
        scoring_config=json.dumps(dict(DEFAULT_CONFIG)),
    )
    db.session.add(s)
    db.session.flush()
    cast = [
        Survivor(
            season_id=s.id,
            name=f"C{i + 1}",
            voted_out_order=i + 1,
            elimination_episode=min(i + 1, 6),
            day_voted_out=i + 1 if i < 5 else 6,
            made_jury=4 <= i + 1 <= 5,
            advantages_found=1 if i in (5, 6) else 0,
            idols_played=1 if i == 7 else 0,
        )
        for i in range(8)
    ]
    pat = User(username="pat", display_name="Pat")
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*cast, pat, sam])
    db.session.flush()

    def pick(user, castaway, kind):
        return Pick(
            user_id=user.id, season_id=s.id, survivor_id=castaway.id, pick_type=kind
        )

    db.session.add_all(
        [
            pick(pat, cast[7], "draft"),
            pick(pat, cast[5], "wildcard"),
            pick(pat, cast[6], "pmr_w"),
            pick(sam, cast[4], "draft"),
            pick(sam, cast[3], "pmr_d"),
            SoleSurvivorPick(
                user_id=pat.id, season_id=s.id, survivor_id=cast[7].id, episode=1
            ),
        ]
    )
    db.session.commit()
    return c, s


def _chart(c, season):
    """{player: {component: points}} as the page hands it to Chart.js."""
    page = c.get(f"/leaderboard/{season.id}").data.decode()
    data = json.loads(re.search(r"const breakdownData = (\[.*?\]);\n", page).group(1))
    return {row["name"]: row["breakdown"] for row in data}


def _standings(season):
    from app.routes import _build_leaderboard

    entries, _ = _build_leaderboard(season)
    return {e["user"].display_name: e for e in entries}


def test_each_bar_adds_up_to_the_players_points(season):
    c, s = season
    chart, standings = _chart(c, s), _standings(s)

    assert set(chart) == {"Pat", "Sam"}
    for name, parts in chart.items():
        assert sum(parts.values()) == pytest.approx(standings[name]["total_points"])


def test_the_fixture_exercises_every_multiplier(season):
    """The old raw sum fails the check above, so it is not passing vacuously:
    Pat's wildcard and wildcard replacement count for less than their
    castaways' points, and the raw breakdowns add up to more than Pat
    scored."""
    _, s = season
    pat = _standings(s)["Pat"]
    kinds = {p["pick_type_raw"]: p for p in pat["picks"]}

    assert {"draft", "wildcard", "pmr_w", "sole_survivor"} <= set(kinds)
    for kind in ("wildcard", "pmr_w"):
        assert kinds[kind]["modified_points"] < kinds[kind]["base_points"], kind
    raw = sum(v for p in pat["picks"] for v in p["breakdown"].values())
    assert raw > pat["total_points"] + 1


def test_keeps_the_chart_keys_and_the_sole_survivor_streak(season):
    from app.routes import BREAKDOWN_LABELS

    c, s = season
    chart = _chart(c, s)

    # Every chart key, looked up by name (tojson sorts them)
    for parts in chart.values():
        assert set(parts) == set(BREAKDOWN_LABELS)
    # An eight-episode streak on the winner, at 1 point an episode
    assert chart["Pat"]["sole_survivor_streak"] == pytest.approx(8)
    assert chart["Sam"]["sole_survivor_streak"] == 0
