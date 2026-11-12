"""A scoring proposal put to the league, and the players' votes on it.

The admin saves a proposed config beside a season's own (scoring components
form, "Propose"), with a time voting closes. Until then, anyone with picks
in the season votes on the Scoring changes page: switch this season, start
next season, or keep the current scoring, with an optional comment. A vote
can be changed until voting closes, and every vote shows with its name.

The admin then adopts the proposal (it becomes the season's scoring) or
withdraws it. The votes stay either way, as the record of the decision.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from .models import Pick, ScoringVote, User, db

logger = logging.getLogger(__name__)

# Display order is the order of the choices on the ballot
CHOICES = {
    "now": "Switch this season now",
    "next": "Start it next season",
    "keep": "Keep the current scoring",
}
COMMENT_MAX_LENGTH = 500


def now_utc():
    return datetime.now(UTC)


def is_open(season):
    """True while the season has a proposal whose voting has not closed."""
    if season.get_proposed_config() is None:
        return False
    closes = season.proposal_closes_at
    return closes is None or now_utc() < closes.replace(tzinfo=UTC)


def _voter_ids(season):
    """Users who may vote: everyone with a pick in the season."""
    rows = db.session.query(Pick.user_id).filter_by(season_id=season.id).distinct()
    return {uid for (uid,) in rows}


def vote_block_reason(season, user):
    """Why this user cannot vote on the season's proposal now, or None if they can."""
    if season.get_proposed_config() is None:
        return "There is no scoring proposal to vote on."
    if not is_open(season):
        return "Voting on this proposal has closed."
    if user.id not in _voter_ids(season):
        return "Only players in this season can vote."
    return None


def cast_vote(season, user, choice, comment):
    """Record or replace the user's vote. Returns an error message, or None. Commits."""
    blocked = vote_block_reason(season, user)
    if blocked:
        return blocked
    if choice not in CHOICES:
        return "Choose one of the options."
    comment = (comment or "").strip()[:COMMENT_MAX_LENGTH]
    vote = ScoringVote.query.filter_by(season_id=season.id, user_id=user.id).first()
    try:
        if vote is None:
            db.session.add(
                ScoringVote(
                    season_id=season.id, user_id=user.id, choice=choice, comment=comment
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


def results(season, viewer=None):
    """Everything the page shows about the vote, or None if nobody can vote.

    `viewer` is the signed-in user, for their own vote and whether they can
    vote now.
    """
    voters = _voter_ids(season)
    votes = (
        ScoringVote.query.filter_by(season_id=season.id)
        .join(User)
        .order_by(User.username)
        .all()
    )
    if not voters and not votes:
        return None
    tally = {key: 0 for key in CHOICES}
    for v in votes:
        tally[v.choice] = tally.get(v.choice, 0) + 1
    mine = None
    blocked = "Sign in to vote."
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        mine = next((v for v in votes if v.user_id == viewer.id), None)
        blocked = vote_block_reason(season, viewer)
    return {
        "choices": CHOICES,
        "tally": tally,
        "votes": votes,
        "voted": len(votes),
        "voters": len(voters),
        "mine": mine,
        "blocked": blocked,
        "open": is_open(season),
        "closes_at": season.proposal_closes_at,
        "comment_max": COMMENT_MAX_LENGTH,
    }


def adopt(season):
    """Make the proposal the season's scoring. Returns an error message, or None."""
    proposed = season.get_proposed_config()
    if proposed is None:
        return "There is no proposal to adopt."
    return _commit(season, scoring_config=season.proposed_scoring_config)


def withdraw(season):
    """Drop the proposal; the season keeps its scoring. Returns an error, or None."""
    if season.get_proposed_config() is None:
        return "There is no proposal to withdraw."
    return _commit(season)


def _commit(season, scoring_config=None):
    try:
        if scoring_config is not None:
            season.scoring_config = scoring_config
        season.proposed_scoring_config = None
        season.proposal_closes_at = None
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Saving the scoring proposal failed (season %s)", season.id)
        return "Could not save. Try again."
    return None
