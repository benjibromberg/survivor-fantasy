"""When Episode 2, and the first episode after the merge, start.

Wildcard picks lock 15 minutes before Episode 2 (see app/wildcards.py) and
replacement picks 15 minutes before the first episode after the merge (see
app/replacements.py). Each start time comes from TVmaze, which lists CBS air times with their UTC offset
before the episodes air. When TVmaze cannot answer, it is estimated from the
survivoR dataset: one week after the premiere, at 8 PM Eastern. Every
new-era season so far (41 to 50) aired Episode 2 exactly seven days after
Episode 1.

TVmaze data is licensed CC BY-SA; the site footer credits it.
"""

import logging
from datetime import UTC, datetime, time, timedelta

import pandas as pd
import requests
from sqlalchemy.exc import SQLAlchemyError

from . import wildcards
from .models import db

logger = logging.getLogger(__name__)

TVMAZE_SHOW_ID = 114  # Survivor (CBS, US)
TVMAZE_EPISODE_URL = f"https://api.tvmaze.com/shows/{TVMAZE_SHOW_ID}/episodebynumber"
TVMAZE_TIMEOUT = (5, 10)  # connect, read (seconds)
# Used only by the survivoR estimate, which has air dates but no times
USUAL_AIR_TIME = time(20, 0)


def _airstamp_to_utc(value):
    """TVmaze `airstamp` (ISO date-time with an offset) as naive UTC, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None  # without an offset the instant is ambiguous
    return moment.astimezone(UTC).replace(tzinfo=None)


def tvmaze_episode_start(season_number, episode=2):
    """Start of a Survivor episode from TVmaze, as naive UTC, or None.

    Returns None, after logging why, for anything short of a clear answer:
    a network error, an unknown episode, or a response that does not describe
    the episode asked for.
    """
    try:
        resp = requests.get(
            TVMAZE_EPISODE_URL,
            params={"season": season_number, "number": episode},
            timeout=TVMAZE_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("TVmaze lookup failed for S%dE%d: %s", season_number, episode, e)
        return None
    if resp.status_code == 404:
        logger.info("TVmaze has no listing for S%dE%d yet", season_number, episode)
        return None
    try:
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("TVmaze lookup failed for S%dE%d: %s", season_number, episode, e)
        return None
    if (
        not isinstance(data, dict)
        or data.get("season") != season_number
        or data.get("number") != episode
    ):
        logger.warning(
            "TVmaze answered S%dE%d with another episode", season_number, episode
        )
        return None
    start = _airstamp_to_utc(data.get("airstamp"))
    if start is None:
        logger.warning(
            "TVmaze S%dE%d has no usable airstamp: %r",
            season_number,
            episode,
            data.get("airstamp"),
        )
    return start


def week_after_episode(episodes, season_number, episode):
    """Estimate the episode after `episode`, as naive UTC, or None.

    One week after `episode`'s air date in the survivoR Episodes sheet, at
    8 PM Eastern.
    """
    if episodes.empty:
        return None
    previous = episodes[
        (episodes["version"] == "US")
        & (episodes["season"] == season_number)
        & (episodes["episode"] == episode)
    ]
    if previous.empty or pd.isna(previous.iloc[0]["episode_date"]):
        return None
    day = pd.Timestamp(previous.iloc[0]["episode_date"]).date() + timedelta(days=7)
    local = datetime.combine(day, USUAL_AIR_TIME, tzinfo=wildcards.LEAGUE_TZ)
    return local.astimezone(UTC).replace(tzinfo=None)


def episode2_from_premiere(episodes, season_number):
    """Estimate Episode 2 from the survivoR Episodes sheet, as naive UTC, or None.

    One week after Episode 1's air date, at 8 PM Eastern.
    """
    return week_after_episode(episodes, season_number, 1)


def _read_episodes():
    """The survivoR Episodes sheet, or None if it cannot be read.

    The sheet is read by data.read_episodes(), which also feeds the stored
    episode titles, so the air dates here and the dates shown on a castaway's
    sheet cannot come from different reads.
    """
    from . import data

    try:
        return data.read_episodes()
    except (OSError, ValueError, KeyError) as e:
        logger.warning("survivoR Episodes sheet unavailable: %s", e)
        return None


def survivor_episode2_start(season_number):
    """Episode 2 estimated from the local survivoR dataset, or None."""
    episodes = _read_episodes()
    if episodes is None:
        return None
    return episode2_from_premiere(episodes, season_number)


def episode2_start(season_number):
    """Return (Episode 2 start as naive UTC, source name), or (None, None)."""
    start = tvmaze_episode_start(season_number)
    if start is not None:
        return start, "TVmaze"
    start = survivor_episode2_start(season_number)
    if start is not None:
        return start, "survivoR"
    return None, None


def _lock_has_passed(starts_at):
    lock = starts_at.replace(tzinfo=UTC) - wildcards.LOCK_LEAD
    return wildcards.now_utc() >= lock


def sync_episode2_time(season):
    """Fill in or update a season's Episode 2 time. Returns True if it changed.

    Only the active season with self-service switched on is looked up, and
    a time the admin typed in is never overwritten. The time only moves while
    the wildcard window is open: once a lock has passed the picks are final,
    and a season whose Episode 2 has already started is not switched on
    after the fact (that would hide wildcards the admin already entered).
    Never raises: a failed lookup is logged and leaves the season as it was.
    """
    if (
        not season.is_active
        or season.episode2_manual
        or season.wildcard_self_service is False
    ):
        return False
    if season.episode2_starts_at is not None and _lock_has_passed(
        season.episode2_starts_at
    ):
        return False

    try:
        start, source = episode2_start(season.number)
    except Exception:
        logger.exception("Episode 2 lookup failed for season %d", season.number)
        return False
    if start is None or start == season.episode2_starts_at or _lock_has_passed(start):
        return False

    try:
        season.episode2_starts_at = start
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Saving the Episode 2 time failed for season %d", season.number
        )
        return False
    logger.info(
        "Season %d: Episode 2 starts %s UTC (from %s)", season.number, start, source
    )
    return True


# ── The first episode after the merge (replacement lock) ──────────────────


def episode_after_merge_start(season_number, merge_episode):
    """Return (start of the episode after the merge as naive UTC, source), or (None, None)."""
    start = tvmaze_episode_start(season_number, merge_episode + 1)
    if start is not None:
        return start, "TVmaze"
    episodes = _read_episodes()
    if episodes is not None:
        start = week_after_episode(episodes, season_number, merge_episode)
        if start is not None:
            return start, "survivoR"
    return None, None


def sync_replacement_time(season):
    """Fill in or update when the first episode after the merge starts.

    Returns True if it changed. Same rules as sync_episode2_time: only the
    active season is looked up, and only once its merge episode is known; a
    time the admin typed in is never overwritten; the time only moves while
    the replacement window is open, and a season whose episode has already
    locked is not switched on after the fact (that would hide replacements
    the admin already entered). Never raises.
    """
    if (
        not season.is_active
        or season.replacement_manual
        or season.merge_episode_num is None
    ):
        return False
    if season.replacement_starts_at is not None and _lock_has_passed(
        season.replacement_starts_at
    ):
        return False

    try:
        start, source = episode_after_merge_start(
            season.number, season.merge_episode_num
        )
    except Exception:
        logger.exception(
            "Post-merge episode lookup failed for season %d", season.number
        )
        return False
    if (
        start is None
        or start == season.replacement_starts_at
        or _lock_has_passed(start)
    ):
        return False

    try:
        season.replacement_starts_at = start
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Saving the replacement lock time failed for season %d", season.number
        )
        return False
    logger.info(
        "Season %d: Episode %d (after the merge) starts %s UTC (from %s)",
        season.number,
        season.merge_episode_num + 1,
        start,
        source,
    )
    return True
