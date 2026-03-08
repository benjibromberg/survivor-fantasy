import json
from datetime import datetime, timezone

from flask_login import UserMixin

from . import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class User(UserMixin, db.Model):
    """Represents both the admin (who logs in via GitHub) and fantasy players
    (created by admin, never log in — just names on the leaderboard)."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(80))
    github_username = db.Column(db.String(80), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    picks = db.relationship('Pick', backref='user', lazy=True)


class Season(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    num_players = db.Column(db.Integer, default=18)
    num_episodes = db.Column(db.Integer, default=13)
    left_at_jury = db.Column(db.Integer, default=11)
    scoring_system = db.Column(db.String(50), default='Classic')
    scoring_config = db.Column(db.Text, default='{}')
    survivors = db.relationship('Survivor', backref='season', lazy=True)
    picks = db.relationship('Pick', backref='season', lazy=True)

    @property
    def merge_threshold(self):
        """Elimination count at which merge occurs (pre-merge tribals)."""
        return self.num_players - self.left_at_jury

    @property
    def current_tribal_count(self):
        voted = [s.voted_out_order for s in self.survivors if s.voted_out_order]
        return max(voted) if voted else 0

    def get_scoring_config(self):
        try:
            return json.loads(self.scoring_config) if self.scoring_config else {}
        except json.JSONDecodeError:
            return {}


class Survivor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    season_id = db.Column(db.Integer, db.ForeignKey('season.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    full_name = db.Column(db.String(150))
    castaway_id = db.Column(db.String(20))  # e.g. US0009
    version_season = db.Column(db.String(10))  # e.g. US50
    image_url = db.Column(db.String(256))
    tribe = db.Column(db.String(50))
    tribe_color = db.Column(db.String(10))  # hex color e.g. #00B8AD
    voted_out_order = db.Column(db.Integer, default=0)  # 0 = still in game
    result = db.Column(db.String(50))  # e.g. "7th voted out", "Sole Survivor"
    made_jury = db.Column(db.Boolean, default=False)
    placement = db.Column(db.Integer)  # 1 = winner, null = still in
    immunity_wins = db.Column(db.Integer, default=0)
    reward_wins = db.Column(db.Integer, default=0)
    # Stats from survivoR
    confessional_count = db.Column(db.Integer, default=0)
    votes_received = db.Column(db.Integer, default=0)
    individual_immunity_wins = db.Column(db.Integer, default=0)
    tribal_immunity_wins = db.Column(db.Integer, default=0)
    idols_found = db.Column(db.Integer, default=0)
    advantages_found = db.Column(db.Integer, default=0)
    advantages_played = db.Column(db.Integer, default=0)
    # Extended stats from survivoR
    tribal_councils_attended = db.Column(db.Integer, default=0)
    correct_votes = db.Column(db.Integer, default=0)
    votes_nullified = db.Column(db.Integer, default=0)
    confessional_time = db.Column(db.Float, default=0)  # seconds
    sit_outs = db.Column(db.Integer, default=0)
    jury_votes_received = db.Column(db.Integer)  # finalists only
    performance_score = db.Column(db.Float)  # survivoR overall score
    elimination_episode = db.Column(db.Integer)  # episode eliminated in (null = still in)
    episode_stats = db.Column(db.Text)  # JSON: {ep: {conf, ii, ti, idol, adv, adv_play, votes, ...}}
    won_fire = db.Column(db.Boolean, default=False)  # won final 4 fire challenge
    # Bio from survivoR Castaway Details
    age = db.Column(db.Integer)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    occupation = db.Column(db.String(150))
    personality_type = db.Column(db.String(10))  # MBTI e.g. ENFP

    def get_episode_stats(self):
        """Return parsed episode_stats dict, or empty dict."""
        if self.episode_stats:
            try:
                return json.loads(self.episode_stats)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    @property
    def stats_url(self):
        """Link to survivorstatsdb.com profile."""
        if self.castaway_id and self.version_season:
            return f'https://survivorstatsdb.com/castaway/{self.version_season}/{self.castaway_id}'
        return None

    picks = db.relationship('Pick', backref='survivor', lazy=True)


class SoleSurvivorPick(db.Model):
    """Tracks sole survivor winner predictions per episode.

    Each row represents a pick change. The pick is active from this episode
    until the next change (or end of season). Players earn 1 × sole_survivor_val
    per consecutive episode they had the winner picked, streaking back from the
    finale.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    season_id = db.Column(db.Integer, db.ForeignKey('season.id'), nullable=False)
    survivor_id = db.Column(db.Integer, db.ForeignKey('survivor.id'), nullable=False)
    episode = db.Column(db.Integer, nullable=False)  # episode this pick becomes active
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='sole_survivor_picks')
    survivor = db.relationship('Survivor')
    season = db.relationship('Season', backref='sole_survivor_picks')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'season_id', 'episode',
                            name='uq_ss_user_season_episode'),
    )


def calculate_ss_streak(ss_picks, season):
    """Calculate sole survivor streak length for one user in one season.

    Args:
        ss_picks: list of SoleSurvivorPick for a single user+season
        season: Season object

    Returns:
        int: consecutive episodes ending at the finale with the winner picked.
             0 if wrong pick, no picks, or season not finished.
    """
    if not ss_picks:
        return 0

    # Find the winner
    winner = None
    for s in season.survivors:
        if s.voted_out_order == season.num_players:
            winner = s
            break

    if not winner:
        return 0  # season not finished

    num_episodes = season.num_episodes or 13

    # Build episode -> survivor_id mapping
    # Each pick is active from its episode until the next change
    sorted_picks = sorted(ss_picks, key=lambda p: p.episode)
    pick_by_episode = {}
    for i, pick in enumerate(sorted_picks):
        start = pick.episode
        end = sorted_picks[i + 1].episode if i + 1 < len(sorted_picks) else num_episodes + 1
        for ep in range(start, min(end, num_episodes + 1)):
            pick_by_episode[ep] = pick.survivor_id

    # Must have winner going into final episode
    if pick_by_episode.get(num_episodes) != winner.id:
        return 0

    # Count consecutive episodes ending at finale
    streak = 0
    for ep in range(num_episodes, 0, -1):
        if pick_by_episode.get(ep) == winner.id:
            streak += 1
        else:
            break

    return streak


class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    season_id = db.Column(db.Integer, db.ForeignKey('season.id'), nullable=False)
    survivor_id = db.Column(db.Integer, db.ForeignKey('survivor.id'), nullable=False)
    pick_type = db.Column(db.String(20), nullable=False)  # draft, wildcard, pmr_w, pmr_d
    pick_order = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'season_id', 'survivor_id',
                            name='uq_user_season_survivor'),
    )
