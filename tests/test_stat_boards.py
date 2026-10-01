"""Tests for the vote stat boards (voting accuracy, votes received)."""

import importlib
import sys

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application, db
        db.session.remove()

    if "config" in sys.modules:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(sys.modules["config"])


def _season_with(db, castaways):
    """Create a season whose survivors carry the given vote stats.

    ``castaways`` is a list of (name, votes_cast, correct_votes, votes_received).
    """
    from app.models import Season, Survivor

    season = Season(number=60, name="Season 60", num_players=len(castaways))
    db.session.add(season)
    db.session.flush()
    for name, cast, correct, received in castaways:
        db.session.add(
            Survivor(
                season_id=season.id,
                name=name,
                tribal_councils_attended=1,
                votes_cast=cast,
                correct_votes=correct,
                votes_received=received,
            )
        )
    db.session.commit()
    return season


def _board(season, label):
    from app.routes import _build_stat_boards

    (board,) = [b for b in _build_stat_boards(season) if b["label"] == label]
    return {row["survivor"].name: row["value"] for row in board["rows"]}


class TestVotingAccuracyHelper:
    def test_percent_of_votes_cast(self):
        from app.routes import _voting_accuracy

        assert _voting_accuracy(3, 4) == 75

    def test_none_when_no_vote_cast(self):
        from app.routes import _voting_accuracy

        assert _voting_accuracy(0, 0) is None
        assert _voting_accuracy(0, None) is None

    def test_zero_percent_when_every_vote_missed(self):
        from app.routes import _voting_accuracy

        assert _voting_accuracy(0, 2) == 0

    def test_missing_correct_count_is_zero(self):
        from app.routes import _voting_accuracy

        assert _voting_accuracy(None, 2) == 0


class TestVotingAccuracyBoard:
    def test_castaway_who_cast_no_vote_is_left_off(self, app):
        """Attending a tribal without voting (Shot in the Dark) is not a miss."""
        _, db = app
        season = _season_with(
            db,
            [
                ("Voter", 1, 1, 0),
                ("Wrong", 1, 0, 0),
                ("NoVote", 0, 0, 6),
            ],
        )

        board = _board(season, "Voting Accuracy")

        assert board == {"Voter": "100% (1/1)", "Wrong": "0% (0/1)"}

    def test_accuracy_uses_votes_cast_as_denominator(self, app):
        """Fourteen correct ballots out of fifteen cast is 93%, never above 100%."""
        _, db = app
        season = _season_with(db, [("Revoter", 15, 14, 0)])

        board = _board(season, "Voting Accuracy")

        assert board == {"Revoter": "93% (14/15)"}


class TestVotesReceivedBoard:
    def test_shows_stored_votes_received(self, app):
        _, db = app
        season = _season_with(db, [("Boot", 0, 0, 6), ("Target", 1, 0, 2)])

        board = _board(season, "Votes Received at Tribal")

        assert board == {"Boot": 6, "Target": 2}
