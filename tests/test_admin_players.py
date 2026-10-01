"""Tests for the season-independent player admin: list, edit, merge, delete."""

import importlib
import sys
from types import SimpleNamespace

import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB with ADMIN_EMAIL configured."""
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
def league(client):
    """Two seasons and four players.

    Pat plays season 61 (linked email, a team name). Sam plays 60 and 61.
    Patricia plays only 60 (a Sole Survivor pick and a team name there), so
    she can be merged into Pat. Stale is an old admin row with no picks.
    """
    c, db = client
    from app.models import Pick, Season, SoleSurvivorPick, Survivor, TeamName, User

    s60 = Season(number=60, name="Season 60", num_players=4)
    s61 = Season(number=61, name="Season 61", is_active=True, num_players=4)
    db.session.add_all([s60, s61])
    db.session.flush()
    cast = {}
    for season in (s60, s61):
        for name in ("Ada", "Bo", "Cy", "Di"):
            surv = Survivor(season_id=season.id, name=name)
            db.session.add(surv)
            cast[(season.number, name)] = surv
    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    sam = User(username="sam", display_name="Sam")
    patricia = User(username="patricia", display_name="Patricia")
    stale = User(username="oldadmin", display_name="Old Admin", is_admin=True)
    db.session.add_all([pat, sam, patricia, stale])
    db.session.flush()

    def pick(user, season, name, ptype="draft"):
        db.session.add(
            Pick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=cast[(season.number, name)].id,
                pick_type=ptype,
            )
        )

    pick(pat, s61, "Ada")
    pick(pat, s61, "Bo", "wildcard")
    pick(sam, s60, "Ada")
    pick(sam, s61, "Cy")
    pick(patricia, s60, "Bo")
    pick(patricia, s60, "Cy")
    db.session.add_all(
        [
            SoleSurvivorPick(
                user_id=patricia.id,
                season_id=s60.id,
                survivor_id=cast[(60, "Di")].id,
                episode=1,
            ),
            TeamName(user_id=pat.id, season_id=s61.id, name="Torch Bearers"),
            TeamName(user_id=patricia.id, season_id=s60.id, name="Old Flames"),
        ]
    )
    db.session.commit()
    return SimpleNamespace(
        c=c, db=db, s60=s60, s61=s61, pat=pat, sam=sam, patricia=patricia, stale=stale
    )


def _login(c, email=ADMIN_EMAIL):
    assert c.get("/login", headers={ACCESS_HEADER: email}).status_code == 302


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _errors(c):
    return [msg for category, msg in _flashes(c) if category == "error"]


def _picks_of(user):
    from app.models import Pick

    return sorted(
        (p.season.number, p.survivor.name, p.pick_type)
        for p in Pick.query.filter_by(user_id=user.id)
    )


# ── The players list, independent of any season ───────────────────────────


class TestPlayersList:
    def test_lists_every_player_with_the_seasons_they_play(self, league):
        _login(league.c)

        page = league.c.get("/admin/players").data.decode()

        assert "Patricia" in page and "Old Admin" in page
        assert "61, 60" in page  # Sam, newest season first
        assert f'href="/admin/player/{league.pat.id}"' in page

    def test_seasons_page_links_to_it(self, league):
        _login(league.c)

        page = league.c.get("/admin/seasons").data.decode()

        assert 'href="/admin/players"' in page

    def test_players_cannot_open_it(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.get("/admin/players")

        assert resp.status_code == 302
        assert ("error", "Admin access required.") in _flashes(league.c)


class TestAddPlayer:
    def test_username_is_the_lowercased_name(self, league):
        """seed.py looks players up by the lowercased name in the pick files."""
        from app.models import User

        _login(league.c)

        league.c.post("/admin/players", data={"name": "Zoe"})

        zoe = User.query.filter_by(display_name="Zoe").one()
        assert zoe.username == "zoe"

    def test_name_taken_in_another_case_is_refused(self, league):
        from app.models import User

        _login(league.c)

        league.c.post("/admin/players", data={"name": "SAM"})

        assert User.query.filter_by(display_name="SAM").count() == 0
        assert _errors(league.c)


# ── One player's page ─────────────────────────────────────────────────────


class TestPlayerPage:
    def test_shows_each_season_with_pick_counts_and_team_name(self, league):
        _login(league.c)

        page = league.c.get(f"/admin/player/{league.patricia.id}").data.decode()

        assert f'href="/admin/season/{league.s60.id}"' in page
        assert f'href="/admin/season/{league.s61.id}"' not in page  # not her season
        assert "Old Flames" in page
        assert 'data-draft="2"' in page
        assert 'data-sole-survivor="1"' in page

    def test_unknown_player_is_404(self, league):
        _login(league.c)

        assert league.c.get("/admin/player/424242").status_code == 404

    def test_team_name_saved_from_the_player_page_returns_there(self, league):
        from app.models import TeamName

        _login(league.c)

        resp = league.c.post(
            f"/admin/players/{league.sam.id}/team-name/{league.s60.id}",
            data={"team_name": "Sam's Squad", "back": "player"},
        )

        assert resp.headers["Location"].endswith(f"/admin/player/{league.sam.id}")
        assert TeamName.for_season(league.s60.id)[league.sam.id] == "Sam's Squad"


class TestEditPlayer:
    def _post(self, league, user, **data):
        return league.c.post(f"/admin/player/{user.id}/edit", data=data)

    def test_rename_updates_display_name_and_username(self, league):
        _login(league.c)

        self._post(league, league.sam, name="Samuel", email="")

        assert league.sam.display_name == "Samuel"
        assert league.sam.username == "samuel"

    def test_name_used_by_another_player_is_refused(self, league):
        _login(league.c)

        self._post(league, league.sam, name="pat", email="")

        assert league.sam.display_name == "Sam"
        assert _errors(league.c)

    def test_changing_only_the_case_of_your_own_name_is_fine(self, league):
        _login(league.c)

        self._post(league, league.sam, name="SAM", email="")

        assert league.sam.display_name == "SAM"
        assert not _errors(league.c)

    def test_blank_name_is_refused(self, league):
        _login(league.c)

        self._post(league, league.sam, name="  ", email="")

        assert league.sam.display_name == "Sam"
        assert _errors(league.c)

    def test_links_an_email(self, league):
        _login(league.c)

        self._post(league, league.sam, name="Sam", email="Sam@Example.com ")

        assert league.sam.email == "sam@example.com"

    def test_email_linked_to_someone_else_is_refused(self, league):
        _login(league.c)

        self._post(league, league.sam, name="Sam", email=PAT_EMAIL)

        assert league.sam.email is None
        assert _errors(league.c)

    def test_admin_login_email_cannot_be_changed_here(self, league):
        """Unlinking it would make the next admin login create a new row."""
        from app.models import User

        _login(league.c)
        admin = User.query.filter_by(username=ADMIN_EMAIL).one()
        admin.email = None
        league.sam.email = ADMIN_EMAIL
        league.db.session.commit()

        self._post(league, league.sam, name="Sam", email="")

        assert league.sam.email == ADMIN_EMAIL
        assert _errors(league.c)


# ── Merge ─────────────────────────────────────────────────────────────────


class TestMergePlayers:
    def _merge(self, league, source, target):
        return league.c.post(
            f"/admin/player/{source.id}/merge", data={"into": str(target.id)}
        )

    def test_moves_picks_sole_survivor_picks_and_team_names(self, league):
        from app.models import SoleSurvivorPick, TeamName, User

        _login(league.c)
        patricia_id = league.patricia.id

        resp = self._merge(league, league.patricia, league.pat)

        assert resp.headers["Location"].endswith(f"/admin/player/{league.pat.id}")
        assert league.db.session.get(User, patricia_id) is None
        assert _picks_of(league.pat) == [
            (60, "Bo", "draft"),
            (60, "Cy", "draft"),
            (61, "Ada", "draft"),
            (61, "Bo", "wildcard"),
        ]
        assert SoleSurvivorPick.query.filter_by(user_id=league.pat.id).count() == 1
        assert TeamName.for_season(league.s60.id)[league.pat.id] == "Old Flames"
        assert league.pat.email == PAT_EMAIL

    def test_moves_the_email_when_only_the_source_has_one(self, league):
        _login(league.c)
        league.pat.email = None
        league.patricia.email = "pt@example.com"
        league.db.session.commit()

        self._merge(league, league.patricia, league.pat)

        assert league.pat.email == "pt@example.com"

    def test_refuses_when_both_play_the_same_season(self, league):
        from app.models import User

        _login(league.c)

        self._merge(league, league.sam, league.pat)

        assert league.db.session.get(User, league.sam.id) is not None
        assert _picks_of(league.pat) == [(61, "Ada", "draft"), (61, "Bo", "wildcard")]
        assert any("Season 61" in msg for msg in _errors(league.c))

    def test_refuses_when_both_have_an_email(self, league):
        _login(league.c)
        league.patricia.email = "pt@example.com"
        league.db.session.commit()

        self._merge(league, league.patricia, league.pat)

        assert league.patricia.email == "pt@example.com"
        assert _errors(league.c)

    def test_refuses_merging_a_player_into_themselves(self, league):
        _login(league.c)

        self._merge(league, league.pat, league.pat)

        assert _errors(league.c)

    def test_refuses_merging_away_the_row_you_are_logged_in_as(self, league):
        from app.models import User

        _login(league.c)
        admin = User.query.filter_by(username=ADMIN_EMAIL).one()

        self._merge(league, admin, league.stale)

        assert league.db.session.get(User, admin.id) is not None
        assert _errors(league.c)

    def test_merges_an_empty_duplicate(self, league):
        from app.models import User

        _login(league.c)
        stale_id = league.stale.id

        self._merge(league, league.stale, league.pat)

        assert league.db.session.get(User, stale_id) is None
        assert not _errors(league.c)


# ── Delete ────────────────────────────────────────────────────────────────


class TestDeletePlayer:
    def test_a_stale_admin_flag_does_not_block_deleting(self, league):
        """is_admin is set from ADMIN_EMAIL at login; old rows keep a stale True."""
        from app.models import User

        _login(league.c)
        stale_id = league.stale.id

        league.c.post(f"/admin/players/{stale_id}/delete")

        assert league.db.session.get(User, stale_id) is None

    def test_cannot_delete_the_row_you_are_logged_in_as(self, league):
        from app.models import User

        _login(league.c)
        admin = User.query.filter_by(username=ADMIN_EMAIL).one()

        league.c.post(f"/admin/players/{admin.id}/delete")

        assert league.db.session.get(User, admin.id) is not None
        assert _errors(league.c)

    def test_cannot_delete_the_row_holding_the_admin_email(self, league):
        from app.models import User

        _login(league.c)
        admin = User.query.filter_by(username=ADMIN_EMAIL).one()
        admin.email = None
        league.sam.email = ADMIN_EMAIL
        league.db.session.commit()

        league.c.post(f"/admin/players/{league.sam.id}/delete")

        assert league.db.session.get(User, league.sam.id) is not None
        assert _errors(league.c)


class TestSettingsName:
    def test_admin_cannot_take_another_players_name(self, league):
        from app.models import User

        _login(league.c)

        league.c.post("/settings", data={"display_name": "Pat"})

        admin = User.query.filter_by(username=ADMIN_EMAIL).one()
        assert admin.display_name != "Pat"
        assert _errors(league.c)


class TestRenameKeepsTheAdminLogin:
    def test_renaming_the_admin_row_keyed_on_the_email_keeps_its_username(self, league):
        """auth.py finds that row by username == ADMIN_EMAIL when no email is linked."""
        from app.models import User

        _login(league.c)
        admin = User.query.filter_by(username=ADMIN_EMAIL).one()

        league.c.post(
            f"/admin/player/{admin.id}/edit", data={"name": "Boss", "email": ""}
        )

        assert admin.display_name == "Boss"
        assert admin.username == ADMIN_EMAIL
        _login(league.c)
        assert User.query.count() == 5  # no second admin row
