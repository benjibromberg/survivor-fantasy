"""Tests for app/highlights — Character Journey Cards event detection and badges."""
import pytest
from analyze_scoring import SimSurvivor, SimSeason
from app.highlights import (
    generate_highlights, Event, Badge,
    IMMUNITY, TRIBAL_IMMUNITY, IDOL, ADVANTAGE, VOTES,
    TRIBE, MERGE, ELIMINATION, WINNER, JURY, FIRE,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def make_season(num_players=18, left_at_jury=8, n_finalists=3, survivors=None):
    return SimSeason(
        number=50, name='Test', num_players=num_players,
        left_at_jury=left_at_jury, n_finalists=n_finalists,
        survivors=survivors or [],
    )


def ep_stats(*episodes):
    """Build episode_stats dict from a list of per-episode stat dicts.

    Each episode dict should have the delta values (not cumulative).
    This helper accumulates them to match _build_cumulative output.
    """
    cumulative = {}
    running = {
        'conf': 0, 'ii': 0, 'ti': 0, 'idol': 0, 'idol_play': 0,
        'adv': 0, 'adv_play': 0, 'votes': 0, 'tribe': '', 'nullified': 0,
    }
    for i, ep in enumerate(episodes, 1):
        for k, v in ep.items():
            if k == 'tribe':
                running[k] = v
            else:
                running[k] = running.get(k, 0) + v
        cumulative[str(i)] = dict(running)
    return cumulative


# ── Empty / no-op cases ───────────────────────────────────────────────────

class TestEmptyStats:
    def test_none_episode_stats(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=None)
        events, badges = generate_highlights(s, make_season(), None)
        assert events == []
        assert badges == []

    def test_empty_episode_stats(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats={})
        events, badges = generate_highlights(s, make_season(), None)
        assert events == []
        assert badges == []


# ── "Started on [tribe]" ─────────────────────────────────────────────────

class TestStartedOnTribe:
    def test_first_event_is_tribe(self):
        s = SimSurvivor(id=1, name='Kenzie', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'tribe': 'Nami'},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        assert events[0].event_type == TRIBE
        assert events[0].text == 'Started on Nami'
        assert events[0].episode == 1


# ── Individual immunity wins ─────────────────────────────────────────────

class TestImmunityWins:
    def test_single_immunity_win(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        imm = [e for e in events if e.event_type == IMMUNITY]
        assert len(imm) == 1
        assert imm[0].text == 'Won individual immunity'
        assert imm[0].detail_text is None  # No "Nth win" for first

    def test_multiple_immunity_wins(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1},
                            {},
                            {'ii': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        imm = [e for e in events if e.event_type == IMMUNITY]
        assert len(imm) == 2
        assert imm[1].detail_text == '2nd win this season'


# ── Tribal immunity wins ─────────────────────────────────────────────────

class TestTribalImmunity:
    def test_single_tribal_immunity(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ti': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        ti = [e for e in events if e.event_type == TRIBAL_IMMUNITY]
        assert len(ti) == 1
        assert ti[0].text == 'Won tribal immunity'

    def test_multiple_tribal_immunity_aggregated(self):
        """Multiple tribal immunity wins on same tribe aggregate into one event."""
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ti': 1},
                            {'ti': 1},
                            {'ti': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        ti = [e for e in events if e.event_type == TRIBAL_IMMUNITY]
        assert len(ti) == 1
        assert ti[0].text == 'Won 3x tribal immunity'
        assert ti[0].detail_text == 'Ep 2–4'

    def test_tribal_immunity_flushed_at_swap(self):
        """Tribal immunity aggregate flushes when tribe changes."""
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ti': 1},
                            {'ti': 1},
                            {'tribe': 'Belo'},
                            {'ti': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        ti = [e for e in events if e.event_type == TRIBAL_IMMUNITY]
        assert len(ti) == 2  # One per tribe phase
        assert ti[0].text == 'Won 2x tribal immunity'
        assert ti[1].text == 'Won tribal immunity'

    def test_tribal_immunity_flushed_at_merge(self):
        """Tribal immunity aggregate flushes at merge."""
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ti': 1},
                            {'ti': 1},
                            {'tribe': 'Merged'},
                        ))
        events, _ = generate_highlights(s, make_season(), merge_episode=4)
        ti = [e for e in events if e.event_type == TRIBAL_IMMUNITY]
        assert len(ti) == 1
        assert ti[0].text == 'Won 2x tribal immunity'


# ── Idol found / played ──────────────────────────────────────────────────

class TestIdols:
    def test_idol_found(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'idol': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        idols = [e for e in events if e.event_type == IDOL]
        assert len(idols) == 1
        assert idols[0].text == 'Found a Hidden Immunity Idol'

    def test_idol_played_with_nullified(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'idol_play': 1, 'nullified': 3},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        plays = [e for e in events if e.event_type == IDOL and 'Played' in e.text]
        assert len(plays) == 1
        assert plays[0].detail_text == 'nullified 3 votes'

    def test_idol_played_without_nullified(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'idol_play': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        plays = [e for e in events if e.event_type == IDOL and 'Played' in e.text]
        assert len(plays) == 1
        assert plays[0].detail_text is None


# ── Advantages ────────────────────────────────────────────────────────────

class TestAdvantages:
    def test_advantage_found_and_played(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'adv': 1},
                            {'adv_play': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        advs = [e for e in events if e.event_type == ADVANTAGE]
        assert len(advs) == 2
        assert advs[0].text == 'Found an advantage'
        assert advs[1].text == 'Played an advantage'


# ── Votes survived ────────────────────────────────────────────────────────

class TestVotesSurvived:
    def test_survived_votes(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'votes': 3},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        votes = [e for e in events if e.event_type == VOTES]
        assert len(votes) == 1
        assert votes[0].text == 'Survived 3 votes'

    def test_eliminated_same_episode_not_survived(self):
        """Votes received in the elimination episode should NOT fire 'survived'."""
        s = SimSurvivor(id=1, name='Test', voted_out_order=5, made_jury=False,
                        elimination_episode=2,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'votes': 4},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        votes = [e for e in events if e.event_type == VOTES]
        assert len(votes) == 0

    def test_cumulative_votes_detail(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'votes': 2},
                            {},
                            {'votes': 3},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        votes = [e for e in events if e.event_type == VOTES]
        assert len(votes) == 2
        assert votes[0].detail_text is None  # First time, no total
        assert votes[1].detail_text == '5 total this season'

    def test_single_vote_grammar(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'votes': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        votes = [e for e in events if e.event_type == VOTES]
        assert votes[0].text == 'Survived 1 vote'


# ── Tribe swap ────────────────────────────────────────────────────────────

class TestTribeSwap:
    def test_tribe_swap(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'tribe': 'Nami'},
                            {'tribe': 'Belo'},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        swaps = [e for e in events if e.event_type == TRIBE and 'Swapped' in e.text]
        assert len(swaps) == 1
        assert swaps[0].text == 'Swapped to Belo'
        assert swaps[0].episode == 3


# ── Merge ─────────────────────────────────────────────────────────────────

class TestMerge:
    def test_made_merge(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'tribe': 'Nami'},
                            {'tribe': 'Merged'},
                        ))
        events, _ = generate_highlights(s, make_season(), merge_episode=3)
        merge = [e for e in events if e.event_type == MERGE]
        assert len(merge) == 1
        assert merge[0].text == 'Made the merge'
        assert merge[0].episode == 3

    def test_eliminated_before_merge(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=2, made_jury=False,
                        elimination_episode=2,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'tribe': 'Nami'},
                            {'tribe': 'Merged'},
                        ))
        events, _ = generate_highlights(s, make_season(), merge_episode=3)
        merge = [e for e in events if e.event_type == MERGE]
        assert len(merge) == 0

    def test_no_merge_detected(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'tribe': 'Nami'},
                        ))
        events, _ = generate_highlights(s, make_season(), merge_episode=None)
        merge = [e for e in events if e.event_type == MERGE]
        assert len(merge) == 0


# ── Terminal events ───────────────────────────────────────────────────────

class TestTerminalEvents:
    def test_elimination(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=5, made_jury=False,
                        elimination_episode=3,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {},
                            {},
                        ))
        season = make_season(num_players=18)
        events, _ = generate_highlights(s, season, None)
        elim = [e for e in events if e.event_type == ELIMINATION]
        assert len(elim) == 1
        assert '14th place' in elim[0].text
        assert elim[0].episode == 3

    def test_winner(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=18, made_jury=False,
                        elimination_episode=13,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                        ))
        season = make_season(num_players=18)
        events, _ = generate_highlights(s, season, None)
        winner = [e for e in events if e.event_type == WINNER]
        assert len(winner) == 1
        assert winner[0].text == 'Won Sole Survivor'

    def test_jury_member(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=12, made_jury=True,
                        elimination_episode=8,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                        ))
        events, _ = generate_highlights(s, make_season(), None)
        jury = [e for e in events if e.event_type == JURY]
        assert len(jury) == 1
        assert jury[0].text == 'Became a jury member'

    def test_fire_making_win(self):
        # Fire loser is 4th place (voted_out_order=15 with 18 players, 3 finalists)
        fire_loser = SimSurvivor(id=2, name='Loser', voted_out_order=15,
                                 made_jury=False, elimination_episode=12)
        winner = SimSurvivor(id=1, name='Winner', voted_out_order=18,
                             made_jury=False, won_fire=True, elimination_episode=13,
                             episode_stats=ep_stats({'tribe': 'Nami'}))
        season = make_season(num_players=18, n_finalists=3,
                             survivors=[winner, fire_loser])
        events, _ = generate_highlights(winner, season, None)
        fire = [e for e in events if e.event_type == FIRE]
        assert len(fire) == 1
        assert fire[0].text == 'Won fire-making challenge'
        assert fire[0].episode == 12  # Fire loser's elimination ep

    def test_fire_making_loser(self):
        """4th place finisher lost fire — should say 'Lost fire-making' not 'Voted out'."""
        fire_loser = SimSurvivor(id=1, name='Rizo', voted_out_order=15,
                                 made_jury=False, elimination_episode=12,
                                 episode_stats=ep_stats({'tribe': 'Nami'}))
        season = make_season(num_players=18, n_finalists=3,
                             survivors=[fire_loser])
        events, _ = generate_highlights(fire_loser, season, None)
        # Should NOT have "Voted out"
        voted_out = [e for e in events if e.event_type == ELIMINATION]
        assert len(voted_out) == 0
        # Should have "Lost fire-making challenge"
        fire = [e for e in events if e.event_type == FIRE and 'Lost' in e.text]
        assert len(fire) == 1
        assert fire[0].text == 'Lost fire-making challenge — 4th place'


# ── as_of_episode filtering ──────────────────────────────────────────────

class TestAsOfFiltering:
    def test_events_after_as_of_excluded(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1},
                            {},
                            {'ii': 1},
                        ))
        events, _ = generate_highlights(s, make_season(), None, as_of_episode=2)
        imm = [e for e in events if e.event_type == IMMUNITY]
        assert len(imm) == 1  # Only ep 2, not ep 4

    def test_terminal_events_excluded_after_as_of(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=5, made_jury=True,
                        elimination_episode=5,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {},
                            {},
                        ))
        events, _ = generate_highlights(s, make_season(), None, as_of_episode=3)
        elim = [e for e in events if e.event_type == ELIMINATION]
        jury = [e for e in events if e.event_type == JURY]
        assert len(elim) == 0
        assert len(jury) == 0

    def test_none_as_of_shows_all(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=5, made_jury=False,
                        elimination_episode=3,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {},
                            {},
                        ))
        events, _ = generate_highlights(s, make_season(num_players=18), None)
        elim = [e for e in events if e.event_type == ELIMINATION]
        assert len(elim) == 1


# ── Badge generation ──────────────────────────────────────────────────────

class TestBadges:
    def test_priority_ordering(self):
        """Immunity > idol > merge > votes > advantage."""
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1, 'idol': 1, 'votes': 2, 'adv': 1},
                        ))
        _, badges = generate_highlights(s, make_season(), merge_episode=2)
        # Immunity, Idol, Merge, Votes — in that priority order
        assert len(badges) == 4
        assert badges[0].css_class == IMMUNITY
        assert badges[1].css_class == IDOL
        assert badges[2].css_class == MERGE
        assert badges[3].css_class == VOTES

    def test_max_4_badges(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        won_fire=True, elimination_episode=13,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1, 'idol': 1, 'idol_play': 1, 'votes': 2, 'adv': 1},
                        ))
        season = make_season(num_players=18, n_finalists=3, survivors=[
            SimSurvivor(id=2, name='X', voted_out_order=15, made_jury=False,
                        elimination_episode=12)])
        _, badges = generate_highlights(s, season, merge_episode=2)
        assert len(badges) <= 4

    def test_immunity_aggregated(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'ii': 1},
                            {},
                            {'ii': 1},
                            {},
                            {'ii': 1},
                        ))
        _, badges = generate_highlights(s, make_season(), None)
        imm = [b for b in badges if b.css_class == IMMUNITY]
        assert len(imm) == 1
        assert imm[0].label == '3x Immunity'

    def test_votes_aggregated(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                            {'votes': 2},
                            {},
                            {'votes': 3},
                        ))
        _, badges = generate_highlights(s, make_season(), None)
        votes = [b for b in badges if b.css_class == VOTES]
        assert len(votes) == 1
        assert votes[0].label == 'Survived 5 Votes'

    def test_no_badges_no_events(self):
        s = SimSurvivor(id=1, name='Test', voted_out_order=0, made_jury=False,
                        episode_stats=ep_stats(
                            {'tribe': 'Nami'},
                        ))
        _, badges = generate_highlights(s, make_season(), None)
        assert len(badges) == 0
