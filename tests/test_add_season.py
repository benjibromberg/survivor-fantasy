"""Tests for add_season.py: adding a season and its draft without re-seeding."""

import importlib
import json
import os
import sqlite3
import sys

import pytest

import add_season


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


CASTAWAYS = ["Ada", "Bo", "Cy", "Dee Dee"]


def _season(db, number=60, active=False, castaways=CASTAWAYS):
    from app.models import Season, Survivor

    season = Season(number=number, name=f"Season {number}", is_active=active)
    db.session.add(season)
    db.session.flush()
    for name in castaways:
        db.session.add(Survivor(season_id=season.id, name=name))
    db.session.commit()
    return season


def _pick_file(tmp_path, data, name="season60.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


DRAFT = {
    "scoring": "default",
    "picks": {
        "PlayerA": [
            {"survivor": "Ada", "type": "d", "order": 1},
            {"survivor": "Cy", "type": "w"},
        ],
        "PlayerB": [{"survivor": "Dee Dee", "type": "d", "order": 1}],
    },
}


class TestValidatePickFile:
    def test_valid_file_has_no_problems(self):
        assert add_season.validate_pick_file(DRAFT, CASTAWAYS) == []

    def test_unknown_castaway_is_reported(self):
        data = {"picks": {"PlayerA": [{"survivor": "Zed", "type": "d"}]}}
        problems = add_season.validate_pick_file(data, CASTAWAYS)
        assert len(problems) == 1
        assert "Zed" in problems[0]

    def test_prefix_of_a_castaway_name_is_not_accepted(self):
        """The loader would prefix-match "Dee" to "Dee Dee"; require the exact name."""
        data = {"picks": {"PlayerA": [{"survivor": "Dee", "type": "d"}]}}
        assert add_season.validate_pick_file(data, CASTAWAYS)

    def test_wrong_case_is_reported(self):
        data = {"picks": {"PlayerA": [{"survivor": "ada", "type": "d"}]}}
        assert add_season.validate_pick_file(data, CASTAWAYS)

    def test_name_the_loader_would_remap_is_reported(self):
        """seed.NICKNAME_MAP rewrites some names before lookup ("niko" -> "Sifu")."""
        data = {"picks": {"PlayerA": [{"survivor": "Niko", "type": "d"}]}}
        problems = add_season.validate_pick_file(data, CASTAWAYS + ["Niko"])
        assert any("remap" in p for p in problems)

    def test_name_the_loader_maps_to_itself_is_fine(self):
        """Some NICKNAME_MAP entries are identities ("sol" -> "Sol")."""
        data = {"picks": {"PlayerA": [{"survivor": "Sol", "type": "d"}]}}
        assert add_season.validate_pick_file(data, CASTAWAYS + ["Sol"]) == []

    def test_unknown_type_is_reported(self):
        data = {"picks": {"PlayerA": [{"survivor": "Ada", "type": "wildcard"}]}}
        problems = add_season.validate_pick_file(data, CASTAWAYS)
        assert any("wildcard" in p for p in problems)

    def test_missing_picks_key_is_reported(self):
        assert add_season.validate_pick_file({"scoring": "default"}, CASTAWAYS)

    def test_sole_survivor_entries_are_checked(self):
        data = dict(DRAFT)
        data["sole_survivor_picks"] = {
            "PlayerA": [{"survivor": "Zed", "episode": 1}, {"survivor": "Bo"}]
        }
        problems = add_season.validate_pick_file(data, CASTAWAYS)
        assert any("Zed" in p for p in problems)
        assert any("episode" in p for p in problems)


class TestLoadDraft:
    def test_loads_every_pick_in_the_file(self, app, tmp_path):
        _, db = app
        from app.models import Pick, User

        season = _season(db)

        add_season.load_draft(season, _pick_file(tmp_path, DRAFT))

        got = sorted(
            (db.session.get(User, p.user_id).display_name, p.survivor.name, p.pick_type)
            for p in Pick.query.filter_by(season_id=season.id)
        )
        assert got == [
            ("PlayerA", "Ada", "draft"),
            ("PlayerA", "Cy", "wildcard"),
            ("PlayerB", "Dee Dee", "draft"),
        ]

    def test_refuses_a_season_that_already_has_picks(self, app, tmp_path):
        _, db = app
        from app.models import Pick

        season = _season(db)
        path = _pick_file(tmp_path, DRAFT)
        add_season.load_draft(season, path)

        with pytest.raises(ValueError, match="already has picks"):
            add_season.load_draft(season, path)

        assert Pick.query.filter_by(season_id=season.id).count() == 3

    def test_invalid_file_writes_nothing(self, app, tmp_path):
        _, db = app
        from app.models import Pick, User

        season = _season(db)
        bad = {
            "picks": {
                "PlayerA": [
                    {"survivor": "Ada", "type": "d"},
                    {"survivor": "Zed", "type": "d"},
                ]
            }
        }

        with pytest.raises(ValueError, match="Zed"):
            add_season.load_draft(season, _pick_file(tmp_path, bad))

        assert Pick.query.count() == 0
        assert User.query.count() == 0


class TestCreateSeason:
    def test_creates_an_inactive_season_and_refreshes_it(self, app, monkeypatch):
        _, db = app
        from app.models import Season, Survivor

        _season(db, number=60, active=True)

        def fake_refresh(season):
            db.session.add(Survivor(season_id=season.id, name="Ada"))
            db.session.commit()
            return 1, []

        monkeypatch.setattr("app.data.refresh_season", fake_refresh)

        season = add_season.create_season(61, scrape=False)

        assert season.is_active is False
        assert Survivor.query.filter_by(season_id=season.id).count() == 1
        assert [s.number for s in Season.query.filter_by(is_active=True)] == [60]

    def test_failed_refresh_removes_the_empty_season(self, app, monkeypatch):
        _, _db = app
        from app.models import Season

        def boom(_season):
            raise ValueError("No survivoR data for season 61")

        monkeypatch.setattr("app.data.refresh_season", boom)

        with pytest.raises(ValueError, match="No survivoR data"):
            add_season.create_season(61, scrape=False)

        assert Season.query.filter_by(number=61).first() is None

    def test_refuses_an_existing_season(self, app):
        _, db = app
        _season(db, number=61)

        with pytest.raises(ValueError, match="already exists"):
            add_season.create_season(61, scrape=False)

    def test_refuses_pre_new_era_seasons(self, app):
        with pytest.raises(ValueError, match="41"):
            add_season.create_season(40, scrape=False)


class TestActivate:
    def test_activating_deactivates_the_others(self, app):
        _, db = app
        from app.models import Season

        _season(db, number=60, active=True)
        new = _season(db, number=61)

        add_season.activate(new)

        assert [s.number for s in Season.query.filter_by(is_active=True)] == [61]


class TestBackup:
    def test_backup_is_a_readable_copy_beside_the_database(self, app, tmp_path):
        _, db = app
        _season(db, number=60)

        path = add_season.backup_database("pre-s61")

        assert os.path.dirname(path) == str(tmp_path / "backups")
        con = sqlite3.connect(path)
        assert con.execute("select number from season").fetchall() == [(60,)]
        con.close()
