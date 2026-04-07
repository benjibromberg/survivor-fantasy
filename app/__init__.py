from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def _add_missing_columns():
    """Add columns that create_all() won't add to existing tables."""
    import sqlalchemy
    inspector = sqlalchemy.inspect(db.engine)
    survivor_cols = {c['name'] for c in inspector.get_columns('survivor')}
    new_cols = {
        'elimination_episode': 'INTEGER',
        'episode_stats': 'TEXT',
        'tribal_councils_attended': 'INTEGER DEFAULT 0',
        'correct_votes': 'INTEGER DEFAULT 0',
        'votes_nullified': 'INTEGER DEFAULT 0',
        'confessional_time': 'REAL DEFAULT 0',
        'sit_outs': 'INTEGER DEFAULT 0',
        'jury_votes_received': 'INTEGER',
        'performance_score': 'REAL',
        'day_voted_out': 'INTEGER',
    }
    with db.engine.begin() as conn:
        for col, col_type in new_cols.items():
            if col not in survivor_cols:
                conn.execute(
                    sqlalchemy.text(f'ALTER TABLE survivor ADD COLUMN {col} {col_type}')
                )
    # Season table columns
    season_cols = {c['name'] for c in inspector.get_columns('season')}
    season_new = {
        'merge_episode_num': 'INTEGER',
    }
    with db.engine.begin() as conn:
        for col, col_type in season_new.items():
            if col not in season_cols:
                conn.execute(
                    sqlalchemy.text(f'ALTER TABLE season ADD COLUMN {col} {col_type}')
                )


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = 'auth.login'

    from .auth import auth_bp
    from .routes import main_bp, _ensure_contrast
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    app.jinja_env.filters['contrast'] = _ensure_contrast

    @app.context_processor
    def inject_seasons():
        from .models import Season
        return {'all_seasons': Season.query.order_by(Season.number.desc()).all()}

    with app.app_context():
        db.create_all()
        _add_missing_columns()

    # Start background scheduler (skip in reloader child process)
    import os
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from .scheduler import init_scheduler
        init_scheduler(app)

    return app
