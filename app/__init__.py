import sqlite3

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

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


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"

    from .auth import auth_bp
    from .routes import _ensure_contrast, main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    app.jinja_env.filters["contrast"] = _ensure_contrast

    @app.context_processor
    def inject_seasons():
        from .models import Season

        return {"all_seasons": Season.query.order_by(Season.number.desc()).all()}

    with app.app_context():
        db.create_all()
        _add_missing_columns()

    # Start background scheduler (skip in reloader child process)
    import os

    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from .scheduler import init_scheduler

        init_scheduler(app)

    return app
