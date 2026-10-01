"""Castaway links to survivorstatsdb.com."""

from app.models import Survivor


def test_links_the_career_profile_by_castaway_id():
    surv = Survivor(name="Rob", castaway_id="US0771", version_season="US51")

    assert surv.stats_url == "https://survivorstatsdb.com/castaway?id=careerUS0771"


def test_season_is_not_needed():
    surv = Survivor(name="Rob", castaway_id="US0771")

    assert surv.stats_url == "https://survivorstatsdb.com/castaway?id=careerUS0771"


def test_no_link_without_a_castaway_id():
    assert Survivor(name="Unknown").stats_url is None
