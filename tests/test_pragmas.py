"""Tests for SQLite PRAGMA configuration — foreign keys and WAL mode (#17)."""

import importlib
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB.

    Must reload the config module after setting DATABASE_URL, because
    Config class attributes are evaluated at import time and cached in
    sys.modules.  Also patches out the scheduler to avoid background threads.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Force config module to re-evaluate with the new env var
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    # Prevent scheduler from starting during tests
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application, db
        db.session.remove()

    # Restore original config for other test modules
    if "config" in sys.modules:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(sys.modules["config"])


# ── PRAGMA foreign_keys ───────────────────────────────────────────────────


class TestForeignKeys:
    def test_pragma_foreign_keys_is_on(self, app):
        """PRAGMA foreign_keys should return 1 on every connection."""
        _, db = app
        result = db.session.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1

    def test_fk_violation_raises_integrity_error(self, app):
        """Inserting a Survivor with a nonexistent season_id should fail."""
        _, db = app
        from app.models import Survivor

        bad_survivor = Survivor(
            season_id=9999,
            name="Ghost",
            voted_out_order=0,
        )
        db.session.add(bad_survivor)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.session.flush()
        db.session.rollback()

    def test_fk_enforced_on_fresh_session(self, app):
        """FK enforcement persists after session cycling."""
        _, db = app
        db.session.remove()
        result = db.session.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1

    def test_fk_enforced_on_pick_without_user(self, app):
        """Pick with nonexistent user_id should be rejected."""
        _, db = app
        from app.models import Pick, Season, Survivor

        season = Season(number=99, name="Test", num_players=18)
        db.session.add(season)
        db.session.flush()

        survivor = Survivor(season_id=season.id, name="Castaway", voted_out_order=0)
        db.session.add(survivor)
        db.session.flush()

        bad_pick = Pick(
            user_id=9999,
            season_id=season.id,
            survivor_id=survivor.id,
            pick_type="draft",
        )
        db.session.add(bad_pick)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.session.flush()
        db.session.rollback()


# ── PRAGMA journal_mode ───────────────────────────────────────────────────


class TestJournalMode:
    def test_wal_mode_active(self, app):
        """WAL mode should be active on the file-backed test database."""
        _, db = app
        mode = db.session.execute(text("PRAGMA journal_mode")).scalar()
        assert mode == "wal"

    def test_wal_persists_across_sessions(self, app):
        """WAL should still be active after session cycling."""
        _, db = app
        db.session.remove()
        mode = db.session.execute(text("PRAGMA journal_mode")).scalar()
        assert mode == "wal"


# ── Both PRAGMAs on raw connection ────────────────────────────────────────


class TestRawConnection:
    def test_pragmas_on_raw_dbapi_connection(self, app):
        """Verify PRAGMAs are set on the underlying DBAPI connection, not just via ORM."""
        _, db = app
        connection = db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys")
            assert cursor.fetchone()[0] == 1
            cursor.execute("PRAGMA journal_mode")
            assert cursor.fetchone()[0] == "wal"
        finally:
            connection.close()


# ── Test DB isolation ─────────────────────────────────────────────────────


class TestDbIsolation:
    def test_uses_temp_db_not_project_db(self, app, tmp_path):
        """Verify the app is actually using the temp DB, not the project root's DB."""
        application, _ = app
        uri = application.config["SQLALCHEMY_DATABASE_URI"]
        assert str(tmp_path) in uri
