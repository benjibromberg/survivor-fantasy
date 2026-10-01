"""Tests for seed.py season selection, pick-file loading and the active season."""

import importlib
import json
import sys

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application, db
        db.session.remove()

    if "config" in sys.modules:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(sys.modules["config"])


# ── Helpers ───────────────────────────────────────────────────────────────


def _add_season(db, number, castaways=("Castaway",)):
    """Create a Season with the given castaway names and return it."""
    from app.models import Season, Survivor

    season = Season(number=number, name=f"Season {number}", num_players=18)
    db.session.add(season)
    db.session.flush()
    for name in castaways:
        db.session.add(Survivor(season_id=season.id, name=name, voted_out_order=0))
    db.session.commit()
    return season


def _write_pick_file(picks_dir, filename, picks):
    """Write a pick JSON file and return its path."""
    path = picks_dir / filename
    path.write_text(json.dumps({"scoring": "default", "picks": picks}))
    return path


# ── --seasons parsing ─────────────────────────────────────────────────────


class TestParseSeasonsArg:
    def test_absent_returns_none(self):
        """No --seasons flag means "not specified", not an empty list."""
        from seed import parse_seasons_arg

        assert parse_seasons_arg([]) is None
        assert parse_seasons_arg(["--no-scrape", "--picks-dir", "./picks"]) is None

    def test_space_separated_form(self):
        from seed import parse_seasons_arg

        assert parse_seasons_arg(["--seasons", "46,47"]) == [46, 47]

    def test_equals_form(self):
        from seed import parse_seasons_arg

        assert parse_seasons_arg(["--seasons=46,47"]) == [46, 47]

    def test_flag_without_value_is_treated_as_absent(self):
        from seed import parse_seasons_arg

        assert parse_seasons_arg(["--no-scrape", "--seasons"]) is None


# ── Season list resolution ────────────────────────────────────────────────


class TestResolveSeasonNums:
    def test_default_list_includes_51(self):
        from seed import DEFAULT_SEASONS, resolve_season_nums

        assert 51 in DEFAULT_SEASONS
        assert 51 in resolve_season_nums(None)

    def test_no_pick_files_builds_exactly_the_defaults(self):
        from seed import DEFAULT_SEASONS, resolve_season_nums

        assert resolve_season_nums(None) == sorted(DEFAULT_SEASONS)

    def test_defaults_unioned_with_pick_file_seasons(self):
        """A season with a pick file is built even if it is not a default."""
        from seed import DEFAULT_SEASONS, resolve_season_nums

        assert 48 not in DEFAULT_SEASONS
        assert 52 not in DEFAULT_SEASONS

        result = resolve_season_nums(None, [52, 48])
        assert set(result) == set(DEFAULT_SEASONS) | {48, 52}

    def test_union_is_sorted_without_duplicates(self):
        """A pick file for a default season must not build that season twice."""
        from seed import DEFAULT_SEASONS, resolve_season_nums

        result = resolve_season_nums(None, [52, DEFAULT_SEASONS[0]])
        assert result == sorted(set(result))
        assert len(result) == len(DEFAULT_SEASONS) + 1

    def test_accepts_discovered_pick_files(self, tmp_path):
        """The dict returned by discover_pick_files() feeds straight in."""
        from seed import DEFAULT_SEASONS, discover_pick_files, resolve_season_nums

        (tmp_path / "season52.json").write_text("{}")
        (tmp_path / "season48_snakedraft.json").write_text("{}")

        result = resolve_season_nums(None, discover_pick_files(str(tmp_path)))
        assert set(result) == set(DEFAULT_SEASONS) | {48, 52}

    def test_explicit_seasons_are_not_widened(self):
        """--seasons is authoritative: pick files do not add to it."""
        from seed import resolve_season_nums

        assert resolve_season_nums([46, 47], [51, 52]) == [46, 47]

    def test_explicit_seasons_are_not_widened_by_defaults(self):
        from seed import resolve_season_nums

        assert resolve_season_nums([46]) == [46]


# ── Pick-file loading ─────────────────────────────────────────────────────


class TestLoadPickFiles:
    def test_loads_picks_for_built_seasons(self, app, tmp_path, capsys):
        """Every pick file whose season exists is loaded, with no warning."""
        _, db = app
        from app.models import Pick
        from seed import discover_pick_files, load_pick_files

        season50 = _add_season(db, 50)
        season51 = _add_season(db, 51)
        picks_dir = tmp_path / "picks"
        picks_dir.mkdir()
        for filename in ("season50.json", "season51.json"):
            _write_pick_file(
                picks_dir,
                filename,
                {"PlayerA": [{"survivor": "Castaway", "type": "d"}]},
            )

        load_pick_files(str(picks_dir), discover_pick_files(str(picks_dir)))

        assert {p.season_id for p in Pick.query.all()} == {season50.id, season51.id}
        assert "WARNING" not in capsys.readouterr().out

    def test_warns_when_season_was_not_built(self, app, tmp_path, capsys):
        """A pick file for an unbuilt season is skipped loudly, naming file and season."""
        _, db = app
        from app.models import Pick
        from seed import discover_pick_files, load_pick_files

        _add_season(db, 50)
        picks_dir = tmp_path / "picks"
        picks_dir.mkdir()
        _write_pick_file(
            picks_dir,
            "season51.json",
            {"PlayerA": [{"survivor": "Castaway", "type": "d"}]},
        )

        load_pick_files(str(picks_dir), discover_pick_files(str(picks_dir)))

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "season51.json" in out
        assert "season 51" in out
        assert Pick.query.count() == 0

    def test_skipped_file_does_not_block_other_seasons(self, app, tmp_path, capsys):
        """Only the unbuilt season's file is skipped; the rest still load."""
        _, db = app
        from app.models import Pick
        from seed import discover_pick_files, load_pick_files

        season50 = _add_season(db, 50)
        picks_dir = tmp_path / "picks"
        picks_dir.mkdir()
        _write_pick_file(
            picks_dir,
            "season50.json",
            {"PlayerA": [{"survivor": "Castaway", "type": "d"}]},
        )
        _write_pick_file(
            picks_dir,
            "season51_snakedraft.json",
            {"PlayerB": [{"survivor": "Castaway", "type": "d"}]},
        )

        load_pick_files(str(picks_dir), discover_pick_files(str(picks_dir)))

        out = capsys.readouterr().out
        assert "season51_snakedraft.json" in out
        assert "season50.json" not in out
        assert [p.season_id for p in Pick.query.all()] == [season50.id]


# ── Active season ─────────────────────────────────────────────────────────


class TestResolveActiveSeason:
    def test_defaults_to_highest_built_season(self, app):
        """A requested season that was never built must not win the default."""
        _, db = app
        from seed import resolve_active_season

        _add_season(db, 49)
        _add_season(db, 50)

        assert resolve_active_season().number == 50

    def test_explicit_season_wins(self, app):
        _, db = app
        from seed import resolve_active_season

        _add_season(db, 49)
        _add_season(db, 50)

        assert resolve_active_season(49).number == 49

    def test_explicit_season_not_built_returns_none(self, app):
        _, db = app
        from seed import resolve_active_season

        _add_season(db, 50)

        assert resolve_active_season(51) is None

    def test_no_seasons_returns_none(self, app):
        from seed import resolve_active_season

        assert resolve_active_season() is None
