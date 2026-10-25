"""Admin refreshes from survivoR, run beside the request instead of inside it.

The admin's Refresh button and creating a season both download survivoR and
refresh the season. Inside the request that took 27.9 s on the production
host, against gunicorn's 30 s worker timeout for its sync workers; past it
the worker is killed, the admin gets an error page and the season is not
refreshed. Retries (app/data.py) and the headshot fetch on creation make the
time depend on other people's servers, so no fixed timeout is safe, and
Cloudflare's proxy in front answers 524 once the app has been silent for its
Proxy Read Timeout (125 s by default) whatever gunicorn allows. So the
request only claims the refresh and starts a thread for it, then answers at
once; the worker's main loop goes on serving and reporting to gunicorn while
the thread works.

The refresh's state is JSON on the season row (Season.refresh_status), so
either gunicorn worker can show it on the season's admin page, and a second
click landing on the other worker finds the refresh already running. A
refresh whose worker died mid-way (a deploy, a crash) stays "running" in the
row; after LOST_AFTER it is shown as lost and a new one may start.

The 08:00 refresh (app/scheduler.py) already runs in the scheduler's thread
and does not come through here.
"""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import update

from . import schedule
from .data import (
    download_survivor_data,
    export_all_picks,
    generate_season_images,
    refresh_season,
)
from .models import Season, db

logger = logging.getLogger(__name__)

# A refresh takes about 30 s on the production host, and retries add seconds.
# Only an upstream that hangs on request after request could keep one running
# this long, and then a second may start beside it; a refresh lost with its
# worker holds the button no longer than this.
LOST_AFTER = timedelta(minutes=15)


def _now():
    return datetime.now(UTC)


def _read(season):
    """The season's refresh_status as a dict, or None."""
    try:
        value = json.loads(season.refresh_status or "null")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _seconds_since(stamp, now):
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return max(0, int((now - then).total_seconds()))


def _span(seconds):
    """A duration as the admin page words it: 28 s, 4 min, 3 h, 2 days."""
    if seconds is None:
        return "an unknown time"
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 60 * 60:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


def status(season):
    """What the season's admin page shows about its last refresh, or None.

    state is "running", "lost" (running for longer than LOST_AFTER, so its
    worker is gone) or "finished". Also: step (what a running refresh is
    doing), elapsed (seconds since it started), took (seconds a finished
    one ran), ago (seconds since it finished), each also worded by _span as
    elapsed_text, took_text and ago_text, and messages ((category, text)
    pairs, as a flash would carry them).
    """
    raw = _read(season)
    if raw is None:
        return None
    now = _now()
    elapsed = _seconds_since(raw.get("started"), now)
    state = raw.get("state")
    if state == "running" and (elapsed is None or elapsed > LOST_AFTER.total_seconds()):
        state = "lost"
    finished_ago = _seconds_since(raw.get("finished"), now)
    took = None
    if elapsed is not None and finished_ago is not None:
        took = elapsed - finished_ago
    return {
        "state": state,
        "step": raw.get("step"),
        "elapsed": elapsed,
        "took": took,
        "ago": finished_ago,
        "elapsed_text": _span(elapsed),
        "took_text": _span(took),
        "ago_text": _span(finished_ago),
        "messages": [tuple(m) for m in raw.get("messages", [])],
    }


def start(season, kind, fetch_images=False):
    """Claim a refresh of season and start it in a thread. True if started.

    kind is "refresh" (the admin's button) or "create" (a season just
    created; fetch_images also mirrors its headshots). Refuses while a
    refresh of the season is running and not yet lost. The claim is a
    conditional UPDATE against the status this request read, so of two
    clicks landing on both workers at once only one starts a refresh.
    """
    current = status(season)
    if current is not None and current["state"] == "running":
        return False
    season_id = season.id
    seen = season.refresh_status
    claim = json.dumps(
        {
            "state": "running",
            "kind": kind,
            "step": "starting",
            "started": _now().isoformat(),
            "messages": [],
        }
    )
    unchanged = (
        Season.refresh_status.is_(None)
        if seen is None
        else Season.refresh_status == seen
    )
    result = db.session.execute(
        update(Season)
        .where(Season.id == season_id, unchanged)
        .values(refresh_status=claim)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    if result.rowcount != 1:
        return False
    app = current_app._get_current_object()
    _start_thread(lambda: _run(app, season_id, kind, fetch_images))
    return True


def _start_thread(target):
    """Run target in a daemon thread, so a worker shutting down need not
    wait for it. tests/conftest.py joins it before each request returns."""
    thread = threading.Thread(target=target, name="survivor-refresh", daemon=True)
    thread.start()
    return thread


def _run(app, season_id, kind, fetch_images):
    """The refresh itself, in its own app context and database session."""
    with app.app_context():
        season = db.session.get(Season, season_id)
        if season is None:
            return
        record = _Record(season)
        try:
            if kind == "create":
                _create(season, record, fetch_images)
            else:
                _refresh(season, record)
        except Exception as e:  # anything a step did not expect
            db.session.rollback()
            logger.exception("Refresh of season %d failed", season.number)
            record.messages.append(("error", f"Refresh stopped: {e}"))
        finally:
            record.finish()


class _Record:
    """Writes a running refresh's progress to its season row."""

    def __init__(self, season):
        self.season = season
        self.raw = _read(season) or {}
        self.messages = []

    def step(self, text):
        self.raw["step"] = text
        self._save()

    def finish(self):
        self.raw.update(
            state="finished",
            step=None,
            finished=_now().isoformat(),
            messages=self.messages,
        )
        self._save()

    def _save(self):
        try:
            self.season.refresh_status = json.dumps(self.raw)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "Recording the refresh of season %d failed", self.season.number
            )


def _download_and_refresh(season, record):
    """Download survivoR and refresh the season; (count, warnings) or None."""
    try:
        record.step("downloading survivoR")
        commit = download_survivor_data()
        record.step("updating the castaways")
        count, day_warnings = refresh_season(season)
    except Exception as e:
        db.session.rollback()
        record.messages.append(("error", f"Refresh failed: {e}"))
        return None
    if commit:
        record.messages.append(("success", f"Data from survivoR commit {commit[:7]}."))
    return count, day_warnings


def _refresh(season, record):
    """What the admin's Refresh button did inside the request."""
    _export_picks(record, "before")
    result = _download_and_refresh(season, record)
    if result is not None:
        count, day_warnings = result
        record.messages.insert(
            0, ("success", f"Refreshed {count} survivors from survivoR dataset!")
        )
        record.messages += [("error", f"Data warning: {w}") for w in day_warnings]
    record.step("looking up when Episode 2 starts")
    schedule.sync_episode2_time(season)
    _export_picks(record, "after")


def _create(season, record, fetch_images):
    """What creating a season did inside the request, after creating it."""
    result = _download_and_refresh(season, record)
    if result is None:
        return
    count, day_warnings = result
    record.messages.insert(0, ("success", f"Loaded {count} survivors from survivoR."))
    record.messages += [("error", f"Data warning: {w}") for w in day_warnings]
    if fetch_images:
        record.step("fetching headshots")
        matched = generate_season_images(season)
        record.messages.append(("success", f"Found {matched} headshot images."))


def _export_picks(record, when):
    """Export picks around a refresh, so a refresh that changes data cannot
    lose them. A failure is logged, as it was inside the request."""
    record.step(f"exporting picks ({when} the refresh)")
    try:
        export_all_picks()
    except Exception as e:
        logger.warning("Pick export %s refresh failed: %s", when, e)
