import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def refresh_active_seasons(app):
    """Download latest survivoR data and refresh all active seasons.

    Also looks up when Episode 2 starts (the wildcard lock), first and
    independently, so a survivoR download failure does not hold it up.

    Each season is refreshed under the same claim as an admin refresh
    (app/refresh_jobs.py), so this job, which runs in both gunicorn workers,
    never runs refresh_season beside an admin refresh or beside the other
    worker's job; a cast first published during such an overlap could be
    inserted twice. A season whose refresh is held elsewhere is skipped, and
    when every season is, nothing is downloaded. The season's admin page
    shows the run like any other refresh.
    """
    with app.app_context():
        from . import db, refresh_jobs
        from .data import (
            download_survivor_data,
            export_all_picks,
            hand_entered_survivor_count,
            refresh_season,
        )
        from .models import Season
        from .schedule import sync_episode2_time

        for season in Season.query.filter_by(is_active=True).all():
            sync_episode2_time(season)

        claimed = []
        for season in Season.query.filter_by(is_active=True).all():
            held = hand_entered_survivor_count(season)
            if held:
                logger.warning(
                    f"Auto-refresh skipped season {season.number}: {held} "
                    "hand-entered castaway(s) would be duplicated rather than "
                    "matched. This runs unattended, so it declines rather than "
                    "corrupting the cast."
                )
                continue
            claim = refresh_jobs.claim(season, "daily")
            if claim is None:
                logger.warning(
                    f"Auto-refresh skipped season {season.number}: another "
                    "refresh of it is running"
                )
                continue
            claimed.append((season, claim))
        if not claimed:
            return

        def report(step):
            for _season, claim in claimed:
                claim.beat(step)

        try:
            commit = download_survivor_data(progress=report)
        except Exception as e:
            logger.error(f"Failed to download survivoR data: {e}")
            # Recording the failure commits the session; save nothing else.
            db.session.rollback()
            for _season, claim in claimed:
                claim.messages.append(("error", f"Refresh failed: {e}"))
                claim.finish()
            return

        # Export picks before refresh (preserve current state)
        try:
            export_all_picks()
        except Exception as e:
            logger.warning(f"Pick export before refresh failed: {e}")

        for season, claim in claimed:
            try:
                if not claim.beat("updating the castaways"):
                    continue  # claimed by another refresh while this one was quiet
                count, day_warnings = refresh_season(season)
                logger.info(
                    f"Auto-refresh: updated {count} survivors for season {season.number}"
                )
                claim.messages.append(
                    ("success", f"Refreshed {count} survivors from survivoR dataset!")
                )
                note = refresh_jobs.commit_message(commit)
                if note:
                    claim.messages.append(note)
                for w in day_warnings:
                    logger.warning(f"Season {season.number} day data: {w}")
                    claim.messages.append(("error", f"Data warning: {w}"))
            except Exception as e:
                # refresh_season can fail after changing the season; recording
                # the failure commits the session, so undo those changes first.
                db.session.rollback()
                logger.error(f"Auto-refresh failed for season {season.number}: {e}")
                claim.messages.append(("error", f"Refresh failed: {e}"))
            finally:
                claim.finish()

        # Export picks after refresh (capture updated names/stats)
        try:
            export_all_picks()
        except Exception as e:
            logger.warning(f"Pick export after refresh failed: {e}")


def init_scheduler(app):
    """Start the background scheduler. Refreshes data daily at 8am EST.

    The survivoR dataset updates a couple days after Wednesday night episodes
    at unpredictable times, so a daily check is more reliable than weekly.
    """
    if scheduler.running:
        return

    scheduler.add_job(
        refresh_active_seasons,
        trigger=CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        args=[app],
        id="daily_refresh",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started: auto-refresh daily at 8am EST")
