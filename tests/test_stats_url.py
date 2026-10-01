"""Castaway links to survivorstatsdb.com."""

from app.models import Survivor


def test_links_the_season_page():
    surv = Survivor(name="Rob", castaway_id="US0771", version_season="US51")

    assert surv.stats_url == "https://survivorstatsdb.com/castaway?id=US51US0771"


def test_falls_back_to_the_career_page_without_a_season():
    surv = Survivor(name="Rob", castaway_id="US0771")

    assert surv.stats_url == "https://survivorstatsdb.com/castaway?id=careerUS0771"


def test_no_link_without_a_castaway_id():
    assert Survivor(name="Unknown", version_season="US51").stats_url is None
