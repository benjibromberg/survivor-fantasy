import hashlib
import logging
import sqlite3
from contextlib import contextmanager

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import safe_join

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _add_missing_columns():
    """Auto-detect and add columns that exist in models but not in the database.

    Compares SQLAlchemy model metadata against the actual schema and issues
    ALTER TABLE ADD COLUMN for any missing columns.  Scalar Python-side
    defaults (int, float, bool, str) are translated to SQL DEFAULT clauses
    so existing rows get backfilled.
    """
    import logging

    import sqlalchemy

    log = logging.getLogger(__name__)
    inspector = sqlalchemy.inspect(db.engine)
    dialect = db.engine.dialect

    for table in db.Model.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # create_all() handles entirely new tables

        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        added = []

        with db.engine.begin() as conn:
            for column in table.columns:
                if column.name in existing_cols:
                    continue

                col_type = column.type.compile(dialect=dialect)
                stmt = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"

                # Translate scalar Python-side defaults to SQL DEFAULT so
                # existing rows are backfilled (ORM default= only applies to
                # new INSERTs, not ALTER TABLE).
                if column.default is not None and column.default.is_scalar:
                    val = column.default.arg
                    if isinstance(val, bool):
                        stmt += f" DEFAULT {int(val)}"
                    elif isinstance(val, (int, float)):
                        stmt += f" DEFAULT {val}"
                    elif isinstance(val, str):
                        escaped = val.replace("'", "''")
                        stmt += f" DEFAULT '{escaped}'"

                conn.execute(sqlalchemy.text(stmt))
                added.append(column.name)

        if added:
            log.info("Added columns to %s: %s", table.name, ", ".join(added))


def _add_missing_indexes():
    """Create indexes declared on models but absent from the database.

    create_all() only emits indexes for tables it creates, so an index
    declared on a table that already exists (for example on a column that
    _add_missing_columns() just added) would otherwise never be built.
    """
    import logging

    import sqlalchemy

    log = logging.getLogger(__name__)
    inspector = sqlalchemy.inspect(db.engine)

    for table in db.Model.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue

        existing = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in existing:
                index.create(bind=db.engine)
                log.info("Created index %s on %s", index.name, table.name)


@contextmanager
def _schema_sync_lock():
    """Hold an exclusive cross-process lock while the schema is created or synced.

    Gunicorn starts several workers and each one calls create_app(), so each
    runs create_all() and the column and index sync against the same database
    at the same moment. Unserialized, the loser of a CREATE TABLE, ALTER TABLE
    or CREATE INDEX fails with "already exists" / "duplicate column name" (or
    "database is locked" while the journal mode is switched) and that worker
    dies at boot. The lock is a file beside the database, so the second worker
    waits and then finds the schema complete.
    """
    path = db.engine.url.database
    if db.engine.dialect.name != "sqlite" or not path or path == ":memory:":
        yield
        return
    try:
        import fcntl
    except ImportError:  # not POSIX: no lock available, same behaviour as before
        logging.getLogger(__name__).warning(
            "No file locking on this platform; schema sync is not serialized"
        )
        yield
        return

    with open(f"{path}.schema-lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


# {static filename: content version}, filled on first use. A running
# container's static files never change, so a restart is the only refresh.
static_versions = {}


def _static_version(static_folder, filename):
    """First 12 hex digits of the file's SHA-256, or None if it cannot be read.

    Content, not mtime: an image rebuild rewrites every mtime, and an
    unchanged file should keep its URL (and its cache entries).
    """
    if filename not in static_versions:
        path = safe_join(static_folder, filename)
        try:
            with open(path, "rb") as f:
                static_versions[filename] = hashlib.sha256(f.read()).hexdigest()[:12]
        except (OSError, TypeError):  # TypeError: safe_join refused the path
            static_versions[filename] = None
    return static_versions[filename]


def _version_static_urls(app):
    """Add ?v=<content version> to every url_for("static", ...).

    A changed file then has a new URL, so no browser or Cloudflare edge can
    keep serving an old copy of it against new page markup.
    """

    @app.url_defaults
    def add_static_version(endpoint, values):
        if endpoint != "static" or "v" in values or "filename" not in values:
            return
        version = _static_version(app.static_folder, values["filename"])
        if version:
            values["v"] = version


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"

    from .auth import auth_bp
    from .headshots import headshots_bp
    from .routes import _ensure_contrast, main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(headshots_bp)
    app.register_blueprint(main_bp)

    app.jinja_env.filters["contrast"] = _ensure_contrast
    _version_static_urls(app)

    @app.context_processor
    def inject_seasons():
        from .models import Season

        return {"all_seasons": Season.query.order_by(Season.number.desc()).all()}

    with app.app_context(), _schema_sync_lock():
        db.create_all()
        _add_missing_columns()
        _add_missing_indexes()

    # Start background scheduler (skip in reloader child process)
    import os

    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from .scheduler import init_scheduler

        init_scheduler(app)

    return app
