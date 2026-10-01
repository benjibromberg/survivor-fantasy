"""Refresh season data from the survivoR dataset (open-source, hosted on GitHub)."""

import hashlib
import io
import json
import logging
import os
import time
from urllib.parse import quote

import pandas as pd
import requests

from .models import Pick, Season, SoleSurvivorPick, Survivor, TeamName, User, db

logger = logging.getLogger(__name__)

SURVIVOR_DATA_URL = (
    "https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx"
)


# Store survivoR.xlsx alongside the database so Docker's appuser can write it.
# In Docker: DATABASE_URL=sqlite:////app/data/survivor_fantasy.db → /app/data/survivoR.xlsx
# Locally:   default DB is ./survivor_fantasy.db → ./survivoR.xlsx (unchanged behavior)
def _data_dir():
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "", 1)
        return os.path.dirname(db_path) or "."
    return "."


SURVIVOR_DATA_FILE = os.path.join(_data_dir(), "survivoR.xlsx")


def _build_nickname_map():
    """Build castaway_id → most common name across all US seasons.

    For returning players who appear with different names (e.g. Oscar vs Ozzy),
    uses the name that appears most often across their seasons.
    """
    from collections import Counter

    castaways = pd.read_excel(SURVIVOR_DATA_FILE, "Castaways")
    us = castaways[castaways["version"] == "US"]
    result = {}
    for cid, group in us.groupby("castaway_id"):
        names = group["castaway"].tolist()
        if len(set(names)) > 1:
            result[cid] = Counter(names).most_common(1)[0][0]
    return result


def us_season_filter(df, season_number):
    """Filter a DataFrame to a specific US season."""
    return df[(df["version"] == "US") & (df["season"] == season_number)]


def get_idol_ids(advantage_details, season_number=None):
    """Get the set of advantage_ids that are Hidden Immunity Idols.

    Args:
        advantage_details: Advantage Details DataFrame.
        season_number: If given, filter to this season only.
    """
    if advantage_details.empty:
        return set()
    mask = advantage_details["advantage_type"].str.contains(
        "Idol", case=False, na=False
    )
    if season_number is not None:
        mask = mask & (advantage_details["season"] == season_number)
    return set(advantage_details[mask]["advantage_id"])


def get_fire_winners(vote_history):
    """Get fire challenge winner rows from a vote history DataFrame.

    Returns filtered DataFrame. Callers can build set(castaway_id) or
    set(zip(season, castaway_id)) as needed.
    """
    if vote_history.empty or "vote_event" not in vote_history.columns:
        return vote_history.iloc[0:0]
    return vote_history[
        vote_history["vote_event"].str.contains("Fire", case=False, na=False)
        & (vote_history["vote_event_outcome"] == "Won")
    ]


def votes_cast_rows(s_vh):
    """Vote History rows where the castaway actually cast a vote.

    Vote History has one row per castaway per vote: castaway_id is the voter,
    vote_id is who they voted for, and voted_out_id is who left at that tribal
    (the same on every row of the tribal). vote_id is empty when no vote was
    cast (Shot in the Dark, a lost vote, the fire-making challenge).
    """
    return s_vh[s_vh["vote_id"].notna()]


def correct_vote_rows(s_vh):
    """Vote History rows where the castaway voted for the person who left."""
    cast = votes_cast_rows(s_vh)
    return cast[cast["vote_id"] == cast["voted_out_id"]]


def compute_castaway_stats(s_conf, s_vh, s_cr, s_am, idol_ids):
    """Compute per-castaway aggregate stats from pre-filtered season DataFrames.

    Returns dict with: conf_totals, votes_against, votes_cast, correct_votes,
    indiv_imm, tribal_imm, idols_found, idols_played, adv_found (non-idol),
    adv_played (non-idol).
    """
    conf_totals = (
        s_conf.groupby("castaway_id")["confessional_count"].sum()
        if not s_conf.empty
        else pd.Series(dtype=int)
    )
    # Votes received are counted on vote_id (the target of each vote). Grouping
    # on voted_out_id would credit the boot with every row of their tribal.
    if not s_vh.empty:
        votes_against = votes_cast_rows(s_vh).groupby("vote_id").size()
        votes_cast = votes_cast_rows(s_vh).groupby("castaway_id").size()
        correct_votes = correct_vote_rows(s_vh).groupby("castaway_id").size()
    else:
        votes_against = votes_cast = correct_votes = pd.Series(dtype=int)
    indiv_imm = (
        s_cr[s_cr["won_individual_immunity"] == 1].groupby("castaway_id").size()
        if not s_cr.empty
        else pd.Series(dtype=int)
    )
    tribal_imm = (
        s_cr[s_cr["won_tribal_immunity"] == 1].groupby("castaway_id").size()
        if not s_cr.empty
        else pd.Series(dtype=int)
    )

    if not s_am.empty:
        idols_found = (
            s_am[
                s_am["event"].str.contains("Found", na=False)
                & s_am["advantage_id"].isin(idol_ids)
            ]
            .groupby("castaway_id")
            .size()
        )
        idols_played = (
            s_am[(s_am["event"] == "Played") & s_am["advantage_id"].isin(idol_ids)]
            .groupby("castaway_id")
            .size()
        )
        adv_found = (
            s_am[
                s_am["event"].str.contains("Found", na=False)
                & ~s_am["advantage_id"].isin(idol_ids)
            ]
            .groupby("castaway_id")
            .size()
        )
        adv_played = (
            s_am[(s_am["event"] == "Played") & ~s_am["advantage_id"].isin(idol_ids)]
            .groupby("castaway_id")
            .size()
        )
    else:
        idols_found = idols_played = adv_found = adv_played = pd.Series(dtype=int)

    return {
        "conf_totals": conf_totals,
        "votes_against": votes_against,
        "votes_cast": votes_cast,
        "correct_votes": correct_votes,
        "indiv_imm": indiv_imm,
        "tribal_imm": tribal_imm,
        "idols_found": idols_found,
        "idols_played": idols_played,
        "adv_found": adv_found,
        "adv_played": adv_played,
    }


def download_survivor_data():
    """Download the latest survivoR.xlsx from GitHub."""
    resp = requests.get(SURVIVOR_DATA_URL, timeout=60)
    resp.raise_for_status()
    with open(SURVIVOR_DATA_FILE, "wb") as f:
        f.write(resp.content)
    logger.info("Downloaded latest survivoR.xlsx")


def refresh_season(season):
    """Update a season's survivor data from survivoR.xlsx.

    Updates boot order, jury status, placement, results, challenge stats,
    confessionals, votes, advantages, and per-episode cumulative stats.
    """
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, "Castaways")
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, "Confessionals")
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, "Vote History")
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, "Challenge Results")
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, "Advantage Movement")
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, "Advantage Details")
    tribe_mapping = pd.read_excel(SURVIVOR_DATA_FILE, "Tribe Mapping")
    tribe_colours = pd.read_excel(SURVIVOR_DATA_FILE, "Tribe Colours")
    castaway_scores = pd.read_excel(SURVIVOR_DATA_FILE, "Castaway Scores")
    jury_votes = pd.read_excel(SURVIVOR_DATA_FILE, "Jury Votes")
    castaway_details = pd.read_excel(SURVIVOR_DATA_FILE, "Castaway Details")

    def us(df):
        return us_season_filter(df, season.number)

    cast = us(castaways)
    if cast.empty:
        raise ValueError(f"No survivoR data for season {season.number}")

    # Build lookups
    cast_by_id = {row["castaway_id"]: row for _, row in cast.iterrows()}

    # Bio data from Castaway Details (keyed by castaway_id, no version column)
    details_by_id = {
        row["castaway_id"]: row
        for _, row in castaway_details.iterrows()
        if pd.notna(row.get("castaway_id"))
    }

    # --- Season totals ---
    s_conf = us(confessionals)
    s_vh = us(vote_history)
    s_cr = us(challenge_results)
    s_am = us(advantage_movement)

    idol_ids = get_idol_ids(advantage_details, season.number)
    stats = compute_castaway_stats(s_conf, s_vh, s_cr, s_am, idol_ids)
    conf_totals = stats["conf_totals"]
    votes_against = stats["votes_against"]
    votes_cast_totals = stats["votes_cast"]
    correct_totals = stats["correct_votes"]
    indiv_imm = stats["indiv_imm"]
    tribal_imm = stats["tribal_imm"]
    idols_found = stats["idols_found"]
    idols_played = stats["idols_played"]
    adv_found = stats["adv_found"]
    adv_played = stats["adv_played"]

    # Additional stats beyond the shared computation
    conf_time_totals = (
        s_conf.groupby("castaway_id")["confessional_time"].sum()
        if "confessional_time" in s_conf.columns
        else pd.Series(dtype=float)
    )
    reward_wins = s_cr[s_cr["won"] == 1].groupby("castaway_id").size()

    # Tribal councils attended (each row in vote_history = one castaway at one tribal)
    tribals_attended = (
        s_vh.groupby("castaway_id")["episode"].apply(
            lambda x: x.drop_duplicates().count()
        )
        if not s_vh.empty
        else pd.Series(dtype=int)
    )

    # Votes cast, and the subset that were for the person actually voted out
    votes_cast = votes_cast_rows(s_vh) if not s_vh.empty else s_vh
    correct = correct_vote_rows(s_vh) if not s_vh.empty else s_vh

    # Votes nullified by idol plays
    nullified_totals = pd.Series(dtype=int)
    if not s_am.empty:
        played_rows = s_am[s_am["event"] == "Played"]
        if "votes_nullified" in played_rows.columns:
            nullified_totals = (
                played_rows.groupby("castaway_id")["votes_nullified"]
                .sum()
                .dropna()
                .astype(int)
            )

    # Sit-outs
    sit_out_totals = (
        s_cr[s_cr["sit_out"] == 1].groupby("castaway_id").size()
        if not s_cr.empty and "sit_out" in s_cr.columns
        else pd.Series(dtype=int)
    )

    # Fire challenge winners
    fire_winners = set(get_fire_winners(s_vh)["castaway_id"])

    # Jury votes received (for finalists)
    s_jv = us(jury_votes)
    jury_vote_totals = (
        s_jv[s_jv["vote"] == 1].groupby("finalist_id").size()
        if not s_jv.empty and "vote" in s_jv.columns
        else pd.Series(dtype=int)
    )

    # Performance scores
    s_scores = us(castaway_scores)
    perf_scores = (
        {row["castaway_id"]: row.get("score_overall") for _, row in s_scores.iterrows()}
        if not s_scores.empty
        else {}
    )

    # Tribe mapping & colours for per-episode tribe tracking
    s_tm = us(tribe_mapping)
    s_tc = us(tribe_colours)
    tribe_color_map = (
        {row["tribe"]: row["tribe_colour"] for _, row in s_tc.iterrows()}
        if not s_tc.empty
        else {}
    )

    # --- Per-episode incremental data ---
    # Include Tribe Mapping episodes so episode_stats covers merge even when
    # confessional/challenge/vote data hasn't been published yet.
    all_episodes = set()
    if not s_conf.empty:
        all_episodes.update(s_conf["episode"].dropna().astype(int))
    if not s_cr.empty:
        all_episodes.update(s_cr["episode"].dropna().astype(int))
    if not s_vh.empty:
        all_episodes.update(s_vh["episode"].dropna().astype(int))
    if not s_tm.empty:
        all_episodes.update(s_tm["episode"].dropna().astype(int))
    max_episode = max(all_episodes) if all_episodes else 0

    def _ep_counts(df, castaway_col, episode_col, filter_fn=None):
        """Return {castaway_id: {episode: count}} from a DataFrame."""
        if df.empty:
            return {}
        filtered = filter_fn(df) if filter_fn else df
        if filtered.empty:
            return {}
        grouped = filtered.groupby([castaway_col, episode_col]).size()
        result = {}
        for (cid, ep), count in grouped.items():
            result.setdefault(cid, {})[int(ep)] = int(count)
        return result

    def _ep_sums(df, castaway_col, episode_col, value_col, filter_fn=None):
        """Return {castaway_id: {episode: sum}} from a DataFrame."""
        if df.empty:
            return {}
        filtered = filter_fn(df) if filter_fn else df
        if filtered.empty:
            return {}
        grouped = filtered.groupby([castaway_col, episode_col])[value_col].sum()
        result = {}
        for (cid, ep), val in grouped.items():
            result.setdefault(cid, {})[int(ep)] = float(val) if pd.notna(val) else 0
        return result

    # Confessionals per episode
    conf_by_ep = {}
    conf_time_by_ep = {}
    if not s_conf.empty:
        for _, r in s_conf.iterrows():
            cid, ep = r["castaway_id"], int(r["episode"])
            conf_by_ep.setdefault(cid, {})[ep] = conf_by_ep.get(cid, {}).get(
                ep, 0
            ) + int(r["confessional_count"])
            if "confessional_time" in r and pd.notna(r["confessional_time"]):
                conf_time_by_ep.setdefault(cid, {})[ep] = conf_time_by_ep.get(
                    cid, {}
                ).get(ep, 0) + float(r["confessional_time"])

    votes_by_ep = _ep_counts(s_vh, "vote_id", "episode")
    ii_by_ep = _ep_counts(
        s_cr,
        "castaway_id",
        "episode",
        lambda df: df[df["won_individual_immunity"] == 1],
    )
    ti_by_ep = _ep_counts(
        s_cr, "castaway_id", "episode", lambda df: df[df["won_tribal_immunity"] == 1]
    )
    reward_by_ep = _ep_counts(
        s_cr, "castaway_id", "episode", lambda df: df[df["won"] == 1]
    )
    sit_out_by_ep = (
        _ep_counts(s_cr, "castaway_id", "episode", lambda df: df[df["sit_out"] == 1])
        if "sit_out" in s_cr.columns
        else {}
    )

    idol_found_by_ep = (
        _ep_counts(
            s_am,
            "castaway_id",
            "episode",
            lambda df: df[
                df["event"].str.contains("Found", na=False)
                & df["advantage_id"].isin(idol_ids)
            ],
        )
        if not s_am.empty
        else {}
    )
    idol_play_by_ep = (
        _ep_counts(
            s_am,
            "castaway_id",
            "episode",
            lambda df: df[
                (df["event"] == "Played") & df["advantage_id"].isin(idol_ids)
            ],
        )
        if not s_am.empty
        else {}
    )
    adv_found_by_ep = (
        _ep_counts(
            s_am,
            "castaway_id",
            "episode",
            lambda df: df[
                df["event"].str.contains("Found", na=False)
                & ~df["advantage_id"].isin(idol_ids)
            ],
        )
        if not s_am.empty
        else {}
    )
    adv_played_by_ep = (
        _ep_counts(
            s_am,
            "castaway_id",
            "episode",
            lambda df: df[
                (df["event"] == "Played") & ~df["advantage_id"].isin(idol_ids)
            ],
        )
        if not s_am.empty
        else {}
    )
    nullified_by_ep = (
        _ep_sums(
            s_am,
            "castaway_id",
            "episode",
            "votes_nullified",
            lambda df: df[df["event"] == "Played"],
        )
        if not s_am.empty and "votes_nullified" in s_am.columns
        else {}
    )

    # Tribals attended per episode (unique episode counts per castaway)
    tribals_by_ep = _ep_counts(s_vh, "castaway_id", "episode")
    # Votes cast and correct votes per episode
    votes_cast_by_ep = (
        _ep_counts(votes_cast, "castaway_id", "episode") if not votes_cast.empty else {}
    )
    correct_by_ep = (
        _ep_counts(correct, "castaway_id", "episode") if not correct.empty else {}
    )

    # Tribe per castaway per episode + detect merge episode from tribe_status
    tribe_by_ep = {}  # {castaway_id: {episode: (tribe, color, tribe_status)}}
    detected_merge_ep = None
    if not s_tm.empty:
        for _, r in s_tm.iterrows():
            cid, ep = r["castaway_id"], int(r["episode"])
            tribe_name = r["tribe"]
            tribe_status = r["tribe_status"] if pd.notna(r.get("tribe_status")) else ""
            tribe_by_ep.setdefault(cid, {})[ep] = (
                tribe_name,
                tribe_color_map.get(tribe_name, ""),
                tribe_status,
            )
            if tribe_status == "Merged" and (
                detected_merge_ep is None or ep < detected_merge_ep
            ):
                detected_merge_ep = ep
    season.merge_episode_num = detected_merge_ep

    def _build_cumulative(cid):
        """Build cumulative stats dict {episode_str: {...}} for a castaway."""
        cumulative = {}
        running = {
            "conf": 0,
            "conf_time": 0,
            "ii": 0,
            "ti": 0,
            "reward": 0,
            "idol": 0,
            "idol_play": 0,
            "adv": 0,
            "adv_play": 0,
            "votes": 0,
            "tribals": 0,
            "votes_cast": 0,
            "correct_votes": 0,
            "nullified": 0,
            "sit_outs": 0,
        }
        last_tribe = ("", "", "")
        for ep in range(1, max_episode + 1):
            running["conf"] += conf_by_ep.get(cid, {}).get(ep, 0)
            running["conf_time"] += conf_time_by_ep.get(cid, {}).get(ep, 0)
            running["votes"] += votes_by_ep.get(cid, {}).get(ep, 0)
            running["ii"] += ii_by_ep.get(cid, {}).get(ep, 0)
            running["ti"] += ti_by_ep.get(cid, {}).get(ep, 0)
            running["reward"] += reward_by_ep.get(cid, {}).get(ep, 0)
            running["idol"] += idol_found_by_ep.get(cid, {}).get(ep, 0)
            running["idol_play"] += idol_play_by_ep.get(cid, {}).get(ep, 0)
            running["adv"] += adv_found_by_ep.get(cid, {}).get(ep, 0)
            running["adv_play"] += adv_played_by_ep.get(cid, {}).get(ep, 0)
            running["nullified"] += int(nullified_by_ep.get(cid, {}).get(ep, 0))
            running["sit_outs"] += sit_out_by_ep.get(cid, {}).get(ep, 0)
            # Tribals: count 1 if they appeared in vote history this episode
            if tribals_by_ep.get(cid, {}).get(ep, 0) > 0:
                running["tribals"] += 1
            running["votes_cast"] += votes_cast_by_ep.get(cid, {}).get(ep, 0)
            running["correct_votes"] += correct_by_ep.get(cid, {}).get(ep, 0)
            # Tribe at this episode
            if ep in tribe_by_ep.get(cid, {}):
                last_tribe = tribe_by_ep[cid][ep]
            ep_data = dict(running)
            ep_data["tribe"] = last_tribe[0]
            ep_data["tribe_color"] = last_tribe[1]
            ep_data["tribe_status"] = last_tribe[2]
            cumulative[str(ep)] = ep_data
        return cumulative

    # Apply nicknames for returning players (e.g. Oscar → Ozzy)
    nickname_map = _build_nickname_map()

    # Build lookup of existing survivors by castaway_id
    existing = {
        s.castaway_id: s
        for s in Survivor.query.filter_by(season_id=season.id).all()
        if s.castaway_id
    }

    # Season metadata from survivoR (required for new-era seasons 41+)
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, "Season Summary")
    ss = season_summary[
        (season_summary["version"] == "US")
        & (season_summary["season"] == season.number)
    ]
    if not ss.empty:
        row = ss.iloc[0]
        if pd.isna(row["n_cast"]):
            raise ValueError(
                f"Season {season.number}: survivoR data missing n_cast. Only new-era seasons (41+) are supported."
            )
        n_cast = int(row["n_cast"])
        # For in-progress seasons, n_jury/n_finalists may be NaN — store as None
        n_finalists = int(row["n_finalists"]) if pd.notna(row["n_finalists"]) else None
        n_jury = int(row["n_jury"]) if pd.notna(row["n_jury"]) else None
        season.num_players = n_cast
        season.n_finalists = n_finalists
        if n_jury is not None and n_finalists is not None:
            season.left_at_jury = n_jury + n_finalists
        else:
            season.left_at_jury = None
        # Update season name if still default
        if season.name == f"Season {season.number}":
            raw_name = (
                ss.iloc[0]["season_name"] if pd.notna(ss.iloc[0]["season_name"]) else ""
            )
            if ":" in str(raw_name):
                clean = raw_name.split(":")[-1].strip()
                season.name = f"Season {clean}" if clean.isdigit() else clean
            elif raw_name:
                season.name = raw_name

    updated = 0
    for cid, row in cast_by_id.items():
        surv = existing.get(cid)
        if not surv:
            # Create new survivor
            name = nickname_map.get(cid, row["castaway"])
            tribe = (
                row["original_tribe"] if pd.notna(row.get("original_tribe")) else None
            )
            surv = Survivor(
                season_id=season.id,
                name=name,
                full_name=row["full_name"] if pd.notna(row.get("full_name")) else None,
                castaway_id=cid,
                version_season=row["version_season"]
                if pd.notna(row.get("version_season"))
                else None,
                tribe=tribe,
                tribe_color=tribe_color_map.get(tribe),
            )
            db.session.add(surv)
            db.session.flush()

        # Use most common name for returning players
        if cid in nickname_map:
            surv.name = nickname_map[cid]

        # Core game data
        surv.voted_out_order = int(row["order"]) if pd.notna(row["order"]) else 0
        surv.made_jury = bool(row["jury"]) if pd.notna(row["jury"]) else False
        surv.placement = int(row["place"]) if pd.notna(row["place"]) else None
        surv.result = row["result"] if pd.notna(row["result"]) else None
        surv.elimination_episode = (
            int(row["episode"]) if pd.notna(row.get("episode")) else None
        )
        surv.day_voted_out = int(row["day"]) if pd.notna(row.get("day")) else None

        # Season totals
        surv.confessional_count = int(conf_totals.get(cid, 0))
        surv.confessional_time = float(conf_time_totals.get(cid, 0))
        surv.votes_received = int(votes_against.get(cid, 0))
        surv.individual_immunity_wins = int(indiv_imm.get(cid, 0))
        surv.tribal_immunity_wins = int(tribal_imm.get(cid, 0))
        surv.immunity_wins = surv.individual_immunity_wins
        surv.reward_wins = int(reward_wins.get(cid, 0))
        surv.idols_found = int(idols_found.get(cid, 0))
        surv.idols_played = int(idols_played.get(cid, 0))
        surv.advantages_found = int(adv_found.get(cid, 0))
        surv.advantages_played = int(adv_played.get(cid, 0))
        surv.tribal_councils_attended = int(tribals_attended.get(cid, 0))
        surv.votes_cast = int(votes_cast_totals.get(cid, 0))
        surv.correct_votes = int(correct_totals.get(cid, 0))
        surv.votes_nullified = int(nullified_totals.get(cid, 0))
        surv.sit_outs = int(sit_out_totals.get(cid, 0))
        surv.won_fire = cid in fire_winners
        surv.jury_votes_received = (
            int(jury_vote_totals.get(cid, 0)) if cid in jury_vote_totals else None
        )
        score = perf_scores.get(cid)
        surv.performance_score = float(score) if pd.notna(score) else None

        # Per-episode cumulative stats
        surv.episode_stats = (
            json.dumps(_build_cumulative(cid)) if max_episode > 0 else None
        )

        # Bio from Castaways + Castaway Details
        surv.age = int(row["age"]) if pd.notna(row.get("age")) else None
        surv.city = row["city"] if pd.notna(row.get("city")) else None
        surv.state = row["state"] if pd.notna(row.get("state")) else None
        details = details_by_id.get(cid)
        if details is not None:
            surv.occupation = (
                details["occupation"] if pd.notna(details.get("occupation")) else None
            )
            surv.personality_type = (
                details["personality_type"]
                if pd.notna(details.get("personality_type"))
                else None
            )

        updated += 1

    # Validate day_voted_out monotonicity (day should not decrease as order increases)
    day_warnings = []
    ordered = sorted(
        [
            (s.voted_out_order, s.day_voted_out, s.name)
            for s in season.survivors
            if s.voted_out_order and s.voted_out_order > 0 and s.day_voted_out
        ],
        key=lambda x: x[0],
    )
    prev_day, prev_name = 0, ""
    for order, day, name in ordered:
        if day < prev_day:
            day_warnings.append(
                f"{name} (order={order}, day={day}) eliminated before {prev_name} (day={prev_day})"
            )
            # Clear bad day data for this player so scoring falls back safely
        prev_day, prev_name = day, name
    if day_warnings:
        import logging

        logger = logging.getLogger(__name__)
        for w in day_warnings:
            logger.warning("Season %d day data error: %s", season.number, w)

    db.session.commit()

    from .predictions import clear_cache

    clear_cache()
    from .routes import _compare_cache

    _compare_cache.clear()

    return updated, day_warnings


# survivoR name → image site first name (where survivoR name doesn't match URL)
NAME_TO_SITE = {
    "jelinsky": "david",
    "tk": "terran",
    "sol": "solomon",
    "j. maya": "janani",
}

# Same, but scoped to one season: {season number: {survivoR name: site name}}.
# Use this when the site name is specific to one castaway, so a same-named
# castaway in another season is unaffected.
SEASON_NAME_TO_SITE = {
    51: {"danny": "kilby"},  # filed under surname
}

HEADSHOT_URL = (
    "https://www.fantasysurvivorgame.com/images/{season}/biopics/{site_name}BIO.jpg"
)


def headshot_url_candidates(season_number, name):
    """Return the headshot URLs to try for a castaway, most likely first.

    Pure function: no network, no database. Order:
      1. season-specific override (SEASON_NAME_TO_SITE)
      2. global override (NAME_TO_SITE), else the first name
      3. the full name, for multi-word names (e.g. "Thien An")
      4. the name from step 2 wrapped in double quotes (names like Q)
    Site names are URL-encoded and duplicates are dropped.
    """
    parts = name.lower().split()
    if not parts:
        return []
    name_key = " ".join(parts)
    site_name = NAME_TO_SITE.get(name_key, parts[0])

    site_names = []
    season_override = SEASON_NAME_TO_SITE.get(season_number, {}).get(name_key)
    if season_override:
        site_names.append(season_override)
    site_names.append(site_name)
    if len(parts) > 1:
        site_names.append(name_key)
    site_names.append(f'"{site_name}"')

    return [
        HEADSHOT_URL.format(season=season_number, site_name=quote(s, safe=""))
        for s in dict.fromkeys(site_names)
    ]


# ── Self-hosted headshots ────────────────────────────────────────────────
# Remote headshots are 440 px JPEGs (~86 KB) shown at <= 80 px. We mirror each
# one once: resize to HEADSHOT_SIZE, encode as WebP, and store it on the data
# volume under a content-hashed filename so it can be cached forever. The
# volume is the only place appuser can write (see _data_dir()).
HEADSHOT_SIZE = 160  # long edge in px: 2x the largest display size (80 px)
HEADSHOT_WEBP_QUALITY = 80
HEADSHOT_URL_PREFIX = "/headshots"  # served by app/headshots.py
HEADSHOT_CONNECT_TIMEOUT = 5
HEADSHOT_READ_TIMEOUT = 15
HEADSHOT_MAX_BYTES = 5 * 1024 * 1024  # refuse anything larger than this
HEADSHOT_REQUEST_DELAY = 0.2  # seconds between network fetches (polite)


def headshots_dir():
    """Directory holding resized headshots (alongside the DB by default).

    Override with HEADSHOTS_DIR. Resolved per call so it follows DATABASE_URL.
    """
    return os.environ.get("HEADSHOTS_DIR") or os.path.join(_data_dir(), "headshots")


def _local_headshot_path(image_url):
    """Return the on-disk path for a local image_url, or None if it isn't one."""
    prefix = HEADSHOT_URL_PREFIX + "/"
    if not image_url or not image_url.startswith(prefix):
        return None
    parts = image_url[len(prefix) :].split("/")
    if len(parts) != 2 or not all(parts) or ".." in parts:
        return None
    return os.path.join(headshots_dir(), *parts)


def _download_headshot(url):
    """GET a headshot and return its bytes, or None if unavailable.

    Failures (network error, non-200, oversized body) are logged, never raised.
    """
    try:
        with requests.get(
            url,
            timeout=(HEADSHOT_CONNECT_TIMEOUT, HEADSHOT_READ_TIMEOUT),
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                return None
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > HEADSHOT_MAX_BYTES:
                    logger.warning("Headshot too large, skipping: %s", url)
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as e:
        logger.warning("Headshot download failed for %s: %s", url, e)
        return None


def _encode_headshot(raw):
    """Resize image bytes to HEADSHOT_SIZE and return WebP bytes."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((HEADSHOT_SIZE, HEADSHOT_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=HEADSHOT_WEBP_QUALITY, method=6)
        return out.getvalue()


def _store_headshot(season_number, raw):
    """Resize + store a downloaded headshot; return its local URL path.

    The filename is a hash of the source bytes, so a changed image gets a new
    URL (safe to cache forever) and an unchanged one is not rewritten.
    """
    digest = hashlib.sha256(raw).hexdigest()[:16]
    filename = f"{digest}.webp"
    season_dir = os.path.join(headshots_dir(), str(season_number))
    path = os.path.join(season_dir, filename)
    if not os.path.exists(path):
        data = _encode_headshot(raw)
        os.makedirs(season_dir, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return f"{HEADSHOT_URL_PREFIX}/{season_number}/{filename}"


def generate_season_images(season, force=False):
    """Mirror a season's headshots from fantasysurvivorgame.com onto our disk.

    For each survivor, tries headshot_url_candidates() in order and keeps the
    first that downloads and decodes. The image is resized to a small WebP in
    headshots_dir() and Survivor.image_url is set to its local path.

    Idempotent: a survivor whose image_url already points at an existing local
    file is skipped without any network call (pass force=True to re-fetch).
    Failures are logged and never raise; a survivor with no usable image keeps
    its previous image_url (None renders the letter placeholder).
    Returns number of survivors with a usable image.
    """
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    matched = 0
    fetched_any = False
    for surv in survivors:
        existing = _local_headshot_path(surv.image_url)
        if not force and existing and os.path.exists(existing):
            matched += 1
            continue

        for url in headshot_url_candidates(season.number, surv.name):
            if fetched_any:
                time.sleep(HEADSHOT_REQUEST_DELAY)
            fetched_any = True
            raw = _download_headshot(url)
            if raw is None:
                continue
            try:
                surv.image_url = _store_headshot(season.number, raw)
            except Exception as e:  # Pillow raises many types on bad data
                logger.warning(
                    "Season %d headshot unusable for %s (%s): %s",
                    season.number,
                    surv.name,
                    url,
                    e,
                )
                continue
            matched += 1
            break

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    logger.info("Season %d images: %d/%d", season.number, matched, len(survivors))
    return matched


def generate_all_season_images(force=False):
    """Run generate_season_images() for every season.

    Returns {season_number: (matched, total_survivors)}.
    """
    totals = dict(
        db.session.query(Survivor.season_id, db.func.count(Survivor.id))
        .group_by(Survivor.season_id)
        .all()
    )
    return {
        season.number: (generate_season_images(season, force), totals.get(season.id, 0))
        for season in Season.query.all()
    }


# Pick exports go alongside the database, for the same reason as survivoR.xlsx:
# in Docker the working directory (/app) is a root-owned image layer, and the
# data volume is the only place appuser can write.
# In Docker: /app/data/picks.  Locally: ./picks (unchanged behavior).
# Resolved per call rather than at import so it always follows DATABASE_URL.
def default_picks_dir():
    return os.path.join(_data_dir(), "picks")


def export_season_picks(season, picks_dir=None):
    """Export all picks for a season to a JSON file.

    Produces a file compatible with seed.py's load_picks_from_json, extended
    with sole_survivor_picks, custom scoring_config, the players' team names
    for the season, and the season's Episode 2 start time.

    Writes to picks_dir, or default_picks_dir() when not given.

    Returns the filepath written, or None if the season has no picks and no
    Sole Survivor picks.
    """
    picks_dir = os.path.realpath(picks_dir or default_picks_dir())
    os.makedirs(picks_dir, exist_ok=True)

    picks = (
        Pick.query.filter_by(season_id=season.id)
        .order_by(Pick.user_id, Pick.pick_order)
        .all()
    )
    ss_picks = (
        SoleSurvivorPick.query.filter_by(season_id=season.id)
        .order_by(SoleSurvivorPick.user_id, SoleSurvivorPick.episode)
        .all()
    )
    if not picks and not ss_picks:
        return None

    surv_by_id = {s.id: s for s in Survivor.query.filter_by(season_id=season.id)}

    # Build picks by user display name
    picks_data = {}
    for pick in picks:
        user = db.session.get(User, pick.user_id)
        name = user.display_name or user.username
        if name not in picks_data:
            picks_data[name] = []

        type_codes = {"draft": "d", "wildcard": "w", "pmr_w": "pmr_w", "pmr_d": "pmr_d"}
        surv = surv_by_id.get(pick.survivor_id)
        entry = {
            "survivor": surv.name if surv else f"id:{pick.survivor_id}",
            "type": type_codes.get(pick.pick_type, pick.pick_type),
        }
        if pick.pick_type == "draft" and pick.pick_order is not None:
            entry["order"] = pick.pick_order
        picks_data[name].append(entry)

    # Build sole survivor picks
    ss_data = {}
    for sp in ss_picks:
        user = db.session.get(User, sp.user_id)
        name = user.display_name or user.username
        if name not in ss_data:
            ss_data[name] = []
        surv = surv_by_id.get(sp.survivor_id)
        ss_data[name].append(
            {
                "survivor": surv.name if surv else f"id:{sp.survivor_id}",
                "episode": sp.episode,
            }
        )

    # Determine scoring label
    from .scoring.classic import DEFAULT_CONFIG, LEGACY_CONFIG

    config = season.get_scoring_config()
    if config == LEGACY_CONFIG:
        scoring = "legacy"
    elif config == DEFAULT_CONFIG:
        scoring = "default"
    else:
        scoring = "custom"

    # Team names, keyed by player the same way as the picks above
    names_by_user = TeamName.for_season(season.id)
    team_names = {}
    if names_by_user:
        for user in User.query.filter(User.id.in_(names_by_user)):
            team_names[user.display_name or user.username] = names_by_user[user.id]

    result = {"scoring": scoring, "picks": picks_data}
    if scoring == "custom":
        result["scoring_config"] = config
    if ss_data:
        result["sole_survivor_picks"] = ss_data
    if team_names:
        result["team_names"] = team_names
    if season.episode2_starts_at is not None:
        # Stored as naive UTC; the Z makes that explicit in the file
        result["episode2_starts_at"] = season.episode2_starts_at.isoformat() + "Z"

    filepath = os.path.join(picks_dir, f"season{season.number}.json")
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(
        "Exported picks for season %d to %s (%d players, %d picks)",
        season.number,
        filepath,
        len(picks_data),
        len(picks),
    )
    return filepath


PLAYERS_FILE = "players.json"


def export_player_emails(picks_dir=None):
    """Write players.json: the login email linked to each player.

    Emails belong to a player, not a season, so they get their own file
    beside the season files, keyed by player name the same way. The file is
    rewritten on every export, even when nobody is linked, so a stale copy
    cannot bring back an email that was unlinked. Each entry also records the
    username, a stable key the loader does not use yet (it matches on the
    name, like the season files).

    Returns the filepath written.
    """
    picks_dir = os.path.realpath(picks_dir or default_picks_dir())
    os.makedirs(picks_dir, exist_ok=True)

    linked = User.query.filter(User.email.isnot(None)).order_by(User.username)
    players = {
        user.display_name or user.username: {
            "email": user.email,
            "username": user.username,
        }
        for user in linked
    }

    filepath = os.path.join(picks_dir, PLAYERS_FILE)
    with open(filepath, "w") as f:
        json.dump({"players": players}, f, indent=2)

    logger.info("Exported %d linked player email(s) to %s", len(players), filepath)
    return filepath


def export_all_picks(picks_dir=None):
    """Export picks for all seasons that have picks, plus linked player emails.

    Returns the season files written. players.json is written too but is not
    in the list, which callers report as a count of seasons.
    """
    exported = []
    for season in Season.query.all():
        path = export_season_picks(season, picks_dir)
        if path:
            exported.append(path)
    export_player_emails(picks_dir)
    return exported
