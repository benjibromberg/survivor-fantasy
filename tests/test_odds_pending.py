"""The leaderboard explains why win odds are missing before the merge."""

import importlib
import sys

import pytest

NOTE = "Win odds will show up around the merge"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB. The scheduler is stubbed."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db, predictions

    # No survivoR.xlsx: historical rates fall back to the empty defaults
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    predictions.clear_cache()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


def _season(db, boots=2, **structure):
    """Active six-castaway season after `boots` boots, with two drafted teams."""
    from app.models import Pick, Season, Survivor, User

    season = Season(
        number=99, name="Season 99", is_active=True, num_players=6, **structure
    )
    db.session.add(season)
    db.session.flush()
    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=i + 1 if i < boots else 0,
        )
        for i in range(6)
    ]
    pat = User(username="pat", display_name="Pat")
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*survivors, pat, sam])
    db.session.flush()
    for user, surv in ((pat, survivors[2]), (sam, survivors[3])):
        db.session.add(
            Pick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=surv.id,
                pick_type="draft",
            )
        )
    db.session.commit()
    return season


KNOWN = {"left_at_jury": 4, "n_finalists": 3}


class TestStructureKnown:
    def test_unknown_without_jury_size(self, client):
        _c, db = client
        from app.predictions import season_structure_known

        assert season_structure_known(_season(db, n_finalists=3)) is False

    def test_unknown_without_finalist_count(self, client):
        _c, db = client
        from app.predictions import season_structure_known

        assert season_structure_known(_season(db, left_at_jury=4)) is False

    def test_known_with_both(self, client):
        _c, db = client
        from app.predictions import season_structure_known

        assert season_structure_known(_season(db, **KNOWN)) is True


class TestLeaderboardNote:
    def test_shown_while_the_jury_size_is_unknown(self, client):
        c, db = client
        season = _season(db)

        page = c.get(f"/leaderboard/{season.id}").data.decode()

        assert NOTE in page
        assert "% win" not in page

    def test_hidden_once_the_odds_can_be_computed(self, client):
        c, db = client
        season = _season(db, **KNOWN)

        page = c.get(f"/leaderboard/{season.id}").data.decode()

        assert NOTE not in page
        assert "% win" in page

    def test_hidden_on_a_finished_season(self, client):
        c, db = client
        season = _season(db, boots=6)

        page = c.get(f"/leaderboard/{season.id}").data.decode()

        assert NOTE not in page

    def test_hidden_on_a_past_episode_view(self, client):
        """Historical views never show odds, so there is nothing to explain."""
        c, db = client
        season = _season(db)

        page = c.get(f"/leaderboard/{season.id}?as_of=1").data.decode()

        assert NOTE not in page
