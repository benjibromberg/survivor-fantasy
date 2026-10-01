"""Player wildcard self-service: the edit window, the lock, and the reveal.

A season opts in when the admin enters its Episode 2 start time. From then on:

- a player with draft picks sets their own wildcard and may change it freely;
- 15 minutes before Episode 2 picks lock for everyone: no player can set or
  change a wildcard after that, and the admin enters any that are missing;
- wildcards stay hidden, and score nothing on the public pages, until every
  player has picked. Each player sees their own on My Team and the admin sees
  all of them on the picks page.

Seasons without an Episode 2 time keep the old behaviour: the admin enters
wildcards and they are always visible.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from .models import Pick, Survivor, User, db

logger = logging.getLogger(__name__)

LOCK_LEAD = timedelta(minutes=15)
# The league keeps Eastern time: air times are entered and shown in it
LEAGUE_TZ = ZoneInfo("America/New_York")


def now_utc():
    return datetime.now(UTC)


# ── Episode 2 time ────────────────────────────────────────────────────────


def is_self_service(season):
    return season.episode2_starts_at is not None


def lock_at(season):
    """Aware UTC moment the wildcard locks, or None if self-service is off."""
    if season.episode2_starts_at is None:
        return None
    return season.episode2_starts_at.replace(tzinfo=UTC) - LOCK_LEAD


def parse_league_time(value):
    """Parse a datetime-local form value in league time into naive UTC.

    Returns None for a blank value. Raises ValueError for anything else that
    is not a valid local date and time.
    """
    value = (value or "").strip()
    if not value:
        return None
    local = datetime.fromisoformat(value)
    if local.tzinfo is not None:
        raise ValueError("expected a local time without an offset")
    return local.replace(tzinfo=LEAGUE_TZ).astimezone(UTC).replace(tzinfo=None)


def to_form_value(naive_utc):
    """Naive UTC datetime as a datetime-local form value in league time."""
    if naive_utc is None:
        return ""
    local = naive_utc.replace(tzinfo=UTC).astimezone(LEAGUE_TZ)
    return local.strftime("%Y-%m-%dT%H:%M")


def format_league_time(aware):
    """Aware datetime as e.g. 'Wed Oct 7, 7:45 PM ET'."""
    local = aware.astimezone(LEAGUE_TZ)
    hour = local.hour % 12 or 12
    return f"{local:%a %b} {local.day}, {hour}:{local:%M %p} ET"


# ── Who has picked, and the reveal ────────────────────────────────────────


def _picker_ids(season):
    """Return (ids of players with draft picks, ids of users with a wildcard)."""
    rows = (
        db.session.query(Pick.user_id, Pick.pick_type)
        .filter(Pick.season_id == season.id, Pick.pick_type.in_(("draft", "wildcard")))
        .distinct()
        .all()
    )
    drafters = {uid for uid, ptype in rows if ptype == "draft"}
    wildcarders = {uid for uid, ptype in rows if ptype == "wildcard"}
    return drafters, wildcarders


def are_hidden(season):
    """True while wildcards must stay off the public pages for this season."""
    if not is_self_service(season):
        return False
    drafters, wildcarders = _picker_ids(season)
    return bool(drafters - wildcarders)


def scored_picks(picks, hidden):
    """Drop wildcards from `picks` when they are hidden (see are_hidden)."""
    if not hidden:
        return picks
    return [p for p in picks if p.pick_type != "wildcard"]


def progress(season):
    """Return (players who have picked, players who must pick, those still to pick).

    The last item is a list of User rows ordered by name.
    """
    drafters, wildcarders = _picker_ids(season)
    waiting_ids = drafters - wildcarders
    waiting = []
    if waiting_ids:
        waiting = (
            User.query.filter(User.id.in_(waiting_ids)).order_by(User.username).all()
        )
    return len(drafters & wildcarders), len(drafters), waiting


# ── A player's own wildcard ───────────────────────────────────────────────


def current_pick(season, user):
    """The user's wildcard Pick for the season, or None."""
    return Pick.query.filter_by(
        user_id=user.id, season_id=season.id, pick_type="wildcard"
    ).first()


def eligible_survivors(season, user):
    """Castaways this player may take as a wildcard.

    Anyone still in the game who is not already on the player's own team.
    A castaway on another player's team stays eligible: that is how the
    league has always played it (most past wildcards were someone else's
    draft pick), and wildcards are secret until the reveal, so refusing a
    clash with another wildcard would leak that pick.
    """
    own = db.select(Pick.survivor_id).where(
        Pick.season_id == season.id,
        Pick.user_id == user.id,
        Pick.pick_type != "wildcard",
    )
    return (
        Survivor.query.filter(
            Survivor.season_id == season.id,
            Survivor.voted_out_order == 0,
            Survivor.id.notin_(own),
        )
        .order_by(Survivor.name)
        .all()
    )


def edit_block_reason(season, user, existing=None):
    """Why this player cannot set or change their wildcard now, or None if they can.

    `existing` is the player's current wildcard Pick, if already loaded.
    """
    if not season.is_active:
        return "Wildcards can only be picked for the active season."
    if not is_self_service(season):
        return "Wildcard picks are not open for this season."
    has_draft = (
        Pick.query.filter_by(
            user_id=user.id, season_id=season.id, pick_type="draft"
        ).first()
        is not None
    )
    if not has_draft:
        return "You need draft picks in this season before picking a wildcard."
    if existing is None:
        existing = current_pick(season, user)
    if now_utc() >= lock_at(season):
        if existing is not None:
            return "Your wildcard is locked."
        return "Wildcard picks are locked. Ask the league admin to enter yours."
    return None


def set_pick(season, user, raw_survivor_id):
    """Set, change, or (with a blank id) remove the player's wildcard.

    Returns an error message for the player, or None on success. Commits.
    """
    existing = current_pick(season, user)
    blocked = edit_block_reason(season, user, existing)
    if blocked:
        return blocked

    raw = (raw_survivor_id or "").strip()
    if not raw:
        if existing is None:
            return None
        return _commit(lambda: db.session.delete(existing))

    # Looked up among this player's eligible castaways, so an id from another
    # season, an eliminated castaway, or one already on their team all miss
    eligible = {str(s.id): s for s in eligible_survivors(season, user)}
    survivor = eligible.get(raw)
    if survivor is None:
        return "That castaway is not available as a wildcard."

    def apply():
        if existing is not None:
            existing.survivor_id = survivor.id
        else:
            db.session.add(
                Pick(
                    user_id=user.id,
                    season_id=season.id,
                    survivor_id=survivor.id,
                    pick_type="wildcard",
                )
            )

    return _commit(apply)


def player_view(season, user):
    """What My Team shows a player about their wildcard, or None if self-service is off."""
    if not is_self_service(season):
        return None
    pick = current_pick(season, user)
    blocked = edit_block_reason(season, user, pick)
    picked, total, _waiting = progress(season)
    lock = lock_at(season)
    return {
        "pick": pick,
        "blocked": blocked,
        "locked": now_utc() >= lock,
        "options": [] if blocked else eligible_survivors(season, user),
        "lock_label": format_league_time(lock),
        "picked": picked,
        "total": total,
        "hidden": picked < total,
    }


def admin_view(season):
    """What the season admin page shows about the wildcard window."""
    view = {
        "form_value": to_form_value(season.episode2_starts_at),
        "enabled": is_self_service(season),
    }
    if view["enabled"]:
        picked, total, waiting = progress(season)
        view.update(
            lock_label=format_league_time(lock_at(season)),
            picked=picked,
            total=total,
            waiting=waiting,
        )
    return view


def _commit(change):
    try:
        change()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Saving a wildcard pick failed")
        return "Could not save your wildcard. Try again."
    return None
