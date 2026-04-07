"""Refresh season data from the survivoR dataset (open-source, hosted on GitHub)."""
import json
import logging
import os

import pandas as pd
import requests

from .models import db, Season, Survivor, Pick, User, SoleSurvivorPick

logger = logging.getLogger(__name__)

SURVIVOR_DATA_URL = 'https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx'

# Store survivoR.xlsx alongside the database so Docker's appuser can write it.
# In Docker: DATABASE_URL=sqlite:////app/data/survivor_fantasy.db → /app/data/survivoR.xlsx
# Locally:   default DB is ./survivor_fantasy.db → ./survivoR.xlsx (unchanged behavior)
def _data_dir():
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('sqlite:///'):
        db_path = db_url.replace('sqlite:///', '', 1)
        return os.path.dirname(db_path) or '.'
    return '.'

SURVIVOR_DATA_FILE = os.path.join(_data_dir(), 'survivoR.xlsx')


def _build_nickname_map():
    """Build castaway_id → most common name across all US seasons.

    For returning players who appear with different names (e.g. Oscar vs Ozzy),
    uses the name that appears most often across their seasons.
    """
    from collections import Counter
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    us = castaways[castaways['version'] == 'US']
    result = {}
    for cid, group in us.groupby('castaway_id'):
        names = group['castaway'].tolist()
        if len(set(names)) > 1:
            result[cid] = Counter(names).most_common(1)[0][0]
    return result


def us_season_filter(df, season_number):
    """Filter a DataFrame to a specific US season."""
    return df[(df['version'] == 'US') & (df['season'] == season_number)]


def get_idol_ids(advantage_details, season_number=None):
    """Get the set of advantage_ids that are Hidden Immunity Idols.

    Args:
        advantage_details: Advantage Details DataFrame.
        season_number: If given, filter to this season only.
    """
    if advantage_details.empty:
        return set()
    mask = advantage_details['advantage_type'].str.contains('Idol', case=False, na=False)
    if season_number is not None:
        mask = mask & (advantage_details['season'] == season_number)
    return set(advantage_details[mask]['advantage_id'])


def get_fire_winners(vote_history):
    """Get fire challenge winner rows from a vote history DataFrame.

    Returns filtered DataFrame. Callers can build set(castaway_id) or
    set(zip(season, castaway_id)) as needed.
    """
    if vote_history.empty or 'vote_event' not in vote_history.columns:
        return vote_history.iloc[0:0]
    return vote_history[
        vote_history['vote_event'].str.contains('Fire', case=False, na=False) &
        (vote_history['vote_event_outcome'] == 'Won')
    ]


def compute_castaway_stats(s_conf, s_vh, s_cr, s_am, idol_ids):
    """Compute per-castaway aggregate stats from pre-filtered season DataFrames.

    Returns dict with: conf_totals, votes_against, indiv_imm, tribal_imm,
    idols_found, idols_played, adv_found (non-idol), adv_played (non-idol).
    """
    conf_totals = s_conf.groupby('castaway_id')['confessional_count'].sum() \
        if not s_conf.empty else pd.Series(dtype=int)
    votes_against = s_vh.groupby('vote_id').size() \
        if not s_vh.empty else pd.Series(dtype=int)
    indiv_imm = s_cr[s_cr['won_individual_immunity'] == 1].groupby('castaway_id').size() \
        if not s_cr.empty else pd.Series(dtype=int)
    tribal_imm = s_cr[s_cr['won_tribal_immunity'] == 1].groupby('castaway_id').size() \
        if not s_cr.empty else pd.Series(dtype=int)

    if not s_am.empty:
        idols_found = s_am[
            s_am['event'].str.contains('Found', na=False) &
            s_am['advantage_id'].isin(idol_ids)
        ].groupby('castaway_id').size()
        idols_played = s_am[
            (s_am['event'] == 'Played') &
            s_am['advantage_id'].isin(idol_ids)
        ].groupby('castaway_id').size()
        adv_found = s_am[
            s_am['event'].str.contains('Found', na=False) &
            ~s_am['advantage_id'].isin(idol_ids)
        ].groupby('castaway_id').size()
        adv_played = s_am[
            (s_am['event'] == 'Played') &
            ~s_am['advantage_id'].isin(idol_ids)
        ].groupby('castaway_id').size()
    else:
        idols_found = idols_played = adv_found = adv_played = pd.Series(dtype=int)

    return {
        'conf_totals': conf_totals,
        'votes_against': votes_against,
        'indiv_imm': indiv_imm,
        'tribal_imm': tribal_imm,
        'idols_found': idols_found,
        'idols_played': idols_played,
        'adv_found': adv_found,
        'adv_played': adv_played,
    }


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
    confessionals, votes, advantages, and per-episode cumulative stats.
    """
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, 'Confessionals')
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, 'Vote History')
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details')
    tribe_mapping = pd.read_excel(SURVIVOR_DATA_FILE, 'Tribe Mapping')
    tribe_colours = pd.read_excel(SURVIVOR_DATA_FILE, 'Tribe Colours')
    castaway_scores = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaway Scores')
    jury_votes = pd.read_excel(SURVIVOR_DATA_FILE, 'Jury Votes')
    castaway_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaway Details')

    us = lambda df: us_season_filter(df, season.number)

    cast = us(castaways)
    if cast.empty:
        raise ValueError(f'No survivoR data for season {season.number}')

    # Build lookups
    cast_by_id = {row['castaway_id']: row for _, row in cast.iterrows()}

    # Bio data from Castaway Details (keyed by castaway_id, no version column)
    details_by_id = {row['castaway_id']: row for _, row in castaway_details.iterrows()
                     if pd.notna(row.get('castaway_id'))}

    # --- Season totals ---
    s_conf = us(confessionals)
    s_vh = us(vote_history)
    s_cr = us(challenge_results)
    s_am = us(advantage_movement)

    idol_ids = get_idol_ids(advantage_details, season.number)
    stats = compute_castaway_stats(s_conf, s_vh, s_cr, s_am, idol_ids)
    conf_totals = stats['conf_totals']
    votes_against = stats['votes_against']
    indiv_imm = stats['indiv_imm']
    tribal_imm = stats['tribal_imm']
    idols_found = stats['idols_found']
    idols_played = stats['idols_played']
    adv_found = stats['adv_found']
    adv_played = stats['adv_played']

    # Additional stats beyond the shared computation
    conf_time_totals = s_conf.groupby('castaway_id')['confessional_time'].sum() \
        if 'confessional_time' in s_conf.columns else pd.Series(dtype=float)
    reward_wins = s_cr[s_cr['won'] == 1].groupby('castaway_id').size()

    # Tribal councils attended (each row in vote_history = one castaway at one tribal)
    tribals_attended = s_vh.groupby('castaway_id')['episode'].apply(
        lambda x: x.drop_duplicates().count()
    ) if not s_vh.empty else pd.Series(dtype=int)

    # Correct votes (voted for person who was actually voted out)
    correct = s_vh[s_vh['vote_id'] == s_vh['voted_out_id']] if not s_vh.empty else s_vh
    correct_totals = correct.groupby('castaway_id').size() if not correct.empty else pd.Series(dtype=int)

    # Votes nullified by idol plays
    nullified_totals = pd.Series(dtype=int)
    if not s_am.empty:
        played_rows = s_am[s_am['event'] == 'Played']
        if 'votes_nullified' in played_rows.columns:
            nullified_totals = played_rows.groupby('castaway_id')['votes_nullified'].sum().dropna().astype(int)

    # Sit-outs
    sit_out_totals = s_cr[s_cr['sit_out'] == 1].groupby('castaway_id').size() \
        if not s_cr.empty and 'sit_out' in s_cr.columns else pd.Series(dtype=int)

    # Fire challenge winners
    fire_winners = set(get_fire_winners(s_vh)['castaway_id'])

    # Jury votes received (for finalists)
    s_jv = us(jury_votes)
    jury_vote_totals = s_jv[s_jv['vote'] == 1].groupby('finalist_id').size() \
        if not s_jv.empty and 'vote' in s_jv.columns else pd.Series(dtype=int)

    # Performance scores
    s_scores = us(castaway_scores)
    perf_scores = {row['castaway_id']: row.get('score_overall')
                   for _, row in s_scores.iterrows()} if not s_scores.empty else {}

    # Tribe mapping & colours for per-episode tribe tracking
    s_tm = us(tribe_mapping)
    s_tc = us(tribe_colours)
    tribe_color_map = {row['tribe']: row['tribe_colour']
                       for _, row in s_tc.iterrows()} if not s_tc.empty else {}

    # --- Per-episode incremental data ---
    # Include Tribe Mapping episodes so episode_stats covers merge even when
    # confessional/challenge/vote data hasn't been published yet.
    all_episodes = set()
    if not s_conf.empty:
        all_episodes.update(s_conf['episode'].dropna().astype(int))
    if not s_cr.empty:
        all_episodes.update(s_cr['episode'].dropna().astype(int))
    if not s_vh.empty:
        all_episodes.update(s_vh['episode'].dropna().astype(int))
    if not s_tm.empty:
        all_episodes.update(s_tm['episode'].dropna().astype(int))
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
            cid, ep = r['castaway_id'], int(r['episode'])
            conf_by_ep.setdefault(cid, {})[ep] = \
                conf_by_ep.get(cid, {}).get(ep, 0) + int(r['confessional_count'])
            if 'confessional_time' in r and pd.notna(r['confessional_time']):
                conf_time_by_ep.setdefault(cid, {})[ep] = \
                    conf_time_by_ep.get(cid, {}).get(ep, 0) + float(r['confessional_time'])

    votes_by_ep = _ep_counts(s_vh, 'vote_id', 'episode')
    ii_by_ep = _ep_counts(s_cr, 'castaway_id', 'episode',
                          lambda df: df[df['won_individual_immunity'] == 1])
    ti_by_ep = _ep_counts(s_cr, 'castaway_id', 'episode',
                          lambda df: df[df['won_tribal_immunity'] == 1])
    reward_by_ep = _ep_counts(s_cr, 'castaway_id', 'episode',
                              lambda df: df[df['won'] == 1])
    sit_out_by_ep = _ep_counts(s_cr, 'castaway_id', 'episode',
                               lambda df: df[df['sit_out'] == 1]) \
        if 'sit_out' in s_cr.columns else {}

    idol_found_by_ep = _ep_counts(
        s_am, 'castaway_id', 'episode',
        lambda df: df[df['event'].str.contains('Found', na=False) &
                       df['advantage_id'].isin(idol_ids)]
    ) if not s_am.empty else {}
    idol_play_by_ep = _ep_counts(
        s_am, 'castaway_id', 'episode',
        lambda df: df[(df['event'] == 'Played') &
                       df['advantage_id'].isin(idol_ids)]
    ) if not s_am.empty else {}
    adv_found_by_ep = _ep_counts(
        s_am, 'castaway_id', 'episode',
        lambda df: df[df['event'].str.contains('Found', na=False) &
                       ~df['advantage_id'].isin(idol_ids)]
    ) if not s_am.empty else {}
    adv_played_by_ep = _ep_counts(
        s_am, 'castaway_id', 'episode',
        lambda df: df[(df['event'] == 'Played') &
                       ~df['advantage_id'].isin(idol_ids)]
    ) if not s_am.empty else {}
    nullified_by_ep = _ep_sums(
        s_am, 'castaway_id', 'episode', 'votes_nullified',
        lambda df: df[df['event'] == 'Played']
    ) if not s_am.empty and 'votes_nullified' in s_am.columns else {}

    # Tribals attended per episode (unique episode counts per castaway)
    tribals_by_ep = _ep_counts(s_vh, 'castaway_id', 'episode')
    # Correct votes per episode
    correct_by_ep = _ep_counts(correct, 'castaway_id', 'episode') if not correct.empty else {}

    # Tribe per castaway per episode + detect merge episode from tribe_status
    tribe_by_ep = {}  # {castaway_id: {episode: (tribe, color, tribe_status)}}
    detected_merge_ep = None
    if not s_tm.empty:
        for _, r in s_tm.iterrows():
            cid, ep = r['castaway_id'], int(r['episode'])
            tribe_name = r['tribe']
            tribe_status = r['tribe_status'] if pd.notna(r.get('tribe_status')) else ''
            tribe_by_ep.setdefault(cid, {})[ep] = (
                tribe_name, tribe_color_map.get(tribe_name, ''), tribe_status
            )
            if tribe_status == 'Merged' and (detected_merge_ep is None or ep < detected_merge_ep):
                detected_merge_ep = ep
    season.merge_episode_num = detected_merge_ep

    def _build_cumulative(cid):
        """Build cumulative stats dict {episode_str: {...}} for a castaway."""
        cumulative = {}
        running = {
            'conf': 0, 'conf_time': 0, 'ii': 0, 'ti': 0, 'reward': 0,
            'idol': 0, 'idol_play': 0, 'adv': 0, 'adv_play': 0, 'votes': 0,
            'tribals': 0, 'correct_votes': 0, 'nullified': 0, 'sit_outs': 0,
        }
        last_tribe = ('', '', '')
        for ep in range(1, max_episode + 1):
            running['conf'] += conf_by_ep.get(cid, {}).get(ep, 0)
            running['conf_time'] += conf_time_by_ep.get(cid, {}).get(ep, 0)
            running['votes'] += votes_by_ep.get(cid, {}).get(ep, 0)
            running['ii'] += ii_by_ep.get(cid, {}).get(ep, 0)
            running['ti'] += ti_by_ep.get(cid, {}).get(ep, 0)
            running['reward'] += reward_by_ep.get(cid, {}).get(ep, 0)
            running['idol'] += idol_found_by_ep.get(cid, {}).get(ep, 0)
            running['idol_play'] += idol_play_by_ep.get(cid, {}).get(ep, 0)
            running['adv'] += adv_found_by_ep.get(cid, {}).get(ep, 0)
            running['adv_play'] += adv_played_by_ep.get(cid, {}).get(ep, 0)
            running['nullified'] += int(nullified_by_ep.get(cid, {}).get(ep, 0))
            running['sit_outs'] += sit_out_by_ep.get(cid, {}).get(ep, 0)
            # Tribals: count 1 if they appeared in vote history this episode
            if tribals_by_ep.get(cid, {}).get(ep, 0) > 0:
                running['tribals'] += 1
            running['correct_votes'] += correct_by_ep.get(cid, {}).get(ep, 0)
            # Tribe at this episode
            if ep in tribe_by_ep.get(cid, {}):
                last_tribe = tribe_by_ep[cid][ep]
            ep_data = dict(running)
            ep_data['tribe'] = last_tribe[0]
            ep_data['tribe_color'] = last_tribe[1]
            ep_data['tribe_status'] = last_tribe[2]
            cumulative[str(ep)] = ep_data
        return cumulative

    # Apply nicknames for returning players (e.g. Oscar → Ozzy)
    nickname_map = _build_nickname_map()

    # Build lookup of existing survivors by castaway_id
    existing = {s.castaway_id: s for s in Survivor.query.filter_by(season_id=season.id).all()
                if s.castaway_id}

    # Season metadata from survivoR (required for new-era seasons 41+)
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, 'Season Summary')
    ss = season_summary[
        (season_summary['version'] == 'US') &
        (season_summary['season'] == season.number)
    ]
    if not ss.empty:
        row = ss.iloc[0]
        if pd.isna(row['n_cast']):
            raise ValueError(
                f"Season {season.number}: survivoR data missing n_cast. "
                f"Only new-era seasons (41+) are supported.")
        n_cast = int(row['n_cast'])
        # For in-progress seasons, n_jury/n_finalists may be NaN — store as None
        n_finalists = int(row['n_finalists']) if pd.notna(row['n_finalists']) else None
        n_jury = int(row['n_jury']) if pd.notna(row['n_jury']) else None
        season.num_players = n_cast
        season.n_finalists = n_finalists
        if n_jury is not None and n_finalists is not None:
            season.left_at_jury = n_jury + n_finalists
        else:
            season.left_at_jury = None
        # Update season name if still default
        if season.name == f'Season {season.number}':
            raw_name = ss.iloc[0]['season_name'] if pd.notna(ss.iloc[0]['season_name']) else ''
            if ':' in str(raw_name):
                clean = raw_name.split(':')[-1].strip()
                season.name = f'Season {clean}' if clean.isdigit() else clean
            elif raw_name:
                season.name = raw_name

    updated = 0
    for cid, row in cast_by_id.items():
        surv = existing.get(cid)
        if not surv:
            # Create new survivor
            name = nickname_map.get(cid, row['castaway'])
            tribe = row['original_tribe'] if pd.notna(row.get('original_tribe')) else None
            surv = Survivor(
                season_id=season.id,
                name=name,
                full_name=row['full_name'] if pd.notna(row.get('full_name')) else None,
                castaway_id=cid,
                version_season=row['version_season'] if pd.notna(row.get('version_season')) else None,
                tribe=tribe,
                tribe_color=tribe_color_map.get(tribe),
            )
            db.session.add(surv)
            db.session.flush()

        # Use most common name for returning players
        if cid in nickname_map:
            surv.name = nickname_map[cid]

        # Core game data
        surv.voted_out_order = int(row['order']) if pd.notna(row['order']) else 0
        surv.made_jury = bool(row['jury']) if pd.notna(row['jury']) else False
        surv.placement = int(row['place']) if pd.notna(row['place']) else None
        surv.result = row['result'] if pd.notna(row['result']) else None
        surv.elimination_episode = int(row['episode']) if pd.notna(row.get('episode')) else None
        surv.day_voted_out = int(row['day']) if pd.notna(row.get('day')) else None

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
        surv.correct_votes = int(correct_totals.get(cid, 0))
        surv.votes_nullified = int(nullified_totals.get(cid, 0))
        surv.sit_outs = int(sit_out_totals.get(cid, 0))
        surv.won_fire = cid in fire_winners
        surv.jury_votes_received = int(jury_vote_totals.get(cid, 0)) \
            if cid in jury_vote_totals else None
        score = perf_scores.get(cid)
        surv.performance_score = float(score) if pd.notna(score) else None

        # Per-episode cumulative stats
        surv.episode_stats = json.dumps(_build_cumulative(cid)) if max_episode > 0 else None

        # Bio from Castaways + Castaway Details
        surv.age = int(row['age']) if pd.notna(row.get('age')) else None
        surv.city = row['city'] if pd.notna(row.get('city')) else None
        surv.state = row['state'] if pd.notna(row.get('state')) else None
        details = details_by_id.get(cid)
        if details is not None:
            surv.occupation = details['occupation'] if pd.notna(details.get('occupation')) else None
            surv.personality_type = details['personality_type'] if pd.notna(details.get('personality_type')) else None

        updated += 1

    # Validate day_voted_out monotonicity (day should not decrease as order increases)
    day_warnings = []
    ordered = sorted(
        [(s.voted_out_order, s.day_voted_out, s.name) for s in season.survivors
         if s.voted_out_order and s.voted_out_order > 0 and s.day_voted_out],
        key=lambda x: x[0])
    prev_day, prev_name = 0, ''
    for order, day, name in ordered:
        if day < prev_day:
            day_warnings.append(
                f'{name} (order={order}, day={day}) eliminated before '
                f'{prev_name} (day={prev_day})')
            # Clear bad day data for this player so scoring falls back safely
        prev_day, prev_name = day, name
    if day_warnings:
        import logging
        logger = logging.getLogger(__name__)
        for w in day_warnings:
            logger.warning('Season %d day data error: %s', season.number, w)

    db.session.commit()

    from .predictions import clear_cache
    clear_cache()
    from .routes import _compare_cache
    _compare_cache.clear()

    return updated, day_warnings


# survivoR name → image site first name (where survivoR name doesn't match URL)
NAME_TO_SITE = {
    'jelinsky': 'david',
    'tk': 'terran',
    'sol': 'solomon',
    'j. maya': 'janani',
}


def generate_season_images(season):
    """Generate biopic image URLs for a season from fantasysurvivorgame.com.

    Pattern: /images/{season}/biopics/{firstname}BIO.jpg
    Returns number of images found.
    """
    matched = 0
    for surv in Survivor.query.filter_by(season_id=season.id).all():
        name_key = surv.name.lower()
        site_name = NAME_TO_SITE.get(name_key, surv.name.split()[0].lower())

        candidates = [
            f'https://www.fantasysurvivorgame.com/images/{season.number}/biopics/{site_name}BIO.jpg',
            f'https://www.fantasysurvivorgame.com/images/{season.number}/biopics/%22{site_name}%22BIO.jpg',
        ]
        for url in candidates:
            try:
                resp = requests.head(url, timeout=5)
                if resp.status_code == 200:
                    surv.image_url = url
                    matched += 1
                    break
            except Exception:
                pass

    db.session.commit()
    total = Survivor.query.filter_by(season_id=season.id).count()
    logger.info('Season %d images: %d/%d', season.number, matched, total)
    return matched


PICKS_DIR = 'picks'


def export_season_picks(season, picks_dir=None):
    """Export all picks for a season to a JSON file.

    Produces a file compatible with seed.py's load_picks_from_json, extended
    with sole_survivor_picks and custom scoring_config.

    Returns the filepath written, or None if no picks exist.
    """
    picks_dir = picks_dir or PICKS_DIR
    os.makedirs(picks_dir, exist_ok=True)

    picks = (Pick.query.filter_by(season_id=season.id)
             .order_by(Pick.user_id, Pick.pick_order).all())
    if not picks:
        return None

    surv_by_id = {s.id: s for s in Survivor.query.filter_by(season_id=season.id)}

    # Build picks by user display name
    picks_data = {}
    for pick in picks:
        user = db.session.get(User, pick.user_id)
        name = user.display_name or user.username
        if name not in picks_data:
            picks_data[name] = []

        type_codes = {'draft': 'd', 'wildcard': 'w', 'pmr_w': 'pmr_w', 'pmr_d': 'pmr_d'}
        surv = surv_by_id.get(pick.survivor_id)
        entry = {
            'survivor': surv.name if surv else f'id:{pick.survivor_id}',
            'type': type_codes.get(pick.pick_type, pick.pick_type),
        }
        if pick.pick_type == 'draft' and pick.pick_order is not None:
            entry['order'] = pick.pick_order
        picks_data[name].append(entry)

    # Build sole survivor picks
    ss_picks = SoleSurvivorPick.query.filter_by(season_id=season.id).order_by(
        SoleSurvivorPick.user_id, SoleSurvivorPick.episode).all()
    ss_data = {}
    for sp in ss_picks:
        user = db.session.get(User, sp.user_id)
        name = user.display_name or user.username
        if name not in ss_data:
            ss_data[name] = []
        surv = surv_by_id.get(sp.survivor_id)
        ss_data[name].append({
            'survivor': surv.name if surv else f'id:{sp.survivor_id}',
            'episode': sp.episode,
        })

    # Determine scoring label
    from .scoring.classic import DEFAULT_CONFIG, LEGACY_CONFIG
    config = season.get_scoring_config()
    if config == LEGACY_CONFIG:
        scoring = 'legacy'
    elif config == DEFAULT_CONFIG:
        scoring = 'default'
    else:
        scoring = 'custom'

    result = {'scoring': scoring, 'picks': picks_data}
    if scoring == 'custom':
        result['scoring_config'] = config
    if ss_data:
        result['sole_survivor_picks'] = ss_data

    filepath = os.path.join(picks_dir, f'season{season.number}.json')
    with open(filepath, 'w') as f:
        json.dump(result, f, indent=2)

    logger.info('Exported picks for season %d to %s (%d players, %d picks)',
                season.number, filepath, len(picks_data), len(picks))
    return filepath


def export_all_picks(picks_dir=None):
    """Export picks for all seasons that have picks."""
    exported = []
    for season in Season.query.all():
        path = export_season_picks(season, picks_dir)
        if path:
            exported.append(path)
    return exported
