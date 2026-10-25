"""Admin refreshes from survivoR run beside the request, not inside it.

The Refresh button and season creation used to download survivoR and refresh
the season inside the request. On the production host that took 27.9 s
against gunicorn's 30 s worker timeout, and past it the worker is killed: the
admin gets an error page and the season is not refreshed. Retries and the
headshot fetch make the time depend on other people's servers, so the work
now runs in a thread of its own (app/refresh_jobs.py). The request answers at
once, and the season's admin page shows the refresh running, then what it
reported, from the season row, so either gunicorn worker can show it.

Every other test waits for the thread before the request returns
(tests/conftest.py); the first class here lets it outlive the request.
"""

import importlib
import sys
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests

ADMIN_EMAIL = "admin@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"
COMMIT = "c720d36c6c8706c052a822bcda850ee273d08c49"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB, admin login configured, CSRF off."""
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
        db.engine.dispose()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def survivor(monkeypatch):
    """Stand-ins for the network and the heavy work a refresh does.

    `refreshed` and `images` list season numbers and `exports` counts pick
    exports. Set `download_error` or `refresh_error` to make that step raise,
    or `hold` to an Event the refresh waits for; `entered` is set once the
    refresh has started.
    """
    from app import refresh_jobs

    state = SimpleNamespace(
        refreshed=[],
        images=[],
        exports=0,
        download_error=None,
        refresh_error=None,
        hold=None,
        entered=threading.Event(),
    )

    def download():
        if state.download_error:
            raise state.download_error
        return COMMIT

    def refresh(season):
        state.entered.set()
        if state.hold is not None:
            assert state.hold.wait(30), "the test never released the refresh"
        if state.refresh_error:
            raise state.refresh_error
        state.refreshed.append(season.number)
        return 18, ["Day 3 is missing"]

    def images(season):
        state.images.append(season.number)
        return 12

    def export():
        state.exports += 1
        return []

    monkeypatch.setattr(refresh_jobs, "download_survivor_data", download)
    monkeypatch.setattr(refresh_jobs, "refresh_season", refresh)
    monkeypatch.setattr(refresh_jobs, "generate_season_images", images)
    monkeypatch.setattr(refresh_jobs, "export_all_picks", export)
    monkeypatch.setattr("app.schedule.episode2_start", lambda _n: (None, None))
    return state


@pytest.fixture()
def outliving(refreshes_finish_before_the_response, monkeypatch):
    """Let refresh threads outlive the request; join them at teardown."""
    from app import refresh_jobs

    real_start = refreshes_finish_before_the_response
    threads = []

    def start(target):
        thread = real_start(target)
        threads.append(thread)
        return thread

    monkeypatch.setattr(refresh_jobs, "_start_thread", start)
    yield threads
    for thread in threads:
        thread.join(timeout=30)


def _login(c):
    assert c.get("/login", headers={ACCESS_HEADER: ADMIN_EMAIL}).status_code == 302


def _season(db, number=51):
    from app.models import Season

    season = Season(number=number, name=f"Season {number}")
    db.session.add(season)
    db.session.commit()
    return season


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _page(c, season):
    return c.get(f"/admin/season/{season.id}").data.decode()


def _status(db, season):
    from app import refresh_jobs

    db.session.refresh(season)
    return refresh_jobs.status(season)


class TestTheRequestDoesNotWait:
    def test_refresh_answers_while_the_refresh_is_still_running(
        self, client, survivor, outliving
    ):
        c, db = client
        season = _season(db)
        _login(c)
        survivor.hold = threading.Event()

        try:
            resp = c.post(f"/admin/season/{season.id}/refresh")

            assert resp.status_code == 302
            assert survivor.entered.wait(10)
            assert survivor.refreshed == []
            assert _status(db, season)["state"] == "running"
            page = _page(c, season)
            assert "Refreshing from survivoR" in page
            assert 'http-equiv="refresh"' in page
        finally:
            survivor.hold.set()
            for thread in outliving:
                thread.join(timeout=30)

        assert survivor.refreshed == [51]
        assert _status(db, season)["state"] == "finished"
        assert 'http-equiv="refresh"' not in _page(c, season)

    def test_creating_a_season_answers_while_its_refresh_is_still_running(
        self, client, survivor, outliving
    ):
        c, db = client
        from app.models import Season

        _login(c)
        survivor.hold = threading.Event()

        try:
            resp = c.post("/admin/seasons", data={"number": "61"})

            assert resp.status_code == 302
            assert survivor.entered.wait(10)
            season = Season.query.filter_by(number=61).one()
            assert _status(db, season)["state"] == "running"
        finally:
            survivor.hold.set()
            for thread in outliving:
                thread.join(timeout=30)

        assert survivor.refreshed == [61]
        assert _status(db, season)["state"] == "finished"


class TestWhatTheAdminSees:
    def test_the_flash_says_the_refresh_started(self, client, survivor):
        c, db = client
        season = _season(db)
        _login(c)

        c.post(f"/admin/season/{season.id}/refresh")

        assert any(
            category == "success" and "started" in message.lower()
            for category, message in _flashes(c)
        )

    def test_a_finished_refresh_reports_what_it_did(self, client, survivor):
        c, db = client
        season = _season(db)
        _login(c)

        c.post(f"/admin/season/{season.id}/refresh")

        page = _page(c, season)
        assert "Refreshed 18 survivors" in page
        assert COMMIT[:7] in page
        assert "Data warning: Day 3 is missing" in page
        assert "Last refresh from survivoR finished" in page
        assert survivor.exports == 2

    def test_a_failed_download_is_reported(self, client, survivor):
        c, db = client
        season = _season(db)
        _login(c)
        survivor.download_error = requests.ConnectionError("upstream is down")

        c.post(f"/admin/season/{season.id}/refresh")

        status = _status(db, season)
        assert status["state"] == "finished"
        assert ("error", "Refresh failed: upstream is down") in status["messages"]
        assert "Refresh failed: upstream is down" in _page(c, season)
        assert survivor.refreshed == []

    def test_a_step_that_raises_unexpectedly_still_ends_the_refresh(
        self, client, survivor, monkeypatch
    ):
        """No step may leave the season showing a refresh that never ends."""
        c, db = client
        season = _season(db)
        _login(c)

        def broken(_season):
            raise RuntimeError("lookup exploded")

        monkeypatch.setattr("app.schedule.sync_episode2_time", broken)

        c.post(f"/admin/season/{season.id}/refresh")

        status = _status(db, season)
        assert status["state"] == "finished"
        assert any("lookup exploded" in m for _, m in status["messages"])

    def test_a_new_season_reports_its_castaways_and_headshots(self, client, survivor):
        c, db = client
        from app.models import Season

        _login(c)

        c.post("/admin/seasons", data={"number": "61", "fetch_images": "on"})

        season = Season.query.filter_by(number=61).one()
        page = _page(c, season)
        assert "Loaded 18 survivors" in page
        assert "Found 12 headshot images" in page
        assert survivor.images == [61]

    def test_a_new_season_without_headshots_fetches_none(self, client, survivor):
        c, _db = client
        _login(c)

        c.post("/admin/seasons", data={"number": "61"})

        assert survivor.refreshed == [61]
        assert survivor.images == []

    def test_a_season_never_refreshed_here_shows_no_refresh(self, client, survivor):
        c, db = client
        season = _season(db)
        _login(c)

        page = _page(c, season)

        assert "Refreshing from survivoR" not in page
        assert "Last refresh from survivoR" not in page
        assert 'http-equiv="refresh"' not in page


class TestOneRefreshAtATime:
    @pytest.fixture()
    def lost(self, monkeypatch):
        """Refreshes are claimed but their thread never runs, as when the
        worker running one is restarted mid-refresh, until `lost.end()`."""
        from app import refresh_jobs

        normal = refresh_jobs._start_thread
        monkeypatch.setattr(refresh_jobs, "_start_thread", lambda _target: None)
        return SimpleNamespace(
            end=lambda: monkeypatch.setattr(refresh_jobs, "_start_thread", normal)
        )

    @pytest.fixture()
    def clock(self, monkeypatch):
        """The refresh clock; set `clock.now` to move it."""
        from app import refresh_jobs

        clock = SimpleNamespace(now=refresh_jobs._now())
        monkeypatch.setattr(refresh_jobs, "_now", lambda: clock.now)
        return clock

    def test_a_second_click_while_one_runs_starts_nothing(
        self, client, survivor, outliving
    ):
        c, db = client
        season = _season(db)
        _login(c)
        survivor.hold = threading.Event()

        try:
            c.post(f"/admin/season/{season.id}/refresh")
            assert survivor.entered.wait(10)
            c.post(f"/admin/season/{season.id}/refresh")
            flashes = _flashes(c)
        finally:
            survivor.hold.set()
            for thread in outliving:
                thread.join(timeout=30)

        assert survivor.refreshed == [51]
        assert len(outliving) == 1
        assert any(
            category == "error" and "already running" in message
            for category, message in flashes
        )

    def test_a_refresh_still_within_its_time_is_not_taken_for_lost(
        self, client, survivor, clock, lost
    ):
        from app import refresh_jobs

        c, db = client
        season = _season(db)
        _login(c)
        c.post(f"/admin/season/{season.id}/refresh")
        lost.end()
        clock.now += refresh_jobs.LOST_AFTER - timedelta(minutes=1)

        c.post(f"/admin/season/{season.id}/refresh")

        assert survivor.refreshed == []
        assert _status(db, season)["state"] == "running"

    def test_a_refresh_that_never_finished_does_not_block_the_next(
        self, client, survivor, clock, lost
    ):
        from app import refresh_jobs

        c, db = client
        season = _season(db)
        _login(c)
        c.post(f"/admin/season/{season.id}/refresh")
        lost.end()
        clock.now += refresh_jobs.LOST_AFTER + timedelta(minutes=1)

        assert _status(db, season)["state"] == "lost"
        assert "never finished" in _page(c, season)

        c.post(f"/admin/season/{season.id}/refresh")

        assert survivor.refreshed == [51]
        assert _status(db, season)["state"] == "finished"

    def test_the_claim_is_refused_when_another_worker_got_there_first(
        self, client, survivor
    ):
        """The other worker's click lands between this one reading the season
        and claiming it; only one of them may start a refresh."""
        import sqlalchemy

        from app import refresh_jobs

        c, db = client
        season = _season(db)
        _login(c)
        assert season.refresh_status is None
        with db.engine.begin() as conn:
            conn.execute(
                sqlalchemy.text("UPDATE season SET refresh_status = :s WHERE id = :i"),
                {"s": '{"state": "running", "claimed": "elsewhere"}', "i": season.id},
            )

        assert refresh_jobs.start(season, kind="refresh") is False
        assert survivor.refreshed == []
