"""Character Journey Cards — auto-generated narrative highlights from episode_stats.

Data flow:
    episode_stats JSON ──┐
    (per-survivor,       │    ┌──────────────────────┐
     immutable during    ├───►│  generate_highlights  │──► (events, badges)
     request)            │    └──────────────────────┘
    Survivor ORM attrs ──┘         ▲           ▲
    (voted_out_order,              │           │
     elimination_ep,          merge_ep    as_of_ep
     made_jury, won_fire)    (from route)  (from route)

IMPORTANT: Do not move highlight generation into _build_leaderboard().
That function is also called in a loop for past_winner_badges, which
would cause O(N × survivors × episodes) wasted computation.
"""

from collections import namedtuple

Event = namedtuple("Event", ["episode", "event_type", "text", "detail_text"])

# Badge display: (label, css_class)
Badge = namedtuple("Badge", ["label", "css_class"])

# Event types (used for CSS color-coding)
IMMUNITY = "immunity"
TRIBAL_IMMUNITY = "tribal_immunity"
IDOL = "idol"
ADVANTAGE = "advantage"
VOTES = "votes"
TRIBE = "tribe"
MERGE = "merge"
ELIMINATION = "elimination"
WINNER = "winner"
JURY = "jury"
FIRE = "fire"


def generate_highlights(survivor, season, merge_episode, as_of_episode=None):
    """Generate narrative events and compact badges for a survivor's journey.

    Args:
        survivor: Survivor ORM object (or SimSurvivor for tests).
        season: Season ORM object (or SimSeason for tests).
        merge_episode: Episode number where merge occurred (int or None).
        as_of_episode: If set, truncate events to this episode (int or None).
            This is an episode number, not an elimination count.

    Returns:
        (events, badges): events is a list of Event namedtuples sorted by episode,
            badges is a list of Badge namedtuples (max 4).
    """
    ep_stats = survivor.get_episode_stats()
    if not ep_stats:
        return [], []

    events = []
    episodes = sorted(int(k) for k in ep_stats)

    if as_of_episode is not None:
        episodes = [ep for ep in episodes if ep <= as_of_episode]

    if not episodes:
        return [], []

    # Track aggregates for detail text
    total_ii = 0
    total_votes_survived = 0
    # Tribal immunity: aggregate per tribe phase, flush on swap/merge/end
    ti_phase_count = 0
    ti_phase_start = None
    ti_phase_end = None

    def _flush_tribal_immunity():
        """Emit aggregated tribal immunity event for the current tribe phase."""
        nonlocal ti_phase_count, ti_phase_start, ti_phase_end
        if ti_phase_count > 0:
            if ti_phase_count == 1:
                text = "Won tribal immunity"
            else:
                text = f"Won {ti_phase_count}x tribal immunity"
            ep_range = (
                f"Ep {ti_phase_start}–{ti_phase_end}"
                if ti_phase_start != ti_phase_end
                else None
            )
            events.append(Event(ti_phase_end, TRIBAL_IMMUNITY, text, ep_range))
        ti_phase_count = 0
        ti_phase_start = None
        ti_phase_end = None

    prev = {}
    for ep in episodes:
        cur = ep_stats[str(ep)]

        if not prev:
            # First episode: "Started on [tribe]"
            tribe = cur.get("tribe", "")
            if tribe:
                events.append(Event(ep, TRIBE, f"Started on {tribe}", None))
            prev = cur
            continue

        # --- Diff-based event detection ---

        # Individual immunity win
        ii_delta = cur.get("ii", 0) - prev.get("ii", 0)
        if ii_delta > 0:
            total_ii += ii_delta
            detail = None
            if total_ii > 1:
                detail = f"{_ordinal(total_ii)} win this season"
            events.append(Event(ep, IMMUNITY, "Won individual immunity", detail))

        # Tribal immunity win (aggregated per tribe phase)
        ti_delta = cur.get("ti", 0) - prev.get("ti", 0)
        if ti_delta > 0:
            ti_phase_count += ti_delta
            if ti_phase_start is None:
                ti_phase_start = ep
            ti_phase_end = ep

        # Idol found
        idol_delta = cur.get("idol", 0) - prev.get("idol", 0)
        if idol_delta > 0:
            events.append(Event(ep, IDOL, "Found a Hidden Immunity Idol", None))

        # Idol played
        idol_play_delta = cur.get("idol_play", 0) - prev.get("idol_play", 0)
        if idol_play_delta > 0:
            nullified_delta = cur.get("nullified", 0) - prev.get("nullified", 0)
            detail = (
                f"nullified {nullified_delta} vote{'s' if nullified_delta != 1 else ''}"
                if nullified_delta > 0
                else None
            )
            events.append(Event(ep, IDOL, "Played an idol", detail))

        # Advantage found
        adv_delta = cur.get("adv", 0) - prev.get("adv", 0)
        if adv_delta > 0:
            events.append(Event(ep, ADVANTAGE, "Found an advantage", None))

        # Advantage played
        adv_play_delta = cur.get("adv_play", 0) - prev.get("adv_play", 0)
        if adv_play_delta > 0:
            events.append(Event(ep, ADVANTAGE, "Played an advantage", None))

        # Votes received and survived
        votes_delta = cur.get("votes", 0) - prev.get("votes", 0)
        if votes_delta > 0 and survivor.elimination_episode != ep:
            total_votes_survived += votes_delta
            detail = None
            if total_votes_survived > votes_delta:
                detail = f"{total_votes_survived} total this season"
            events.append(
                Event(
                    ep,
                    VOTES,
                    f"Survived {votes_delta} vote{'s' if votes_delta != 1 else ''}",
                    detail,
                )
            )

        # Tribe swap — flush tribal immunity aggregate before recording swap
        cur_tribe = cur.get("tribe", "")
        prev_tribe = prev.get("tribe", "")
        if cur_tribe and prev_tribe and cur_tribe != prev_tribe:
            _flush_tribal_immunity()
            events.append(Event(ep, TRIBE, f"Swapped to {cur_tribe}", None))

        # Merge — flush tribal immunity before merge (tribal challenges end at merge)
        if merge_episode and ep == merge_episode:
            _flush_tribal_immunity()
            still_in = survivor.voted_out_order == 0 or (
                survivor.elimination_episode is not None
                and survivor.elimination_episode > ep
            )
            if still_in:
                events.append(Event(ep, MERGE, "Made the merge", None))

        prev = cur

    # Flush any remaining tribal immunity from the last phase
    _flush_tribal_immunity()

    # --- Terminal events ---
    # Gate on elimination_episode (never modified by _apply_as_of), not voted_out_order
    _add_terminal_events(events, survivor, season, as_of_episode)

    events.sort(key=lambda e: (e.episode or 0, _event_type_order(e.event_type)))

    badges = _generate_badges(events, total_ii, total_votes_survived)

    return events, badges


def _add_terminal_events(events, survivor, season, as_of_episode):
    """Add terminal events (elimination, winner, jury, fire) if within as_of range."""
    elim_ep = survivor.elimination_episode

    # If as_of is set, only show terminal events that happened by that episode
    if as_of_episode is not None and elim_ep is not None and elim_ep > as_of_episode:
        return

    num_players = season.num_players or 0
    is_winner = survivor.voted_out_order == num_players and num_players > 0

    if survivor.won_fire:
        # Fire-making win — use elimination_episode of fire loser (4th place)
        # Approximate: fire happens at the finalist threshold episode
        fire_ep = elim_ep  # Winner's elim_ep is the finale
        # Look for the fire loser (4th place with 3 finalists)
        n_finalists = season.n_finalists
        if n_finalists and num_players:
            fire_threshold = num_players - n_finalists
            for s in season.survivors:
                if s.voted_out_order == fire_threshold and s.elimination_episode:
                    fire_ep = s.elimination_episode
                    break
        if as_of_episode is None or (fire_ep and fire_ep <= as_of_episode):
            events.append(Event(fire_ep, FIRE, "Won fire-making challenge", None))

    if is_winner:
        events.append(Event(elim_ep, WINNER, "Won Sole Survivor", None))
    elif survivor.voted_out_order > 0:
        place = (
            _ordinal(num_players - survivor.voted_out_order + 1)
            if num_players
            else None
        )
        place_text = f" — {place} place" if place else ""
        # Fire loser: eliminated by losing fire-making challenge, not voted out
        n_finalists = season.n_finalists
        is_fire_loser = (
            n_finalists
            and num_players
            and survivor.voted_out_order == num_players - n_finalists
        )
        if is_fire_loser:
            events.append(
                Event(elim_ep, FIRE, f"Lost fire-making challenge{place_text}", None)
            )
        else:
            events.append(Event(elim_ep, ELIMINATION, f"Voted out{place_text}", None))

    if survivor.made_jury:
        events.append(Event(elim_ep, JURY, "Became a jury member", None))


def _generate_badges(events, total_ii, total_votes_survived):
    """Generate max 4 compact badges from events, ordered by priority."""
    badges = []

    # Priority 1: Immunity wins (aggregated)
    if total_ii > 0:
        label = f"{total_ii}x Immunity" if total_ii > 1 else "Won Immunity"
        badges.append(Badge(label, IMMUNITY))

    # Priority 2: Idol found/played (separate badges)
    idol_found = sum(1 for e in events if e.event_type == IDOL and "Found" in e.text)
    idol_played = sum(1 for e in events if e.event_type == IDOL and "Played" in e.text)
    if idol_found:
        badges.append(
            Badge(
                "Idol Found" if idol_found == 1 else f"{idol_found}x Idol Found", IDOL
            )
        )
    if idol_played:
        badges.append(
            Badge(
                "Idol Played" if idol_played == 1 else f"{idol_played}x Idol Played",
                IDOL,
            )
        )

    # Priority 3: Made merge
    if any(e.event_type == MERGE for e in events):
        badges.append(Badge("Made Merge", MERGE))

    # Priority 4: Votes survived (aggregated)
    if total_votes_survived > 0:
        badges.append(
            Badge(
                f"Survived {total_votes_survived} Vote{'s' if total_votes_survived != 1 else ''}",
                VOTES,
            )
        )

    # Priority 5: Advantage found/played
    adv_count = sum(1 for e in events if e.event_type == ADVANTAGE)
    if adv_count:
        badges.append(
            Badge(
                f"{adv_count}x Advantage" if adv_count > 1 else "Advantage", ADVANTAGE
            )
        )

    # Priority 6: Fire-making win
    if any(e.event_type == FIRE for e in events):
        badges.append(Badge("Won Fire", FIRE))

    return badges[:4]


def _ordinal(n):
    """Return ordinal string: 1 → '1st', 2 → '2nd', etc."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _event_type_order(event_type):
    """Sort order for events within the same episode."""
    order = {
        TRIBE: 0,
        MERGE: 1,
        IMMUNITY: 2,
        TRIBAL_IMMUNITY: 3,
        IDOL: 4,
        ADVANTAGE: 5,
        VOTES: 6,
        FIRE: 7,
        JURY: 8,
        ELIMINATION: 9,
        WINNER: 10,
    }
    return order.get(event_type, 99)
