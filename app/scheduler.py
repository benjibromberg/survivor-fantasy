import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def refresh_active_seasons(app):
    """Download latest survivoR data and refresh all active seasons.

    Also looks up when Episode 2 starts (the wildcard lock), first and
    independently, so a survivoR download failure does not hold it up.
    """
    with app.app_context():
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

        try:
            download_survivor_data()
        except Exception as e:
            logger.error(f"Failed to download survivoR data: {e}")
            return

        # Export picks before refresh (preserve current state)
        try:
            export_all_picks()
        except Exception as e:
            logger.warning(f"Pick export before refresh failed: {e}")

        active = Season.query.filter_by(is_active=True).all()
        for season in active:
            held = hand_entered_survivor_count(season)
            if held:
                logger.warning(
                    f"Auto-refresh skipped season {season.number}: {held} "
                    "hand-entered castaway(s) would be duplicated rather than "
                    "matched. This runs unattended, so it declines rather than "
                    "corrupting the cast."
                )
                continue
            try:
                count, day_warnings = refresh_season(season)
                logger.info(
                    f"Auto-refresh: updated {count} survivors for season {season.number}"
                )
                for w in day_warnings:
                    logger.warning(f"Season {season.number} day data: {w}")
            except Exception as e:
                logger.error(f"Auto-refresh failed for season {season.number}: {e}")

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
