"""Tests for player self-login and per-season team names."""

import importlib
import sys
from types import SimpleNamespace

import pytest
import sqlalchemy
from sqlalchemy import text

ADMIN_EMAIL = "admin@example.com"
PLAYER_EMAIL = "pat@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB with ADMIN_EMAIL configured.

    Config attributes are evaluated at import time, so the config module is
    reloaded after the env vars are set. The scheduler is stubbed to avoid
    background threads. CSRF is disabled so forms can be posted directly.
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
def league(client, tmp_path, monkeypatch):
    """Active six-castaway season mid-game with two players, plus an old season.

    Pat's email is linked; Sam's is not. Four remaining castaways keeps the
    win-probability simulation exhaustive, so the leaderboard renders quickly
    and deterministically.
    """
    c, db = client
    from app import predictions
    from app.models import Pick, Season, Survivor, User

    # No survivoR.xlsx: historical rates fall back to the empty defaults
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()

    season = Season(
        number=99,
        name="Season 99",
        is_active=True,
        num_players=6,
        num_episodes=6,
        left_at_jury=4,
        n_finalists=3,
    )
    old_season = Season(number=98, name="Season 98", is_active=False, num_players=6)
    db.session.add_all([season, old_season])
    db.session.flush()

    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=i + 1 if i < 2 else 0,
        )
        for i in range(6)
    ]
    db.session.add_all(survivors)

    pat = User(username="pat", display_name="Pat", email=PLAYER_EMAIL)
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([pat, sam])
    db.session.flush()

    db.session.add_all(
        [
            Pick(
                user_id=pat.id,
                season_id=season.id,
                survivor_id=survivors[2].id,
                pick_type="draft",
            ),
            Pick(
                user_id=sam.id,
                season_id=season.id,
                survivor_id=survivors[3].id,
                pick_type="draft",
            ),
        ]
    )
    db.session.commit()

    yield SimpleNamespace(
        c=c,
        db=db,
        season=season,
        old_season=old_season,
        survivors=survivors,
        pat=pat,
        sam=sam,
    )

    predictions.clear_cache()


def _login(c, email):
    resp = c.get("/login", headers={ACCESS_HEADER: email})
    assert resp.status_code == 302
    return resp


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _session_user_id(c):
    with c.session_transaction() as sess:
        return sess.get("_user_id")


def _team_name(user, season):
    from app.models import TeamName

    row = TeamName.query.filter_by(user_id=user.id, season_id=season.id).first()
    return row.name if row else None


# ── Login: Access email → player ──────────────────────────────────────────


class TestPlayerLogin:
    def test_linked_email_logs_in_as_that_player(self, league):
        _login(league.c, PLAYER_EMAIL)

        assert _session_user_id(league.c) == str(league.pat.id)
        assert league.pat.is_admin is False

    def test_email_match_ignores_case_and_whitespace(self, league):
        _login(league.c, "  Pat@Example.COM ")

        assert _session_user_id(league.c) == str(league.pat.id)

    def test_unlinked_email_is_not_logged_in_and_creates_no_user(self, league):
        from app.models import User

        before = User.query.count()

        _login(league.c, "stranger@example.com")

        assert _session_user_id(league.c) is None
        assert User.query.count() == before
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_admin_email_linked_to_a_player_logs_in_as_that_player(self, league):
        from app.models import User

        league.sam.email = ADMIN_EMAIL
        league.db.session.commit()
        before = User.query.count()

        _login(league.c, ADMIN_EMAIL)

        assert _session_user_id(league.c) == str(league.sam.id)
        assert league.sam.is_admin is True
        assert User.query.count() == before

    def test_admin_email_reuses_the_admin_row_from_before_email_linking(self, league):
        from app.models import User

        legacy = User(username=ADMIN_EMAIL, display_name="admin", is_admin=True)
        league.db.session.add(legacy)
        league.db.session.commit()
        before = User.query.count()

        _login(league.c, ADMIN_EMAIL)

        assert _session_user_id(league.c) == str(legacy.id)
        assert User.query.count() == before

    def test_only_the_admin_email_is_admin(self, league):
        """A stale is_admin flag does not survive a non-admin login."""
        league.pat.is_admin = True
        league.db.session.commit()

        _login(league.c, PLAYER_EMAIL)

        assert _session_user_id(league.c) == str(league.pat.id)
        assert league.pat.is_admin is False

    def test_player_can_log_in_when_admin_email_is_not_configured(self, league):
        from flask import current_app

        current_app.config["ADMIN_EMAIL"] = ""

        _login(league.c, PLAYER_EMAIL)

        assert _session_user_id(league.c) == str(league.pat.id)
        assert league.pat.is_admin is False


class TestDevLogin:
    """DEV_LOGIN defaults on outside production, so these run without Access."""

    def test_user_param_logs_in_as_that_player(self, league):
        resp = league.c.get("/dev-login?user=pat")

        assert resp.status_code == 302
        assert _session_user_id(league.c) == str(league.pat.id)

    def test_unknown_user_param_does_not_log_in(self, league):
        league.c.get("/dev-login?user=nobody")

        assert _session_user_id(league.c) is None
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_user_param_is_refused_when_dev_login_is_off(self, league):
        from flask import current_app

        current_app.config["DEV_LOGIN"] = False

        league.c.get("/dev-login?user=pat")

        assert _session_user_id(league.c) is None


class TestPlayerGetsNoAdminUi:
    def test_admin_pages_reject_a_player(self, league):
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.get("/admin/seasons")

        assert resp.status_code == 302
        assert ("error", "Admin access required.") in _flashes(league.c)

    def test_player_cannot_change_their_display_name(self, league):
        """Display names key the pick export files, so only the admin edits them."""
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.post("/settings", data={"display_name": "Sam"})

        assert resp.status_code == 302
        assert league.pat.display_name == "Pat"
        assert ("error", "Admin access required.") in _flashes(league.c)

    def test_admin_can_still_change_their_display_name(self, league):
        from app.models import User

        _login(league.c, ADMIN_EMAIL)

        league.c.post("/settings", data={"display_name": "The Host"})

        admin = User.query.filter_by(username=ADMIN_EMAIL).one()
        assert admin.display_name == "The Host"

    def test_nav_shows_my_team_but_no_admin_link(self, league):
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b'href="/my-team"' in resp.data
        assert b'href="/admin/seasons"' not in resp.data
        assert b'href="/settings"' not in resp.data
        assert b'href="/logout"' in resp.data


# ── My Team page ──────────────────────────────────────────────────────────


class TestMyTeamPage:
    def test_requires_login(self, league):
        resp = league.c.get("/my-team")

        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_the_active_season(self, league):
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.get("/my-team")

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/my-team/{league.season.id}")

    def test_shows_only_my_picks(self, league):
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b"Castaway3" in resp.data  # Pat's draft pick
        assert b"Castaway4" not in resp.data  # Sam's draft pick

    def test_without_an_active_season_explains_instead_of_failing(self, league):
        league.season.is_active = False
        league.db.session.commit()
        _login(league.c, PLAYER_EMAIL)

        resp = league.c.get("/my-team")

        assert resp.status_code == 200
        assert b"No active season" in resp.data

    def test_unknown_season_is_404(self, league):
        _login(league.c, PLAYER_EMAIL)

        assert league.c.get("/my-team/424242").status_code == 404


# ── Per-season team name ──────────────────────────────────────────────────


class TestTeamName:
    def _post(self, league, name, season=None):
        season = season or league.season
        return league.c.post(f"/my-team/{season.id}/name", data={"team_name": name})

    def test_player_names_their_team(self, league):
        _login(league.c, PLAYER_EMAIL)

        resp = self._post(league, "Torch Snuffers")

        assert resp.status_code == 302
        assert _team_name(league.pat, league.season) == "Torch Snuffers"

    def test_renaming_updates_the_same_row(self, league):
        from app.models import TeamName

        _login(league.c, PLAYER_EMAIL)
        self._post(league, "Torch Snuffers")

        self._post(league, "Blindside Brigade")

        assert _team_name(league.pat, league.season) == "Blindside Brigade"
        assert TeamName.query.count() == 1

    def test_whitespace_is_trimmed_and_collapsed(self, league):
        _login(league.c, PLAYER_EMAIL)

        self._post(league, "   Torch \t  Snuffers  ")

        assert _team_name(league.pat, league.season) == "Torch Snuffers"

    def test_blank_name_clears_it(self, league):
        _login(league.c, PLAYER_EMAIL)
        self._post(league, "Torch Snuffers")

        self._post(league, "   ")

        assert _team_name(league.pat, league.season) is None

    def test_overlong_name_is_rejected(self, league):
        from app.models import TEAM_NAME_MAX_LENGTH

        _login(league.c, PLAYER_EMAIL)
        self._post(league, "Torch Snuffers")

        self._post(league, "x" * (TEAM_NAME_MAX_LENGTH + 1))

        assert _team_name(league.pat, league.season) == "Torch Snuffers"
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_name_at_the_length_limit_is_accepted(self, league):
        from app.models import TEAM_NAME_MAX_LENGTH

        _login(league.c, PLAYER_EMAIL)

        self._post(league, "x" * TEAM_NAME_MAX_LENGTH)

        assert _team_name(league.pat, league.season) == "x" * TEAM_NAME_MAX_LENGTH

    def test_player_cannot_name_a_team_for_an_inactive_season(self, league):
        _login(league.c, PLAYER_EMAIL)

        self._post(league, "Time Travellers", season=league.old_season)

        assert _team_name(league.pat, league.old_season) is None
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_requires_login(self, league):
        resp = self._post(league, "Torch Snuffers")

        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert _team_name(league.pat, league.season) is None

    def test_names_are_per_season(self, league):
        from app.models import TeamName

        league.db.session.add(
            TeamName(
                user_id=league.pat.id, season_id=league.old_season.id, name="Old Guard"
            )
        )
        league.db.session.commit()
        _login(league.c, PLAYER_EMAIL)

        self._post(league, "Torch Snuffers")

        assert _team_name(league.pat, league.old_season) == "Old Guard"
        assert _team_name(league.pat, league.season) == "Torch Snuffers"

    def test_leaderboard_shows_the_team_name_with_the_player(self, league):
        _login(league.c, PLAYER_EMAIL)
        self._post(league, "Torch Snuffers")

        resp = league.c.get(f"/leaderboard/{league.season.id}")

        assert resp.status_code == 200
        assert b"Torch Snuffers" in resp.data
        assert b"Pat" in resp.data

    def test_team_name_is_html_escaped_on_the_leaderboard(self, league):
        _login(league.c, PLAYER_EMAIL)
        self._post(league, "<b>Bold</b>")

        resp = league.c.get(f"/leaderboard/{league.season.id}")

        assert b"<b>Bold</b>" not in resp.data
        assert b"&lt;b&gt;Bold&lt;/b&gt;" in resp.data


# ── Admin: link emails, override team names ───────────────────────────────


class TestAdminLinksEmails:
    def _post(self, league, user, email):
        return league.c.post(f"/admin/players/{user.id}/email", data={"email": email})

    def test_admin_links_an_email_normalised(self, league):
        _login(league.c, ADMIN_EMAIL)

        resp = self._post(league, league.sam, "  Sam@Example.COM ")

        assert resp.status_code == 302
        assert league.sam.email == "sam@example.com"

    def test_linked_player_can_then_log_in(self, league):
        _login(league.c, ADMIN_EMAIL)
        self._post(league, league.sam, "sam@example.com")
        league.c.get("/logout")

        _login(league.c, "sam@example.com")

        assert _session_user_id(league.c) == str(league.sam.id)

    def test_email_already_linked_to_another_player_is_rejected(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, league.sam, PLAYER_EMAIL)

        assert league.sam.email is None
        assert league.pat.email == PLAYER_EMAIL
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_resaving_a_players_own_email_is_fine(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, league.pat, PLAYER_EMAIL)

        assert league.pat.email == PLAYER_EMAIL
        assert not any(category == "error" for category, _msg in _flashes(league.c))

    @pytest.mark.parametrize(
        "bad", ["not-an-email", "@example.com", "sam@", "sam @example.com", "a@b@c"]
    )
    def test_malformed_email_is_rejected(self, league, bad):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, league.sam, bad)

        assert league.sam.email is None
        assert any(category == "error" for category, _msg in _flashes(league.c))

    def test_blank_email_unlinks(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, league.pat, "")

        assert league.pat.email is None

    def test_player_cannot_link_emails(self, league):
        _login(league.c, PLAYER_EMAIL)

        self._post(league, league.sam, "sam@example.com")

        assert league.sam.email is None
        assert ("error", "Admin access required.") in _flashes(league.c)

    def test_unknown_player_is_404(self, league):
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.post("/admin/players/424242/email", data={"email": "a@b.co"})

        assert resp.status_code == 404

    def test_players_page_shows_linked_emails(self, league):
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.get("/admin/players")

        assert resp.status_code == 200
        assert PLAYER_EMAIL.encode() in resp.data


class TestAdminOverridesTeamNames:
    def _post(self, league, user, season, name):
        return league.c.post(
            f"/admin/players/{user.id}/team-name/{season.id}",
            data={"team_name": name},
        )

    def test_admin_sets_any_players_team_name(self, league):
        _login(league.c, ADMIN_EMAIL)

        resp = self._post(league, league.sam, league.season, "Sam's Squad")

        assert resp.status_code == 302
        assert _team_name(league.sam, league.season) == "Sam's Squad"

    def test_admin_can_edit_an_inactive_season(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, league.sam, league.old_season, "Old Guard")

        assert _team_name(league.sam, league.old_season) == "Old Guard"

    def test_player_cannot_override_another_players_team_name(self, league):
        _login(league.c, PLAYER_EMAIL)

        self._post(league, league.sam, league.season, "Hijacked")

        assert _team_name(league.sam, league.season) is None
        assert ("error", "Admin access required.") in _flashes(league.c)

    def test_unknown_player_or_season_is_404(self, league):
        _login(league.c, ADMIN_EMAIL)

        assert (
            self._post(
                league, SimpleNamespace(id=424242), league.season, "x"
            ).status_code
            == 404
        )
        assert (
            self._post(league, league.sam, SimpleNamespace(id=424242), "x").status_code
            == 404
        )

    def test_players_page_for_a_season_shows_team_names(self, league):
        _login(league.c, ADMIN_EMAIL)
        self._post(league, league.sam, league.season, "Sam's Squad")

        resp = league.c.get(f"/admin/players/{league.season.id}")

        assert resp.status_code == 200
        assert b"Sam&#39;s Squad" in resp.data


class TestDeletesCascadeTeamNames:
    """Foreign keys are enforced, so team names must go before their parent."""

    def test_deleting_a_player_removes_their_team_names(self, league):
        from app.models import TeamName, User

        league.db.session.add(
            TeamName(user_id=league.sam.id, season_id=league.season.id, name="Squad")
        )
        league.db.session.commit()
        sam_id = league.sam.id
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.post(f"/admin/players/{sam_id}/delete")

        assert resp.status_code == 302
        assert league.db.session.get(User, sam_id) is None
        assert TeamName.query.count() == 0

    def test_deleting_a_season_removes_its_team_names(self, league):
        from app.models import Season, TeamName

        league.db.session.add(
            TeamName(user_id=league.sam.id, season_id=league.season.id, name="Squad")
        )
        league.db.session.commit()
        season_id = league.season.id
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.post(f"/admin/season/{season_id}/delete")

        assert resp.status_code == 302
        assert league.db.session.get(Season, season_id) is None
        assert TeamName.query.count() == 0


# ── Schema sync on a database that predates this feature ──────────────────


class TestSchemaSyncForExistingDatabase:
    def _index_names(self, db):
        return {i["name"] for i in sqlalchemy.inspect(db.engine).get_indexes("user")}

    def _strip_email(self, db):
        """Put the user table back the way it was before email linking."""
        with db.engine.begin() as conn:
            conn.execute(text("DROP INDEX ix_user_email"))
            conn.execute(text("ALTER TABLE user DROP COLUMN email"))
        inspector = sqlalchemy.inspect(db.engine)
        assert "email" not in {c["name"] for c in inspector.get_columns("user")}

    def test_email_column_and_unique_index_are_added(self, client):
        _c, db = client
        from app import _add_missing_columns, _add_missing_indexes

        self._strip_email(db)

        _add_missing_columns()
        _add_missing_indexes()

        inspector = sqlalchemy.inspect(db.engine)
        assert "email" in {c["name"] for c in inspector.get_columns("user")}
        assert "ix_user_email" in self._index_names(db)

    def test_synced_index_enforces_unique_emails(self, client):
        _c, db = client
        from app import _add_missing_columns, _add_missing_indexes

        self._strip_email(db)
        _add_missing_columns()
        _add_missing_indexes()

        insert = text("INSERT INTO user (username, email) VALUES (:u, :e)")
        with db.engine.begin() as conn:
            conn.execute(insert, {"u": "one", "e": "same@example.com"})
        with pytest.raises(sqlalchemy.exc.IntegrityError), db.engine.begin() as conn:
            conn.execute(insert, {"u": "two", "e": "same@example.com"})

    def test_many_players_without_an_email_are_allowed(self, client):
        _c, db = client
        from app.models import User

        db.session.add_all([User(username="one"), User(username="two")])
        db.session.commit()

        assert User.query.filter_by(email=None).count() == 2

    def test_index_sync_is_idempotent(self, client):
        _c, db = client
        from app import _add_missing_indexes

        before = self._index_names(db)

        _add_missing_indexes()

        assert self._index_names(db) == before
