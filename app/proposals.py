"""Scoring proposals put to the league, and the players' votes on them.

The admin proposes a config for a season, with a time voting closes. On
the season admin page that is a starting point (starting_points: the last
proposal, a recorded version or the current scoring) with any values
adjusted by hand. Until voting closes, anyone with picks in the season
votes on the Scoring changes page: switch this season, start next season,
or keep the current scoring, with an optional comment. A vote can be
changed until voting closes, and every vote shows with its name.

The admin then adopts the proposal (it becomes the season's scoring, and the
scoring it replaced is recorded) or withdraws it. Proposals are never
deleted, and votes belong to their proposal, so a new proposal starts with
none and each decision keeps its own record.
"""

import json
import logging
from datetime import UTC

from sqlalchemy.exc import SQLAlchemyError

from . import wildcards
from .models import Pick, ScoringProposal, ScoringVote, User, db
from .scoring.classic import DEFAULT_CONFIG
from .scoring.history import SCORING_VERSIONS, describe_config

logger = logging.getLogger(__name__)

# Display order is the order of the choices on the ballot
CHOICES = {
    "now": "Switch this season now",
    "next": "Start it next season",
    "keep": "Keep the current scoring",
}
COMMENT_MAX_LENGTH = 500


def _now():
    # The league's one clock, shared with the wildcard and replacement locks
    return wildcards.now_utc()


def open_proposal(season):
    """The season's open proposal, or None. Only one is ever open."""
    return (
        ScoringProposal.query.filter_by(season_id=season.id, status="open")
        .order_by(ScoringProposal.id.desc())
        .first()
    )


def adopted_proposals(season):
    """The season's adopted proposals, oldest first."""
    return (
        ScoringProposal.query.filter_by(season_id=season.id, status="adopted")
        .order_by(ScoringProposal.closed_at)
        .all()
    )


def is_open(proposal):
    """True while votes on `proposal` are accepted."""
    return (
        proposal is not None
        and proposal.status == "open"
        and _now() < proposal.closes_at.replace(tzinfo=UTC)
    )


def latest_proposal(season):
    """The season's most recent proposal, whatever its status, or None."""
    return (
        ScoringProposal.query.filter_by(season_id=season.id)
        .order_by(ScoringProposal.id.desc())
        .first()
    )


def _effective(config):
    """Every key the season form saves: the config's value, else the default.

    The same key set admin_season_settings stores, so a proposal made from a
    starting point has the shape of one typed in by hand. A key outside
    DEFAULT_CONFIG (Legacy's old replacement_multiplier) is dropped; scoring
    merges DEFAULT_CONFIG first, so its default already shadowed that key.
    """
    return {key: config.get(key, default) for key, default in DEFAULT_CONFIG.items()}


def _version_label(config):
    version, diffs = describe_config(config)
    if not diffs:
        return version["name"]
    plural = "s" if len(diffs) != 1 else ""
    return f"{version['name']}, {len(diffs)} value{plural} edited"


def starting_points(season):
    """Configs a new proposal can start from, and the key to select first.

    Returns ([{"key", "label", "group", "config", "changes"}], default key).
    `config` is complete (every DEFAULT_CONFIG key) and `changes` counts the
    values it would change from the season's current scoring. The season's
    latest proposal comes first, so a recorded version proposed with
    adjustments can be put to the league again without typing them; then the
    recorded versions, newest first; then the season's current scoring.

    The latest proposal is selected first unless it was adopted (then it is
    the current scoring already), else the newest recorded version.
    """
    current = _effective(season.get_scoring_config())

    def point(key, label, group, config):
        config = _effective(config)
        changes = sum(1 for k in DEFAULT_CONFIG if config[k] != current[k])
        return {
            "key": key,
            "label": label,
            "group": group,
            "config": config,
            "changes": changes,
        }

    points = []
    default = None
    latest = latest_proposal(season)
    if latest is not None:
        config = latest.get_config()
        if latest.status == "open":
            label = f"The open proposal ({_version_label(config)})"
        else:
            label = f"Last proposal, {latest.status} ({_version_label(config)})"
        points.append(point(f"proposal:{latest.id}", label, "Proposals", config))
        if latest.status != "adopted":
            default = points[-1]["key"]
    for version in reversed(SCORING_VERSIONS):
        points.append(
            point(
                f"version:{version['key']}",
                version["name"],
                "Recorded versions",
                version["config"],
            )
        )
    if default is None:
        default = f"version:{SCORING_VERSIONS[-1]['key']}"
    points.append(
        point("current", "This season's current scoring", "This season", current)
    )
    return points, default


def starting_config(season, key):
    """The complete config a starting point names, or None if it is not offered.

    Only what starting_points lists is accepted: a proposal key is honoured
    only while it is still the season's latest, so a form left open while
    another proposal was made cannot propose something the page no longer
    shows.
    """
    points, _default = starting_points(season)
    for p in points:
        if p["key"] == key:
            return dict(p["config"])
    return None


def propose(season, config, closes_at):
    """Open a proposal for the season. Returns an error message, or None. Commits.

    `closes_at` is naive UTC and must be in the future. An open proposal is
    withdrawn and replaced, so its votes stay with it and the new one starts
    with none: votes cast on other values do not carry over.
    """
    if closes_at is None:
        return "Enter when voting closes."
    if closes_at.replace(tzinfo=UTC) <= _now():
        return "Voting has to close in the future."
    current = open_proposal(season)
    try:
        if current is not None:
            _close(current, "withdrawn")
        db.session.add(
            ScoringProposal(
                season_id=season.id, config=json.dumps(config), closes_at=closes_at
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Saving a scoring proposal failed (season %s)", season.id)
        return "Could not save the proposal. Try again."
    return None


def _voter_ids(season):
    """Users who may vote: everyone with a pick in the season."""
    rows = db.session.query(Pick.user_id).filter_by(season_id=season.id).distinct()
    return {uid for (uid,) in rows}


def _block_reason(proposal, voter_ids, user):
    if proposal is None:
        return "There is no scoring proposal to vote on."
    if not is_open(proposal):
        return "Voting on this proposal has closed."
    if user.id not in voter_ids:
        return "Only players in this season can vote."
    return None


def cast_vote(season, user, choice, comment):
    """Record or replace the user's vote. Returns an error message, or None. Commits."""
    proposal = open_proposal(season)
    blocked = _block_reason(proposal, _voter_ids(season), user)
    if blocked:
        return blocked
    if choice not in CHOICES:
        return "Choose one of the options."
    comment = (comment or "").strip()[:COMMENT_MAX_LENGTH]
    vote = ScoringVote.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()
    try:
        if vote is None:
            db.session.add(
                ScoringVote(
                    proposal_id=proposal.id,
                    user_id=user.id,
                    choice=choice,
                    comment=comment,
                )
            )
        else:
            vote.choice = choice
            vote.comment = comment
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Saving a scoring vote failed")
        return "Could not save your vote. Try again."
    return None


def results(proposal, season, viewer=None):
    """What the page shows about a proposal's vote, or None without a proposal.

    `viewer` is the signed-in user, for their own vote and whether they can
    vote now.
    """
    if proposal is None:
        return None
    voter_ids = _voter_ids(season)
    votes = (
        ScoringVote.query.filter_by(proposal_id=proposal.id)
        .join(User)
        .order_by(User.username)
        .all()
    )
    tally = {key: 0 for key in CHOICES}
    for v in votes:
        tally[v.choice] = tally.get(v.choice, 0) + 1
    mine = None
    blocked = "Sign in to vote."
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        mine = next((v for v in votes if v.user_id == viewer.id), None)
        blocked = _block_reason(proposal, voter_ids, viewer)
    return {
        "choices": CHOICES,
        "tally": tally,
        "votes": votes,
        "voted": len(votes),
        "voters": len(voter_ids),
        "mine": mine,
        "blocked": blocked,
        "open": is_open(proposal),
        "closes_at": proposal.closes_at,
        "comment_max": COMMENT_MAX_LENGTH,
    }


def adopt(season):
    """Make the open proposal the season's scoring. Returns an error, or None."""
    proposal = open_proposal(season)
    if proposal is None:
        return "There is no proposal to adopt."
    try:
        proposal.replaced_config = season.scoring_config
        season.scoring_config = proposal.config
        _close(proposal, "adopted")
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Adopting a scoring proposal failed (season %s)", season.id)
        return "Could not adopt the proposal. Try again."
    return None


def withdraw(season):
    """Close the open proposal without applying it. Returns an error, or None."""
    proposal = open_proposal(season)
    if proposal is None:
        return "There is no proposal to withdraw."
    try:
        _close(proposal, "withdrawn")
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Withdrawing a scoring proposal failed (season %s)", season.id)
        return "Could not withdraw the proposal. Try again."
    return None


def _close(proposal, status):
    """Mark a proposal adopted or withdrawn (the caller commits)."""
    proposal.status = status
    proposal.closed_at = _now().replace(tzinfo=None)
