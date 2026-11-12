"""seed._resolve_survivor: how a pick file's castaway name finds its Survivor.

survivor_map is keyed by lower-cased castaway name, as build_season_from_
survivor_db builds it. The values here are stand-in strings, since only the
lookup is under test.
"""

from seed import _resolve_survivor


def test_season_51_danny_finds_survivors_kilby():
    """survivoR renamed US0760 to Kilby; the league's files still say Danny."""
    season_51 = {"kilby": "Kilby (US0760)", "devin": "Devin"}
    assert _resolve_survivor("Danny", season_51) == "Kilby (US0760)"


def test_a_season_with_its_own_danny_keeps_him():
    """A season with a Danny and no Kilby keeps its Danny, by exact name.

    The prefix match alone would not do: it takes the first name that starts
    with "danny", here another castaway listed ahead of him.
    """
    season = {"danny b": "Danny B", "danny": "Danny (US0652)"}
    assert _resolve_survivor("Danny", season) == "Danny (US0652)"


def test_the_mapped_name_wins_when_the_season_has_both():
    """With both castaways present, the mapping says which one the file meant."""
    both = {"niko": "Niko", "sifu": "Sifu"}
    assert _resolve_survivor("Niko", both) == "Sifu"


def test_a_prefix_still_resolves():
    assert _resolve_survivor("Dee", {"dee dee": "Dee Dee"}) == "Dee Dee"


def test_an_unknown_name_resolves_to_nothing():
    assert _resolve_survivor("Zed", {"ada": "Ada", "bo": "Bo"}) is None
