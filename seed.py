"""Seed the database from survivoR.xlsx (all survivor data) and optional JSON files (pick assignments).

Usage:
    python seed.py                          # Build DEFAULT_SEASONS, no picks
    python seed.py --picks-dir ./picks      # Also load pick JSON files from directory
    python seed.py --no-scrape              # Skip network calls
    python seed.py --seasons 46,47,49,50    # Build exactly these seasons
    python seed.py --active=49              # Mark this season active

Without --seasons, the seasons built are DEFAULT_SEASONS plus, when --picks-dir
is given, every season that has a pick file in that directory.  Without
--active, the highest season that was built is marked active.
"""

import json
import os
import sys
from datetime import UTC, datetime

import pandas as pd
import requests as http_requests
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.data import (
    PLAYERS_FILE,
    SURVIVOR_DATA_FILE,
    _build_nickname_map,
    compute_castaway_stats,
    generate_all_season_images,
    get_idol_ids,
    refresh_season,
    us_season_filter,
)
from app.models import (
    TEAM_NAME_MAX_LENGTH,
    Pick,
    Season,
    SoleSurvivorPick,
    Survivor,
    TeamName,
    User,
    db,
    normalize_email,
)

SURVIVOR_DATA_URL = (
    "https://github.com/doehm/survivoR/raw/refs/heads/master/dev/xlsx/survivoR.xlsx"
)

# Seasons built when --seasons is not given (see resolve_season_nums)
DEFAULT_SEASONS = [45, 46, 47, 49, 50, 51]

# Nickname mapping: legacy xlsx shorthand → survivoR castaway name
NICKNAME_MAP = {
    "tiff": "Tiffany",
    "jem": "Jem",
    "jess": "Jess",
    "jelinsky": "Jelinsky",
    "tk": "TK",
    "sol": "Sol",
    "mc": "MC",
    "annie": "Annie",
    "soph": "Soph",
    "brandon donlon": "Brandon",
    "brandon meyer": "Brando",
    "niko": "Sifu",
}


def ensure_survivor_data():
    """Download survivoR.xlsx from GitHub if it doesn't exist locally."""
    if os.path.exists(SURVIVOR_DATA_FILE):
        return
    print(f"Downloading {SURVIVOR_DATA_FILE} from survivoR GitHub repo...")
    resp = http_requests.get(SURVIVOR_DATA_URL, timeout=120)
    resp.raise_for_status()
    with open(SURVIVOR_DATA_FILE, "wb") as f:
        f.write(resp.content)
    print(f"Downloaded {SURVIVOR_DATA_FILE} ({len(resp.content) / 1024 / 1024:.1f} MB)")


def load_survivor_ref():
    """Load reference tables from survivoR.xlsx."""
    castaways = pd.read_excel(SURVIVOR_DATA_FILE, "Castaways")
    tribe_colours = pd.read_excel(SURVIVOR_DATA_FILE, "Tribe Colours")
    season_summary = pd.read_excel(SURVIVOR_DATA_FILE, "Season Summary")
    confessionals = pd.read_excel(SURVIVOR_DATA_FILE, "Confessionals")
    vote_history = pd.read_excel(SURVIVOR_DATA_FILE, "Vote History")
    challenge_results = pd.read_excel(SURVIVOR_DATA_FILE, "Challenge Results")
    advantage_movement = pd.read_excel(SURVIVOR_DATA_FILE, "Advantage Movement")
    advantage_details = pd.read_excel(SURVIVOR_DATA_FILE, "Advantage Details")
    castaway_details = pd.read_excel(SURVIVOR_DATA_FILE, "Castaway Details")
    return (
        castaways,
        tribe_colours,
        season_summary,
        confessionals,
        vote_history,
        challenge_results,
        advantage_movement,
        advantage_details,
        castaway_details,
    )


def build_season_from_survivor_db(season_number, ref_data):
    """Create a Season and all its Survivors from survivoR data."""
    if season_number < 41:
        raise ValueError(
            f"Season {season_number}: only new-era seasons (41+) are supported."
        )
    (
        castaways,
        tribe_colours,
        season_summary,
        confessionals,
        vote_history,
        challenge_results,
        advantage_movement,
        advantage_details,
        castaway_details,
    ) = ref_data
    cast = us_season_filter(castaways, season_number).copy()
    if cast.empty:
        raise ValueError(f"No survivoR data for season {season_number}")

    # Season metadata
    ss = us_season_filter(season_summary, season_number)
    season_name = (
        ss.iloc[0]["season_name"] if not ss.empty else f"Season {season_number}"
    )
    # Clean up name (e.g. "Survivor: 49" → "Season 49")
    if ":" in str(season_name):
        season_name = season_name.split(":")[-1].strip()
        if season_name.isdigit():
            season_name = f"Season {season_name}"

    if ss.empty:
        raise ValueError(
            f"Season {season_number}: no Season Summary data in survivoR. Only new-era seasons (41+) are supported."
        )
    row = ss.iloc[0]
    if pd.isna(row["n_cast"]):
        raise ValueError(
            f"Season {season_number}: survivoR data missing n_cast. Only new-era seasons (41+) are supported."
        )
    n_cast = int(row["n_cast"])
    # For in-progress seasons, n_jury/n_finalists may be NaN — store as None
    n_finalists = int(row["n_finalists"]) if pd.notna(row["n_finalists"]) else None
    n_jury = int(row["n_jury"]) if pd.notna(row["n_jury"]) else None
    left_at_jury = (
        (n_jury + n_finalists)
        if (n_jury is not None and n_finalists is not None)
        else None
    )

    # Build tribe color lookup
    tc = us_season_filter(tribe_colours, season_number)
    tribe_color_map = {}
    for _, row in tc.iterrows():
        tribe_color_map[row["tribe"]] = row["tribe_colour"]

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
    details_by_id = {
        r["castaway_id"]: r
        for _, r in castaway_details.iterrows()
        if pd.notna(r.get("castaway_id"))
    }

    # Create survivors (apply nicknames for returning players)
    nickname_map = _build_nickname_map()
    survivor_map = {}  # castaway name (lower) → Survivor
    for _, row in cast.iterrows():
        cid = row["castaway_id"] if pd.notna(row.get("castaway_id")) else None
        name = nickname_map.get(cid, row["castaway"]) if cid else row["castaway"]
        order = int(row["order"]) if pd.notna(row["order"]) else 0
        made_jury = bool(row["jury"]) if pd.notna(row["jury"]) else False
        place = int(row["place"]) if pd.notna(row["place"]) else None
        tribe = row["original_tribe"] if pd.notna(row.get("original_tribe")) else None
        tribe_color = tribe_color_map.get(tribe)
        full_name = row["full_name"] if pd.notna(row.get("full_name")) else None
        castaway_id = row["castaway_id"] if pd.notna(row.get("castaway_id")) else None
        version_season = (
            row["version_season"] if pd.notna(row.get("version_season")) else None
        )
        result = row["result"] if pd.notna(row.get("result")) else None
        elimination_episode = (
            int(row["episode"]) if pd.notna(row.get("episode")) else None
        )
        day_voted_out = int(row["day"]) if pd.notna(row.get("day")) else None

        # Bio data
        details = details_by_id.get(cid)
        occupation = (
            details["occupation"]
            if details is not None and pd.notna(details.get("occupation"))
            else None
        )
        personality_type = (
            details["personality_type"]
            if details is not None and pd.notna(details.get("personality_type"))
            else None
        )

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
            age=int(row["age"]) if pd.notna(row.get("age")) else None,
            city=row["city"] if pd.notna(row.get("city")) else None,
            state=row["state"] if pd.notna(row.get("state")) else None,
            occupation=occupation,
            personality_type=personality_type,
            elimination_episode=elimination_episode,
            day_voted_out=day_voted_out,
        )
        db.session.add(surv)
        db.session.flush()
        survivor_map[name.lower()] = surv

    # Populate aggregate stats
    def us(df):
        return us_season_filter(df, season_number)

    idol_ids = get_idol_ids(advantage_details, season_number)
    stats = compute_castaway_stats(
        us(confessionals),
        us(vote_history),
        us(challenge_results),
        us(advantage_movement),
        idol_ids,
    )
    conf_totals = stats["conf_totals"]
    votes_against = stats["votes_against"]
    indiv_imm = stats["indiv_imm"]
    tribal_imm = stats["tribal_imm"]
    idols_found = stats["idols_found"]
    adv_found = stats["adv_found"]
    adv_played = stats["adv_played"]

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


def _get_or_create_player(player_name):
    """Return the fantasy player with this name, creating them if needed.

    Names match case-insensitively; the name as written becomes the display name.
    """
    user = User.query.filter_by(username=player_name.lower()).first()
    if not user:
        user = User(username=player_name.lower(), display_name=player_name)
        db.session.add(user)
        db.session.flush()
    return user


def _parse_utc(value):
    """Parse an ISO date-time from a pick file into naive UTC.

    A value with an offset (or Z) is converted; one without is taken as UTC.
    Raises ValueError for anything else.
    """
    if not isinstance(value, str):
        raise ValueError("expected an ISO date-time string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def load_picks_from_json(filepath, season, survivor_map):
    """Load pick assignments, scoring config, and SS picks from a JSON file.

    JSON format:
        {"scoring": "legacy"|"default"|"custom",
         "scoring_config": {...},  # only when scoring="custom"
         "picks": {"Player": [{"survivor": "Name", "type": "d", "order": 1}, ...]},
         "sole_survivor_picks": {"Player": [{"survivor": "Name", "episode": 1}, ...]},
         "team_names": {"Player": "Team Name"},
         "episode2_starts_at": "2026-10-08T00:00:00Z"}

    Type codes: d=draft, w=wildcard, pmr_w=pmr_w, pmr_d=pmr_d
    """
    import json as _json

    from app.scoring.classic import DEFAULT_CONFIG, LEGACY_CONFIG

    filepath = os.path.realpath(filepath)
    with open(filepath) as f:
        data = _json.load(f)

    # Apply scoring config
    scoring = data.get("scoring", "default")
    if scoring == "legacy":
        season.scoring_config = _json.dumps(LEGACY_CONFIG)
    elif scoring == "custom" and "scoring_config" in data:
        season.scoring_config = _json.dumps(data["scoring_config"])
    else:
        season.scoring_config = _json.dumps(DEFAULT_CONFIG)

    picks_data = data.get("picks", data)  # fallback to top-level if no 'picks' key
    pick_types = {"d": "draft", "w": "wildcard", "pmr_w": "pmr_w", "pmr_d": "pmr_d"}
    pick_count = 0

    for player_name, picks in picks_data.items():
        user = _get_or_create_player(player_name)

        for entry in picks:
            surv_name = entry["survivor"]
            survivor = _resolve_survivor(surv_name, survivor_map)
            if not survivor:
                print(
                    f'    WARNING: "{surv_name}" not found in survivoR data for season {season.number}'
                )
                continue

            # Match pick type
            cell = entry["type"].strip().lower()
            matched_type = None
            for code, ptype in sorted(pick_types.items(), key=lambda x: -len(x[0])):
                if code in cell:
                    matched_type = ptype
                    break
            if matched_type:
                db.session.add(
                    Pick(
                        user_id=user.id,
                        season_id=season.id,
                        survivor_id=survivor.id,
                        pick_type=matched_type,
                        pick_order=entry.get("order"),
                    )
                )
                pick_count += 1

    # Load sole survivor picks
    ss_count = 0
    ss_data = data.get("sole_survivor_picks", {})
    for player_name, ss_picks in ss_data.items():
        user = _get_or_create_player(player_name)

        for entry in ss_picks:
            survivor = _resolve_survivor(entry["survivor"], survivor_map)
            if not survivor:
                print(
                    f'    WARNING: SS pick "{entry["survivor"]}" not found for season {season.number}'
                )
                continue
            db.session.add(
                SoleSurvivorPick(
                    user_id=user.id,
                    season_id=season.id,
                    survivor_id=survivor.id,
                    episode=entry["episode"],
                )
            )
            ss_count += 1

    # Restore team names
    team_count = 0
    team_names = data.get("team_names") or {}
    if not isinstance(team_names, dict):
        print(f"    WARNING: team_names for season {season.number} is not an object")
        team_names = {}
    for player_name, raw in team_names.items():
        name = TeamName.clean(raw) if isinstance(raw, str) else ""
        if not name or len(name) > TEAM_NAME_MAX_LENGTH:
            print(
                f'    WARNING: team name for "{player_name}" skipped for season '
                f"{season.number} (empty, not text, or over "
                f"{TEAM_NAME_MAX_LENGTH} characters)"
            )
            continue
        user = _get_or_create_player(player_name)
        TeamName.set_for(user.id, season.id, name)
        team_count += 1

    # Restore the Episode 2 start time (opens wildcard self-service)
    if data.get("episode2_starts_at") is not None:
        try:
            season.episode2_starts_at = _parse_utc(data["episode2_starts_at"])
        except ValueError as e:
            print(
                f"    WARNING: episode2_starts_at for season {season.number} "
                f"skipped: {e}"
            )

    db.session.commit()
    ss_msg = f", {ss_count} SS picks" if ss_count else ""
    team_msg = f", {team_count} team names" if team_count else ""
    print(
        f"  Picks for {season.name}: {len(picks_data)} players, "
        f"{pick_count} picks{ss_msg}{team_msg}"
    )


def generate_image_urls():
    """Mirror headshots for every season (see app.data.generate_season_images)."""
    for number, (matched, total) in generate_all_season_images().items():
        print(f"  Season {number}: {matched}/{total} images")


def discover_pick_files(picks_dir):
    """Auto-discover pick JSON files in a directory.

    Scans for season*.json files and extracts the season number from the
    filename.  When multiple files match the same season (e.g. season47.json
    and season47_snakedraft.json), the canonical ``season{N}.json`` name wins
    — this is the format produced by ``export_season_picks()``.
    """
    import glob
    import re

    files = {}
    for path in sorted(glob.glob(os.path.join(picks_dir, "season*.json"))):
        basename = os.path.basename(path)
        match = re.match(r"season(\d+)", basename)
        if not match:
            continue
        num = int(match.group(1))
        # Prefer exact season{N}.json (auto-exported) over suffixed variants
        if num not in files or basename == f"season{num}.json":
            files[num] = path
    return files


def load_pick_files(picks_dir, pick_files):
    """Load each discovered pick file into its season.

    ``pick_files`` maps season number to path, as returned by
    ``discover_pick_files()``.  A file whose season was not built (left out of
    --seasons, or missing from survivoR) is skipped with a warning: the tables
    that held those picks are already gone, so a silent skip loses them.
    """
    picks_dir = os.path.realpath(picks_dir)
    for snum, filepath in sorted(pick_files.items()):
        filepath = os.path.realpath(filepath)
        if not filepath.startswith(picks_dir):
            print(
                f"  Skipping {os.path.basename(filepath)}: path escapes picks directory"
            )
            continue
        season = Season.query.filter_by(number=snum).first()
        if not season:
            print(
                f"  WARNING: {os.path.basename(filepath)} skipped: season {snum} "
                "was not built, so its picks were NOT loaded"
            )
            continue
        survivor_map = {
            s.name.lower(): s for s in Survivor.query.filter_by(season_id=season.id)
        }
        load_picks_from_json(filepath, season, survivor_map)


def load_player_emails(picks_dir):
    """Restore linked login emails from players.json in picks_dir.

    The file is written by export_all_picks(). A player named in it who does
    not exist yet is created, so a linked player with no picks survives a
    re-seed. Returns the number of emails restored.

    A missing file is not an error. An unreadable one is reported and skipped:
    by this point the tables are already rebuilt, and the picks matter more
    than the emails, which the admin can link again.
    """
    picks_dir = os.path.realpath(picks_dir)
    filepath = os.path.realpath(os.path.join(picks_dir, PLAYERS_FILE))
    if not filepath.startswith(picks_dir) or not os.path.isfile(filepath):
        return 0

    try:
        with open(filepath) as f:
            players = json.load(f)["players"]
        if not isinstance(players, dict):
            raise ValueError('"players" is not an object')
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"  WARNING: {PLAYERS_FILE} could not be read, no emails restored: {e}")
        return 0

    restored = 0
    try:
        for player_name, entry in players.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("email"), str):
                print(f'  WARNING: {PLAYERS_FILE} entry for "{player_name}" skipped')
                continue
            email = normalize_email(entry["email"])
            if not email:
                continue
            user = _get_or_create_player(player_name)
            owner = User.query.filter(User.email == email, User.id != user.id).first()
            if owner:
                print(
                    f'  WARNING: email for "{player_name}" skipped: already linked '
                    f"to {owner.display_name or owner.username}"
                )
                continue
            user.email = email
            restored += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return restored


def parse_seasons_arg(argv):
    """Return the season numbers given via --seasons, or None if it is absent.

    Accepts both ``--seasons 46,47`` and ``--seasons=46,47``.
    """
    seasons = None
    for idx, arg in enumerate(argv):
        if arg.startswith("--seasons="):
            seasons = [int(s) for s in arg.split("=")[1].split(",")]
        elif arg == "--seasons" and idx + 1 < len(argv):
            seasons = [int(s) for s in argv[idx + 1].split(",")]
    return seasons


def resolve_season_nums(explicit_seasons, pick_file_seasons=()):
    """Decide which seasons to build.

    An explicit --seasons list is authoritative.  Otherwise build
    DEFAULT_SEASONS plus every season that has a pick file, so a season with
    picks cannot drop out of a default re-seed (which wipes every table).
    """
    if explicit_seasons is not None:
        return list(explicit_seasons)
    return sorted(set(DEFAULT_SEASONS) | set(pick_file_seasons))


def resolve_active_season(active_season=None):
    """Return the Season to mark active, or None if there is none.

    --active names it explicitly.  The default is the highest season that was
    actually built, not the highest requested: a requested season that
    survivoR has no data for yet would otherwise leave no season active.
    """
    if active_season is not None:
        return Season.query.filter_by(number=active_season).first()
    return Season.query.order_by(Season.number.desc()).first()


def count_pick_rows():
    """Count pick rows (regular and Sole Survivor) in the current database.

    Runs COUNT(*) only on the tables that exist, so a database with no tables
    yet counts as zero instead of raising.
    """
    from sqlalchemy import func, inspect, select

    existing = set(inspect(db.engine).get_table_names())
    total = 0
    for model in (Pick, SoleSurvivorPick):
        table = model.__table__
        if table.name in existing:
            total += db.session.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
    return total


def count_linked_emails():
    """Count players with a linked login email in the current database.

    Zero when the user table or its email column does not exist yet.
    """
    from sqlalchemy import func, inspect, select

    inspector = inspect(db.engine)
    table = User.__table__
    if table.name not in inspector.get_table_names():
        return 0
    if "email" not in {c["name"] for c in inspector.get_columns(table.name)}:
        return 0
    return db.session.execute(
        select(func.count()).select_from(table).where(table.c.email.isnot(None))
    ).scalar_one()


def export_picks_before_drop():
    """Export all picks ahead of db.drop_all() and return the paths written.

    Fails closed: a failed export is only skipped when the database holds no
    picks and no linked emails (first run, or nothing entered yet). If either
    exists and could not be backed up, exit non-zero so the caller never
    reaches the drop.
    """
    from app.data import default_picks_dir, export_all_picks

    try:
        return export_all_picks()
    except Exception as e:
        db.session.rollback()
        pick_rows = count_pick_rows()
        linked_emails = count_linked_emails()
        if pick_rows or linked_emails:
            sys.exit(
                f"Error: could not export picks to {default_picks_dir()}: {e}\n"
                f"The database holds {pick_rows} pick row(s) and {linked_emails} "
                "linked email(s) that re-seeding would delete, so nothing was "
                "dropped. Fix the export and run seed.py again."
            )
        print(f"Pick export skipped (no picks in the database): {e}")
        return []


def main():
    no_scrape = "--no-scrape" in sys.argv

    if not no_scrape:
        ensure_survivor_data()

    # Parse --seasons (None if absent: the default list is resolved below,
    # once the pick files are known)
    explicit_seasons = parse_seasons_arg(sys.argv[1:])

    # Parse --picks-dir
    picks_dir = None
    for arg in sys.argv[1:]:
        if arg.startswith("--picks-dir="):
            picks_dir = arg.split("=", 1)[1]
        elif arg == "--picks-dir":
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                picks_dir = sys.argv[idx + 1]

    # Parse --active (which season to mark active, default: highest built)
    active_season = None
    for arg in sys.argv[1:]:
        if arg.startswith("--active="):
            active_season = int(arg.split("=")[1])

    app = create_app()
    with app.app_context():
        # Safety net: export all picks before dropping tables. Exits here,
        # before the drop, if picks exist and cannot be backed up.
        exported = export_picks_before_drop()
        if exported:
            print(
                f"Auto-exported picks for {len(exported)} season(s) "
                f"to {os.path.dirname(exported[0])}"
            )
            for p in exported:
                print(f"  {p}")

        # Check --picks-dir while the old tables still exist. It runs after the
        # export because the export may have just created the directory.
        if picks_dir:
            picks_dir = os.path.realpath(picks_dir)
            if not os.path.isdir(picks_dir):
                print(f"Error: --picks-dir {picks_dir} is not a directory")
                sys.exit(1)

        print("Dropping and recreating all tables...")
        db.drop_all()
        db.create_all()

        if not no_scrape:
            print("Downloading latest survivoR.xlsx...")
            resp = http_requests.get(SURVIVOR_DATA_URL, timeout=30)
            resp.raise_for_status()
            with open(SURVIVOR_DATA_FILE, "wb") as f:
                f.write(resp.content)

        print("Loading survivoR reference data...")
        ref_data = load_survivor_ref()

        # Discover pick files before building: without --seasons, every season
        # that has a pick file is built alongside DEFAULT_SEASONS
        pick_files = {}
        if picks_dir:
            pick_files = discover_pick_files(picks_dir)
        season_nums = resolve_season_nums(explicit_seasons, pick_files)

        # Build seasons from survivoR data
        print("\nBuilding seasons from survivoR database...")
        for snum in season_nums:
            try:
                season, smap = build_season_from_survivor_db(snum, ref_data)
                print(
                    f"  {season.name}: {season.num_players} survivors, "
                    f"{sum(1 for s in smap.values() if s.tribe)} with tribes"
                )
            except ValueError as e:
                print(f"  Skipping season {snum}: {e}")

        # Load picks from JSON files if --picks-dir provided
        if picks_dir:
            print(f"\nLoading pick assignments from {picks_dir}...")
            load_pick_files(picks_dir, pick_files)
            restored = load_player_emails(picks_dir)
            if restored:
                print(f"  Linked emails restored for {restored} player(s)")

        # Enrich all seasons with episode_stats, elimination_episode, etc.
        print("\nRunning refresh_season for per-episode data...")
        for season in Season.query.all():
            _, day_warnings = refresh_season(season)
            print(f"  {season.name}: refreshed")
            for w in day_warnings:
                print(f"    WARNING: {w}")

        # Mark active season
        active = resolve_active_season(active_season)
        if active:
            active.is_active = True
            db.session.commit()
            print(f"\nActive season: {active.name}")

        if not no_scrape:
            print("\nGenerating image URLs...")
            generate_image_urls()
        else:
            print("Skipping image URL generation (--no-scrape)")

        # Set admin from env var (Cloudflare Access email)
        admin_email = os.environ.get("ADMIN_EMAIL", "").lower()
        if admin_email:
            admin = User.query.filter_by(username=admin_email).first()
            if not admin:
                admin = User(
                    username=admin_email,
                    display_name=admin_email.split("@")[0],
                    is_admin=True,
                )
                db.session.add(admin)
            else:
                admin.is_admin = True
            db.session.commit()

        print(
            f"\nDone! {User.query.count()} users, {Season.query.count()} seasons, "
            f"{Survivor.query.count()} survivors, {Pick.query.count()} picks"
        )


if __name__ == "__main__":
    main()
