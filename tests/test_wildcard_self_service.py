"""Tests for player wildcard self-service: edit window, lock, and reveal."""

import importlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
SAM_EMAIL = "sam@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"

# Episode 2 airs Wed 7 Oct 2026, 8:00 PM Eastern (EDT, UTC-4)
EP2_UTC = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
LOCK_UTC = EP2_UTC - timedelta(minutes=15)
BEFORE_LOCK = LOCK_UTC - timedelta(days=1)
AFTER_LOCK = LOCK_UTC + timedelta(minutes=1)


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
    """Active season after Episode 1 with wildcard self-service switched on.

    Castaway1 is out. Pat drafted Castaway3 and Sam drafted Castaway4. A
    player may take any castaway still in the game who is not on their own
    team. The clock starts a day before the lock; tests move it with
    `league.at(...)`.
    """
    c, db = client
    from app import predictions, routes, wildcards
    from app.models import Pick, Season, Survivor, User

    # No survivoR.xlsx: historical rates fall back to the empty defaults
    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()
    routes._compare_cache.clear()

    clock = {"now": BEFORE_LOCK}
    monkeypatch.setattr(wildcards, "now_utc", lambda: clock["now"])

    season = Season(
        number=99,
        name="Season 99",
        is_active=True,
        num_players=6,
        num_episodes=6,
        left_at_jury=4,
        n_finalists=3,
        episode2_starts_at=EP2_UTC.replace(tzinfo=None),
    )
    other_season = Season(number=98, name="Season 98", is_active=False, num_players=6)
    db.session.add_all([season, other_season])
    db.session.flush()

    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=1 if i == 0 else 0,
        )
        for i in range(6)
    ]
    outsider = Survivor(season_id=other_season.id, name="Outsider", voted_out_order=0)
    db.session.add_all([*survivors, outsider])

    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    sam = User(username="sam", display_name="Sam", email=SAM_EMAIL)
    fan = User(username="fan", display_name="Fan", email="fan@example.com")
    db.session.add_all([pat, sam, fan])
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

    def at(moment):
        clock["now"] = moment

    yield SimpleNamespace(
        c=c,
        db=db,
        season=season,
        other_season=other_season,
        survivors=survivors,
        outsider=outsider,
        pat=pat,
        sam=sam,
        fan=fan,
        at=at,
    )

    predictions.clear_cache()
    routes._compare_cache.clear()


def _login(c, email):
    resp = c.get("/login", headers={ACCESS_HEADER: email})
    assert resp.status_code == 302


def _logout(c):
    c.get("/logout")


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _has_error(c):
    return any(category == "error" for category, _msg in _flashes(c))


def _pick(league, survivor, season=None):
    """POST a wildcard choice as whoever is logged in."""
    season = season or league.season
    survivor_id = survivor if isinstance(survivor, str) else survivor.id
    return league.c.post(
        f"/my-team/{season.id}/wildcard", data={"survivor_id": survivor_id}
    )


def _pick_as(league, email, survivor):
    _login(league.c, email)
    resp = _pick(league, survivor)
    _logout(league.c)
    return resp


def _wildcard(user, season):
    """Name of the user's wildcard castaway, or None."""
    from app.models import Pick

    picks = Pick.query.filter_by(
        user_id=user.id, season_id=season.id, pick_type="wildcard"
    ).all()
    assert len(picks) <= 1
    return picks[0].survivor.name if picks else None


def _board(season):
    """Leaderboard entries keyed by player username."""
    from app.routes import _build_leaderboard

    entries, _ = _build_leaderboard(season)
    return {e["user"].username: e for e in entries}


def _pick_names(entry):
    return {p["survivor"] for p in entry["picks"]}


# ── Setting and changing a wildcard ───────────────────────────────────────


class TestPlayerSetsWildcard:
    def test_player_picks_a_wildcard(self, league):
        _login(league.c, PAT_EMAIL)

        resp = _pick(league, league.survivors[4])

        assert resp.status_code == 302
        assert _wildcard(league.pat, league.season) == "Castaway5"

    def test_player_can_change_it_repeatedly_before_the_lock(self, league):
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])
        _pick(league, league.survivors[5])
        _pick(league, league.survivors[1])

        assert _wildcard(league.pat, league.season) == "Castaway2"

    def test_changing_the_wildcard_leaves_draft_picks_alone(self, league):
        from app.models import Pick

        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])
        _pick(league, league.survivors[5])

        drafts = Pick.query.filter_by(
            user_id=league.pat.id, season_id=league.season.id, pick_type="draft"
        ).all()
        assert [p.survivor.name for p in drafts] == ["Castaway3"]

    def test_blank_choice_removes_the_wildcard_before_the_lock(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        _pick(league, "")

        assert _wildcard(league.pat, league.season) is None
        assert ("success", "Wildcard removed.") in _flashes(league.c)

    def test_two_players_may_pick_the_same_castaway(self, league):
        """Picks are secret until the reveal, so a clash cannot be refused."""
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[4])

        assert _wildcard(league.pat, league.season) == "Castaway5"
        assert _wildcard(league.sam, league.season) == "Castaway5"

    def test_requires_login(self, league):
        resp = _pick(league, league.survivors[4])

        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert _wildcard(league.pat, league.season) is None


class TestIneligibleChoices:
    def test_eliminated_castaway_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[0])

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_castaway_on_another_players_team_is_allowed(self, league):
        """How the league plays it: most past wildcards were someone's draft pick."""
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[3])  # Sam's draft pick

        assert _wildcard(league.pat, league.season) == "Castaway4"

    def test_own_draft_pick_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[2])

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_castaway_from_another_season_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)

        _pick(league, league.outsider)

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    @pytest.mark.parametrize("bad", ["abc", "424242", "-1", "1.5"])
    def test_garbage_survivor_id_is_rejected_without_crashing(self, league, bad):
        _login(league.c, PAT_EMAIL)

        resp = _pick(league, bad)

        assert resp.status_code == 302
        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_a_rejected_change_keeps_the_previous_wildcard(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        _pick(league, league.survivors[0])  # eliminated

        assert _wildcard(league.pat, league.season) == "Castaway5"


class TestWhoMayPick:
    def test_player_without_draft_picks_cannot_pick(self, league):
        from app.models import Pick

        _login(league.c, "fan@example.com")

        _pick(league, league.survivors[4])

        assert Pick.query.filter_by(user_id=league.fan.id).count() == 0
        assert _has_error(league.c)

    def test_not_available_when_self_service_is_off(self, league):
        league.season.episode2_starts_at = None
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_not_available_for_an_inactive_season(self, league):
        league.season.is_active = False
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_unknown_season_is_404(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.post("/my-team/424242/wildcard", data={"survivor_id": "1"})

        assert resp.status_code == 404


# ── Lock: 15 minutes before Episode 2 ─────────────────────────────────────


class TestLock:
    def test_lock_is_fifteen_minutes_before_episode_two(self, league):
        from app import wildcards

        assert wildcards.lock_at(league.season) == LOCK_UTC

    def test_submitted_wildcard_cannot_change_after_the_lock(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(AFTER_LOCK)

        _pick(league, league.survivors[5])

        assert _wildcard(league.pat, league.season) == "Castaway5"
        assert _has_error(league.c)

    def test_submitted_wildcard_cannot_be_removed_after_the_lock(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(AFTER_LOCK)

        _pick(league, "")

        assert _wildcard(league.pat, league.season) == "Castaway5"

    def test_change_is_allowed_one_second_before_the_lock(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(LOCK_UTC - timedelta(seconds=1))

        _pick(league, league.survivors[5])

        assert _wildcard(league.pat, league.season) == "Castaway6"

    def test_change_is_refused_at_the_lock_instant(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(LOCK_UTC)

        _pick(league, league.survivors[5])

        assert _wildcard(league.pat, league.season) == "Castaway5"

    def test_player_who_has_not_submitted_cannot_pick_after_the_lock(self, league):
        """The lock closes picks for everyone, not only those who submitted."""
        league.at(AFTER_LOCK)
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])

        assert _wildcard(league.pat, league.season) is None
        assert _has_error(league.c)

    def test_first_pick_is_refused_at_the_lock_instant(self, league):
        league.at(LOCK_UTC)
        _login(league.c, PAT_EMAIL)

        _pick(league, league.survivors[4])

        assert _wildcard(league.pat, league.season) is None

    def test_admin_can_enter_a_missing_wildcard_after_the_lock(self, league):
        league.at(AFTER_LOCK)
        _login(league.c, ADMIN_EMAIL)

        league.c.post(
            f"/admin/picks/{league.season.id}",
            data={
                "user_id": league.pat.id,
                "draft": [league.survivors[2].id],
                "wildcard": league.survivors[4].id,
            },
        )

        assert _wildcard(league.pat, league.season) == "Castaway5"

    def test_admin_can_still_change_a_locked_wildcard(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        league.at(AFTER_LOCK)
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.post(
            f"/admin/picks/{league.season.id}",
            data={
                "user_id": league.pat.id,
                "draft": [league.survivors[2].id],
                "wildcard": league.survivors[5].id,
            },
        )

        assert resp.status_code == 302
        assert _wildcard(league.pat, league.season) == "Castaway6"


# ── Reveal: hidden until every player has picked ──────────────────────────


class TestHiddenUntilEveryoneHasPicked:
    def test_wildcard_is_hidden_from_the_leaderboard_until_all_have_picked(
        self, league
    ):
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        board = _board(league.season)

        assert _pick_names(board["pat"]) == {"Castaway3"}

    def test_hidden_wildcard_scores_no_points_yet(self, league):
        before = _board(league.season)["pat"]["total_points"]

        _pick_as(league, PAT_EMAIL, league.survivors[4])

        assert _board(league.season)["pat"]["total_points"] == before

    def test_wildcards_appear_and_score_once_everyone_has_picked(self, league):
        before = _board(league.season)["pat"]["total_points"]
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        _pick_as(league, SAM_EMAIL, league.survivors[5])

        board = _board(league.season)
        assert _pick_names(board["pat"]) == {"Castaway3", "Castaway5"}
        assert _pick_names(board["sam"]) == {"Castaway4", "Castaway6"}
        assert board["pat"]["total_points"] > before

    def test_hidden_wildcard_is_absent_from_the_leaderboard_page(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        for viewer in (None, SAM_EMAIL, PAT_EMAIL):
            if viewer:
                _login(league.c, viewer)
            resp = league.c.get(f"/leaderboard/{league.season.id}")
            assert resp.status_code == 200
            assert b"Castaway5" not in resp.data, viewer
            _logout(league.c)

    def test_revealed_wildcard_is_on_the_leaderboard_page(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[5])

        resp = league.c.get(f"/leaderboard/{league.season.id}")

        assert b"Castaway5" in resp.data
        assert b"Castaway6" in resp.data

    def test_progression_chart_excludes_hidden_wildcards(self, league):
        before = _board(league.season)
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        resp = league.c.get(f"/leaderboard/{league.season.id}")

        match = re.search(rb"const progDatasets = (\[.*\]);", resp.data)
        assert match, "progression chart data not found on the page"
        final = {d["name"]: d["points"][-1] for d in json.loads(match.group(1))}
        assert final["Pat"] == round(before["pat"]["total_points"], 2)
        assert final["Sam"] == round(before["sam"]["total_points"], 2)

    def test_owner_sees_their_own_hidden_wildcard_on_my_team(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b"Castaway5" in resp.data
        assert b"1 of 2" in resp.data

    def test_another_player_does_not_see_it_on_their_my_team(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _login(league.c, SAM_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        # Castaway5 is still offered to Sam as a choice, but nothing marks it
        # as taken or shows it as a current pick
        assert b"my-wildcard-current" not in resp.data
        assert b" selected" not in resp.data
        assert b"0 of 2" not in resp.data and b"1 of 2" in resp.data

    def test_admin_still_sees_hidden_wildcards_on_the_picks_page(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.get(f"/admin/picks/{league.season.id}")

        assert resp.status_code == 200
        assert f'data-wildcard="{league.survivors[4].id}"'.encode() in resp.data

    def test_seasons_without_self_service_keep_showing_wildcards(self, league):
        """Past seasons have no Episode 2 time and not everyone has a wildcard."""
        from app.models import Pick

        league.season.episode2_starts_at = None
        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=league.season.id,
                survivor_id=league.survivors[4].id,
                pick_type="wildcard",
            )
        )
        league.db.session.commit()

        assert _pick_names(_board(league.season)["pat"]) == {"Castaway3", "Castaway5"}

    def test_removing_a_wildcard_hides_everyones_again(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[5])

        _pick_as(league, SAM_EMAIL, "")

        assert _pick_names(_board(league.season)["pat"]) == {"Castaway3"}


class TestCachesFollowTheReveal:
    def _add_wildcard(self, league, user, survivor):
        from app.models import Pick

        league.db.session.add(
            Pick(
                user_id=user.id,
                season_id=league.season.id,
                survivor_id=survivor.id,
                pick_type="wildcard",
            )
        )
        league.db.session.commit()

    def test_hidden_wildcard_does_not_change_win_probabilities(self, league):
        from app import predictions

        base, *_ = predictions.calculate_win_probabilities(league.season)
        base_pcts = {uid: d["win_pct"] for uid, d in base.items()}
        assert base_pcts, "simulation produced no results"
        predictions.clear_cache()

        self._add_wildcard(league, league.pat, league.survivors[4])

        hidden, *_ = predictions.calculate_win_probabilities(league.season)
        assert {uid: d["win_pct"] for uid, d in hidden.items()} == base_pcts

    def test_prediction_cache_key_changes_when_wildcards_are_revealed(self, league):
        from app.predictions import _cache_key

        self._add_wildcard(league, league.pat, league.survivors[4])
        config = league.season.get_scoring_config()
        hidden_key = _cache_key(league.season, config)

        # Switching self-service off reveals without touching any pick row
        league.season.episode2_starts_at = None
        league.db.session.commit()

        assert _cache_key(league.season, config) != hidden_key

    def test_compare_cache_key_changes_when_wildcards_are_revealed(self, league):
        from app.routes import _compare_cache_key

        self._add_wildcard(league, league.pat, league.survivors[4])
        hidden_key = _compare_cache_key(league.season)

        league.season.episode2_starts_at = None
        league.db.session.commit()

        assert _compare_cache_key(league.season) != hidden_key

    def test_compare_page_excludes_hidden_wildcards(self, league):
        before = _board(league.season)["pat"]["total_points"]
        self._add_wildcard(league, league.pat, league.survivors[4])

        resp = league.c.get(f"/compare/{league.season.id}")

        assert resp.status_code == 200
        match = re.search(rb"const progressionData = (\[.*\]);", resp.data)
        assert match, "compare chart data not found on the page"
        current = json.loads(match.group(1))[0]  # "Current Settings" preset
        final = {d["name"]: d["points"][-1] for d in current}
        assert final["Pat"] == round(before, 2)


# ── My Team page ──────────────────────────────────────────────────────────


class TestMyTeamWildcardSection:
    def test_offers_only_eligible_castaways(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        options = set(re.findall(rb'<option value="(\d+)"', resp.data))
        # In the game and not Pat's own draft pick (Castaway3)
        eligible = {str(league.survivors[i].id).encode() for i in (1, 3, 4, 5)}
        assert options == eligible

    def test_shows_the_lock_time_in_eastern(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b"Wed Oct 7, 7:45 PM ET" in resp.data

    def test_locked_wildcard_shows_no_form(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(AFTER_LOCK)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b'name="survivor_id"' not in resp.data
        assert b"Castaway5" in resp.data
        assert b"locked" in resp.data.lower()

    def test_after_the_lock_a_player_without_a_wildcard_sees_no_form(self, league):
        league.at(AFTER_LOCK)
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b'name="survivor_id"' not in resp.data
        assert b"my-wildcard-current" not in resp.data
        assert b"Wildcard picks are locked" in resp.data

    def test_no_wildcard_section_when_self_service_is_off(self, league):
        league.season.episode2_starts_at = None
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b'name="survivor_id"' not in resp.data


# ── Admin: Episode 2 start time ───────────────────────────────────────────


class TestAdminSetsEpisodeTwoTime:
    def _post(self, league, value):
        return league.c.post(
            f"/admin/season/{league.season.id}/wildcard-window",
            data={"episode2_starts_at": value},
        )

    def test_admin_enters_eastern_time_stored_as_utc(self, league):
        league.season.episode2_starts_at = None
        league.db.session.commit()
        _login(league.c, ADMIN_EMAIL)

        resp = self._post(league, "2026-10-07T20:00")

        assert resp.status_code == 302
        assert league.season.episode2_starts_at == datetime(2026, 10, 8, 0, 0)

    def test_winter_time_uses_the_standard_offset(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "2027-03-03T20:00")  # EST, UTC-5

        assert league.season.episode2_starts_at == datetime(2027, 3, 4, 1, 0)

    def test_blank_switches_self_service_off(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "")

        assert league.season.episode2_starts_at is None

    def test_invalid_time_is_rejected(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "next wednesday")

        assert league.season.episode2_starts_at == EP2_UTC.replace(tzinfo=None)
        assert _has_error(league.c)

    def test_player_cannot_set_it(self, league):
        _login(league.c, PAT_EMAIL)

        self._post(league, "2027-01-01T20:00")

        assert league.season.episode2_starts_at == EP2_UTC.replace(tzinfo=None)
        assert ("error", "Admin access required.") in _flashes(league.c)

    def test_season_admin_page_shows_the_time_and_who_has_picked(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.get(f"/admin/season/{league.season.id}")

        assert resp.status_code == 200
        assert b'value="2026-10-07T20:00"' in resp.data
        assert b"1 of 2" in resp.data
        assert b"Sam" in resp.data  # still to pick


class TestSchemaSync:
    def test_episode_two_column_is_added_to_an_existing_database(self, client):
        import sqlalchemy
        from sqlalchemy import text

        _c, db = client
        from app import _add_missing_columns

        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE season DROP COLUMN episode2_starts_at"))

        _add_missing_columns()

        cols = {c["name"] for c in sqlalchemy.inspect(db.engine).get_columns("season")}
        assert "episode2_starts_at" in cols
