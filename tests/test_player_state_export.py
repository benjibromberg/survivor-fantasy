"""Tests for exporting and restoring linked emails, team names and the Episode 2 time."""

import importlib
import json
import sys
from datetime import datetime

import pytest

ADMIN_EMAIL = "admin@example.com"
EP2 = datetime(2026, 10, 8, 0, 0)  # naive UTC, as stored


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB in ``tmp_path/data``."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data_dir / 'test.db'}")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application, db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def league(app):
    """One season, two players with a draft pick each. Pat is linked and has a team name."""
    from types import SimpleNamespace

    from app.models import Pick, Season, Survivor, TeamName, User

    _, db = app
    season = Season(number=99, name="Test", num_players=18, episode2_starts_at=EP2)
    db.session.add(season)
    db.session.flush()

    survivors = [
        Survivor(season_id=season.id, name=f"Castaway{i + 1}", voted_out_order=0)
        for i in range(2)
    ]
    pat = User(username="pat", display_name="Pat", email="pat@example.com")
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*survivors, pat, sam])
    db.session.flush()

    db.session.add_all(
        [
            Pick(
                user_id=pat.id,
                season_id=season.id,
                survivor_id=survivors[0].id,
                pick_type="draft",
                pick_order=1,
            ),
            Pick(
                user_id=sam.id,
                season_id=season.id,
                survivor_id=survivors[1].id,
                pick_type="draft",
                pick_order=2,
            ),
            TeamName(user_id=pat.id, season_id=season.id, name="Torch Snuffers"),
        ]
    )
    db.session.commit()
    return SimpleNamespace(db=db, season=season, survivors=survivors, pat=pat, sam=sam)


def _read(path):
    with open(path) as f:
        return json.load(f)


def _survivor_map(league):
    return {s.name.lower(): s for s in league.survivors}


def _wipe_player_state(league):
    """Drop the rows a re-seed would lose, keeping the season and castaways."""
    from app.models import Pick, TeamName, User

    # Deleted through the session (not a bulk delete) so no stale object
    # lingers in the identity map when the ids are reused
    for model in (TeamName, Pick, User):
        for row in model.query.all():
            league.db.session.delete(row)
        league.db.session.flush()
    league.season.episode2_starts_at = None
    league.db.session.commit()


# ── Season file: team names and the Episode 2 time ────────────────────────


class TestSeasonFileExport:
    def test_team_names_are_exported_by_player_name(self, league, tmp_path):
        from app.data import export_season_picks

        data = _read(export_season_picks(league.season, str(tmp_path / "out")))

        assert data["team_names"] == {"Pat": "Torch Snuffers"}

    def test_episode_two_time_is_exported_as_utc(self, league, tmp_path):
        from app.data import export_season_picks

        data = _read(export_season_picks(league.season, str(tmp_path / "out")))

        assert data["episode2_starts_at"] == "2026-10-08T00:00:00Z"

    def test_keys_are_omitted_when_there_is_nothing_to_export(self, league, tmp_path):
        """A season without this state exports the same shape as before."""
        from app.data import export_season_picks
        from app.models import TeamName

        TeamName.query.delete()
        league.season.episode2_starts_at = None
        league.db.session.commit()

        data = _read(export_season_picks(league.season, str(tmp_path / "out")))

        assert "team_names" not in data
        assert "episode2_starts_at" not in data

    def test_team_names_of_other_seasons_are_not_mixed_in(self, league, tmp_path):
        from app.data import export_season_picks
        from app.models import Season, TeamName

        other = Season(number=98, name="Other", num_players=18)
        league.db.session.add(other)
        league.db.session.flush()
        league.db.session.add(
            TeamName(user_id=league.sam.id, season_id=other.id, name="Old Guard")
        )
        league.db.session.commit()

        data = _read(export_season_picks(league.season, str(tmp_path / "out")))

        assert data["team_names"] == {"Pat": "Torch Snuffers"}


class TestSeasonFileRestore:
    def test_round_trip_restores_team_names_and_episode_two_time(
        self, league, tmp_path
    ):
        from app.data import export_season_picks
        from app.models import TeamName, User
        from seed import load_picks_from_json

        path = export_season_picks(league.season, str(tmp_path / "out"))
        _wipe_player_state(league)

        load_picks_from_json(path, league.season, _survivor_map(league))

        assert league.season.episode2_starts_at == EP2
        pat = User.query.filter_by(username="pat").one()
        names = {(t.user_id, t.season_id): t.name for t in TeamName.query.all()}
        assert names == {(pat.id, league.season.id): "Torch Snuffers"}

    def test_file_without_the_new_keys_still_loads(self, league, tmp_path):
        """Pick files written before this change have neither key."""
        from app.models import Pick, TeamName
        from seed import load_picks_from_json

        _wipe_player_state(league)
        path = tmp_path / "season99.json"
        path.write_text(
            json.dumps({"picks": {"Pat": [{"survivor": "Castaway1", "type": "d"}]}})
        )

        load_picks_from_json(str(path), league.season, _survivor_map(league))

        assert Pick.query.count() == 1
        assert TeamName.query.count() == 0
        assert league.season.episode2_starts_at is None

    def test_team_name_for_a_player_without_picks_creates_the_player(
        self, league, tmp_path
    ):
        from app.models import TeamName, User
        from seed import load_picks_from_json

        _wipe_player_state(league)
        path = tmp_path / "season99.json"
        path.write_text(
            json.dumps({"picks": {}, "team_names": {"Robin": "Late Arrivals"}})
        )

        load_picks_from_json(str(path), league.season, _survivor_map(league))

        robin = User.query.filter_by(username="robin").one()
        assert TeamName.query.one().user_id == robin.id

    @pytest.mark.parametrize("bad", ["", "   ", "x" * 41, 7, None])
    def test_unusable_team_name_is_skipped_not_fatal(self, league, tmp_path, bad):
        from app.models import Pick, TeamName
        from seed import load_picks_from_json

        _wipe_player_state(league)
        path = tmp_path / "season99.json"
        path.write_text(
            json.dumps(
                {
                    "picks": {"Pat": [{"survivor": "Castaway1", "type": "d"}]},
                    "team_names": {"Pat": bad},
                }
            )
        )

        load_picks_from_json(str(path), league.season, _survivor_map(league))

        assert Pick.query.count() == 1
        assert TeamName.query.count() == 0

    @pytest.mark.parametrize("bad", ["next wednesday", "2026-13-45T00:00:00Z", 12345])
    def test_unusable_episode_two_time_is_skipped_not_fatal(
        self, league, tmp_path, bad, capsys
    ):
        from app.models import Pick
        from seed import load_picks_from_json

        _wipe_player_state(league)
        path = tmp_path / "season99.json"
        path.write_text(
            json.dumps(
                {
                    "picks": {"Pat": [{"survivor": "Castaway1", "type": "d"}]},
                    "episode2_starts_at": bad,
                }
            )
        )

        load_picks_from_json(str(path), league.season, _survivor_map(league))

        assert Pick.query.count() == 1
        assert league.season.episode2_starts_at is None
        assert "WARNING" in capsys.readouterr().out

    def test_offset_time_is_converted_to_utc(self, league, tmp_path):
        """A hand-edited file may give the time with an offset instead of Z."""
        from seed import load_picks_from_json

        _wipe_player_state(league)
        path = tmp_path / "season99.json"
        path.write_text(
            json.dumps({"picks": {}, "episode2_starts_at": "2026-10-07T20:00:00-04:00"})
        )

        load_picks_from_json(str(path), league.season, _survivor_map(league))

        assert league.season.episode2_starts_at == EP2


# ── players.json: linked login emails ─────────────────────────────────────


class TestPlayerEmailExport:
    def test_export_all_writes_linked_emails(self, league, tmp_path):
        from app.data import export_all_picks

        out = tmp_path / "out"
        export_all_picks(str(out))

        assert _read(out / "players.json") == {
            "players": {"Pat": {"email": "pat@example.com", "username": "pat"}}
        }

    def test_export_all_still_returns_only_season_files(self, league, tmp_path):
        from app.data import export_all_picks

        out = tmp_path / "out"
        paths = export_all_picks(str(out))

        assert [p.rsplit("/", 1)[-1] for p in paths] == ["season99.json"]

    def test_file_is_rewritten_empty_when_nobody_is_linked(self, league, tmp_path):
        """A stale file must not bring back an email that was unlinked."""
        from app.data import export_all_picks

        out = tmp_path / "out"
        export_all_picks(str(out))
        league.pat.email = None
        league.db.session.commit()

        export_all_picks(str(out))

        assert _read(out / "players.json") == {"players": {}}

    def test_linked_player_without_picks_is_exported(self, league, tmp_path):
        from app.data import export_all_picks
        from app.models import User

        league.db.session.add(
            User(username="robin", display_name="Robin", email="robin@example.com")
        )
        league.db.session.commit()

        out = tmp_path / "out"
        export_all_picks(str(out))

        assert _read(out / "players.json")["players"]["Robin"] == {
            "email": "robin@example.com",
            "username": "robin",
        }

    def test_players_file_is_not_mistaken_for_a_season_file(self, league, tmp_path):
        from app.data import export_all_picks
        from seed import discover_pick_files

        out = tmp_path / "out"
        export_all_picks(str(out))

        assert list(discover_pick_files(str(out))) == [99]


class TestPlayerEmailRestore:
    def test_round_trip_restores_emails(self, league, tmp_path):
        from app.data import export_all_picks
        from app.models import User
        from seed import load_pick_files, load_player_emails

        out = tmp_path / "out"
        export_all_picks(str(out))
        _wipe_player_state(league)

        load_pick_files(str(out), {99: str(out / "season99.json")})
        load_player_emails(str(out))

        emails = {u.username: u.email for u in User.query.all()}
        assert emails == {"pat": "pat@example.com", "sam": None}

    def test_linked_player_without_picks_is_recreated(self, league, tmp_path):
        from app.models import User
        from seed import load_player_emails

        _wipe_player_state(league)
        (tmp_path / "players.json").write_text(
            json.dumps({"players": {"Robin": {"email": "Robin@Example.com"}}})
        )

        load_player_emails(str(tmp_path))

        robin = User.query.filter_by(username="robin").one()
        assert robin.display_name == "Robin"
        assert robin.email == "robin@example.com"

    def test_missing_file_is_a_no_op(self, league, tmp_path):
        from app.models import User
        from seed import load_player_emails

        before = User.query.count()

        assert load_player_emails(str(tmp_path)) == 0
        assert User.query.count() == before

    def test_second_player_with_the_same_email_is_skipped(
        self, league, tmp_path, capsys
    ):
        from app.models import User
        from seed import load_player_emails

        _wipe_player_state(league)
        (tmp_path / "players.json").write_text(
            json.dumps(
                {
                    "players": {
                        "Pat": {"email": "same@example.com"},
                        "Sam": {"email": "same@example.com"},
                    }
                }
            )
        )

        assert load_player_emails(str(tmp_path)) == 1

        linked = [u.username for u in User.query.filter(User.email.isnot(None))]
        assert linked == ["pat"]
        assert "WARNING" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "content",
        ["not json at all", "[]", '{"players": []}', '{"players": {"Pat": 5}}'],
    )
    def test_malformed_file_is_reported_not_fatal(
        self, league, tmp_path, capsys, content
    ):
        from app.models import User
        from seed import load_player_emails

        (tmp_path / "players.json").write_text(content)

        assert load_player_emails(str(tmp_path)) == 0

        assert User.query.filter_by(username="pat").one().email == "pat@example.com"
        assert "WARNING" in capsys.readouterr().out

    def test_blank_email_is_ignored(self, league, tmp_path):
        from app.models import User
        from seed import load_player_emails

        (tmp_path / "players.json").write_text(
            json.dumps({"players": {"Sam": {"email": "  "}}})
        )

        assert load_player_emails(str(tmp_path)) == 0
        assert User.query.filter_by(username="sam").one().email is None


# ── seed.py pre-drop guard ────────────────────────────────────────────────


class TestSeedGuardCountsLinkedEmails:
    def test_aborts_when_export_fails_and_only_linked_emails_exist(
        self, app, monkeypatch
    ):
        """Linked emails are also lost by the drop, so they block it too."""
        from app.models import User
        from seed import export_picks_before_drop

        _, db = app
        db.session.add(User(username="pat", email="pat@example.com"))
        db.session.commit()

        def denied(*_args, **_kwargs):
            raise PermissionError(13, "Permission denied", "picks/players.json")

        monkeypatch.setattr("app.data.export_all_picks", denied)

        with pytest.raises(SystemExit) as exc:
            export_picks_before_drop()

        assert exc.value.code not in (0, None)
        assert User.query.count() == 1
