"""Tests for hand-entered casts: typing a season's castaways in by hand.

survivoR has no cast for a season until it has premiered (season 51's cast
first appeared in survivoR.xlsx three days after episode 1 aired), so a league
that drafts before the premiere needs the admin to enter the castaways.

A hand-entered castaway is a Survivor row with no castaway_id. That is the
marker a later pass uses to match them to the dataset once it publishes.
"""

import importlib
import io
import json
import os
import sys

import pytest
from PIL import Image

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

    from app import create_app, db, predictions

    # No survivoR.xlsx, which is the whole point: historical rates fall back
    # to the empty defaults rather than reaching for a dataset that has no
    # cast for this season yet.
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()

    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    predictions.clear_cache()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def league(client):
    """An empty pre-premiere season 52, plus season 51 with a published cast.

    Season 52 is what the admin is about to type a cast into. Season 51 stands
    in for a season survivoR has already published, so tests can tell the two
    kinds of castaway apart. Pat is a player, not an admin.
    """
    from types import SimpleNamespace

    from app.models import Season, Survivor, User

    c, db = client
    pre = Season(number=52, name="Season 52", is_active=False)
    published = Season(number=51, name="Season 51", num_players=18)
    db.session.add_all([pre, published])
    db.session.flush()
    ada = Survivor(
        season_id=published.id,
        name="Ada",
        castaway_id="US0801",
        version_season="US51",
        voted_out_order=0,
    )
    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    db.session.add_all([ada, pat])
    db.session.commit()
    return SimpleNamespace(c=c, db=db, pre=pre, published=published, ada=ada, pat=pat)


def _login(c, email=ADMIN_EMAIL):
    assert c.get("/login", headers={ACCESS_HEADER: email}).status_code == 302


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _errors(c):
    return [msg for category, msg in _flashes(c) if category == "error"]


def _jpeg_bytes(size=(440, 440), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _add(league, name, season=None, headshot=None):
    """POST the add-castaway form as whoever is logged in."""
    season = season or league.pre
    data = {"name": name}
    if headshot is not None:
        data["headshot"] = headshot
    return league.c.post(
        f"/admin/season/{season.id}/survivors/add",
        data=data,
        content_type="multipart/form-data",
    )


def _cast(league, season=None):
    """Castaway names in a season, alphabetically."""
    from app.models import Survivor

    season = season or league.pre
    return sorted(s.name for s in Survivor.query.filter_by(season_id=season.id))


def _survivor(league, name, season=None):
    from app.models import Survivor

    season = season or league.pre
    return Survivor.query.filter_by(season_id=season.id, name=name).one()


# ── The marker that makes later reconciliation possible ───────────────────


class TestHandEnteredMarker:
    def test_castaway_without_a_castaway_id_is_hand_entered(self, league):
        from app.models import Survivor

        assert Survivor(season_id=league.pre.id, name="Bo").is_hand_entered is True

    def test_castaway_from_survivor_data_is_not_hand_entered(self, league):
        assert league.ada.is_hand_entered is False

    def test_a_hand_entered_row_and_a_dataset_row_can_share_a_name(self, league):
        """Which is why a refresh of a hand-entered season duplicates its cast.

        refresh_season() keys existing castaways on castaway_id and skips rows
        without one, and nothing at the database level stops the pair, so it
        inserts the dataset's castaway beside the hand-entered one. Matching
        the two is the deferred half of this feature.
        """
        from app.models import Survivor

        league.db.session.add_all(
            [
                Survivor(season_id=league.pre.id, name="Bo", voted_out_order=0),
                Survivor(
                    season_id=league.pre.id,
                    name="Bo",
                    castaway_id="US0802",
                    voted_out_order=0,
                ),
            ]
        )
        league.db.session.commit()

        rows = Survivor.query.filter_by(season_id=league.pre.id, name="Bo").all()
        assert [s.is_hand_entered for s in rows] == [True, False]


# ── Adding a castaway ─────────────────────────────────────────────────────


class TestAddCastaway:
    def test_admin_adds_a_castaway_by_name(self, league):
        _login(league.c)

        resp = _add(league, "Bo")

        assert resp.status_code == 302
        assert _cast(league) == ["Bo"]

    def test_added_castaway_has_no_castaway_id_yet(self, league):
        """That is what a later pass matches against survivoR by name."""
        _login(league.c)

        _add(league, "Bo")

        bo = _survivor(league, "Bo")
        assert bo.castaway_id is None
        assert bo.version_season is None
        assert bo.is_hand_entered is True

    def test_added_castaway_is_still_in_the_game(self, league):
        _login(league.c)

        _add(league, "Bo")

        assert _survivor(league, "Bo").voted_out_order == 0

    def test_several_castaways_build_up_the_cast(self, league):
        _login(league.c)

        for name in ("Bo", "Cy", "Dee Dee"):
            _add(league, name)

        assert _cast(league) == ["Bo", "Cy", "Dee Dee"]

    def test_surrounding_whitespace_is_trimmed(self, league):
        _login(league.c)

        _add(league, "  Dee   Dee  ")

        assert _cast(league) == ["Dee Dee"]

    def test_blank_name_is_refused(self, league):
        _login(league.c)

        _add(league, "   ")

        assert _cast(league) == []
        assert _errors(league.c)

    def test_a_name_longer_than_the_column_is_refused(self, league):
        _login(league.c)

        _add(league, "B" * 101)

        assert _cast(league) == []
        assert _errors(league.c)

    def test_duplicate_name_is_refused_whatever_the_case(self, league):
        _login(league.c)
        _add(league, "Bo")

        _add(league, "bo")

        assert _cast(league) == ["Bo"]
        assert _errors(league.c)

    def test_the_same_name_in_another_season_is_fine(self, league):
        """Castaways are per-season; Ada already plays season 51."""
        _login(league.c)

        _add(league, "Ada")

        assert _cast(league) == ["Ada"]
        assert _cast(league, league.published) == ["Ada"]

    def test_unknown_season_is_not_found(self, league):
        _login(league.c)

        resp = league.c.post("/admin/season/4242/survivors/add", data={"name": "Bo"})

        assert resp.status_code == 404

    def test_a_failed_commit_rolls_back_and_says_so(self, league, monkeypatch):
        from sqlalchemy.exc import SQLAlchemyError

        _login(league.c)

        def boom():
            raise SQLAlchemyError("no room at the inn")

        monkeypatch.setattr(league.db.session, "commit", boom)

        _add(league, "Bo")

        monkeypatch.undo()
        league.db.session.rollback()
        assert _cast(league) == []
        assert _errors(league.c) == ["Could not add Bo."]


class TestAddCastawayAccess:
    def test_a_player_cannot_add_a_castaway(self, league):
        _login(league.c, PAT_EMAIL)

        resp = _add(league, "Bo")

        assert resp.status_code == 302
        assert "/admin" not in resp.headers["Location"]
        assert _cast(league) == []

    def test_a_visitor_is_sent_to_log_in(self, league):
        resp = _add(league, "Bo")

        assert resp.status_code == 302
        assert _cast(league) == []


# ── The optional headshot ─────────────────────────────────────────────────


class TestAddCastawayHeadshot:
    def test_no_file_leaves_the_letter_placeholder(self, league):
        _login(league.c)

        _add(league, "Bo")

        assert _survivor(league, "Bo").image_url is None

    def test_an_empty_file_field_is_not_an_upload(self, league):
        """Browsers post the file part with an empty filename when none is chosen."""
        _login(league.c)

        _add(league, "Bo", headshot=(io.BytesIO(b""), ""))

        assert _cast(league) == ["Bo"]
        assert _survivor(league, "Bo").image_url is None

    def test_an_uploaded_headshot_is_stored_as_a_local_webp(self, league):
        from app.data import headshots_dir

        _login(league.c)

        _add(league, "Bo", headshot=(io.BytesIO(_jpeg_bytes()), "bo.jpg"))

        bo = _survivor(league, "Bo")
        assert bo.image_url.startswith("/headshots/52/")
        path = os.path.join(headshots_dir(), *bo.image_url.split("/")[2:])
        with Image.open(path) as img:
            assert img.format == "WEBP"
            assert max(img.size) == 160

    def test_the_castaway_is_served_the_stored_headshot(self, league):
        _login(league.c)
        _add(league, "Bo", headshot=(io.BytesIO(_jpeg_bytes()), "bo.jpg"))

        resp = league.c.get(_survivor(league, "Bo").image_url)

        assert resp.status_code == 200
        assert resp.mimetype == "image/webp"

    def test_a_file_that_is_not_an_image_adds_nobody(self, league):
        _login(league.c)

        _add(league, "Bo", headshot=(io.BytesIO(b"not an image"), "bo.jpg"))

        assert _cast(league) == []
        assert _errors(league.c)

    def test_an_oversized_file_adds_nobody(self, league, monkeypatch):
        monkeypatch.setattr("app.data.HEADSHOT_MAX_BYTES", 256)
        _login(league.c)

        _add(league, "Bo", headshot=(io.BytesIO(_jpeg_bytes()), "bo.jpg"))

        assert _cast(league) == []
        assert _errors(league.c)


# ── The admin page the cast is entered on ─────────────────────────────────


class TestSeasonDetailPage:
    def _page(self, league, season=None):
        season = season or league.pre
        resp = league.c.get(f"/admin/season/{season.id}")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    def test_an_empty_season_offers_the_form_instead_of_a_dead_end(self, league):
        _login(league.c)

        html = self._page(league)

        assert f"/admin/season/{league.pre.id}/survivors/add" in html
        assert 'name="name"' in html
        assert 'name="headshot"' in html
        assert "Use the Refresh button above to fetch data from survivoR." not in html

    def test_the_form_is_still_there_once_the_cast_has_started(self, league):
        _login(league.c)
        _add(league, "Bo")

        html = self._page(league)

        assert f"/admin/season/{league.pre.id}/survivors/add" in html
        assert "Bo" in html

    def test_the_page_says_reconciliation_is_not_built_yet(self, league):
        """Refreshing a hand-entered season duplicates its cast today.

        refresh_season() keys existing castaways on castaway_id and skips rows
        without one, so it adds the dataset's cast beside the hand-entered
        rows. DELETE THIS TEST, and the caution it checks, when the matching
        pass lands.
        """
        _login(league.c)

        assert "not automatic yet" in self._page(league)

    def test_the_upload_field_is_labelled(self, league):
        _login(league.c)

        html = self._page(league)

        assert 'for="add-survivor-name"' in html
        assert 'id="add-survivor-name"' in html
        assert 'for="add-survivor-headshot"' in html
        assert 'id="add-survivor-headshot"' in html

    def test_hand_entered_castaways_are_marked_as_such(self, league):
        _login(league.c)
        _add(league, "Bo")

        assert ">hand-entered<" in self._page(league)

    def test_a_published_cast_is_not_marked_hand_entered(self, league):
        _login(league.c)

        assert ">hand-entered<" not in self._page(league, league.published)

    def test_a_published_castaway_has_no_remove_button(self, league):
        _login(league.c)

        html = self._page(league, league.published)

        assert f"/survivors/{league.ada.id}/remove" not in html

    def test_a_hand_entered_castaway_has_a_remove_button_until_it_is_picked(
        self, league
    ):
        from app.models import Pick

        _login(league.c)
        _add(league, "Bo")
        bo = _survivor(league, "Bo")
        assert f"/survivors/{bo.id}/remove" in self._page(league)

        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=league.pre.id,
                survivor_id=bo.id,
                pick_type="draft",
            )
        )
        league.db.session.commit()

        assert f"/survivors/{bo.id}/remove" not in self._page(league)


# ── Removing one again (an undo, never a reconciliation strategy) ──────────


class TestRemoveCastaway:
    def _remove(self, league, survivor, season=None):
        season = season or league.pre
        return league.c.post(
            f"/admin/season/{season.id}/survivors/{survivor.id}/remove"
        )

    def _pick(self, league, survivor, pick_type="draft"):
        from app.models import Pick

        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=survivor.season_id,
                survivor_id=survivor.id,
                pick_type=pick_type,
            )
        )
        league.db.session.commit()

    def test_a_hand_entered_castaway_with_no_picks_is_removed(self, league):
        _login(league.c)
        _add(league, "Bo")

        resp = self._remove(league, _survivor(league, "Bo"))

        assert resp.status_code == 302
        assert _cast(league) == []

    def test_a_castaway_someone_drafted_is_kept(self, league):
        from app.models import Pick

        _login(league.c)
        _add(league, "Bo")
        bo = _survivor(league, "Bo")
        self._pick(league, bo)

        self._remove(league, bo)

        assert _cast(league) == ["Bo"]
        assert Pick.query.count() == 1
        assert _errors(league.c)

    def test_a_castaway_someone_picked_to_win_is_kept(self, league):
        from app.models import SoleSurvivorPick

        _login(league.c)
        _add(league, "Bo")
        bo = _survivor(league, "Bo")
        league.db.session.add(
            SoleSurvivorPick(
                user_id=league.pat.id,
                season_id=league.pre.id,
                survivor_id=bo.id,
                episode=1,
            )
        )
        league.db.session.commit()

        self._remove(league, bo)

        assert _cast(league) == ["Bo"]
        assert SoleSurvivorPick.query.count() == 1
        assert _errors(league.c)

    def test_a_castaway_from_survivor_data_is_kept(self, league):
        """This route only undoes a hand entry; survivoR rows are the dataset's."""
        _login(league.c)

        self._remove(league, league.ada, season=league.published)

        assert _cast(league, league.published) == ["Ada"]
        assert _errors(league.c)

    def test_a_castaway_belonging_to_another_season_is_not_found(self, league):
        """The survivor id has to belong to the season in the URL."""
        _login(league.c)
        _add(league, "Bo")
        bo = _survivor(league, "Bo")

        resp = self._remove(league, bo, season=league.published)

        assert resp.status_code == 404
        assert _cast(league) == ["Bo"]

    def test_a_player_cannot_remove_a_castaway(self, league):
        _login(league.c)
        _add(league, "Bo")
        bo = _survivor(league, "Bo")
        league.c.get("/logout")
        _login(league.c, PAT_EMAIL)

        self._remove(league, bo)

        assert _cast(league) == ["Bo"]


# ── What the whole point is: drafting a hand-entered cast ─────────────────


class TestDraftBeforeThePremiere:
    """A draft on a season with no survivoR data has to be viewable.

    Checked through the real pages, not by calling the builders: a season
    with no episode stats, no jury size and no finalist count is exactly the
    shape the scoring, odds and highlights code has the least data for.
    """

    @pytest.fixture()
    def drafted(self, league):
        """Three hand-entered castaways, one of them drafted by Pat.

        The draft goes in through the admin picks page, the way a real one
        would, so the hand-entered castaways have to be selectable there.
        """
        _login(league.c)
        for name in ("Bo", "Cy", "Dee Dee"):
            _add(league, name)
        resp = league.c.post(
            f"/admin/picks/{league.pre.id}",
            data={
                "user_id": league.pat.id,
                "draft": [_survivor(league, "Bo").id],
                "sole_survivor": _survivor(league, "Cy").id,
                "ss_episode": "1",
            },
        )
        assert resp.status_code == 302
        league.c.get("/logout")
        return league

    def test_the_draft_lands_against_the_hand_entered_castaway(self, drafted):
        from app.models import Pick

        pick = Pick.query.one()
        assert pick.survivor.name == "Bo"
        assert pick.survivor.is_hand_entered is True

    def test_the_public_leaderboard_renders(self, drafted):
        resp = drafted.c.get(f"/leaderboard/{drafted.pre.id}")

        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert "Bo" in page
        # Odds need the jury size, which survivoR has not published either
        assert "Win odds will show up" in page

    def test_the_player_can_see_their_team(self, drafted):
        _login(drafted.c, PAT_EMAIL)

        resp = drafted.c.get(f"/my-team/{drafted.pre.id}")

        assert resp.status_code == 200
        assert "Bo" in resp.get_data(as_text=True)

    def test_the_season_stats_page_renders(self, drafted):
        resp = drafted.c.get(f"/stats/{drafted.pre.id}")

        assert resp.status_code == 200

    def test_the_picks_export_names_the_hand_entered_castaways(self, drafted, tmp_path):
        """The draft is exportable, so it is not lost to a later re-seed."""
        from app.data import export_season_picks

        path = export_season_picks(drafted.pre, picks_dir=str(tmp_path / "picks"))

        with open(path) as f:
            exported = json.load(f)
        assert exported["picks"]["Pat"] == [{"survivor": "Bo", "type": "d", "order": 1}]
        assert exported["sole_survivor_picks"]["Pat"] == [
            {"survivor": "Cy", "episode": 1}
        ]
