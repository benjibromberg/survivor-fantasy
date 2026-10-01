"""Add a season, and optionally its draft, to an existing database.

Unlike seed.py this never drops anything: it backs the database up, adds one
season, and leaves every other season, pick and player alone.

Usage:
    python add_season.py 52                               # Create season 52 (inactive)
    python add_season.py 52 --picks picks/season52.json   # ...and load its draft
    python add_season.py 52 --picks FILE --activate       # ...and make it the active season
    python add_season.py 52 --no-scrape                   # Skip the survivoR download and headshots

If the season already exists, --picks loads a draft into it (only when it has
no picks yet) and --activate activates it. Pick file format: picks/README.md.
"""

import argparse
import json
import os
import sqlite3
import sys
import time

PICK_TYPES = ("d", "w", "pmr_w", "pmr_d")


def backup_database(label):
    """Copy the SQLite database into a backups/ directory beside it.

    Uses the SQLite online backup API, which is consistent in WAL mode with
    the app running (copying the .db file alone is not). Returns the backup
    path, or None when the database is not SQLite.
    """
    from app.models import db

    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    db_path = os.path.realpath(url.database)
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir, f"survivor_fantasy-{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        if dst.execute("pragma integrity_check").fetchone() != ("ok",):
            raise RuntimeError(f"backup {backup_path} failed its integrity check")
    finally:
        src.close()
        dst.close()
    return backup_path


def create_season(number, scrape=True):
    """Create a season and populate it from survivoR. Returns the Season.

    The season is created inactive, so the homepage keeps showing the current
    season until activate() is called. If the data load fails, the empty
    season is removed again and the error is re-raised.
    """
    from app import data
    from app.models import Season, Survivor, db

    if number < 41:
        raise ValueError("Only new-era seasons (41+) are supported.")
    if Season.query.filter_by(number=number).first():
        raise ValueError(f"Season {number} already exists.")

    if scrape:
        data.download_survivor_data()

    season = Season(number=number, name=f"Season {number}", is_active=False)
    db.session.add(season)
    db.session.commit()
    season_id = season.id
    try:
        count, day_warnings = data.refresh_season(season)
    except Exception:
        db.session.rollback()
        Survivor.query.filter_by(season_id=season_id).delete()
        db.session.delete(db.session.get(Season, season_id))
        db.session.commit()
        raise
    print(f"  {season.name}: {count} castaways")
    for warning in day_warnings:
        print(f"    WARNING: {warning}")

    if scrape:
        matched = data.generate_season_images(season)
        print(f"  Headshots: {matched}/{count}")
    return season


def validate_pick_file(data, castaway_names):
    """Return a list of problems with a pick file, empty when it is loadable.

    seed.load_picks_from_json is forgiving: it prefix-matches castaway names,
    rewrites some through NICKNAME_MAP, and skips what it cannot place with a
    warning. Loading a draft into a live database should not guess, so every
    name here has to be an exact castaway name and every type a known code.
    """
    from seed import NICKNAME_MAP

    problems = []
    names = set(castaway_names)

    def check_name(player, name, what):
        remapped = NICKNAME_MAP.get(str(name).lower(), name)
        if str(remapped).lower() != str(name).lower():
            problems.append(
                f'{player}: {what} "{name}" would be remapped to "{remapped}" '
                "by the loader; use the castaway's exact name"
            )
        elif name not in names:
            problems.append(
                f'{player}: {what} "{name}" is not a castaway in this season '
                "(names are case-sensitive and must match exactly)"
            )

    picks = data.get("picks") if isinstance(data, dict) else None
    if not isinstance(picks, dict) or not picks:
        return ['the file has no "picks" object keyed by player name']

    for player, entries in picks.items():
        for entry in entries:
            check_name(player, entry.get("survivor"), "pick")
            if entry.get("type") not in PICK_TYPES:
                problems.append(
                    f'{player}: pick type "{entry.get("type")}" is not one of '
                    f"{', '.join(PICK_TYPES)}"
                )

    for player, entries in (data.get("sole_survivor_picks") or {}).items():
        for entry in entries:
            check_name(player, entry.get("survivor"), "Sole Survivor pick")
            if not isinstance(entry.get("episode"), int):
                problems.append(
                    f"{player}: Sole Survivor pick needs an integer episode"
                )
    return problems


def load_draft(season, filepath):
    """Load a pick file into a season that has no picks yet.

    Validates the whole file first and raises ValueError, before writing
    anything, if any entry would not load exactly as written.
    """
    from app.models import Pick, SoleSurvivorPick, Survivor
    from seed import load_picks_from_json

    filepath = os.path.realpath(filepath)
    with open(filepath) as f:
        data = json.load(f)

    if (
        Pick.query.filter_by(season_id=season.id).count()
        or SoleSurvivorPick.query.filter_by(season_id=season.id).count()
    ):
        raise ValueError(
            f"Season {season.number} already has picks; edit them in the admin "
            "panel instead of loading a file over them."
        )

    survivors = Survivor.query.filter_by(season_id=season.id).all()
    problems = validate_pick_file(data, [s.name for s in survivors])
    if problems:
        raise ValueError(
            f"{os.path.basename(filepath)} was not loaded:\n  " + "\n  ".join(problems)
        )

    load_picks_from_json(filepath, season, {s.name.lower(): s for s in survivors})

    # Read the result back from the database and compare it with the file
    type_codes = {"draft": "d", "wildcard": "w", "pmr_w": "pmr_w", "pmr_d": "pmr_d"}
    wanted = sorted(
        (player.lower(), entry["survivor"], entry["type"])
        for player, entries in data["picks"].items()
        for entry in entries
    )
    got = sorted(
        (pick.user.username, pick.survivor.name, type_codes[pick.pick_type])
        for pick in Pick.query.filter_by(season_id=season.id)
    )
    if got != wanted:
        raise RuntimeError(
            f"Season {season.number}: the picks in the database do not match "
            f"{os.path.basename(filepath)} after loading"
        )


def activate(season):
    """Make this the only active season (same effect as the admin toggle).

    Also looks up when its Episode 2 starts, which opens wildcard self-service.
    """
    from app.models import Season, db
    from app.schedule import sync_episode2_time

    Season.query.filter(Season.id != season.id).update({"is_active": False})
    season.is_active = True
    db.session.commit()
    sync_episode2_time(season)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Add a season to an existing database without re-seeding."
    )
    parser.add_argument("season", type=int, help="season number, e.g. 52")
    parser.add_argument("--picks", help="pick JSON file to load into the season")
    parser.add_argument(
        "--activate", action="store_true", help="make this the active season"
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="skip the survivoR download and the headshot fetch",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()

    from app import create_app
    from app.models import Season

    app = create_app()
    with app.app_context():
        backup = backup_database(f"pre-s{args.season}")
        print(f"Backup: {backup or 'skipped (not a SQLite database)'}")

        try:
            season = Season.query.filter_by(number=args.season).first()
            if season:
                print(f"Season {args.season} already exists; not recreating it.")
            else:
                print(f"Creating season {args.season}...")
                season = create_season(args.season, scrape=not args.no_scrape)

            if args.picks:
                load_draft(season, args.picks)

            if args.activate:
                activate(season)
                print(f"Active season: {season.name}")
            elif not season.is_active:
                print(
                    f"{season.name} is inactive. Re-run with --activate, or use "
                    "the toggle on the admin seasons page, when it is ready."
                )
        except ValueError as e:
            sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
