"""Refresh season data from the survivoR dataset (open-source, hosted on GitHub)."""
import logging

import pandas as pd
import requests

from .models import db, Season, Survivor

logger = logging.getLogger(__name__)

SURVIVOR_DATA_URL = 'https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx'
SURVIVOR_DATA_FILE = 'survivoR.xlsx'


def download_survivor_data():
    """Download the latest survivoR.xlsx from GitHub."""
    resp = requests.get(SURVIVOR_DATA_URL, timeout=60)
    resp.raise_for_status()
    with open(SURVIVOR_DATA_FILE, 'wb') as f:
        f.write(resp.content)
    logger.info('Downloaded latest survivoR.xlsx')


def refresh_season(season):
    """Update a season's survivor data from survivoR.xlsx.

    Updates boot order, jury status, placement, results, challenge stats,
    confessionals, votes, and advantages for all survivors in the season.
    """
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, 'Confessionals')
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, 'Vote History')
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details')

    us_season = lambda df: df[(df['version'] == 'US') & (df['season'] == season.number)]

    cast = us_season(castaways)
    if cast.empty:
        raise ValueError(f'No survivoR data for season {season.number}')

    # Build lookups
    cast_by_id = {row['castaway_id']: row for _, row in cast.iterrows()}

    # Confessionals
    s_conf = us_season(confessionals)
    conf_totals = s_conf.groupby('castaway_id')['confessional_count'].sum()

    # Votes received
    s_vh = us_season(vote_history)
    votes_against = s_vh.groupby('vote_id').size()

    # Challenge results
    s_cr = us_season(challenge_results)
    indiv_imm = s_cr[s_cr['won_individual_immunity'] == 1].groupby('castaway_id').size()
    tribal_imm = s_cr[s_cr['won_tribal_immunity'] == 1].groupby('castaway_id').size()
    reward_wins = s_cr[s_cr['won'] == 1].groupby('castaway_id').size()

    # Advantages
    s_am = us_season(advantage_movement)
    idol_ids = set(
        advantage_details.query(
            f"season == {season.number} and advantage_type.str.contains('Idol', na=False)",
            engine='python'
        )['advantage_id']
    ) if not advantage_details.empty else set()
    idols_found = s_am[
        (s_am['event'].str.contains('Found', na=False)) &
        (s_am['advantage_id'].isin(idol_ids))
    ].groupby('castaway_id').size() if not s_am.empty else pd.Series(dtype=int)
    adv_found = s_am[s_am['event'].str.contains('Found', na=False)].groupby('castaway_id').size()
    adv_played = s_am[s_am['event'] == 'Played'].groupby('castaway_id').size()

    updated = 0
    for surv in Survivor.query.filter_by(season_id=season.id).all():
        cid = surv.castaway_id
        if not cid or cid not in cast_by_id:
            continue

        row = cast_by_id[cid]

        # Core game data
        surv.voted_out_order = int(row['order']) if pd.notna(row['order']) else 0
        surv.made_jury = bool(row['jury']) if pd.notna(row['jury']) else False
        surv.placement = int(row['place']) if pd.notna(row['place']) else None
        surv.result = row['result'] if pd.notna(row['result']) else None

        # Stats
        surv.confessional_count = int(conf_totals.get(cid, 0))
        surv.votes_received = int(votes_against.get(cid, 0))
        surv.individual_immunity_wins = int(indiv_imm.get(cid, 0))
        surv.tribal_immunity_wins = int(tribal_imm.get(cid, 0))
        surv.immunity_wins = surv.individual_immunity_wins
        surv.reward_wins = int(reward_wins.get(cid, 0))
        surv.idols_found = int(idols_found.get(cid, 0))
        surv.advantages_found = int(adv_found.get(cid, 0))
        surv.advantages_played = int(adv_played.get(cid, 0))

        updated += 1

    # Update season player count and jury threshold
    max_order = max((s.voted_out_order for s in Survivor.query.filter_by(season_id=season.id) if s.voted_out_order), default=0)

    db.session.commit()

    from .predictions import clear_cache
    clear_cache()

    return updated
