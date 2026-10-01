"""Tests for the automatic Episode 2 start time (app/schedule.py)."""

import importlib
import json
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest
import requests

ADMIN_EMAIL = "admin@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"

# Wed 30 Sep 2026, 8:00 PM Eastern (EDT, UTC-4), as naive UTC
EP2 = datetime(2026, 10, 1, 0, 0)
EP2_MOVED = datetime(2026, 10, 2, 0, 0)
# The tests run before either lock unless they move the clock
BEFORE_LOCKS = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
AFTER_EP2_LOCK = datetime(2026, 9, 30, 23, 50, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Freeze the wildcard clock. Set `clock["now"]` to move it."""
    now = {"now": BEFORE_LOCKS}
    monkeypatch.setattr("app.wildcards.now_utc", lambda: now["now"])
    return now


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
def lookup(monkeypatch):
    """Replace the network lookup. Set `lookup.value`; `lookup.calls` records seasons."""

    class Lookup:
        value = (EP2, "TVmaze")
        calls = []

    Lookup.calls = []

    def fake(season_number):
        Lookup.calls.append(season_number)
        return Lookup.value

    monkeypatch.setattr("app.schedule.episode2_start", fake)
    return Lookup


def _season(db, number=60, active=True, **fields):
    from app.models import Season

    season = Season(number=number, name=f"Season {number}", is_active=active, **fields)
    db.session.add(season)
    db.session.commit()
    return season


def _login_admin(c):
    assert c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL}).status_code == 302


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _episode(**overrides):
    payload = {
        "season": 60,
        "number": 2,
        "airdate": "2026-09-30",
        "airtime": "20:00",
        "airstamp": "2026-10-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# ── TVmaze ────────────────────────────────────────────────────────────────


class TestTvmazeEpisodeStart:
    def _patch(self, monkeypatch, response=None, error=None):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(url=url, params=params, timeout=timeout)
            if error:
                raise error
            return response

        monkeypatch.setattr("app.schedule.requests.get", fake_get)
        return seen

    def test_returns_the_airstamp_as_naive_utc(self, monkeypatch):
        from app import schedule

        seen = self._patch(monkeypatch, _Response(payload=_episode()))

        assert schedule.tvmaze_episode_start(60, 2) == EP2
        assert seen["url"] == "https://api.tvmaze.com/shows/114/episodebynumber"
        assert seen["params"] == {"season": 60, "number": 2}
        assert seen["timeout"]

    def test_converts_an_offset_airstamp_to_utc(self, monkeypatch):
        from app import schedule

        payload = _episode(airstamp="2026-09-30T20:00:00-04:00")
        self._patch(monkeypatch, _Response(payload=payload))

        assert schedule.tvmaze_episode_start(60, 2) == EP2

    def test_unknown_episode_is_none(self, monkeypatch):
        from app import schedule

        self._patch(monkeypatch, _Response(status_code=404, payload={"status": 404}))

        assert schedule.tvmaze_episode_start(60, 2) is None

    def test_network_error_is_none(self, monkeypatch):
        from app import schedule

        self._patch(monkeypatch, error=requests.ConnectionError("unreachable"))

        assert schedule.tvmaze_episode_start(60, 2) is None

    def test_server_error_is_none(self, monkeypatch):
        from app import schedule

        self._patch(monkeypatch, _Response(status_code=503, payload={}))

        assert schedule.tvmaze_episode_start(60, 2) is None

    @pytest.mark.parametrize(
        "payload",
        [
            _episode(airstamp=None),
            _episode(airstamp=""),
            _episode(airstamp="next wednesday"),
            _episode(airstamp="2026-09-30T20:00:00"),  # no offset: ambiguous
            _episode(season=59),
            _episode(number=3),
            None,
        ],
    )
    def test_unusable_response_is_none(self, monkeypatch, payload):
        from app import schedule

        self._patch(monkeypatch, _Response(payload=payload))

        assert schedule.tvmaze_episode_start(60, 2) is None


# ── survivoR fallback ─────────────────────────────────────────────────────


def _episodes(rows):
    return pd.DataFrame(rows, columns=["version", "season", "episode", "episode_date"])


class TestEpisode2FromPremiere:
    def test_one_week_after_the_premiere_at_eight_eastern(self):
        from app import schedule

        episodes = _episodes([("US", 60, 1, pd.Timestamp("2026-09-23"))])

        assert schedule.episode2_from_premiere(episodes, 60) == EP2

    def test_winter_premiere_uses_the_standard_offset(self):
        from app import schedule

        episodes = _episodes([("US", 60, 1, pd.Timestamp("2026-02-25"))])

        # Wed 4 Mar 2026, 8 PM EST (UTC-5)
        assert schedule.episode2_from_premiere(episodes, 60) == datetime(
            2026, 3, 5, 1, 0
        )

    def test_ignores_other_versions_and_seasons(self):
        from app import schedule

        episodes = _episodes(
            [
                ("AU", 60, 1, pd.Timestamp("2026-01-01")),
                ("US", 59, 1, pd.Timestamp("2026-02-25")),
            ]
        )

        assert schedule.episode2_from_premiere(episodes, 60) is None

    def test_missing_premiere_date_is_none(self):
        from app import schedule

        assert schedule.episode2_from_premiere(_episodes([]), 60) is None
        episodes = _episodes([("US", 60, 1, pd.NaT)])
        assert schedule.episode2_from_premiere(episodes, 60) is None

    def test_missing_dataset_is_none(self, tmp_path, monkeypatch):
        from app import schedule

        monkeypatch.setattr(
            "app.data.SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
        )

        assert schedule.survivor_episode2_start(60) is None


class TestEpisode2Start:
    def test_prefers_tvmaze(self, monkeypatch):
        from app import schedule

        monkeypatch.setattr(schedule, "tvmaze_episode_start", lambda s, e=2: EP2)
        monkeypatch.setattr(schedule, "survivor_episode2_start", lambda s: EP2_MOVED)

        assert schedule.episode2_start(60) == (EP2, "TVmaze")

    def test_falls_back_to_survivor(self, monkeypatch):
        from app import schedule

        monkeypatch.setattr(schedule, "tvmaze_episode_start", lambda s, e=2: None)
        monkeypatch.setattr(schedule, "survivor_episode2_start", lambda s: EP2)

        assert schedule.episode2_start(60) == (EP2, "survivoR")

    def test_none_when_neither_source_knows(self, monkeypatch):
        from app import schedule

        monkeypatch.setattr(schedule, "tvmaze_episode_start", lambda s, e=2: None)
        monkeypatch.setattr(schedule, "survivor_episode2_start", lambda s: None)

        assert schedule.episode2_start(60) == (None, None)


# ── Applying it to a season ───────────────────────────────────────────────


class TestSyncEpisode2Time:
    def test_fills_the_active_season(self, client, lookup):
        _c, db = client
        from app import schedule

        season = _season(db)

        assert schedule.sync_episode2_time(season) is True
        assert season.episode2_starts_at == EP2
        assert season.episode2_manual is False

    def test_never_fills_an_inactive_season(self, client, lookup):
        """A past season with a time could get its wildcards hidden."""
        _c, db = client
        from app import schedule

        season = _season(db, active=False)

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at is None
        assert lookup.calls == []

    def test_never_overwrites_an_admin_time(self, client, lookup):
        _c, db = client
        from app import schedule

        season = _season(db, episode2_starts_at=EP2_MOVED, episode2_manual=True)

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at == EP2_MOVED
        assert lookup.calls == []

    def test_follows_a_schedule_change(self, client, lookup):
        _c, db = client
        from app import schedule

        season = _season(db, episode2_starts_at=EP2)
        lookup.value = (EP2_MOVED, "TVmaze")

        assert schedule.sync_episode2_time(season) is True
        assert season.episode2_starts_at == EP2_MOVED

    def test_keeps_the_known_time_when_the_lookup_fails(self, client, lookup):
        _c, db = client
        from app import schedule

        season = _season(db, episode2_starts_at=EP2)
        lookup.value = (None, None)

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at == EP2

    def test_does_not_move_a_time_whose_lock_has_passed(self, client, lookup, clock):
        """Picks are final once locked, even if the TV schedule changes later."""
        _c, db = client
        from app import schedule

        season = _season(db, episode2_starts_at=EP2)
        lookup.value = (EP2_MOVED, "TVmaze")
        clock["now"] = AFTER_EP2_LOCK

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at == EP2
        assert lookup.calls == []

    def test_does_not_switch_on_after_episode_two_started(self, client, lookup, clock):
        """Turning self-service on late would hide wildcards the admin entered."""
        _c, db = client
        from app import schedule

        season = _season(db)
        clock["now"] = AFTER_EP2_LOCK

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at is None

    def test_skips_a_season_switched_off(self, client, lookup):
        _c, db = client
        from app import schedule

        season = _season(db, wildcard_self_service=False)

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at is None
        assert lookup.calls == []

    def test_a_lookup_that_raises_does_not_propagate(self, client, monkeypatch):
        _c, db = client
        from app import schedule

        def boom(_season_number):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("app.schedule.episode2_start", boom)
        season = _season(db)

        assert schedule.sync_episode2_time(season) is False
        assert season.episode2_starts_at is None


# ── Self-service on and off ───────────────────────────────────────────────


class TestSelfServiceSwitch:
    def test_on_when_the_time_is_known(self, client):
        _c, db = client
        from app import wildcards

        season = _season(db, episode2_starts_at=EP2)

        assert wildcards.is_self_service(season) is True

    def test_off_without_a_time(self, client):
        _c, db = client
        from app import wildcards

        assert wildcards.is_self_service(_season(db)) is False

    def test_switch_turns_it_off_even_with_a_time(self, client):
        _c, db = client
        from app import wildcards

        season = _season(db, episode2_starts_at=EP2, wildcard_self_service=False)

        assert wildcards.is_self_service(season) is False
        assert wildcards.are_hidden(season) is False


# ── Admin form ────────────────────────────────────────────────────────────


class TestAdminWildcardWindow:
    def _post(self, c, season, **data):
        return c.post(f"/admin/season/{season.id}/wildcard-window", data=data)

    def test_typed_time_is_kept_as_an_override(self, client, lookup):
        c, db = client
        season = _season(db)
        _login_admin(c)

        self._post(c, season, episode2_starts_at="2026-10-01T20:00", self_service="on")

        assert season.episode2_starts_at == EP2_MOVED
        assert season.episode2_manual is True
        assert lookup.calls == []

    def test_blank_time_returns_to_automatic(self, client, lookup):
        c, db = client
        season = _season(db, episode2_starts_at=EP2_MOVED, episode2_manual=True)
        _login_admin(c)

        self._post(c, season, episode2_starts_at="", self_service="on")

        assert season.episode2_manual is False
        assert season.episode2_starts_at == EP2
        assert lookup.calls == [60]

    def test_blank_time_with_no_known_schedule_leaves_it_empty(self, client, lookup):
        c, db = client
        lookup.value = (None, None)
        season = _season(db, episode2_starts_at=EP2_MOVED, episode2_manual=True)
        _login_admin(c)

        self._post(c, season, episode2_starts_at="", self_service="on")

        assert season.episode2_starts_at is None
        assert season.episode2_manual is False

    def test_unticked_box_switches_self_service_off(self, client, lookup):
        c, db = client
        from app import wildcards

        season = _season(db, episode2_starts_at=EP2)
        _login_admin(c)

        self._post(c, season, episode2_starts_at="")

        assert season.wildcard_self_service is False
        assert wildcards.is_self_service(season) is False

    def test_ticked_box_switches_it_back_on(self, client, lookup):
        c, db = client
        season = _season(db, episode2_starts_at=EP2, wildcard_self_service=False)
        _login_admin(c)

        self._post(c, season, episode2_starts_at="", self_service="on")

        assert season.wildcard_self_service is True

    def test_admin_page_says_where_the_time_came_from(self, client, lookup):
        c, db = client
        season = _season(db, episode2_starts_at=EP2)
        _login_admin(c)

        page = c.get(f"/admin/season/{season.id}").data.decode()
        assert "filled in automatically" in page

        season.episode2_manual = True
        db.session.commit()
        page = c.get(f"/admin/season/{season.id}").data.decode()
        assert "entered by hand" in page


# ── Where the sync runs ───────────────────────────────────────────────────


class TestSyncTriggers:
    def test_activating_a_season_fills_its_time(self, client, lookup):
        c, db = client
        _season(db, number=60, active=True, episode2_starts_at=EP2)
        new = _season(db, number=61, active=False)
        _login_admin(c)

        c.post(f"/admin/season/{new.id}/toggle-active")

        assert new.is_active is True
        assert new.episode2_starts_at == EP2
        assert lookup.calls == [61]

    def test_deactivating_does_not_look_anything_up(self, client, lookup):
        c, db = client
        season = _season(db, number=60, active=True)
        _login_admin(c)

        c.post(f"/admin/season/{season.id}/toggle-active")

        assert season.is_active is False
        assert lookup.calls == []

    def test_admin_refresh_fills_the_time(self, client, lookup, monkeypatch):
        c, db = client
        season = _season(db)
        monkeypatch.setattr("app.routes.download_survivor_data", lambda: None)
        monkeypatch.setattr("app.routes.refresh_season", lambda s: (0, []))
        monkeypatch.setattr("app.routes.export_all_picks", lambda: [])
        _login_admin(c)

        c.post(f"/admin/season/{season.id}/refresh")

        assert season.episode2_starts_at == EP2

    def test_daily_refresh_fills_the_time(self, client, lookup, monkeypatch):
        _c, db = client
        from flask import current_app

        from app import scheduler

        season = _season(db)
        monkeypatch.setattr("app.data.download_survivor_data", lambda: None)
        monkeypatch.setattr("app.data.refresh_season", lambda s: (0, []))
        monkeypatch.setattr("app.data.export_all_picks", lambda: [])

        scheduler.refresh_active_seasons(current_app._get_current_object())

        db.session.refresh(season)
        assert season.episode2_starts_at == EP2

    def test_add_season_activate_fills_the_time(self, client, lookup):
        _c, db = client
        import add_season

        season = _season(db, active=False)

        add_season.activate(season)

        assert season.is_active is True
        assert season.episode2_starts_at == EP2


# ── Export and re-seed ────────────────────────────────────────────────────


class TestFlagsSurviveAReseed:
    def _roundtrip(self, db, tmp_path, **fields):
        from app.data import export_season_picks
        from app.models import Pick, Season, Survivor, User
        from seed import load_picks_from_json

        season = _season(db, episode2_starts_at=EP2, **fields)
        survivor = Survivor(season_id=season.id, name="Ada")
        user = User(username="playera", display_name="PlayerA")
        db.session.add_all([survivor, user])
        db.session.flush()
        db.session.add(
            Pick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=survivor.id,
                pick_type="draft",
            )
        )
        db.session.commit()

        path = export_season_picks(season, picks_dir=str(tmp_path / "picks"))
        with open(path) as f:
            exported = json.load(f)

        fresh = Season(number=61, name="Season 61")
        db.session.add(fresh)
        db.session.flush()
        db.session.add(Survivor(season_id=fresh.id, name="Ada"))
        db.session.commit()
        survivors = {s.name.lower(): s for s in fresh.survivors}
        load_picks_from_json(path, fresh, survivors)
        return exported, fresh

    def test_defaults_are_not_written_and_come_back_as_defaults(self, client, tmp_path):
        _c, db = client

        exported, fresh = self._roundtrip(db, tmp_path)

        assert "wildcard_self_service" not in exported
        assert "episode2_manual" not in exported
        assert fresh.wildcard_self_service is True
        assert fresh.episode2_manual is False
        assert fresh.episode2_starts_at == EP2

    def test_switched_off_and_manual_time_are_restored(self, client, tmp_path):
        _c, db = client

        exported, fresh = self._roundtrip(
            db, tmp_path, wildcard_self_service=False, episode2_manual=True
        )

        assert exported["wildcard_self_service"] is False
        assert exported["episode2_manual"] is True
        assert fresh.wildcard_self_service is False
        assert fresh.episode2_manual is True


# ── Existing databases ────────────────────────────────────────────────────


class TestSchemaSync:
    def test_new_columns_get_their_defaults_on_existing_rows(self, client):
        from sqlalchemy import text

        _c, db = client
        from app import _add_missing_columns

        _season(db, number=60)
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE season DROP COLUMN wildcard_self_service"))
            conn.execute(text("ALTER TABLE season DROP COLUMN episode2_manual"))

        _add_missing_columns()

        with db.engine.begin() as conn:
            row = conn.execute(
                text("SELECT wildcard_self_service, episode2_manual FROM season")
            ).one()
        assert tuple(row) == (1, 0)
