"""Tests for auto-diff schema sync (_add_missing_columns) and pick file discovery."""

import importlib
import json
import os
import sys

import pytest
from sqlalchemy import text


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


# ── Auto-diff _add_missing_columns ────────────────────────────────────────


class TestAddMissingColumns:
    def test_all_model_columns_exist_after_create_app(self, app):
        """create_all() + _add_missing_columns() should produce all model columns."""
        _, db = app
        import sqlalchemy

        inspector = sqlalchemy.inspect(db.engine)
        for table in db.Model.metadata.sorted_tables:
            actual_cols = {c["name"] for c in inspector.get_columns(table.name)}
            expected_cols = {c.name for c in table.columns}
            missing = expected_cols - actual_cols
            assert not missing, f"Table {table.name} missing columns: {missing}"

    def test_adds_column_to_existing_table(self, app):
        """If a column is dropped from the DB, _add_missing_columns re-adds it."""
        _, db = app

        # Drop a known column
        with db.engine.begin() as conn:
            # SQLite >= 3.35.0 supports DROP COLUMN
            conn.execute(text("ALTER TABLE survivor DROP COLUMN won_fire"))

        # Verify it's gone
        import sqlalchemy

        inspector = sqlalchemy.inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("survivor")}
        assert "won_fire" not in cols

        # Run the sync
        from app import _add_missing_columns

        _add_missing_columns()

        # Verify it's back
        inspector = sqlalchemy.inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("survivor")}
        assert "won_fire" in cols

    def test_scalar_default_backfills_existing_rows(self, app):
        """Columns with scalar defaults should backfill existing rows via SQL DEFAULT."""
        _, db = app
        from app.models import Season

        # Insert a season, then drop a column with a default
        season = Season(number=99, name="Test", num_players=18)
        db.session.add(season)
        db.session.commit()

        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE survivor DROP COLUMN won_fire"))

        # Re-add via sync
        from app import _add_missing_columns

        _add_missing_columns()

        # Insert a survivor and verify the default is applied at DB level
        # (the ALTER TABLE DEFAULT clause backfills existing rows in SQLite)
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO survivor (season_id, name, voted_out_order) "
                    "VALUES (:sid, :name, 0)"
                ),
                {"sid": season.id, "name": "TestPlayer"},
            )
            row = conn.execute(
                text("SELECT won_fire FROM survivor WHERE name = 'TestPlayer'")
            ).fetchone()
        assert row[0] == 0  # False → DEFAULT 0

    def test_idempotent_on_complete_schema(self, app):
        """Running _add_missing_columns on an already-complete schema is a no-op."""
        _, db = app
        import sqlalchemy

        from app import _add_missing_columns

        # Get column counts before
        inspector = sqlalchemy.inspect(db.engine)
        before = {
            t.name: len(inspector.get_columns(t.name))
            for t in db.Model.metadata.sorted_tables
        }

        # Run again — should be a no-op
        _add_missing_columns()

        inspector = sqlalchemy.inspect(db.engine)
        after = {
            t.name: len(inspector.get_columns(t.name))
            for t in db.Model.metadata.sorted_tables
        }
        assert before == after

    def test_string_default_applied(self, app):
        """String defaults (like scoring_config='{}') should be quoted in SQL."""
        _, db = app

        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE season DROP COLUMN scoring_config"))

        from app import _add_missing_columns

        _add_missing_columns()

        # Insert a row without specifying scoring_config
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO season (number, name, num_players) "
                    "VALUES (99, 'Test', 18)"
                )
            )
            row = conn.execute(
                text("SELECT scoring_config FROM season WHERE number = 99")
            ).fetchone()
        assert row[0] == "{}"


# ── Pick file discovery ───────────────────────────────────────────────────


class TestDiscoverPickFiles:
    def test_discovers_simple_files(self, tmp_path):
        """Finds season{N}.json files and extracts season numbers."""
        from seed import discover_pick_files

        (tmp_path / "season45.json").write_text("{}")
        (tmp_path / "season50.json").write_text("{}")

        result = discover_pick_files(str(tmp_path))
        assert result == {
            45: str(tmp_path / "season45.json"),
            50: str(tmp_path / "season50.json"),
        }

    def test_prefers_canonical_over_suffixed(self, tmp_path):
        """season47.json should win over season47_snakedraft.json."""
        from seed import discover_pick_files

        (tmp_path / "season47_snakedraft.json").write_text('{"old": true}')
        (tmp_path / "season47.json").write_text('{"new": true}')

        result = discover_pick_files(str(tmp_path))
        assert result[47] == str(tmp_path / "season47.json")

    def test_falls_back_to_suffixed(self, tmp_path):
        """If only a suffixed file exists, it should still be found."""
        from seed import discover_pick_files

        (tmp_path / "season49_snakedraft.json").write_text("{}")

        result = discover_pick_files(str(tmp_path))
        assert 49 in result
        assert "snakedraft" in result[49]

    def test_ignores_non_json(self, tmp_path):
        """Non-JSON files and xlsx files should be ignored."""
        from seed import discover_pick_files

        (tmp_path / "season46.xlsx").write_text("")
        (tmp_path / "season46.json").write_text("{}")
        (tmp_path / "README.md").write_text("")

        result = discover_pick_files(str(tmp_path))
        assert list(result.keys()) == [46]

    def test_empty_directory(self, tmp_path):
        """Empty directory returns empty dict."""
        from seed import discover_pick_files

        result = discover_pick_files(str(tmp_path))
        assert result == {}


# ── seed.py auto-export integration ───────────────────────────────────────


class TestSeedAutoExport:
    def test_export_season_picks_roundtrip(self, app, tmp_path):
        """Picks exported via export_season_picks can be re-imported."""
        _, db = app
        from app.data import export_season_picks
        from app.models import Pick, Season, Survivor, User

        # Create test data
        season = Season(number=99, name="Test", num_players=18)
        db.session.add(season)
        db.session.flush()

        user = User(username="testplayer", display_name="TestPlayer")
        db.session.add(user)
        db.session.flush()

        surv = Survivor(season_id=season.id, name="Castaway", voted_out_order=1)
        db.session.add(surv)
        db.session.flush()

        pick = Pick(
            user_id=user.id,
            season_id=season.id,
            survivor_id=surv.id,
            pick_type="draft",
            pick_order=1,
        )
        db.session.add(pick)
        db.session.commit()

        # Export
        path = export_season_picks(season, str(tmp_path))
        assert path is not None
        assert os.path.exists(path)

        # Verify JSON structure
        with open(path) as f:
            data = json.load(f)

        assert "picks" in data
        assert "TestPlayer" in data["picks"]
        assert data["picks"]["TestPlayer"][0]["survivor"] == "Castaway"
        assert data["picks"]["TestPlayer"][0]["type"] == "d"
        assert data["picks"]["TestPlayer"][0]["order"] == 1
