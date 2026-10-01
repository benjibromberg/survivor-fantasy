"""Tests for app/data.py — compute_castaway_stats aggregation logic."""

import pandas as pd

from app.data import compute_castaway_stats

# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_df(*cols):
    """Return an empty DataFrame with the given columns."""
    return pd.DataFrame(columns=cols)


def _vote_history(rows):
    """Build a Vote History DataFrame from (castaway_id, vote_id, voted_out_id) tuples.

    One row per castaway per tribal, as in survivoR: castaway_id is the voter,
    vote_id is who they voted for (None if they cast no vote, e.g. Shot in the
    Dark), and voted_out_id is who left at that tribal (the same on every row).
    """
    return pd.DataFrame(rows, columns=["castaway_id", "vote_id", "voted_out_id"])


def _confessionals(rows):
    """Build a Confessionals DataFrame from (castaway_id, confessional_count) tuples."""
    return pd.DataFrame(rows, columns=["castaway_id", "confessional_count"])


def _challenge_results(rows):
    """Build a Challenge Results DataFrame from (castaway_id, won_ii, won_ti) tuples."""
    return pd.DataFrame(
        rows,
        columns=["castaway_id", "won_individual_immunity", "won_tribal_immunity"],
    )


def _advantage_movement(rows):
    """Build an Advantage Movement DataFrame from (castaway_id, advantage_id, event) tuples."""
    return pd.DataFrame(rows, columns=["castaway_id", "advantage_id", "event"])


EMPTY_CONF = _empty_df("castaway_id", "confessional_count")
EMPTY_VH = _empty_df("castaway_id", "vote_id", "voted_out_id")
EMPTY_CR = _empty_df("castaway_id", "won_individual_immunity", "won_tribal_immunity")
EMPTY_AM = _empty_df("castaway_id", "advantage_id", "event")
NO_IDOLS = set()


# ── votes_against: counts votes cast for a castaway (vote_id) ─────────────


class TestVotesAgainst:
    def test_counts_votes_cast_for_each_target(self):
        """One tribal: A and C vote for B, B votes for C, and B goes home."""
        vh = _vote_history(
            [
                ("US0001", "US0002", "US0002"),
                ("US0003", "US0002", "US0002"),
                ("US0002", "US0003", "US0002"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        va = result["votes_against"]
        assert va.get("US0002") == 2  # B received 2 votes
        assert va.get("US0003") == 1  # C received 1 vote and stayed
        assert va.get("US0001", 0) == 0  # A received 0 votes

    def test_boot_is_not_credited_with_every_ballot(self):
        """Core regression: a ten-person tribal where the boot got six votes.

        Six vote for A, two vote for J, and A and J cast no vote (Shot in the
        Dark). Every row carries voted_out_id = A, so grouping by voted_out_id
        credits A with all ten rows and J with none.
        """
        a, j = "US0001", "US0002"
        vh = _vote_history(
            [(f"US01{n:02d}", a, a) for n in range(6)]
            + [("US0201", j, a), ("US0202", j, a)]
            + [(a, None, a), (j, None, a)]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        va = result["votes_against"]
        assert va.get(a) == 6
        assert va.get(j) == 2

    def test_single_vote(self):
        vh = _vote_history([("US0001", "US0002", "US0002")])
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["votes_against"].get("US0002") == 1
        assert result["votes_against"].get("US0001", 0) == 0

    def test_unanimous_vote(self):
        """All voters target the same person."""
        vh = _vote_history(
            [
                ("US0001", "US0005", "US0005"),
                ("US0002", "US0005", "US0005"),
                ("US0003", "US0005", "US0005"),
                ("US0004", "US0005", "US0005"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["votes_against"].get("US0005") == 4

    def test_split_vote(self):
        """Two targets receive different numbers of votes; the majority target leaves."""
        vh = _vote_history(
            [
                ("US0001", "US0003", "US0001"),
                ("US0002", "US0003", "US0001"),
                ("US0003", "US0001", "US0001"),
                ("US0004", "US0001", "US0001"),
                ("US0005", "US0001", "US0001"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        va = result["votes_against"]
        assert va.get("US0001") == 3
        assert va.get("US0003") == 2

    def test_empty_vote_history(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        assert result["votes_against"].empty

    def test_self_vote(self):
        """Edge case: a player votes for themselves (shouldn't happen, but data may contain it)."""
        vh = _vote_history([("US0001", "US0001", "US0001")])
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["votes_against"].get("US0001") == 1


# ── votes_cast / correct_votes: the two halves of voting accuracy ─────────


class TestVotesCastAndCorrect:
    def test_no_vote_rows_are_not_votes_cast(self):
        """A castaway who attends but casts no vote has nothing to be right about."""
        vh = _vote_history(
            [
                ("US0001", "US0003", "US0003"),
                ("US0002", None, "US0003"),  # Shot in the Dark: no vote
                ("US0003", "US0001", "US0003"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["votes_cast"].get("US0001") == 1
        assert result["votes_cast"].get("US0002", 0) == 0
        assert result["correct_votes"].get("US0001") == 1
        assert result["correct_votes"].get("US0002", 0) == 0
        assert result["correct_votes"].get("US0003", 0) == 0

    def test_correct_never_exceeds_cast(self):
        """Revotes and extra votes add rows to both counts, not just the correct one."""
        vh = _vote_history(
            [
                ("US0001", "US0009", "US0009"),  # first vote (tie)
                ("US0001", "US0009", "US0009"),  # extra vote
                ("US0001", "US0009", "US0009"),  # revote
                ("US0001", "US0008", "US0007"),  # a later tribal, wrong
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, vh, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["votes_cast"].get("US0001") == 4
        assert result["correct_votes"].get("US0001") == 3

    def test_empty_vote_history(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        assert result["votes_cast"].empty
        assert result["correct_votes"].empty


# ── confessionals ─────────────────────────────────────────────────────────


class TestConfessionals:
    def test_sums_across_episodes(self):
        conf = _confessionals(
            [
                ("US0001", 3),
                ("US0001", 5),
                ("US0002", 2),
            ]
        )
        result = compute_castaway_stats(conf, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS)
        assert result["conf_totals"].get("US0001") == 8
        assert result["conf_totals"].get("US0002") == 2

    def test_empty_confessionals(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        assert result["conf_totals"].empty


# ── challenge results ─────────────────────────────────────────────────────


class TestChallengeResults:
    def test_individual_immunity(self):
        cr = _challenge_results(
            [
                ("US0001", 1, 0),
                ("US0001", 1, 0),
                ("US0002", 0, 0),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, cr, EMPTY_AM, NO_IDOLS)
        assert result["indiv_imm"].get("US0001") == 2
        assert result["indiv_imm"].get("US0002", 0) == 0

    def test_tribal_immunity(self):
        cr = _challenge_results(
            [
                ("US0001", 0, 1),
                ("US0001", 0, 1),
                ("US0001", 0, 1),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, cr, EMPTY_AM, NO_IDOLS)
        assert result["tribal_imm"].get("US0001") == 3

    def test_empty_challenge_results(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        assert result["indiv_imm"].empty
        assert result["tribal_imm"].empty


# ── advantage movement ────────────────────────────────────────────────────


class TestAdvantageMovement:
    def test_idol_found_and_played(self):
        idol_ids = {"idol_001", "idol_002"}
        am = _advantage_movement(
            [
                ("US0001", "idol_001", "Found"),
                ("US0001", "idol_001", "Played"),
                ("US0001", "idol_002", "Found"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, EMPTY_CR, am, idol_ids)
        assert result["idols_found"].get("US0001") == 2
        assert result["idols_played"].get("US0001") == 1

    def test_non_idol_advantage(self):
        idol_ids = {"idol_001"}
        am = _advantage_movement(
            [
                ("US0001", "adv_001", "Found"),
                ("US0001", "adv_001", "Played"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, EMPTY_CR, am, idol_ids)
        assert result["adv_found"].get("US0001") == 1
        assert result["adv_played"].get("US0001") == 1
        assert result["idols_found"].get("US0001", 0) == 0

    def test_idol_vs_advantage_separation(self):
        """Idols and non-idol advantages are counted separately."""
        idol_ids = {"idol_001"}
        am = _advantage_movement(
            [
                ("US0001", "idol_001", "Found"),
                ("US0001", "adv_001", "Found"),
                ("US0001", "idol_001", "Played"),
                ("US0001", "adv_001", "Played"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, EMPTY_CR, am, idol_ids)
        assert result["idols_found"].get("US0001") == 1
        assert result["idols_played"].get("US0001") == 1
        assert result["adv_found"].get("US0001") == 1
        assert result["adv_played"].get("US0001") == 1

    def test_empty_advantage_movement(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        assert result["idols_found"].empty
        assert result["adv_found"].empty

    def test_found_keyword_matching(self):
        """'Found' matching uses str.contains, so 'Found On Ground' should count."""
        idol_ids = {"idol_001"}
        am = _advantage_movement(
            [
                ("US0001", "idol_001", "Found On Ground"),
            ]
        )
        result = compute_castaway_stats(EMPTY_CONF, EMPTY_VH, EMPTY_CR, am, idol_ids)
        assert result["idols_found"].get("US0001") == 1


# ── all stats together ────────────────────────────────────────────────────


class TestFullIntegration:
    def test_all_stats_computed_together(self):
        """Verify all stat categories work in concert."""
        idol_ids = {"idol_001"}
        conf = _confessionals([("US0001", 5), ("US0002", 3)])
        vh = _vote_history(
            [("US0001", "US0002", "US0002"), ("US0003", "US0002", "US0002")]
        )
        cr = _challenge_results([("US0001", 1, 0), ("US0002", 0, 1)])
        am = _advantage_movement(
            [
                ("US0001", "idol_001", "Found"),
                ("US0002", "adv_001", "Found"),
            ]
        )
        result = compute_castaway_stats(conf, vh, cr, am, idol_ids)
        assert result["conf_totals"].get("US0001") == 5
        assert result["votes_against"].get("US0002") == 2
        assert result["indiv_imm"].get("US0001") == 1
        assert result["tribal_imm"].get("US0002") == 1
        assert result["idols_found"].get("US0001") == 1
        assert result["adv_found"].get("US0002") == 1

    def test_all_empty_returns_empty_series(self):
        result = compute_castaway_stats(
            EMPTY_CONF, EMPTY_VH, EMPTY_CR, EMPTY_AM, NO_IDOLS
        )
        for key in [
            "conf_totals",
            "votes_against",
            "votes_cast",
            "correct_votes",
            "indiv_imm",
            "tribal_imm",
            "idols_found",
            "idols_played",
            "adv_found",
            "adv_played",
        ]:
            assert result[key].empty
