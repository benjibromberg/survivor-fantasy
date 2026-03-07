"""Seed the database from survivoR.xlsx (all survivor data) and optional xlsx files (pick assignments).

Usage:
    python seed.py                          # Build all seasons, no picks
    python seed.py --picks-dir ./picks      # Also load pick xlsx files from directory
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
from app.models import db, User, Season, Survivor, Pick

SURVIVOR_DATA_FILE = 'survivoR.xlsx'
SURVIVOR_DATA_URL = 'https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx'

# Nickname mapping: legacy xlsx shorthand → survivoR castaway name
NICKNAME_MAP = {
    'tiff': 'Tiffany', 'jem': 'Jem', 'jess': 'Jess',
    'jelinsky': 'Jelinsky', 'tk': 'TK', 'sol': 'Sol',
    'mc': 'MC', 'annie': 'Annie', 'soph': 'Soph',
}

# survivoR name → image site first name (only where survivoR name doesn't match the image URL)
NAME_TO_SITE = {
    'jelinsky': 'david',
    'oscar': 'ozzy',
    'tk': 'terran',
    'sol': 'solomon',
}


def load_survivor_ref():
    """Load reference tables from survivoR.xlsx."""
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, 'Castaways')
    tribe_colours = pd.read_excel(SURVIVOR_DATA_FILE, 'Tribe Colours')
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, 'Season Summary')
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, 'Confessionals')
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, 'Vote History')
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, 'Challenge Results')
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Movement')
    return (castaways, tribe_colours, season_summary,
            confessionals, vote_history, challenge_results, advantage_movement)


def build_season_from_survivor_db(season_number, ref_data):
    """Create a Season and all its Survivors from survivoR data."""
    castaways, tribe_colours, season_summary, confessionals, vote_history, challenge_results, advantage_movement = ref_data
    cast = castaways[
        (castaways['version'] == 'US') &
        (castaways['season'] == season_number)
    ].copy()
    if cast.empty:
        raise ValueError(f'No survivoR data for season {season_number}')

    # Season metadata
    ss = season_summary[
        (season_summary['version'] == 'US') &
        (season_summary['season'] == season_number)
    ]
    season_name = ss.iloc[0]['season_name'] if not ss.empty else f'Season {season_number}'
    # Clean up name (e.g. "Survivor: 49" → "Season 49")
    if ':' in str(season_name):
        season_name = season_name.split(':')[-1].strip()
        if season_name.isdigit():
            season_name = f'Season {season_name}'

    n_cast = int(ss.iloc[0]['n_cast']) if not ss.empty and pd.notna(ss.iloc[0]['n_cast']) else len(cast)
    n_jury = int(ss.iloc[0]['n_jury']) if not ss.empty and pd.notna(ss.iloc[0]['n_jury']) else 8
    left_at_jury = n_jury + (int(ss.iloc[0]['n_finalists']) if not ss.empty and pd.notna(ss.iloc[0]['n_finalists']) else 3)

    # Build tribe color lookup
    tc = tribe_colours[
        (tribe_colours['version'] == 'US') &
        (tribe_colours['season'] == season_number)
    ]
    tribe_color_map = {}
    for _, row in tc.iterrows():
        tribe_color_map[row['tribe']] = row['tribe_colour']

    season = Season(
        number=season_number,
        name=season_name,
        is_active=False,
        num_players=n_cast,
        left_at_jury=left_at_jury,
    )
    db.session.add(season)
    db.session.flush()

    # Create survivors
    survivor_map = {}  # castaway name (lower) → Survivor
    for _, row in cast.iterrows():
        name = row['castaway']
        order = int(row['order']) if pd.notna(row['order']) else 0
        made_jury = bool(row['jury']) if pd.notna(row['jury']) else False
        place = int(row['place']) if pd.notna(row['place']) else None
        tribe = row['original_tribe'] if pd.notna(row.get('original_tribe')) else None
        tribe_color = tribe_color_map.get(tribe)
        full_name = row['full_name'] if pd.notna(row.get('full_name')) else None
        castaway_id = row['castaway_id'] if pd.notna(row.get('castaway_id')) else None
        version_season = row['version_season'] if pd.notna(row.get('version_season')) else None
        result = row['result'] if pd.notna(row.get('result')) else None

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
        )
        db.session.add(surv)
        db.session.flush()
        survivor_map[name.lower()] = surv

    # Populate aggregate stats
    us_filter = (lambda df: df[(df['version'] == 'US') & (df['season'] == season_number)])

    # Confessionals
    s_conf = us_filter(confessionals)
    conf_totals = s_conf.groupby('castaway_id')['confessional_count'].sum()

    # Votes received at tribal
    s_vh = us_filter(vote_history)
    votes_against = s_vh.groupby('vote_id').size()

    # Challenge results
    s_cr = us_filter(challenge_results)
    indiv_imm = s_cr[s_cr['won_individual_immunity'] == 1].groupby('castaway_id').size()
    tribal_imm = s_cr[s_cr['won_tribal_immunity'] == 1].groupby('castaway_id').size()

    # Advantages
    s_am = us_filter(advantage_movement)
    idols_found = s_am[(s_am['event'].str.contains('Found', na=False)) &
                       (s_am['advantage_id'].isin(
                           pd.read_excel(SURVIVOR_DATA_FILE, 'Advantage Details').query(
                               f"season == {season_number} and advantage_type.str.contains('Idol', na=False)",
                               engine='python')['advantage_id']
                       ))].groupby('castaway_id').size() if not s_am.empty else pd.Series(dtype=int)
    adv_found = s_am[s_am['event'].str.contains('Found', na=False)].groupby('castaway_id').size()
    adv_played = s_am[s_am['event'] == 'Played'].groupby('castaway_id').size()

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


def load_picks_from_xlsx(filepath, season, survivor_map):
    """Load only pick assignments from a legacy xlsx file. All survivor data comes from survivoR."""
    df = pd.read_excel(filepath)
    df['player'] = df['player'].str.strip()
    fantasy_columns = list(df.columns[3:])

    # Ensure fantasy players exist as Users
    users = {}
    for name in fantasy_columns:
        user = User.query.filter_by(username=name.lower()).first()
        if not user:
            user = User(username=name.lower(), display_name=name)
            db.session.add(user)
            db.session.flush()
        users[name] = user

    # Parse picks
    pick_types = {'d': 'draft', 'w': 'wildcard', 'pmr_w': 'pmr_w', 'pmr_d': 'pmr_d'}
    pick_count = 0
    for _, row in df.iterrows():
        xlsx_name = row['player']
        # Resolve nickname to survivoR name for lookup
        lookup = NICKNAME_MAP.get(xlsx_name.lower(), xlsx_name).lower()
        survivor = survivor_map.get(lookup)
        if not survivor:
            # Try first-name match
            for key, surv in survivor_map.items():
                if key.startswith(xlsx_name.lower()):
                    survivor = surv
                    break
        if not survivor:
            print(f'    WARNING: "{xlsx_name}" not found in survivoR data for season {season.number}')
            continue

        for col_name in fantasy_columns:
            cell = str(row[col_name]).strip().lower()
            if cell == 'nan' or not cell:
                continue
            matched_type = None
            for code, ptype in sorted(pick_types.items(), key=lambda x: -len(x[0])):
                if code in cell:
                    matched_type = ptype
                    break
            if matched_type:
                db.session.add(Pick(
                    user_id=users[col_name].id,
                    season_id=season.id,
                    survivor_id=survivor.id,
                    pick_type=matched_type,
                ))
                pick_count += 1

    db.session.commit()
    print(f'  Picks for {season.name}: {len(fantasy_columns)} players, {pick_count} picks')


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


# Map of season number → pick xlsx filename (for --picks-dir loading)
SEASON_PICK_FILES = {
    46: 'season46.xlsx',
    47: 'season47_snakedraft.xlsx',
    49: 'season49_snakedraft.xlsx',
}


def main():
    no_scrape = '--no-scrape' in sys.argv

    # Parse --seasons (default: 46,47,49,50)
    season_nums = [46, 47, 49, 50]
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

        # Load picks from xlsx files if --picks-dir provided
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
                load_picks_from_xlsx(filepath, season, survivor_map)

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
