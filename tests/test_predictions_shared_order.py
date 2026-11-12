"""Win odds when survivoR records an unordered pair under one boot number.

survivoR has always given a same-day double boot two consecutive orders
(season 51: Alexis 6 and Linnea 7, both day 10), but it might record an
unordered pair under one shared order. The simulation numbered the castaways
still in from the highest order so far, so with orders 1, 2, 2 the five left
of eight were numbered 3 to 7: nobody reached 8, no simulated winner earned
1st place, and every finisher scored a place too low.
"""

import importlib
import sys

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db, predictions

    # No survivoR.xlsx: historical rates fall back to the empty defaults, and
    # with no idols the projected mode draws nothing at random
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield db
        db.session.remove()
    predictions.clear_cache()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


def _season(db, number, boots):
    """Eight castaways, merge after 3 boots (left_at_jury 5), three finalists.

    `boots` is (voted_out_order, day) for C1, C2, ... in turn; the rest are
    still in. Pat drafts C4 and holds C7 as a wildcard, Sam drafts C5 and Kim
    drafts C6, so the odds are split three ways rather than settled.
    """
    from app.models import Pick, Season, Survivor, User

    season = Season(
        number=number,
        name=f"Season {number}",
        num_players=8,
        num_episodes=13,
        left_at_jury=5,
        n_finalists=3,
    )
    db.session.add(season)
    db.session.flush()
    cast = []
    for i in range(8):
        order, day = boots[i] if i < len(boots) else (0, None)
        cast.append(
            Survivor(
                season_id=season.id,
                name=f"S{number}C{i + 1}",
                voted_out_order=order,
                day_voted_out=day,
                elimination_episode=2 if order else None,
                individual_immunity_wins=i % 3,
                advantages_found=1 if i in (4, 6) else 0,
            )
        )
    users = {
        name: User.query.filter_by(username=name).first()
        or User(username=name, display_name=name.title())
        for name in ("pat", "sam", "kim")
    }
    db.session.add_all([*cast, *users.values()])
    db.session.flush()
    for name, index, kind in (
        ("pat", 3, "draft"),
        ("pat", 6, "wildcard"),
        ("sam", 4, "draft"),
        ("kim", 5, "draft"),
    ):
        db.session.add(
            Pick(
                user_id=users[name].id,
                season_id=season.id,
                survivor_id=cast[index].id,
                pick_type=kind,
            )
        )
    db.session.commit()
    return season, cast


def _odds(season):
    """({username: frozen %}, {username: projected %}, scenarios)."""
    from app.predictions import calculate_win_probabilities

    frozen, projected, total, exhaustive, _ = calculate_win_probabilities(season)
    assert exhaustive
    return (
        {v["user"].username: round(v["win_pct"], 6) for v in frozen.values()},
        {v["user"].username: round(v["win_pct"], 6) for v in projected.values()},
        total,
    )


SHARED = [(1, 2), (2, 5), (2, 5)]  # an unordered pair, one order between them
CONSECUTIVE = [(1, 2), (2, 5), (3, 5)]  # how survivoR records the same pair


def test_every_scenario_crowns_a_winner_at_the_last_order(app, monkeypatch):
    """With orders 1, 2, 2 someone reaches order 8 in every scenario, and
    scores 1st place and Final Tribal."""
    from app import predictions

    season, _ = _season(app, 71, SHARED)
    seen = []
    real = predictions._score_all_users

    def spy(scoring, users, user_picks, surv_by_id, season_, *rest):
        top = [s for s in surv_by_id.values() if s.voted_out_order == 8]
        items = scoring.calculate_survivor_points(top[0], season_).items if top else {}
        seen.append((len(top), items.get("placement"), items.get("final_tribal")))
        return real(scoring, users, user_picks, surv_by_id, season_, *rest)

    monkeypatch.setattr(predictions, "_score_all_users", spy)
    _, _, scenarios = _odds(season)

    assert scenarios == 120  # five left of eight: 5! orderings
    assert len(seen) == 2 * scenarios  # frozen and projected
    assert set(seen) == {(1, 5, 2)}  # one winner, 1st place 5, Final Tribal 2


def test_odds_match_the_same_pair_recorded_in_order(app):
    shared, _ = _season(app, 71, SHARED)
    consecutive, _ = _season(app, 72, CONSECUTIVE)

    assert _odds(shared) == _odds(consecutive)


def test_consecutive_orders_keep_the_odds_they_had(app):
    """Measured on this fixture before the fix: numbering from the count of
    castaways out is the same as from the highest order when orders run 1, 2,
    3, so nothing moves."""
    season, _ = _season(app, 72, CONSECUTIVE)

    frozen, projected, scenarios = _odds(season)

    assert scenarios == 120
    assert frozen == projected == {"pat": 60.0, "sam": 20.0, "kim": 20.0}
