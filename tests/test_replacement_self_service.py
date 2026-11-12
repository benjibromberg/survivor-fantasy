"""Tests for player replacement self-service: eligibility, lock and reveal."""

import importlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
SAM_EMAIL = "sam@example.com"
LEE_EMAIL = "lee@example.com"
KIM_EMAIL = "kim@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"

MERGE_EPISODE = 3
# The episode after the merge airs Wed 11 Nov 2026, 8:00 PM Eastern (EST, UTC-5)
NEXT_EP_UTC = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
LOCK_UTC = NEXT_EP_UTC - timedelta(minutes=15)
BEFORE_LOCK = LOCK_UTC - timedelta(hours=4)
AFTER_LOCK = LOCK_UTC + timedelta(minutes=1)


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
def league(client, tmp_path, monkeypatch):
    """Active season just after the merge, with replacement self-service on.

    The merge was Episode 3. survivoR has not published the jury size yet,
    so `left_at_jury` is unknown, as it is for a real season at this point.

    - Castaway1 went out in Episode 1 and Castaway2 in Episode 2.
    - Castaway3 went out in Episode 3, the merge episode.
    - Castaway4 to Castaway9 are still in the game; Castaway9 is on no team.

    Pat drafted Castaway1 (lost before the merge): a draft replacement.
    Sam lost wildcard Castaway2 but no draft pick: a wildcard replacement.
    Lee drafted Castaway3 (out in the merge episode): a draft replacement.
    Kim lost nobody: not eligible.
    """
    c, db = client
    from app import predictions, routes, wildcards
    from app.models import Pick, Season, Survivor, User

    monkeypatch.setattr(
        predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
    )
    predictions.clear_cache()
    routes._compare_cache.clear()

    clock = {"now": BEFORE_LOCK}
    # One league clock: wildcards.now_utc is what both locks read
    monkeypatch.setattr(wildcards, "now_utc", lambda: clock["now"])

    season = Season(
        number=99,
        name="Season 99",
        is_active=True,
        num_players=9,
        num_episodes=8,
        left_at_jury=None,
        n_finalists=None,
        merge_episode_num=MERGE_EPISODE,
        replacement_starts_at=NEXT_EP_UTC.replace(tzinfo=None),
    )
    other_season = Season(number=98, name="Season 98", is_active=False, num_players=6)
    db.session.add_all([season, other_season])
    db.session.flush()

    out = {0: 1, 1: 2, 2: 3}  # index -> episode they went out in
    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=i + 1 if i in out else 0,
            elimination_episode=out.get(i),
            day_voted_out=out[i] * 3 if i in out else None,
        )
        for i in range(9)
    ]
    outsider = Survivor(season_id=other_season.id, name="Outsider", voted_out_order=0)
    db.session.add_all([*survivors, outsider])

    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    sam = User(username="sam", display_name="Sam", email=SAM_EMAIL)
    lee = User(username="lee", display_name="Lee", email=LEE_EMAIL)
    kim = User(username="kim", display_name="Kim", email=KIM_EMAIL)
    db.session.add_all([pat, sam, lee, kim])
    db.session.flush()

    def pick(user, idx, ptype):
        return Pick(
            user_id=user.id,
            season_id=season.id,
            survivor_id=survivors[idx].id,
            pick_type=ptype,
        )

    db.session.add_all(
        [
            pick(pat, 0, "draft"),
            pick(pat, 3, "draft"),
            pick(sam, 4, "draft"),
            pick(sam, 1, "wildcard"),
            pick(lee, 2, "draft"),
            pick(lee, 5, "draft"),
            pick(kim, 6, "draft"),
            pick(kim, 7, "wildcard"),
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
        lee=lee,
        kim=kim,
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
    """POST a replacement choice as whoever is logged in."""
    season = season or league.season
    survivor_id = survivor if isinstance(survivor, str) else survivor.id
    return league.c.post(
        f"/my-team/{season.id}/replacement", data={"survivor_id": survivor_id}
    )


def _pick_as(league, email, survivor):
    _login(league.c, email)
    resp = _pick(league, survivor)
    _logout(league.c)
    return resp


def _replacement(user, season):
    """(castaway name, pick type) of the user's replacement, or None."""
    from app.models import Pick

    picks = Pick.query.filter(
        Pick.user_id == user.id,
        Pick.season_id == season.id,
        Pick.pick_type.in_(("pmr_w", "pmr_d")),
    ).all()
    assert len(picks) <= 1
    return (picks[0].survivor.name, picks[0].pick_type) if picks else None


def _board(season):
    from app.routes import _build_leaderboard

    entries, _ = _build_leaderboard(season)
    return {e["user"].username: e for e in entries}


def _pick_names(entry):
    return {p["survivor"] for p in entry["picks"]}


def _all_eligible_pick(league):
    _pick_as(league, PAT_EMAIL, league.survivors[4])
    _pick_as(league, SAM_EMAIL, league.survivors[5])
    _pick_as(league, LEE_EMAIL, league.survivors[6])


# ── Who is eligible, and for which kind ───────────────────────────────────


class TestEligibility:
    def _entitled(self, league, user):
        from app import replacements
        from app.models import Pick

        picks = Pick.query.filter_by(user_id=user.id, season_id=league.season.id)
        return replacements.entitlement(league.season, picks.all())

    def test_draft_pick_lost_before_the_merge_earns_a_draft_replacement(self, league):
        assert self._entitled(league, league.pat) == "pmr_d"

    def test_only_a_lost_wildcard_earns_a_wildcard_replacement(self, league):
        assert self._entitled(league, league.sam) == "pmr_w"

    def test_castaway_out_in_the_merge_episode_counts(self, league):
        assert self._entitled(league, league.lee) == "pmr_d"

    def test_player_who_lost_nobody_is_not_eligible(self, league):
        assert self._entitled(league, league.kim) is None

    def test_castaway_out_after_the_merge_does_not_count(self, league):
        kim_draft = league.survivors[6]
        kim_draft.voted_out_order = 4
        kim_draft.elimination_episode = MERGE_EPISODE + 1
        league.db.session.commit()

        assert self._entitled(league, league.kim) is None

    def test_lost_draft_wins_over_lost_wildcard(self, league):
        from app.models import Pick

        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=league.season.id,
                survivor_id=league.survivors[1].id,
                pick_type="wildcard",
            )
        )
        league.db.session.commit()

        assert self._entitled(league, league.pat) == "pmr_d"

    def test_nobody_is_eligible_before_the_merge_is_known(self, league):
        league.season.merge_episode_num = None
        league.db.session.commit()

        assert self._entitled(league, league.pat) is None


# ── Setting and changing a replacement ────────────────────────────────────


class TestPlayerSetsReplacement:
    def test_draft_replacement_is_stored_as_a_draft_replacement(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        assert _replacement(league.pat, league.season) == ("Castaway5", "pmr_d")

    def test_wildcard_replacement_is_stored_as_a_wildcard_replacement(self, league):
        _pick_as(league, SAM_EMAIL, league.survivors[5])

        assert _replacement(league.sam, league.season) == ("Castaway6", "pmr_w")

    def test_player_can_change_it_before_the_lock(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, PAT_EMAIL, league.survivors[5])

        assert _replacement(league.pat, league.season) == ("Castaway6", "pmr_d")

    def test_blank_choice_removes_it_before_the_lock(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, PAT_EMAIL, "")

        assert _replacement(league.pat, league.season) is None

    def test_changing_it_leaves_other_picks_alone(self, league):
        from app.models import Pick

        before = Pick.query.filter_by(user_id=league.pat.id).count()
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, PAT_EMAIL, league.survivors[5])

        assert Pick.query.filter_by(user_id=league.pat.id).count() == before + 1

    def test_two_players_may_pick_the_same_castaway(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[6])
        _pick_as(league, SAM_EMAIL, league.survivors[6])

        assert _replacement(league.pat, league.season)[0] == "Castaway7"
        assert _replacement(league.sam, league.season)[0] == "Castaway7"

    def test_castaway_on_another_players_team_is_allowed(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[5])  # Lee's draft pick

        assert _replacement(league.pat, league.season)[0] == "Castaway6"

    def test_saving_leaves_exactly_one_replacement(self, league):
        """Two simultaneous saves could each add a row; the next save heals it."""
        from app.models import Pick

        for idx in (5, 6):
            league.db.session.add(
                Pick(
                    user_id=league.pat.id,
                    season_id=league.season.id,
                    survivor_id=league.survivors[idx].id,
                    pick_type="pmr_d",
                )
            )
        league.db.session.commit()

        _pick_as(league, PAT_EMAIL, league.survivors[4])

        assert _replacement(league.pat, league.season) == ("Castaway5", "pmr_d")

    def test_removing_clears_every_replacement_row(self, league):
        from app.models import Pick

        for idx in (5, 6):
            league.db.session.add(
                Pick(
                    user_id=league.pat.id,
                    season_id=league.season.id,
                    survivor_id=league.survivors[idx].id,
                    pick_type="pmr_d",
                )
            )
        league.db.session.commit()

        _pick_as(league, PAT_EMAIL, "")

        assert _replacement(league.pat, league.season) is None

    def test_requires_login(self, league):
        resp = _pick(league, league.survivors[4])

        assert resp.status_code == 302
        assert _replacement(league.pat, league.season) is None


class TestIneligibleChoicesAndPlayers:
    def test_ineligible_player_cannot_pick(self, league):
        _login(league.c, KIM_EMAIL)
        _pick(league, league.survivors[4])

        assert _has_error(league.c)
        assert _replacement(league.kim, league.season) is None

    def test_own_team_castaway_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[3])  # Pat's own draft pick

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None

    def test_eliminated_castaway_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[1])

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None

    def test_castaway_from_another_season_is_rejected(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.outsider)

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None

    @pytest.mark.parametrize("bad", ["abc", "999999", "-1"])
    def test_garbage_survivor_id_is_rejected_without_crashing(self, league, bad):
        _login(league.c, PAT_EMAIL)
        resp = _pick(league, bad)

        assert resp.status_code == 302
        assert _has_error(league.c)

    def test_not_available_without_a_lock_time(self, league):
        league.season.replacement_starts_at = None
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None

    def test_not_available_for_an_inactive_season(self, league):
        league.season.is_active = False
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None


# ── The lock ──────────────────────────────────────────────────────────────


class TestLock:
    def test_lock_is_fifteen_minutes_before_the_episode_after_the_merge(self, league):
        from app import replacements

        assert replacements.lock_at(league.season) == LOCK_UTC

    def test_change_is_refused_at_the_lock_instant(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        league.at(LOCK_UTC)
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[5])

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season)[0] == "Castaway5"

    def test_change_is_allowed_one_second_before_the_lock(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        league.at(LOCK_UTC - timedelta(seconds=1))
        _pick_as(league, PAT_EMAIL, league.survivors[5])

        assert _replacement(league.pat, league.season)[0] == "Castaway6"

    def test_first_pick_is_refused_after_the_lock(self, league):
        league.at(AFTER_LOCK)
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        assert _has_error(league.c)
        assert _replacement(league.pat, league.season) is None

    def test_admin_can_enter_a_missing_replacement_after_the_lock(self, league):
        league.at(AFTER_LOCK)
        _login(league.c, ADMIN_EMAIL)

        league.c.post(
            f"/admin/picks/{league.season.id}",
            data={
                "user_id": league.pat.id,
                "draft": [league.survivors[0].id, league.survivors[3].id],
                "pmr_d": league.survivors[4].id,
            },
        )

        assert _replacement(league.pat, league.season) == ("Castaway5", "pmr_d")


# ── Reveal: hidden until every eligible player has picked ─────────────────


class TestHiddenUntilEveryEligiblePlayerHasPicked:
    def test_hidden_from_the_leaderboard_until_all_eligible_have_picked(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[5])

        assert _pick_names(_board(league.season)["pat"]) == {"Castaway1", "Castaway4"}

    def test_revealed_once_every_eligible_player_has_picked(self, league):
        _all_eligible_pick(league)

        board = _board(league.season)
        # Kim was not eligible, so the reveal does not wait on Kim
        assert "Castaway5" in _pick_names(board["pat"])
        assert "Castaway6" in _pick_names(board["sam"])
        assert "Castaway7" in _pick_names(board["lee"])

    def test_still_hidden_after_the_lock_while_someone_has_not_picked(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        league.at(AFTER_LOCK)

        assert "Castaway5" not in _pick_names(_board(league.season)["pat"])

    def test_hidden_replacement_is_absent_from_the_leaderboard_page(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[8])

        for viewer in (None, SAM_EMAIL, PAT_EMAIL):
            if viewer:
                _login(league.c, viewer)
            resp = league.c.get(f"/leaderboard/{league.season.id}")
            assert resp.status_code == 200
            assert b"Castaway9" not in resp.data, viewer
            _logout(league.c)

    def test_revealed_replacement_is_on_the_leaderboard_page(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[8])
        _pick_as(league, SAM_EMAIL, league.survivors[5])
        _pick_as(league, LEE_EMAIL, league.survivors[6])

        resp = league.c.get(f"/leaderboard/{league.season.id}")

        assert b"Castaway9" in resp.data

    def test_owner_sees_their_own_hidden_replacement_on_my_team(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b"my-replacement-current" in resp.data
        assert b"1 of 3" in resp.data

    def test_another_player_does_not_see_it_on_their_my_team(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _login(league.c, SAM_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b"my-replacement-current" not in resp.data
        assert b" selected" not in resp.data
        assert b"1 of 3" in resp.data

    def test_seasons_without_self_service_keep_showing_replacements(self, league):
        from app.models import Pick

        league.season.replacement_starts_at = None
        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=league.season.id,
                survivor_id=league.survivors[4].id,
                pick_type="pmr_d",
            )
        )
        league.db.session.commit()

        assert "Castaway5" in _pick_names(_board(league.season)["pat"])

    def test_removing_a_replacement_hides_everyones_again(self, league):
        _all_eligible_pick(league)
        _pick_as(league, LEE_EMAIL, "")

        assert "Castaway5" not in _pick_names(_board(league.season)["pat"])

    @pytest.mark.parametrize("as_of", [0, 1, 3])
    def test_hidden_replacement_is_absent_from_timeline_views(self, league, as_of):
        """The timeline rewinds castaways; the reveal must not rewind with them."""
        _pick_as(league, PAT_EMAIL, league.survivors[8])

        resp = league.c.get(f"/leaderboard/{league.season.id}?as_of={as_of}")

        assert resp.status_code == 200
        assert b"Castaway9" not in resp.data

    def test_revealed_replacement_stays_off_pre_merge_timeline_views(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[8])
        _pick_as(league, SAM_EMAIL, league.survivors[5])
        _pick_as(league, LEE_EMAIL, league.survivors[6])
        sid = league.season.id

        assert b"Castaway9" not in league.c.get(f"/leaderboard/{sid}?as_of=1").data
        assert b"Castaway9" in league.c.get(f"/leaderboard/{sid}?as_of=3").data

    def test_compare_cache_key_changes_at_the_reveal(self, league):
        from app.routes import _compare_cache_key

        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[5])
        hidden_key = _compare_cache_key(league.season)

        _pick_as(league, LEE_EMAIL, league.survivors[6])

        assert _compare_cache_key(league.season) != hidden_key

    def test_prediction_cache_key_changes_at_the_reveal(self, league):
        from app.predictions import _cache_key

        config = league.season.get_scoring_config()
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _pick_as(league, SAM_EMAIL, league.survivors[5])
        hidden_key = _cache_key(league.season, config)

        # Switching self-service off reveals without touching any pick row
        league.season.replacement_starts_at = None
        league.db.session.commit()

        assert _cache_key(league.season, config) != hidden_key

    def test_hidden_replacement_does_not_change_win_probabilities(self, league):
        from app import predictions

        # Odds need the jury size; give the season one
        league.season.left_at_jury = 5
        league.season.n_finalists = 3
        league.db.session.commit()
        base, *_ = predictions.calculate_win_probabilities(league.season)
        base_pcts = {uid: d["win_pct"] for uid, d in base.items()}
        assert base_pcts, "simulation produced no results"
        predictions.clear_cache()

        _pick_as(league, PAT_EMAIL, league.survivors[4])

        hidden, *_ = predictions.calculate_win_probabilities(league.season)
        assert {uid: d["win_pct"] for uid, d in hidden.items()} == base_pcts

    def test_compare_page_excludes_hidden_replacements(self, league):
        from app import replacements

        league.season.scoring_config = json.dumps({"draft_replacement_multiplier": 1})
        league.db.session.commit()
        stats = league.survivors[4]
        stats.individual_immunity_wins = 2
        league.db.session.commit()
        before = _board(league.season)["pat"]["total_points"]
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        assert replacements.are_hidden(league.season)

        resp = league.c.get(f"/compare/{league.season.id}")

        assert resp.status_code == 200
        match = re.search(rb"const progressionData = (\[.*\]);", resp.data)
        assert match, "compare chart data not found on the page"
        current = json.loads(match.group(1))[0]
        final = {d["name"]: d["points"][-1] for d in current}
        assert final["Pat"] == round(before, 2)


# ── Scoring while the jury size is still unknown ──────────────────────────


class TestRevealedReplacementsBeforeTheJurySizeIsKnown:
    def test_revealed_replacement_is_on_the_roster(self, league):
        _all_eligible_pick(league)

        assert league.season.merge_threshold is None
        assert "Castaway5" in _pick_names(_board(league.season)["pat"])

    def test_same_day_boots_still_let_replacements_count(self, league):
        """Two boots on one day: the start point counts days, like the tribal count."""
        from app import replacements

        league.survivors[1].day_voted_out = league.survivors[0].day_voted_out
        league.db.session.commit()

        assert league.season.current_tribal_count == 2
        assert replacements.scoring_start(league.season) == (2, MERGE_EPISODE)
        _all_eligible_pick(league)
        assert "Castaway5" in _pick_names(_board(league.season)["pat"])

    def test_stats_up_to_the_merge_episode_do_not_count(self, league):
        league.season.scoring_config = json.dumps({"draft_replacement_multiplier": 1})
        early, late = league.survivors[4], league.survivors[8]
        # Both found one advantage; Castaway5's came by the end of the merge episode
        for s in (early, late):
            s.advantages_found = 1
        early.episode_stats = json.dumps({str(MERGE_EPISODE): {"adv": 1}})
        late.episode_stats = json.dumps({str(MERGE_EPISODE): {"adv": 0}})
        league.db.session.commit()

        _pick_as(league, PAT_EMAIL, early)
        _pick_as(league, SAM_EMAIL, late)
        _pick_as(league, LEE_EMAIL, late)
        board = _board(league.season)

        def points(entry, name):
            return next(
                p["modified_points"] for p in entry["picks"] if p["survivor"] == name
            )

        assert points(board["lee"], "Castaway9") > points(board["pat"], "Castaway5")


class TestScoringStart:
    def _jury_starts_after_the_merge(self, league):
        """survivoR publishes a jury that starts with Episode 4's boot."""
        kim_draft = league.survivors[6]
        kim_draft.voted_out_order = 4
        kim_draft.elimination_episode = MERGE_EPISODE + 1
        kim_draft.day_voted_out = 12
        league.season.left_at_jury = 5  # merge_threshold = 9 - 5 = 4
        league.db.session.commit()

    def test_self_service_season_counts_from_the_merge_episode(self, league):
        from app import replacements

        self._jury_starts_after_the_merge(league)

        assert replacements.scoring_start(league.season)[1] == MERGE_EPISODE

    def test_other_seasons_keep_the_jury_based_baseline(self, league):
        from app import replacements

        self._jury_starts_after_the_merge(league)
        league.season.replacement_starts_at = None
        league.db.session.commit()

        assert replacements.scoring_start(league.season) == (4, MERGE_EPISODE + 1)


# ── Leaderboard reminders ─────────────────────────────────────────────────


class TestReminders:
    def test_reminders_follow_the_before_the_merge_rule(self, league):
        board = _board(league.season)

        assert "Eligible for draft replacement" in board["pat"]["warnings"]
        assert "Eligible for wildcard replacement" in board["sam"]["warnings"]
        assert "Eligible for draft replacement" in board["lee"]["warnings"]
        assert not any("replacement" in w for w in board["kim"]["warnings"])

    def test_no_reminder_once_picked(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])

        assert not any(
            "replacement" in w for w in _board(league.season)["pat"]["warnings"]
        )


# ── My Team page ──────────────────────────────────────────────────────────


class TestMyTeamReplacementSection:
    def test_offers_only_eligible_castaways(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        section = resp.data.split(b'id="replacement"', 1)[1].split(b"</article>")[0]
        options = set(re.findall(rb'<option value="(\d+)"', section))
        # In the game and not on Pat's own team (Castaway4)
        eligible = {str(league.survivors[i].id).encode() for i in (4, 5, 6, 7, 8)}
        assert options == eligible

    def test_says_which_kind_and_when_it_locks(self, league):
        _login(league.c, SAM_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b"wildcard replacement" in resp.data.lower()
        assert b"Wed Nov 11, 7:45 PM ET" in resp.data

    def test_ineligible_player_sees_no_form(self, league):
        _login(league.c, KIM_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert b'id="replacement"' in resp.data
        assert (
            b"/replacement"
            not in resp.data.split(b'id="replacement"', 1)[1].split(b"</article>")[0]
        )

    def test_locked_replacement_shows_no_form(self, league):
        _login(league.c, PAT_EMAIL)
        _pick(league, league.survivors[4])
        league.at(AFTER_LOCK)

        resp = league.c.get(f"/my-team/{league.season.id}")

        section = resp.data.split(b'id="replacement"', 1)[1].split(b"</article>")[0]
        assert b"<form" not in section
        assert b"Castaway5" in section
        assert b"locked" in section.lower()

    def test_no_section_when_self_service_is_off(self, league):
        league.season.replacement_starts_at = None
        league.db.session.commit()
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/my-team/{league.season.id}")

        assert resp.status_code == 200
        assert b'id="replacement"' not in resp.data


# ── Looking up when the episode after the merge starts ────────────────────


class TestSyncReplacementTime:
    @pytest.fixture()
    def fresh(self, league, monkeypatch):
        from app import schedule

        league.season.replacement_starts_at = None
        league.db.session.commit()
        calls = []

        def fake_tvmaze(season_number, episode=2):
            calls.append((season_number, episode))
            return NEXT_EP_UTC.replace(tzinfo=None)

        monkeypatch.setattr(schedule, "tvmaze_episode_start", fake_tvmaze)
        league.calls = calls
        return league

    def test_looks_up_the_episode_after_the_merge(self, fresh):
        from app import schedule

        assert schedule.sync_replacement_time(fresh.season) is True
        assert fresh.calls == [(99, MERGE_EPISODE + 1)]
        assert fresh.season.replacement_starts_at == NEXT_EP_UTC.replace(tzinfo=None)

    def test_falls_back_to_a_week_after_the_merge_episode(self, fresh, monkeypatch):
        from app import data, schedule

        monkeypatch.setattr(schedule, "tvmaze_episode_start", lambda *a, **k: None)
        episodes = pd.DataFrame(
            [
                {
                    "version": "US",
                    "season": 99,
                    "episode": MERGE_EPISODE,
                    "episode_date": pd.Timestamp("2026-11-04"),
                }
            ]
        )
        monkeypatch.setattr(data, "read_episodes", lambda: episodes)

        assert schedule.sync_replacement_time(fresh.season) is True
        assert fresh.season.replacement_starts_at == NEXT_EP_UTC.replace(tzinfo=None)

    def test_does_nothing_before_the_merge_is_known(self, fresh):
        from app import schedule

        fresh.season.merge_episode_num = None
        fresh.db.session.commit()

        assert schedule.sync_replacement_time(fresh.season) is False
        assert fresh.calls == []

    def test_does_nothing_for_an_inactive_season(self, fresh):
        from app import schedule

        fresh.season.is_active = False
        fresh.db.session.commit()

        assert schedule.sync_replacement_time(fresh.season) is False
        assert fresh.season.replacement_starts_at is None

    def test_never_replaces_a_time_the_admin_typed(self, fresh):
        from app import schedule

        typed = datetime(2026, 11, 12, 2, 0)
        fresh.season.replacement_starts_at = typed
        fresh.season.replacement_manual = True
        fresh.db.session.commit()

        assert schedule.sync_replacement_time(fresh.season) is False
        assert fresh.season.replacement_starts_at == typed

    def test_does_not_switch_on_once_that_episode_has_locked(self, fresh):
        from app import schedule

        fresh.at(AFTER_LOCK)

        assert schedule.sync_replacement_time(fresh.season) is False
        assert fresh.season.replacement_starts_at is None

    def test_a_failed_lookup_changes_nothing(self, fresh, monkeypatch):
        from app import schedule

        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(schedule, "tvmaze_episode_start", boom)

        assert schedule.sync_replacement_time(fresh.season) is False
        assert fresh.season.replacement_starts_at is None

    def test_daily_refresh_looks_it_up(self, fresh, monkeypatch):
        from flask import current_app

        from app import scheduler

        monkeypatch.setattr(
            "app.data.download_survivor_data",
            lambda: (_ for _ in ()).throw(OSError("offline")),
        )
        monkeypatch.setattr("app.schedule.sync_episode2_time", lambda s: False)

        scheduler.refresh_active_seasons(current_app._get_current_object())

        assert fresh.season.replacement_starts_at == NEXT_EP_UTC.replace(tzinfo=None)


# ── Admin: the season page ────────────────────────────────────────────────


class TestAdminReplacementWindow:
    def _post(self, league, value):
        return league.c.post(
            f"/admin/season/{league.season.id}/replacement-window",
            data={"replacement_starts_at": value},
        )

    def test_admin_override_is_stored_as_utc_and_kept(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "2026-11-11T21:00")

        assert league.season.replacement_starts_at == datetime(2026, 11, 12, 2, 0)
        assert league.season.replacement_manual is True

    def test_blank_goes_back_to_automatic(self, league, monkeypatch):
        monkeypatch.setattr("app.schedule.tvmaze_episode_start", lambda *a, **k: None)
        monkeypatch.setattr("app.data.read_episodes", lambda: pd.DataFrame())
        league.season.replacement_manual = True
        league.db.session.commit()
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "")

        assert league.season.replacement_manual is False
        assert league.season.replacement_starts_at is None

    def test_invalid_time_is_rejected(self, league):
        _login(league.c, ADMIN_EMAIL)

        self._post(league, "not a time")

        assert _has_error(league.c)
        assert league.season.replacement_starts_at == NEXT_EP_UTC.replace(tzinfo=None)

    def test_player_cannot_set_it(self, league):
        _login(league.c, PAT_EMAIL)

        self._post(league, "2026-11-11T21:00")

        assert league.season.replacement_starts_at == NEXT_EP_UTC.replace(tzinfo=None)

    def test_season_page_shows_the_lock_and_who_has_picked(self, league):
        _pick_as(league, PAT_EMAIL, league.survivors[4])
        _login(league.c, ADMIN_EMAIL)

        resp = league.c.get(f"/admin/season/{league.season.id}")

        assert resp.status_code == 200
        assert b'value="2026-11-11T20:00"' in resp.data
        assert b"Wed Nov 11, 7:45 PM ET" in resp.data
        section = resp.data.split(b'id="replacement-window"', 1)[1]
        section = section.split(b"</section>")[0]
        assert b"1 of 3" in section
        assert b"Sam" in section and b"Lee" in section
        assert b"Kim" not in section


class TestSchemaSync:
    def test_replacement_columns_are_added_to_an_existing_database(self, client):
        import sqlalchemy
        from sqlalchemy import text

        _c, db = client
        from app import _add_missing_columns

        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE season DROP COLUMN replacement_starts_at"))
            conn.execute(text("ALTER TABLE season DROP COLUMN replacement_manual"))

        _add_missing_columns()

        cols = {c["name"] for c in sqlalchemy.inspect(db.engine).get_columns("season")}
        assert {"replacement_starts_at", "replacement_manual"} <= cols
