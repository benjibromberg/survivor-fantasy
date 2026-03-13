"""Seed the database from survivoR.xlsx (all survivor data) and optional JSON files (pick assignments).

Usage:
    python seed.py                          # Build all seasons, no picks
    python seed.py --picks-dir ./picks      # Also load pick JSON files from directory
    python seed.py --no-scrape              # Skip network calls
    python seed.py --seasons 46,47,49,50    # Only build specific seasons (default: 46,47,49,50)
"""
import os
import sys

import pandas as pd
import requests as http_requests
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.data import (SURVIVOR_DATA_FILE, NAME_TO_SITE, _build_nickname_map,
                      us_season_filter, get_idol_ids, compute_castaway_stats,
                      refresh_season)
from app.models import db, User, Season, Survivor, Pick

SURVIVOR_DATA_URL = 'https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx'

# Nickname mapping: legacy xlsx shorthand → survivoR castaway name
NICKNAME_MAP = {
    'tiff': 'Tiffany', 'jem': 'Jem', 'jess': 'Jess',
    'jelinsky': 'Jelinsky', 'tk': 'TK', 'sol': 'Sol',
    'mc': 'MC', 'annie': 'Annie', 'soph': 'Soph',
    'brandon donlon': 'Brandon', 'brandon meyer': 'Brando', 'niko': 'Sifu',
}


def ensure_survivor_data():
    """Download survivoR.xlsx from GitHub if it doesn't exist locally."""
    if os.path.exists(SURVIVOR_DATA_FILE):
        return
    print(f'Downloading {SURVIVOR_DATA_FILE} from survivoR GitHub repo...')
    resp = http_requests.get(SURVIVOR_DATA_URL, timeout=120)
    resp.raise_for_status()
    with open(SURVIVOR_DATA_FILE, 'wb') as f:
        f.write(resp.content)
    print(f'Downloaded {SURVIVOR_DATA_FILE} ({len(resp.content) / 1024 / 1024:.1f} MB)')


def load_survivor_ref():
    """Load reference tables from survivoR.xlsx."""
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    tribe_colours = pd.read_excel(SURVIVOR_DATA_FILE, 'Tribe Colours')
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, 'Season Summary')
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, 'Confessionals')
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, 'Vote History')
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details')
    castaway_details = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaway Details')
    return (castaways, tribe_colours, season_summary,
            confessionals, vote_history, challenge_results, advantage_movement,
            advantage_details, castaway_details)


def build_season_from_survivor_db(season_number, ref_data):
    """Create a Season and all its Survivors from survivoR data."""
    if season_number < 41:
        raise ValueError(f'Season {season_number}: only new-era seasons (41+) are supported.')
    castaways, tribe_colours, season_summary, confessionals, vote_history, challenge_results, advantage_movement, advantage_details, castaway_details = ref_data
    cast = us_season_filter(castaways, season_number).copy()
    if cast.empty:
        raise ValueError(f'No survivoR data for season {season_number}')

    # Season metadata
    ss = us_season_filter(season_summary, season_number)
    season_name = ss.iloc[0]['season_name'] if not ss.empty else f'Season {season_number}'
    # Clean up name (e.g. "Survivor: 49" → "Season 49")
    if ':' in str(season_name):
        season_name = season_name.split(':')[-1].strip()
        if season_name.isdigit():
            season_name = f'Season {season_name}'

    if ss.empty:
        raise ValueError(f'Season {season_number}: no Season Summary data in survivoR. '
                         'Only new-era seasons (41+) are supported.')
    row = ss.iloc[0]
    if pd.isna(row['n_cast']):
        raise ValueError(f'Season {season_number}: survivoR data missing n_cast. '
                         'Only new-era seasons (41+) are supported.')
    n_cast = int(row['n_cast'])
    # For in-progress seasons, n_jury/n_finalists may be NaN — store as None
    n_finalists = int(row['n_finalists']) if pd.notna(row['n_finalists']) else None
    n_jury = int(row['n_jury']) if pd.notna(row['n_jury']) else None
    left_at_jury = (n_jury + n_finalists) if (n_jury is not None and n_finalists is not None) else None

    # Build tribe color lookup
    tc = us_season_filter(tribe_colours, season_number)
    tribe_color_map = {}
    for _, row in tc.iterrows():
        tribe_color_map[row['tribe']] = row['tribe_colour']

    season = Season(
        number=season_number,
        name=season_name,
        is_active=False,
        num_players=n_cast,
        left_at_jury=left_at_jury,
        n_finalists=n_finalists,
    )
    db.session.add(season)
    db.session.flush()

    # Bio data from Castaway Details (keyed by castaway_id)
    details_by_id = {r['castaway_id']: r for _, r in castaway_details.iterrows()
                     if pd.notna(r.get('castaway_id'))}

    # Create survivors (apply nicknames for returning players)
    nickname_map = _build_nickname_map()
    survivor_map = {}  # castaway name (lower) → Survivor
    for _, row in cast.iterrows():
        cid = row['castaway_id'] if pd.notna(row.get('castaway_id')) else None
        name = nickname_map.get(cid, row['castaway']) if cid else row['castaway']
        order = int(row['order']) if pd.notna(row['order']) else 0
        made_jury = bool(row['jury']) if pd.notna(row['jury']) else False
        place = int(row['place']) if pd.notna(row['place']) else None
        tribe = row['original_tribe'] if pd.notna(row.get('original_tribe')) else None
        tribe_color = tribe_color_map.get(tribe)
        full_name = row['full_name'] if pd.notna(row.get('full_name')) else None
        castaway_id = row['castaway_id'] if pd.notna(row.get('castaway_id')) else None
        version_season = row['version_season'] if pd.notna(row.get('version_season')) else None
        result = row['result'] if pd.notna(row.get('result')) else None
        elimination_episode = int(row['episode']) if pd.notna(row.get('episode')) else None

        # Bio data
        details = details_by_id.get(cid)
        occupation = details['occupation'] if details is not None and pd.notna(details.get('occupation')) else None
        personality_type = details['personality_type'] if details is not None and pd.notna(details.get('personality_type')) else None

        surv = Survivor(
            season_id=season.id,
            name=name,
            full_name=full_name,
            castaway_id=castaway_id,
            version_season=version_season,
            voted_out_order=order,
            result=result,
            made_jury=made_jury,
            placement=place,
            tribe=tribe,
            tribe_color=tribe_color,
            age=int(row['age']) if pd.notna(row.get('age')) else None,
            city=row['city'] if pd.notna(row.get('city')) else None,
            state=row['state'] if pd.notna(row.get('state')) else None,
            occupation=occupation,
            personality_type=personality_type,
            elimination_episode=elimination_episode,
        )
        db.session.add(surv)
        db.session.flush()
        survivor_map[name.lower()] = surv

    # Populate aggregate stats
    us = lambda df: us_season_filter(df, season_number)
    idol_ids = get_idol_ids(advantage_details, season_number)
    stats = compute_castaway_stats(
        us(confessionals), us(vote_history), us(challenge_results),
        us(advantage_movement), idol_ids)
    conf_totals = stats['conf_totals']
    votes_against = stats['votes_against']
    indiv_imm = stats['indiv_imm']
    tribal_imm = stats['tribal_imm']
    idols_found = stats['idols_found']
    adv_found = stats['adv_found']
    adv_played = stats['adv_played']

    for surv in Survivor.query.filter_by(season_id=season.id).all():
        cid = surv.castaway_id
        if cid:
            surv.confessional_count = int(conf_totals.get(cid, 0))
            surv.votes_received = int(votes_against.get(cid, 0))
            surv.individual_immunity_wins = int(indiv_imm.get(cid, 0))
            surv.tribal_immunity_wins = int(tribal_imm.get(cid, 0))
            surv.idols_found = int(idols_found.get(cid, 0))
            surv.advantages_found = int(adv_found.get(cid, 0))
            surv.advantages_played = int(adv_played.get(cid, 0))

    db.session.commit()
    return season, survivor_map


def _resolve_survivor(surv_name, survivor_map):
    """Resolve a survivor name to a Survivor object using nickname map and prefix matching."""
    lookup = NICKNAME_MAP.get(surv_name.lower(), surv_name).lower()
    survivor = survivor_map.get(lookup)
    if not survivor:
        for key, surv in survivor_map.items():
            if key.startswith(surv_name.lower()):
                survivor = surv
                break
    return survivor


def load_picks_from_json(filepath, season, survivor_map):
    """Load pick assignments, scoring config, and SS picks from a JSON file.

    JSON format:
        {"scoring": "legacy"|"default"|"custom",
         "scoring_config": {...},  # only when scoring="custom"
         "picks": {"Player": [{"survivor": "Name", "type": "d", "order": 1}, ...]},
         "sole_survivor_picks": {"Player": [{"survivor": "Name", "episode": 1}, ...]}}

    Type codes: d=draft, w=wildcard, pmr_w=pmr_w, pmr_d=pmr_d
    """
    import json as _json
    from app.scoring.classic import DEFAULT_CONFIG, LEGACY_CONFIG

    with open(filepath) as f:
        data = _json.load(f)

    # Apply scoring config
    scoring = data.get('scoring', 'default')
    if scoring == 'legacy':
        season.scoring_config = _json.dumps(LEGACY_CONFIG)
    elif scoring == 'custom' and 'scoring_config' in data:
        season.scoring_config = _json.dumps(data['scoring_config'])
    else:
        season.scoring_config = _json.dumps(DEFAULT_CONFIG)

    picks_data = data.get('picks', data)  # fallback to top-level if no 'picks' key
    pick_types = {'d': 'draft', 'w': 'wildcard', 'pmr_w': 'pmr_w', 'pmr_d': 'pmr_d'}
    pick_count = 0

    for player_name, picks in picks_data.items():
        # Ensure fantasy player exists as User
        user = User.query.filter_by(username=player_name.lower()).first()
        if not user:
            user = User(username=player_name.lower(), display_name=player_name)
            db.session.add(user)
            db.session.flush()

        for entry in picks:
            surv_name = entry['survivor']
            survivor = _resolve_survivor(surv_name, survivor_map)
            if not survivor:
                print(f'    WARNING: "{surv_name}" not found in survivoR data for season {season.number}')
                continue

            # Match pick type
            cell = entry['type'].strip().lower()
            matched_type = None
            for code, ptype in sorted(pick_types.items(), key=lambda x: -len(x[0])):
                if code in cell:
                    matched_type = ptype
                    break
            if matched_type:
                db.session.add(Pick(
                    user_id=user.id,
                    season_id=season.id,
                    survivor_id=survivor.id,
                    pick_type=matched_type,
                    pick_order=entry.get('order'),
                ))
                pick_count += 1

    # Load sole survivor picks
    ss_count = 0
    ss_data = data.get('sole_survivor_picks', {})
    for player_name, ss_picks in ss_data.items():
        user = User.query.filter_by(username=player_name.lower()).first()
        if not user:
            user = User(username=player_name.lower(), display_name=player_name)
            db.session.add(user)
            db.session.flush()

        for entry in ss_picks:
            survivor = _resolve_survivor(entry['survivor'], survivor_map)
            if not survivor:
                print(f'    WARNING: SS pick "{entry["survivor"]}" not found for season {season.number}')
                continue
            db.session.add(SoleSurvivorPick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=survivor.id,
                episode=entry['episode'],
            ))
            ss_count += 1

    db.session.commit()
    ss_msg = f', {ss_count} SS picks' if ss_count else ''
    print(f'  Picks for {season.name}: {len(picks_data)} players, {pick_count} picks{ss_msg}')


def generate_image_urls():
    """Generate biopic image URLs from fantasysurvivorgame.com using predictable URL pattern.

    No scraping — just constructs URLs from castaway names.
    Pattern: /images/{season}/biopics/{firstname}BIO.jpg
    """
    for season in Season.query.all():
        matched = 0
        for surv in Survivor.query.filter_by(season_id=season.id).all():
            name_key = surv.name.lower()
            site_name = NAME_TO_SITE.get(name_key, surv.name.split()[0].lower())

            # Try the direct name, then quoted version for names like Q
            candidates = [
                f'https://www.fantasysurvivorgame.com/images/{season.number}/biopics/{site_name}BIO.jpg',
                f'https://www.fantasysurvivorgame.com/images/{season.number}/biopics/%22{site_name}%22BIO.jpg',
            ]
            for url in candidates:
                try:
                    resp = http_requests.head(url, timeout=5)
                    if resp.status_code == 200:
                        surv.image_url = url
                        matched += 1
                        break
                except Exception:
                    pass

        db.session.commit()
        total = Survivor.query.filter_by(season_id=season.id).count()
        print(f'  Season {season.number}: {matched}/{total} images')


# Map of season number → pick JSON filename (for --picks-dir loading)
SEASON_PICK_FILES = {
    45: 'season45.json',
    46: 'season46.json',
    47: 'season47_snakedraft.json',
    49: 'season49_snakedraft.json',
}


def main():
    no_scrape = '--no-scrape' in sys.argv

    if not no_scrape:
        ensure_survivor_data()

    # Parse --seasons (default: 46,47,49,50)
    season_nums = [45, 46, 47, 49, 50]
    for arg in sys.argv[1:]:
        if arg.startswith('--seasons='):
            season_nums = [int(s) for s in arg.split('=')[1].split(',')]
        elif arg.startswith('--seasons'):
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                season_nums = [int(s) for s in sys.argv[idx + 1].split(',')]

    # Parse --picks-dir
    picks_dir = None
    for arg in sys.argv[1:]:
        if arg.startswith('--picks-dir='):
            picks_dir = arg.split('=', 1)[1]
        elif arg == '--picks-dir':
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                picks_dir = sys.argv[idx + 1]

    # Parse --active (which season to mark active, default: highest)
    active_season = None
    for arg in sys.argv[1:]:
        if arg.startswith('--active='):
            active_season = int(arg.split('=')[1])

    app = create_app()
    with app.app_context():
        print('Dropping and recreating all tables...')
        db.drop_all()
        db.create_all()

        if not no_scrape:
            print('Downloading latest survivoR.xlsx...')
            resp = http_requests.get(SURVIVOR_DATA_URL, timeout=30)
            resp.raise_for_status()
            with open(SURVIVOR_DATA_FILE, 'wb') as f:
                f.write(resp.content)

        print('Loading survivoR reference data...')
        ref_data = load_survivor_ref()

        # Build seasons from survivoR data
        print(f'\nBuilding seasons from survivoR database...')
        for snum in season_nums:
            try:
                season, smap = build_season_from_survivor_db(snum, ref_data)
                print(f'  {season.name}: {season.num_players} survivors, '
                      f'{sum(1 for s in smap.values() if s.tribe)} with tribes')
            except ValueError as e:
                print(f'  Skipping season {snum}: {e}')

        # Load picks from JSON files if --picks-dir provided
        if picks_dir:
            print(f'\nLoading pick assignments from {picks_dir}...')
            for snum, filename in SEASON_PICK_FILES.items():
                filepath = os.path.join(picks_dir, filename)
                season = Season.query.filter_by(number=snum).first()
                if not season:
                    continue
                if not os.path.exists(filepath):
                    print(f'  Skipping season {snum}: {filepath} not found')
                    continue
                survivor_map = {s.name.lower(): s for s in Survivor.query.filter_by(season_id=season.id)}
                load_picks_from_json(filepath, season, survivor_map)

        # Enrich all seasons with episode_stats, elimination_episode, etc.
        print('\nRunning refresh_season for per-episode data...')
        for season in Season.query.all():
            refresh_season(season)
            print(f'  {season.name}: refreshed')

        # Mark active season
        if active_season is None:
            active_season = max(season_nums)
        active = Season.query.filter_by(number=active_season).first()
        if active:
            active.is_active = True
            db.session.commit()
            print(f'\nActive season: {active.name}')

        if not no_scrape:
            print('\nGenerating image URLs...')
            generate_image_urls()
        else:
            print('Skipping image URL generation (--no-scrape)')

        # Set admin from env var
        admin_username = os.environ.get('ADMIN_GITHUB_USERNAME', '').lower()
        if admin_username:
            admin = User.query.filter_by(username=admin_username).first()
            if admin:
                admin.is_admin = True
                db.session.commit()

        print(f'\nDone! {User.query.count()} users, {Season.query.count()} seasons, '
              f'{Survivor.query.count()} survivors, {Pick.query.count()} picks')


if __name__ == '__main__':
    main()
