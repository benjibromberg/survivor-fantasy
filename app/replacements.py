"""Player replacement self-service: who is eligible, the window, the lock, the reveal.

A player who lost a castaway in or before the merge episode may take one
Replacement. A lost draft pick earns a draft replacement (`pmr_d`); if only
their wildcard went out, it is a wildcard replacement (`pmr_w`). A player who
lost nobody by then is not eligible.

A season is in self-service once the start of the first episode after the
merge is known (looked up automatically, see app/schedule.py, or typed in by
the admin). From then on:

- an eligible player sets their own replacement on My Team and may change it
  freely; the kind of replacement follows from what they lost;
- 15 minutes before that episode picks lock for everyone, and the admin
  enters any that are missing;
- replacements stay hidden, and score nothing on the public pages, until
  every eligible player has picked. Each player sees their own on My Team
  and the admin sees all of them on the picks page.

Seasons without that time keep the old behaviour: the admin enters
replacements and they are always visible.
"""

import logging
from datetime import UTC

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from . import wildcards
from .models import Pick, Survivor, User, db

logger = logging.getLogger(__name__)

TYPES = ("pmr_w", "pmr_d")
KIND_LABELS = {"pmr_d": "draft replacement", "pmr_w": "wildcard replacement"}
# The league keeps one clock and one lock lead, both in app/wildcards.py


# ── Eligibility ───────────────────────────────────────────────────────────


def _lost_by_merge(survivor, merge_episode):
    """True if the castaway's game ended in or before the merge episode."""
    return bool(
        survivor.voted_out_order
        and survivor.elimination_episode is not None
        and survivor.elimination_episode <= merge_episode
    )


def entitlement(season, picks):
    """The replacement a player's picks earn: 'pmr_d', 'pmr_w', or None.

    `picks` is the player's Pick rows for the season. Nobody is eligible
    until the merge episode is known.
    """
    merge_episode = season.merge_episode_num
    if merge_episode is None:
        return None
    lost = {p.pick_type for p in picks if _lost_by_merge(p.survivor, merge_episode)}
    if "draft" in lost:
        return "pmr_d"
    if "wildcard" in lost:
        return "pmr_w"
    return None


def _entitled_and_picked(season):
    """Return ({user id: entitlement} for eligible players, ids with a replacement)."""
    by_user = {}
    picks = (
        Pick.query.options(joinedload(Pick.survivor))
        .filter_by(season_id=season.id)
        .all()
    )
    for pick in picks:
        by_user.setdefault(pick.user_id, []).append(pick)
    entitled = {}
    picked = set()
    for uid, picks in by_user.items():
        kind = entitlement(season, picks)
        if kind:
            entitled[uid] = kind
        if any(p.pick_type in TYPES for p in picks):
            picked.add(uid)
    return entitled, picked


def scoring_start(season):
    """When replacements start counting: (elimination count, baseline episode).

    A replacement is left off the scores until the season has had that many
    eliminations, and then scores only what happens after the baseline
    episode.

    A season in replacement self-service counts from the merge episode in
    Tribe Mapping: picks lock before the next episode, so that is exactly
    "from the moment you pick them", whenever survivoR publishes the jury
    size. Other seasons, every finished one included, keep the jury-based
    merge point, falling back to the merge episode only while the jury size
    is unknown. (None, None) when neither is known.
    """
    merge_elim = season.merge_threshold
    merge_episode = season.merge_episode_num
    if merge_elim is not None and not (
        is_self_service(season) and merge_episode is not None
    ):
        episode = next(
            (
                s.elimination_episode
                for s in season.survivors
                if s.voted_out_order == merge_elim and s.elimination_episode
            ),
            None,
        )
        return merge_elim, episode
    if merge_episode is None:
        return None, None
    # Counted like Season.current_tribal_count: distinct elimination days
    out = [s for s in season.survivors if _lost_by_merge(s, merge_episode)]
    days = {s.day_voted_out for s in out if s.day_voted_out}
    return (len(days) if days else len(out)), merge_episode


def counts_yet(pick_type, tribals, merge_elim):
    """Whether a pick scores yet, `tribals` into the season.

    The one rule for every page that totals picks (the leaderboard, its
    progression line, the compare page). Wildcards are picked after episode
    1, so they count from the first boot. Replacements count once `tribals`
    reaches `merge_elim` (the first value from scoring_start), and not at all
    while it is unknown. Draft picks always count.

    `tribals` is Season.current_tribal_count, taken on the season as shown
    (after _apply_as_of for a past step): distinct elimination days, so a
    same-day double boot counts once, as CLAUDE.md's tribal counting rule says.
    """
    if pick_type == "wildcard":
        return tribals > 0
    if pick_type in TYPES:
        return merge_elim is not None and tribals >= merge_elim
    return True


# ── The window ────────────────────────────────────────────────────────────


def is_self_service(season):
    return season.replacement_starts_at is not None


def lock_at(season):
    """Aware UTC moment replacements lock, or None if self-service is off."""
    if not is_self_service(season):
        return None
    return season.replacement_starts_at.replace(tzinfo=UTC) - wildcards.LOCK_LEAD


# ── The reveal ────────────────────────────────────────────────────────────


def are_hidden(season):
    """True while replacements must stay off the public pages for this season."""
    if not is_self_service(season):
        return False
    entitled, picked = _entitled_and_picked(season)
    return bool(set(entitled) - picked)


def progress(season):
    """Return (eligible players who have picked, eligible players, those still to pick).

    The last item is a list of User rows ordered by name.
    """
    entitled, picked = _entitled_and_picked(season)
    waiting_ids = set(entitled) - picked
    waiting = []
    if waiting_ids:
        waiting = (
            User.query.filter(User.id.in_(waiting_ids)).order_by(User.username).all()
        )
    return len(set(entitled) & picked), len(entitled), waiting


def hidden_types(season):
    """Pick types the public pages must leave out right now."""
    hidden = set()
    if wildcards.are_hidden(season):
        hidden.add("wildcard")
    if are_hidden(season):
        hidden.update(TYPES)
    return frozenset(hidden)


def visible_picks(picks, hidden):
    """Drop picks whose type is in `hidden` (see hidden_types)."""
    if not hidden:
        return picks
    return [p for p in picks if p.pick_type not in hidden]


# ── A player's own replacement ────────────────────────────────────────────


def _own_picks(season, user):
    return Pick.query.filter_by(user_id=user.id, season_id=season.id).all()


def current_pick(season, user):
    """The user's replacement Pick for the season, or None."""
    return Pick.query.filter(
        Pick.user_id == user.id,
        Pick.season_id == season.id,
        Pick.pick_type.in_(TYPES),
    ).first()


def eligible_survivors(season, user):
    """Castaways this player may take as a replacement.

    Anyone still in the game who is not already on the player's own team.
    Castaways on other teams stay eligible, as they have in past seasons, and
    refusing a clash with another player's replacement would leak that pick.
    """
    own = db.select(Pick.survivor_id).where(
        Pick.season_id == season.id,
        Pick.user_id == user.id,
        Pick.pick_type.notin_(TYPES),
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


def edit_block_reason(season, user, existing=None, kind=None):
    """Why this player cannot set or change their replacement now, or None if they can."""
    if not season.is_active:
        return "Replacements can only be picked for the active season."
    if not is_self_service(season):
        return "Replacement picks are not open for this season."
    if kind is None:
        kind = entitlement(season, _own_picks(season, user))
    if kind is None:
        return (
            "You are not eligible for a replacement: none of your castaways "
            "went out by the merge episode."
        )
    if existing is None:
        existing = current_pick(season, user)
    if wildcards.now_utc() >= lock_at(season):
        if existing is not None:
            return "Your replacement is locked."
        return "Replacement picks are locked. Ask the league admin to enter yours."
    return None


def set_pick(season, user, raw_survivor_id):
    """Set, change, or (with a blank id) remove the player's replacement.

    Returns an error message for the player, or None on success. Commits.
    """
    existing = current_pick(season, user)
    kind = entitlement(season, _own_picks(season, user))
    blocked = edit_block_reason(season, user, existing, kind)
    if blocked:
        return blocked

    raw = (raw_survivor_id or "").strip()
    if not raw:
        if existing is None:
            return None
        return _commit(lambda: _delete_replacements(season, user))

    eligible = {str(s.id): s for s in eligible_survivors(season, user)}
    survivor = eligible.get(raw)
    if survivor is None:
        return "That castaway is not available as a replacement."

    def apply():
        # Delete-then-insert in one transaction rather than updating the row
        # read above: two saves racing (a double click) would otherwise both
        # insert. SQLite takes one writer at a time, so the later save either
        # deletes the earlier row or fails and asks the player to try again.
        _delete_replacements(season, user)
        db.session.add(
            Pick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=survivor.id,
                pick_type=kind,
            )
        )

    return _commit(apply)


def _delete_replacements(season, user):
    """Delete every replacement row the player has this season (one, normally)."""
    Pick.query.filter(
        Pick.user_id == user.id,
        Pick.season_id == season.id,
        Pick.pick_type.in_(TYPES),
    ).delete(synchronize_session="fetch")


def player_view(season, user):
    """What My Team shows a player about replacements, or None if self-service is off."""
    if not is_self_service(season):
        return None
    pick = current_pick(season, user)
    kind = entitlement(season, _own_picks(season, user))
    blocked = edit_block_reason(season, user, pick, kind)
    picked, total, _waiting = progress(season)
    lock = lock_at(season)
    return {
        "pick": pick,
        "kind_label": KIND_LABELS.get(kind),
        "blocked": blocked,
        "locked": wildcards.now_utc() >= lock,
        "options": [] if blocked else eligible_survivors(season, user),
        "lock_label": wildcards.format_league_time(lock),
        "picked": picked,
        "total": total,
        "hidden": picked < total,
    }


def admin_view(season):
    """What the season admin page shows about the replacement window."""
    view = {
        "form_value": wildcards.to_form_value(season.replacement_starts_at),
        "enabled": is_self_service(season),
        "manual": bool(season.replacement_manual),
        "merge_known": season.merge_episode_num is not None,
        "next_episode": (
            season.merge_episode_num + 1
            if season.merge_episode_num is not None
            else None
        ),
    }
    if view["enabled"]:
        picked, total, waiting = progress(season)
        view.update(
            lock_label=wildcards.format_league_time(lock_at(season)),
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
        logger.exception("Saving a replacement pick failed")
        return "Could not save your replacement. Try again."
    return None
