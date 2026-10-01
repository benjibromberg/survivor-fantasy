"""Tribes for a season, from Tribe Mapping or, when it is empty, Boot Mapping."""

import pandas as pd

from app.data import first_tribe, season_tribe_rows

TM_COLS = [
    "version",
    "season",
    "episode",
    "day",
    "castaway_id",
    "tribe",
    "tribe_status",
]
BM_COLS = [
    "version",
    "season",
    "episode",
    "order",
    "castaway_id",
    "tribe",
    "tribe_status",
    "game_status",
]


def _tm(rows):
    return pd.DataFrame(rows, columns=TM_COLS)


def _bm(rows):
    return pd.DataFrame(rows, columns=BM_COLS)


EMPTY_TM = _tm([])


class TestSeasonTribeRows:
    def test_uses_tribe_mapping_when_it_has_the_season(self):
        tm = _tm([("US", 60, 1, 2, "A", "Savu", "Original")])
        bm = _bm([("US", 60, 1, 0, "A", "Toka", "Original", "In the game")])

        rows = season_tribe_rows(tm, bm, 60)

        assert rows[["castaway_id", "tribe"]].values.tolist() == [["A", "Savu"]]

    def test_falls_back_to_boot_mapping_when_tribe_mapping_has_no_rows(self):
        """survivoR filled Boot Mapping for Season 51 before Tribe Mapping."""
        tm = _tm([("US", 59, 1, 2, "Z", "Old", "Original")])  # another season only
        bm = _bm(
            [
                ("US", 60, 1, 0, "A", "Savu", "Original", "In the game"),
                ("US", 60, 1, 0, "B", "Toka", "Original", "In the game"),
            ]
        )

        rows = season_tribe_rows(tm, bm, 60)

        assert sorted(
            rows[["castaway_id", "tribe", "tribe_status"]].values.tolist()
        ) == [
            ["A", "Savu", "Original"],
            ["B", "Toka", "Original"],
        ]
        assert list(rows["episode"]) == [1, 1]

    def test_boot_mapping_keeps_the_state_after_the_last_boot_of_an_episode(self):
        """A double-boot episode has a row per boot; the later row wins."""
        bm = _bm(
            [
                ("US", 60, 8, 12, "A", "Toka", "Original", "In the game"),
                ("US", 60, 8, 13, "A", "Fiji", "Merged", "In the game"),
            ]
        )

        rows = season_tribe_rows(EMPTY_TM, bm, 60)

        assert rows[["episode", "tribe", "tribe_status"]].values.tolist() == [
            [8, "Fiji", "Merged"]
        ]

    def test_no_tribe_placeholder_becomes_no_tribe(self):
        """A castaway sent to Exile Island at the start is tribe 'No Tribe'."""
        bm = _bm([("US", 60, 1, 0, "A", "No Tribe", "Exile Island", "Exile Island")])

        rows = season_tribe_rows(EMPTY_TM, bm, 60)

        assert pd.isna(rows.iloc[0]["tribe"])
        assert rows.iloc[0]["tribe_status"] == "Exile Island"

    def test_other_versions_and_seasons_are_ignored(self):
        bm = _bm(
            [
                ("AU", 60, 1, 0, "A", "Savu", "Original", "In the game"),
                ("US", 59, 1, 0, "B", "Toka", "Original", "In the game"),
            ]
        )

        assert season_tribe_rows(EMPTY_TM, bm, 60).empty

    def test_empty_when_neither_sheet_has_the_season(self):
        assert season_tribe_rows(EMPTY_TM, _bm([]), 60).empty


class TestFirstTribe:
    def test_earliest_episode_with_a_tribe(self):
        by_ep = {3: ("Fiji", "#f00", "Swapped"), 1: ("Savu", "#00f", "Original")}

        assert first_tribe(by_ep) == "Savu"

    def test_skips_episodes_without_a_tribe(self):
        by_ep = {1: (None, "", "Exile Island"), 2: ("Toka", "#ff0", "Original")}

        assert first_tribe(by_ep) == "Toka"

    def test_none_when_no_episode_has_a_tribe(self):
        assert first_tribe({}) is None
        assert first_tribe({1: (None, "", "Exile Island")}) is None
