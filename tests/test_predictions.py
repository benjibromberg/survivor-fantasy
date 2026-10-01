"""Tests for the win-probability cache key (app/predictions.py)."""

import importlib
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import event

# ── Fixtures ──────────────────────────────────────────────────────────────


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


@pytest.fixture()
def league(app, tmp_path, monkeypatch):
    """Six-castaway season mid-game: two voted out, four left, two players.

    Four remaining castaways keeps the simulation exhaustive (24 orderings),
    so results are deterministic. The prediction cache is module-level, so it
    is cleared on both sides of each test.
    """
    _, db = app
    from app import predictions
    from app.models import Pick, Season, Survivor, User

    # No survivoR.xlsx: historical rates fall back to the empty defaults
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()

    season = Season(
        number=99,
        name="Test",
        num_players=6,
        num_episodes=6,
        left_at_jury=4,
        n_finalists=3,
    )
    db.session.add(season)
    db.session.flush()

    survivors = []
    for i in range(6):
        survivors.append(
            Survivor(
                season_id=season.id,
                name=f"Castaway{i + 1}",
                voted_out_order=i + 1 if i < 2 else 0,
            )
        )
    db.session.add_all(survivors)

    players = [
        User(username="playerone", display_name="PlayerOne"),
        User(username="playertwo", display_name="PlayerTwo"),
    ]
    db.session.add_all(players)
    db.session.flush()

    db.session.add_all(
        [
            Pick(
                user_id=players[0].id,
                season_id=season.id,
                survivor_id=survivors[2].id,
                pick_type="draft",
            ),
            Pick(
                user_id=players[1].id,
                season_id=season.id,
                survivor_id=survivors[3].id,
                pick_type="draft",
            ),
        ]
    )
    db.session.commit()

    yield SimpleNamespace(db=db, season=season, survivors=survivors, players=players)

    predictions.clear_cache()


def _key(season):
    from app.predictions import _cache_key

    return _cache_key(season, season.get_scoring_config())


def _add_pick(league, player, survivor, pick_type="draft"):
    from app.models import Pick

    pick = Pick(
        user_id=player.id,
        season_id=league.season.id,
        survivor_id=survivor.id,
        pick_type=pick_type,
    )
    league.db.session.add(pick)
    league.db.session.commit()
    return pick


def _add_ss_pick(league, player, survivor, episode):
    from app.models import SoleSurvivorPick

    ss_pick = SoleSurvivorPick(
        user_id=player.id,
        season_id=league.season.id,
        survivor_id=survivor.id,
        episode=episode,
    )
    league.db.session.add(ss_pick)
    league.db.session.commit()
    return ss_pick


def _add_player(league, username):
    from app.models import User

    player = User(username=username, display_name=username.title())
    league.db.session.add(player)
    league.db.session.commit()
    return player


def _count_queries(db, fn):
    """Run fn and return how many SQL statements it issued."""
    statements = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)
    return len(statements)


# ── Cache key: picks ──────────────────────────────────────────────────────


class TestCacheKeyPicks:
    def test_stable_when_nothing_changes(self, league):
        before = _key(league.season)
        league.db.session.expire_all()
        assert _key(league.season) == before

    def test_changes_when_pick_added(self, league):
        before = _key(league.season)
        _add_pick(league, league.players[0], league.survivors[4])
        assert _key(league.season) != before

    def test_changes_when_pick_type_changes(self, league):
        pick = _add_pick(league, league.players[0], league.survivors[4], "draft")
        before = _key(league.season)
        pick.pick_type = "wildcard"
        league.db.session.commit()
        assert _key(league.season) != before

    def test_changes_when_pick_removed(self, league):
        pick = _add_pick(league, league.players[0], league.survivors[4])
        before = _key(league.season)
        league.db.session.delete(pick)
        league.db.session.commit()
        assert _key(league.season) != before

    def test_changes_when_pick_moves_to_another_player(self, league):
        pick = _add_pick(league, league.players[0], league.survivors[4])
        before = _key(league.season)
        pick.user_id = league.players[1].id
        league.db.session.commit()
        assert _key(league.season) != before

    def test_stable_when_same_picks_are_resaved(self, league):
        """The admin picks form deletes and re-adds a player's picks on save,
        so unchanged picks come back with new row ids."""
        pick = _add_pick(league, league.players[0], league.survivors[4], "wildcard")
        before = _key(league.season)
        league.db.session.delete(pick)
        league.db.session.commit()
        _add_pick(league, league.players[0], league.survivors[4], "wildcard")
        assert _key(league.season) == before

    def test_ignores_picks_in_other_seasons(self, league):
        from app.models import Pick, Season, Survivor

        other = Season(number=98, name="Other", num_players=6)
        league.db.session.add(other)
        league.db.session.flush()
        other_survivor = Survivor(season_id=other.id, name="Castaway7")
        league.db.session.add(other_survivor)
        league.db.session.commit()

        before = _key(league.season)
        league.db.session.add(
            Pick(
                user_id=league.players[0].id,
                season_id=other.id,
                survivor_id=other_survivor.id,
                pick_type="draft",
            )
        )
        league.db.session.commit()
        assert _key(league.season) == before


# ── Cache key: sole survivor picks ────────────────────────────────────────


class TestCacheKeySoleSurvivorPicks:
    def test_changes_when_ss_pick_added(self, league):
        before = _key(league.season)
        _add_ss_pick(league, league.players[0], league.survivors[4], episode=1)
        assert _key(league.season) != before

    def test_changes_when_ss_pick_survivor_changes(self, league):
        ss_pick = _add_ss_pick(league, league.players[0], league.survivors[4], 1)
        before = _key(league.season)
        ss_pick.survivor_id = league.survivors[5].id
        league.db.session.commit()
        assert _key(league.season) != before

    def test_changes_when_ss_pick_episode_changes(self, league):
        ss_pick = _add_ss_pick(league, league.players[0], league.survivors[4], 1)
        before = _key(league.season)
        ss_pick.episode = 3
        league.db.session.commit()
        assert _key(league.season) != before

    def test_changes_when_ss_pick_removed(self, league):
        ss_pick = _add_ss_pick(league, league.players[0], league.survivors[4], 1)
        before = _key(league.season)
        league.db.session.delete(ss_pick)
        league.db.session.commit()
        assert _key(league.season) != before

    def test_stable_when_ss_picks_unchanged(self, league):
        _add_ss_pick(league, league.players[0], league.survivors[4], 1)
        _add_ss_pick(league, league.players[0], league.survivors[5], 3)
        before = _key(league.season)
        league.db.session.expire_all()
        assert _key(league.season) == before


# ── Cache key: scoring inputs on the season and its castaways ─────────────


class TestCacheKeyScoringInputs:
    @pytest.mark.parametrize(
        ("attr", "value"),
        [
            ("voted_out_order", 3),
            ("made_jury", True),
            ("won_fire", True),
            ("day_voted_out", 9),
            ("elimination_episode", 4),
            ("individual_immunity_wins", 1),
            ("tribal_immunity_wins", 2),
            ("idols_found", 1),
            ("idols_played", 1),
            ("advantages_found", 1),
            ("advantages_played", 1),
        ],
    )
    def test_changes_when_castaway_scoring_input_changes(self, league, attr, value):
        before = _key(league.season)
        setattr(league.survivors[2], attr, value)
        league.db.session.commit()
        assert _key(league.season) != before

    @pytest.mark.parametrize(
        ("attr", "value"),
        [
            ("num_players", 7),
            ("num_episodes", 7),
            ("left_at_jury", 3),
            ("n_finalists", 2),
            ("scoring_system", "Other"),
        ],
    )
    def test_changes_when_season_structure_changes(self, league, attr, value):
        before = _key(league.season)
        setattr(league.season, attr, value)
        league.db.session.commit()
        assert _key(league.season) != before

    def test_ignores_display_only_castaway_fields(self, league):
        """Stats that never reach the scorer should not force a recompute."""
        before = _key(league.season)
        league.survivors[2].confessional_count = 12
        league.survivors[2].tribe = "Purple"
        league.db.session.commit()
        assert _key(league.season) == before


# ── Cache key: query count ────────────────────────────────────────────────


class TestCacheKeyQueries:
    def test_query_count_does_not_grow_with_players(self, league):
        """One query each for survivors, picks and sole survivor picks."""
        season = league.season
        config = season.get_scoring_config()  # load the season row up front

        from app.predictions import _cache_key

        small = _count_queries(league.db, lambda: _cache_key(season, config))

        for i, username in enumerate(["playerthree", "playerfour", "playerfive"]):
            player = _add_player(league, username)
            _add_pick(league, player, league.survivors[i])
            _add_ss_pick(league, player, league.survivors[i + 2], episode=1)
        config = season.get_scoring_config()

        large = _count_queries(league.db, lambda: _cache_key(season, config))

        assert small == 3
        assert large == small


# ── Cached results follow the picks ───────────────────────────────────────


class TestWinProbabilityCache:
    def test_new_players_pick_is_scored_without_clearing_cache(self, league):
        from app.predictions import calculate_win_probabilities

        frozen, _proj, total, exhaustive, _rates = calculate_win_probabilities(
            league.season
        )
        assert exhaustive and total == 24
        assert set(frozen) == {p.id for p in league.players}

        newcomer = _add_player(league, "playerthree")
        _add_pick(league, newcomer, league.survivors[4])

        frozen, projected, _total, _exh, _rates = calculate_win_probabilities(
            league.season
        )
        assert newcomer.id in frozen
        assert newcomer.id in projected
        assert frozen[newcomer.id]["scenarios_won"] > 0

    def test_ss_pick_is_scored_without_clearing_cache(self, league):
        """A sole survivor pick moves the odds, so it has to be in the key."""
        from app.predictions import calculate_win_probabilities

        one, two = league.players
        frozen, *_ = calculate_win_probabilities(league.season)
        one_before = frozen[one.id]["scenarios_won"]
        two_before = frozen[two.id]["scenarios_won"]
        assert one_before + two_before == 24

        _add_ss_pick(league, two, league.survivors[4], episode=1)

        frozen, *_ = calculate_win_probabilities(league.season)
        assert frozen[two.id]["scenarios_won"] > two_before
        assert frozen[one.id]["scenarios_won"] < one_before

    def test_castaway_stat_is_scored_without_clearing_cache(self, league):
        from app.predictions import calculate_win_probabilities

        one, two = league.players
        frozen, *_ = calculate_win_probabilities(league.season)
        two_before = frozen[two.id]["scenarios_won"]

        # Player two's castaway finds advantages (0.5 points each by default)
        league.survivors[3].advantages_found = 4
        league.db.session.commit()

        frozen, *_ = calculate_win_probabilities(league.season)
        assert frozen[two.id]["scenarios_won"] > two_before
