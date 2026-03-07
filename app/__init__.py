from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = 'auth.login'

    from .auth import auth_bp
    from .routes import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_seasons():
        from .models import Season
        return {'all_seasons': Season.query.order_by(Season.number.desc()).all()}

    with app.app_context():
        db.create_all()

    # Start background scheduler (skip in reloader child process)
    import os
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from .scheduler import init_scheduler
        init_scheduler(app)

    return app
