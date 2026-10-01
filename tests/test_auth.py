"""Tests for Cloudflare Access-based admin login (app/auth.py)."""

import importlib
import sys

import pytest

ADMIN_EMAIL = "benji@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB with ADMIN_EMAIL configured.

    Config attributes are evaluated at import time, so the config module is
    reloaded after the env vars are set. The scheduler is stubbed to avoid
    background threads.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db
    from app.models import User

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application.test_client(), db, User
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


class TestAccessLogin:
    def test_matching_email_becomes_admin(self, client):
        c, _db, User = client
        resp = c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL})
        assert resp.status_code == 302  # redirect to index on success
        user = User.query.filter_by(username=ADMIN_EMAIL).first()
        assert user is not None
        assert user.is_admin is True

    def test_matching_email_is_case_insensitive(self, client):
        c, _db, User = client
        resp = c.get("/login", headers={ACCESS_HEADER: "Benji@Example.COM"})
        assert resp.status_code == 302
        assert User.query.filter_by(username=ADMIN_EMAIL).first() is not None

    def test_non_admin_email_is_not_authorized(self, client):
        c, _db, User = client
        resp = c.get("/login", headers={ACCESS_HEADER: "someone@else.com"})
        assert resp.status_code == 302
        # No account is created or promoted for a non-admin email.
        assert User.query.filter_by(username="someone@else.com").first() is None
        assert User.query.filter_by(is_admin=True).first() is None

    def test_missing_access_header_does_not_log_in(self, client):
        c, _db, User = client
        resp = c.get("/login")
        assert resp.status_code == 302
        assert User.query.filter_by(is_admin=True).first() is None

    def test_repeated_login_does_not_duplicate_admin(self, client):
        c, _db, User = client
        c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL})
        c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL})
        assert User.query.filter_by(username=ADMIN_EMAIL).count() == 1
