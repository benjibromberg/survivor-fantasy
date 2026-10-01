"""Tests for the single-active-season invariant."""

import importlib
import sys

import pytest

ADMIN_EMAIL = "admin@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB with ADMIN_EMAIL configured.

    Config attributes are evaluated at import time, so the config module is
    reloaded after the env vars are set. The scheduler is stubbed to avoid
    background threads. CSRF is disabled so admin forms can be posted directly.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
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
        yield application.test_client(), db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def refreshed(monkeypatch):
    """Stub the survivoR calls made by the season-creation route.

    The real functions hit the network and parse a large xlsx. They are
    patched where app.routes looks them up. Returns the list of season numbers
    passed to refresh_season, so a test can confirm the stub actually ran.
    """
    calls = []

    def fake_refresh(season):
        calls.append(season.number)
        return 18, []

    monkeypatch.setattr("app.routes.download_survivor_data", lambda: None)
    monkeypatch.setattr("app.routes.refresh_season", fake_refresh)
    monkeypatch.setattr("app.routes.generate_season_images", lambda _season: 0)
    return calls


def _login_admin(c):
    resp = c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL})
    assert resp.status_code == 302


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


class TestCreateSeasonIsInactive:
    def test_new_season_does_not_displace_active_season(self, client, refreshed):
        c, db = client
        from app.models import Season

        existing = Season(number=60, name="Season 60", is_active=True)
        db.session.add(existing)
        db.session.commit()
        _login_admin(c)

        resp = c.post("/admin/seasons", data={"number": "61", "fetch_images": "on"})

        assert resp.status_code == 302
        assert refreshed == [61]
        created = Season.query.filter_by(number=61).one()
        assert created.is_active is False
        active = Season.query.filter_by(is_active=True).all()
        assert [s.number for s in active] == [60]

    def test_success_flash_says_season_is_inactive(self, client, refreshed):
        c, _db = client
        _login_admin(c)

        c.post("/admin/seasons", data={"number": "61"})

        created = [
            msg
            for category, msg in _flashes(c)
            if category == "success" and "Season 61 created" in msg
        ]
        assert len(created) == 1
        assert "inactive" in created[0]

    def test_first_season_is_inactive_too(self, client, refreshed):
        """With no seasons at all, a new one still waits for the admin toggle."""
        c, _db = client
        from app.models import Season

        _login_admin(c)

        c.post("/admin/seasons", data={"number": "61"})

        assert Season.query.filter_by(number=61).one().is_active is False
        assert Season.query.filter_by(is_active=True).count() == 0

    def test_model_default_is_inactive(self, client):
        _c, db = client
        from app.models import Season

        season = Season(number=61, name="Season 61")
        db.session.add(season)
        db.session.commit()

        assert season.is_active is False
