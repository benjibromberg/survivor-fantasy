"""Signing visitors in from their Cloudflare Access email without a Login click."""

import importlib
import sys
from types import SimpleNamespace

import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
SAM_EMAIL = "sam@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def league(tmp_path, monkeypatch):
    """An active season and two linked players, Pat and Sam."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db
    from app.models import Season, User

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        db.session.add(Season(number=60, name="Season 60", is_active=True))
        pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
        sam = User(username="sam", display_name="Sam", email=SAM_EMAIL)
        db.session.add_all([pat, sam])
        db.session.commit()
        yield SimpleNamespace(c=application.test_client(), db=db, pat=pat, sam=sam)
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


def _as(email):
    return {ACCESS_HEADER: email}


def _session_user_id(c):
    with c.session_transaction() as sess:
        return sess.get("_user_id")


class TestAutoLogin:
    def test_linked_player_is_signed_in_on_first_page(self, league):
        resp = league.c.get("/rules", headers=_as(PAT_EMAIL))

        assert resp.status_code == 200
        assert _session_user_id(league.c) == str(league.pat.id)
        assert b"My Team" in resp.data  # nav for a signed-in player

    def test_login_required_page_opens_without_a_login_click(self, league):
        resp = league.c.get("/my-team", headers=_as(PAT_EMAIL))

        assert resp.status_code in (200, 302)
        assert "/login" not in resp.headers.get("Location", "")
        assert _session_user_id(league.c) == str(league.pat.id)

    def test_email_is_matched_case_insensitively(self, league):
        league.c.get("/rules", headers=_as("Pat@Example.COM"))

        assert _session_user_id(league.c) == str(league.pat.id)

    def test_admin_email_gets_the_admin_pages(self, league):
        resp = league.c.get("/admin/seasons", headers=_as(ADMIN_EMAIL))

        assert resp.status_code == 200

    def test_admin_flag_follows_the_email_not_the_database(self, league):
        league.pat.is_admin = True  # stale flag
        league.db.session.commit()

        resp = league.c.get("/admin/seasons", headers=_as(PAT_EMAIL))

        assert resp.status_code == 302
        assert league.pat.is_admin is False

    def test_unlinked_email_sees_the_public_page(self, league):
        resp = league.c.get("/rules", headers=_as("stranger@example.com"))

        assert resp.status_code == 200
        assert _session_user_id(league.c) is None
        assert b"Login" in resp.data

    def test_no_header_means_no_sign_in(self, league):
        league.c.get("/rules")

        assert _session_user_id(league.c) is None

    def test_signs_in_only_once_per_session(self, league):
        league.c.get("/rules", headers=_as(PAT_EMAIL))
        league.c.get("/rules", headers=_as(PAT_EMAIL))

        assert _session_user_id(league.c) == str(league.pat.id)

    def test_switches_when_access_reports_someone_else(self, league):
        """A shared browser where a different person signs in to Access."""
        league.c.get("/rules", headers=_as(PAT_EMAIL))

        league.c.get("/rules", headers=_as(SAM_EMAIL))

        assert _session_user_id(league.c) == str(league.sam.id)

    def test_signs_out_when_access_reports_an_unlinked_email(self, league):
        league.c.get("/rules", headers=_as(PAT_EMAIL))

        league.c.get("/rules", headers=_as("stranger@example.com"))

        assert _session_user_id(league.c) is None

    def test_static_files_do_not_sign_anyone_in(self, league):
        league.c.get("/static/style.css", headers=_as(PAT_EMAIL))

        assert _session_user_id(league.c) is None


class TestLogoutStillWorks:
    def test_logout_is_not_undone_by_the_next_page(self, league):
        league.c.get("/rules", headers=_as(PAT_EMAIL))

        league.c.get("/logout", headers=_as(PAT_EMAIL))
        resp = league.c.get("/rules", headers=_as(PAT_EMAIL))

        assert _session_user_id(league.c) is None
        assert b"Login" in resp.data

    def test_clicking_login_after_logout_signs_in_and_resumes_auto_login(self, league):
        league.c.get("/rules", headers=_as(PAT_EMAIL))
        league.c.get("/logout", headers=_as(PAT_EMAIL))

        league.c.get("/login", headers=_as(PAT_EMAIL))
        league.c.get("/rules", headers=_as(PAT_EMAIL))

        assert _session_user_id(league.c) == str(league.pat.id)


class TestLoginButtonUnchanged:
    def test_login_still_signs_in_and_flashes(self, league):
        resp = league.c.get("/login", headers=_as(PAT_EMAIL))

        assert resp.status_code == 302
        assert _session_user_id(league.c) == str(league.pat.id)

    def test_login_with_an_unlinked_email_explains_why(self, league):
        league.c.get("/login", headers=_as("stranger@example.com"))

        with league.c.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("not linked" in msg for _cat, msg in flashes)
