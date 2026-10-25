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
click landing on the other worker finds the refresh already running. Only
the refresh holding the claim writes it (Claim): each write is conditional
on the row still holding exactly what that refresh last wrote. A refresh
reports in at every step, each table and each headshot included; one that
has not for LOST_AFTER (its worker died in a deploy or a crash) is shown as
lost, and the next refresh may claim the season. If the first was only slow,
its next report finds the claim gone and it stops.

The 08:00 refresh (app/scheduler.py) already runs in the scheduler's thread,
so it does not start one here, but it takes the same claim on each season.
"""

import json
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import update

from . import schedule
from .data import (
    SURVIVOR_BRANCH,
    download_survivor_data,
    export_all_picks,
    generate_season_images,
    refresh_season,
)
from .models import Season, db

logger = logging.getLogger(__name__)

# How long a running refresh may go without reporting in before it counts as
# lost. It reports after every table and every headshot; the longest gap is
# one request through all its retries (app/data.py: three attempts, each up
# to 10 s to connect and 60 s between bytes, with waits of at most 30 s), a
# few minutes at worst.
LOST_AFTER = timedelta(minutes=10)


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

    state is "running", "lost" (nothing heard from it for LOST_AFTER, so its
    worker is gone) or "finished". Also: kind ("refresh", "create" or
    "daily"), step (what a running refresh is doing), elapsed (seconds since
    it started), took (seconds a finished one ran), ago (seconds since it
    finished), each also worded by _span as elapsed_text, took_text and
    ago_text, and messages ((category, text) pairs, as a flash would carry
    them).
    """
    raw = _read(season)
    if raw is None:
        return None
    now = _now()
    elapsed = _seconds_since(raw.get("started"), now)
    quiet = _seconds_since(raw.get("beat") or raw.get("started"), now)
    state = raw.get("state")
    if state == "running" and (quiet is None or quiet > LOST_AFTER.total_seconds()):
        state = "lost"
    finished_ago = _seconds_since(raw.get("finished"), now)
    took = None
    if elapsed is not None and finished_ago is not None:
        took = elapsed - finished_ago
    return {
        "state": state,
        "kind": raw.get("kind"),
        "step": raw.get("step"),
        "elapsed": elapsed,
        "took": took,
        "ago": finished_ago,
        "elapsed_text": _span(elapsed),
        "took_text": _span(took),
        "ago_text": _span(finished_ago),
        "messages": [tuple(m) for m in raw.get("messages", [])],
    }


class ClaimLost(Exception):
    """Another refresh claimed the season after this one went quiet."""


class Claim:
    """One refresh's hold on its season's refresh_status.

    Plain data, no database objects, so the request can claim and the thread
    can write. Every write is an UPDATE conditional on the row still holding
    the exact text this claim last wrote, which also carries a random claim
    id; a refresh that has been superseded therefore changes nothing.
    """

    def __init__(self, season_id, season_number, raw, written):
        self.season_id = season_id
        self.season_number = season_number
        self.raw = raw
        self.written = written
        self.messages = []
        self.held = True

    def beat(self, step=None):
        """Report in, with what the refresh is doing now. False if the claim
        is no longer this refresh's."""
        if step is not None:
            self.raw["step"] = step
        self.raw["beat"] = _now().isoformat()
        return self._save()

    def checkpoint(self, step):
        """beat(step), raising ClaimLost if the claim is gone."""
        if not self.beat(step):
            raise ClaimLost(f"season {self.season_number}")

    def finish(self):
        """Record the refresh as finished, with its messages."""
        now = _now().isoformat()
        self.raw.update(
            state="finished", step=None, beat=now, finished=now, messages=self.messages
        )
        self._save()

    def _save(self):
        if not self.held:
            return False
        text = json.dumps(self.raw)
        try:
            result = db.session.execute(
                update(Season)
                .where(
                    Season.id == self.season_id, Season.refresh_status == self.written
                )
                .values(refresh_status=text)
                .execution_options(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "Recording the refresh of season %d failed", self.season_number
            )
            return True  # unknown, so carry on; the next write tries again
        if result.rowcount != 1:
            self.held = False
            logger.warning(
                "Season %d's refresh was claimed by another after this one went "
                "quiet; this one stops",
                self.season_number,
            )
            return False
        self.written = text
        return True


def claim(season, kind):
    """Claim the season's refresh. A Claim, or None if another refresh holds it.

    A refresh still reporting in holds it; a lost one does not. The claim is
    a conditional UPDATE against the status this caller read, so of two
    callers claiming at once (two clicks landing on both workers) only one
    gets it.
    """
    current = status(season)
    if current is not None and current["state"] == "running":
        return None
    season_id, season_number, seen = season.id, season.number, season.refresh_status
    now = _now().isoformat()
    raw = {
        "state": "running",
        "kind": kind,
        "claim": uuid.uuid4().hex,
        "step": "starting",
        "started": now,
        "beat": now,
        "messages": [],
    }
    written = json.dumps(raw)
    unchanged = (
        Season.refresh_status.is_(None)
        if seen is None
        else Season.refresh_status == seen
    )
    result = db.session.execute(
        update(Season)
        .where(Season.id == season_id, unchanged)
        .values(refresh_status=written)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    if result.rowcount != 1:
        return None
    return Claim(season_id, season_number, raw, written)


def start(season, kind, fetch_images=False):
    """Claim a refresh of season and start it in a thread. True if started.

    kind is "refresh" (the admin's button) or "create" (a season just
    created; fetch_images also mirrors its headshots).
    """
    held = claim(season, kind)
    if held is None:
        return False
    app = current_app._get_current_object()
    _start_thread(lambda: _run(app, held, kind, fetch_images))
    return True


def _start_thread(target):
    """Run target in a daemon thread, so a worker shutting down need not
    wait for it. tests/conftest.py joins it before each request returns."""
    thread = threading.Thread(target=target, name="survivor-refresh", daemon=True)
    thread.start()
    return thread


def _run(app, held, kind, fetch_images):
    """The refresh itself, in its own app context and database session."""
    with app.app_context():
        season = db.session.get(Season, held.season_id)
        if season is None:
            return
        try:
            if kind == "create":
                _create(season, held, fetch_images)
            else:
                _refresh(season, held)
        except ClaimLost:
            db.session.rollback()
        except Exception as e:  # anything a step did not expect
            db.session.rollback()
            logger.exception("Refresh of season %d failed", season.number)
            held.messages.append(("error", f"Refresh stopped: {e}"))
        finally:
            held.finish()


def _download_and_refresh(season, held):
    """Download survivoR and refresh the season; (count, warnings) or None.

    A failure is recorded in held's messages; a lost claim is raised.
    """
    try:
        held.checkpoint("downloading survivoR")
        commit = download_survivor_data(progress=held.checkpoint)
        held.checkpoint("updating the castaways")
        count, day_warnings = refresh_season(season)
    except ClaimLost:
        raise
    except Exception as e:
        db.session.rollback()
        held.messages.append(("error", f"Refresh failed: {e}"))
        return None
    note = commit_message(commit)
    if note:
        held.messages.append(note)
    return count, day_warnings


def commit_message(commit):
    """What a refresh reports about the survivoR commit its data came from:
    a (category, text) pair, or None when the download named none."""
    if commit == SURVIVOR_BRANCH:
        return (
            "warning",
            f"Data from survivoR {SURVIVOR_BRANCH}, not pinned to one commit: "
            "GitHub's commit lookup failed (see the log).",
        )
    if commit:
        return ("success", f"Data from survivoR commit {commit[:7]}.")
    return None


def _refresh(season, held):
    """What the admin's Refresh button did inside the request."""
    _export_picks(held, "before")
    result = _download_and_refresh(season, held)
    if result is not None:
        count, day_warnings = result
        held.messages.insert(
            0, ("success", f"Refreshed {count} survivors from survivoR dataset!")
        )
        held.messages += [("error", f"Data warning: {w}") for w in day_warnings]
    held.checkpoint("looking up when Episode 2 starts")
    schedule.sync_episode2_time(season)
    _export_picks(held, "after")


def _create(season, held, fetch_images):
    """What creating a season did inside the request, after creating it."""
    result = _download_and_refresh(season, held)
    if result is None:
        return
    count, day_warnings = result
    held.messages.insert(0, ("success", f"Loaded {count} survivors from survivoR."))
    held.messages += [("error", f"Data warning: {w}") for w in day_warnings]
    if fetch_images:
        held.checkpoint("fetching headshots")
        matched = generate_season_images(season, progress=held.checkpoint)
        held.messages.append(("success", f"Found {matched} headshot images."))


def _export_picks(held, when):
    """Export picks around a refresh, so a refresh that changes data cannot
    lose them. A failure is logged, as it was inside the request."""
    held.checkpoint(f"exporting picks ({when} the refresh)")
    try:
        export_all_picks()
    except Exception as e:
        logger.warning("Pick export %s refresh failed: %s", when, e)
