"""Tests for seed.py season selection."""

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
