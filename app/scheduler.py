import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def refresh_active_seasons(app):
    """Download latest survivoR data and refresh all active seasons."""
    with app.app_context():
        from .models import Season
        from .data import download_survivor_data, refresh_season

        try:
            download_survivor_data()
        except Exception as e:
            logger.error(f'Failed to download survivoR data: {e}')
            return

        active = Season.query.filter_by(is_active=True).all()
        for season in active:
            try:
                count = refresh_season(season)
                logger.info(f'Auto-refresh: updated {count} survivors for season {season.number}')
            except Exception as e:
                logger.error(f'Auto-refresh failed for season {season.number}: {e}')


def init_scheduler(app):
    """Start the background scheduler. Refreshes data daily at 8am EST.

    The survivoR dataset updates a couple days after Wednesday night episodes
    at unpredictable times, so a daily check is more reliable than weekly.
    """
    if scheduler.running:
        return

    scheduler.add_job(
        refresh_active_seasons,
        trigger=CronTrigger(hour=8, minute=0, timezone='America/New_York'),
        args=[app],
        id='daily_refresh',
        replace_existing=True,
    )
    scheduler.start()
    logger.info('Scheduler started: auto-refresh daily at 8am EST')
