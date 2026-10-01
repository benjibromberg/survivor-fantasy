"""Tests for where pick exports are written and how a failed export is handled."""

import importlib
import os
import sys

import pytest

ADMIN_EMAIL = "admin@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB in ``tmp_path/data``.

    The working directory is a separate ``tmp_path/cwd`` so a test can tell
    "beside the database" apart from "relative to the working directory".
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data_dir / 'test.db'}")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        yield application, db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def admin_client(app):
    """Test client already logged in as the admin."""
    application, _db = app
    client = application.test_client()
    client.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL})
    return client


def _make_season(db, number=99):
    """Season with one castaway and one fantasy player, no picks yet."""
    from app.models import Season, Survivor, User

    season = Season(number=number, name="Test", num_players=18)
    db.session.add(season)
    db.session.flush()
    user = User(username="playerone", display_name="PlayerOne")
    surv = Survivor(season_id=season.id, name="Castaway", voted_out_order=0)
    db.session.add_all([user, surv])
    db.session.commit()
    return season, user, surv


def _add_pick(db, season, user, surv):
    from app.models import Pick

    db.session.add(
        Pick(
            user_id=user.id,
            season_id=season.id,
            survivor_id=surv.id,
            pick_type="draft",
            pick_order=1,
        )
    )
    db.session.commit()


def _export_denied(*_args, **_kwargs):
    raise PermissionError(13, "Permission denied", "picks/season99.json")


def _error_flashes(client):
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    return [message for category, message in flashes if category == "error"]


# ── Default export directory ──────────────────────────────────────────────


class TestDefaultPicksDir:
    def test_follows_database_url_at_call_time(self, tmp_path, monkeypatch):
        """The export directory is ``picks/`` beside whatever DATABASE_URL names now."""
        from app.data import default_picks_dir

        first = tmp_path / "first"
        second = tmp_path / "second"

        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{first / 'app.db'}")
        assert default_picks_dir() == str(first / "picks")

        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{second / 'app.db'}")
        assert default_picks_dir() == str(second / "picks")

    def test_relative_to_cwd_when_database_url_unset(self, monkeypatch):
        """Local default (no DATABASE_URL) stays ``./picks``."""
        from app.data import default_picks_dir

        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert os.path.normpath(default_picks_dir()) == "picks"


class TestExportLocation:
    def test_default_export_lands_beside_database(self, app, tmp_path):
        """With no picks_dir argument the file goes to the data dir, not the cwd."""
        _, db = app
        from app.data import export_season_picks

        season, user, surv = _make_season(db)
        _add_pick(db, season, user, surv)

        path = export_season_picks(season)

        expected = tmp_path / "data" / "picks" / "season99.json"
        assert expected.is_file()
        assert os.path.realpath(path) == os.path.realpath(expected)
        assert not (tmp_path / "cwd" / "picks").exists()

    def test_export_all_uses_same_default(self, app, tmp_path):
        _, db = app
        from app.data import export_all_picks

        season, user, surv = _make_season(db)
        _add_pick(db, season, user, surv)

        paths = export_all_picks()

        assert len(paths) == 1
        assert (tmp_path / "data" / "picks" / "season99.json").is_file()

    def test_picks_dir_argument_overrides_default(self, app, tmp_path):
        _, db = app
        from app.data import export_season_picks

        season, user, surv = _make_season(db)
        _add_pick(db, season, user, surv)
        elsewhere = tmp_path / "elsewhere"

        path = export_season_picks(season, str(elsewhere))

        assert (elsewhere / "season99.json").is_file()
        assert os.path.realpath(path) == os.path.realpath(elsewhere / "season99.json")
        assert not (tmp_path / "data" / "picks").exists()


# ── seed.py pre-drop export decision ──────────────────────────────────────


class TestSeedExportBeforeDrop:
    def test_aborts_when_export_fails_and_picks_exist(self, app, monkeypatch):
        """Picks that cannot be backed up must stop the seed before the drop."""
        _, db = app
        from app.models import Pick
        from seed import export_picks_before_drop

        season, user, surv = _make_season(db)
        _add_pick(db, season, user, surv)
        monkeypatch.setattr("app.data.export_all_picks", _export_denied)

        with pytest.raises(SystemExit) as excinfo:
            export_picks_before_drop()

        assert excinfo.value.code not in (0, None)
        assert "Permission denied" in str(excinfo.value.code)
        assert Pick.query.count() == 1

    def test_aborts_when_only_sole_survivor_picks_exist(self, app, monkeypatch):
        _, db = app
        from app.models import SoleSurvivorPick
        from seed import export_picks_before_drop

        season, user, surv = _make_season(db)
        db.session.add(
            SoleSurvivorPick(
                user_id=user.id, season_id=season.id, survivor_id=surv.id, episode=1
            )
        )
        db.session.commit()
        monkeypatch.setattr("app.data.export_all_picks", _export_denied)

        with pytest.raises(SystemExit) as excinfo:
            export_picks_before_drop()

        assert excinfo.value.code not in (0, None)

    def test_proceeds_when_export_fails_and_no_picks(self, app, monkeypatch):
        """Empty database: a failed export has nothing to lose."""
        _, db = app
        from seed import export_picks_before_drop

        _make_season(db)
        monkeypatch.setattr("app.data.export_all_picks", _export_denied)

        assert export_picks_before_drop() == []

    def test_proceeds_when_tables_missing(self, app):
        """No tables yet: the real export raises, and the seed carries on."""
        _, db = app
        from seed import export_picks_before_drop

        db.drop_all()

        assert export_picks_before_drop() == []

    def test_returns_exported_paths_on_success(self, app, tmp_path):
        _, db = app
        from seed import export_picks_before_drop

        season, user, surv = _make_season(db)
        _add_pick(db, season, user, surv)

        paths = export_picks_before_drop()

        assert [os.path.basename(p) for p in paths] == ["season99.json"]
        assert (tmp_path / "data" / "picks" / "season99.json").is_file()
