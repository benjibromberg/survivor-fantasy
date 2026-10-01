"""The castaway sheet: hero header, tabs, and the Episodes table.

The episode_stats fixtures below are built in the shape the real column
actually has, which is the whole point of them. Its totals are CUMULATIVE,
and it keeps repeating the final totals for every episode after the castaway
is voted out. A fixture that stored per-episode counts, or that stopped at the
boot, would agree with a wrong implementation and pass.
"""

import importlib
import json
import re
import sys
from types import SimpleNamespace

import pytest

ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


def _cumulative(per_episode, total_episodes):
    """Turn per-episode activity into the cumulative, carried-forward blob."""
    blob, running = {}, {}
    for ep in range(1, total_episodes + 1):
        for key, val in per_episode.get(ep, {}).items():
            running[key] = running.get(key, 0) + val
        blob[str(ep)] = {
            "conf": running.get("conf", 0),
            "ii": running.get("ii", 0),
            "ti": running.get("ti", 0),
            "idol": running.get("idol", 0),
            "adv": running.get("adv", 0),
            "votes": running.get("votes", 0),
            "tribe": "Lulu",
            "tribe_color": "#fde732",
        }
    return json.dumps(blob)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def league(client):
    """One player, two castaways: one voted out in episode 2, one still in.

    Both carry episode_stats out to episode 5, because that is what the real
    column does regardless of when a castaway left.
    """
    from app.models import Pick, Season, Survivor, User

    c, db = client
    season = Season(number=98, name="Season 98", is_active=True, num_players=4)
    db.session.add(season)
    db.session.flush()

    boot = Survivor(
        season_id=season.id,
        name="Boot",
        full_name="Boot McBoot",
        voted_out_order=1,
        elimination_episode=2,
        castaway_id="US980",
        version_season="US98",
        age=31,
        occupation="Waiter;Photographer",
        episode_stats=_cumulative({1: {"conf": 5}, 2: {"conf": 3, "votes": 6}}, 5),
    )
    runner = Survivor(
        season_id=season.id,
        name="Runner",
        full_name="Runner Up",
        voted_out_order=0,
        castaway_id="US981",
        version_season="US98",
        age=29,
        occupation="Teacher",
        episode_stats=_cumulative(
            {1: {"conf": 2}, 2: {"conf": 4, "ii": 1}, 3: {"conf": 1}}, 5
        ),
    )
    quiet = Survivor(
        season_id=season.id,
        name="Quiet",
        voted_out_order=0,
        castaway_id="US982",
        version_season="US98",
        episode_stats=_cumulative({}, 5),
    )
    # The winner carries an elimination_episode too: the finale is where their
    # game ended. So do both runners-up and whoever loses the fire challenge.
    champ = Survivor(
        season_id=season.id,
        name="Champ",
        full_name="Champ Winner",
        voted_out_order=4,
        elimination_episode=5,
        result="Sole Survivor",
        castaway_id="US983",
        version_season="US98",
        episode_stats=_cumulative({1: {"conf": 3}, 5: {"conf": 2, "ii": 1}}, 5),
    )
    pat = User(username="pat", display_name="Pat", email="pat@example.com")
    db.session.add_all([boot, runner, quiet, champ, pat])
    db.session.flush()
    db.session.add_all(
        Pick(
            user_id=pat.id,
            season_id=season.id,
            survivor_id=s.id,
            pick_type="draft",
        )
        for s in (boot, runner, quiet, champ)
    )
    db.session.commit()
    return SimpleNamespace(
        c=c,
        season=season,
        pat=pat,
        boot=boot,
        runner=runner,
        quiet=quiet,
        champ=champ,
    )


def _page(league):
    resp = league.c.get(f"/leaderboard/{league.season.id}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _card(html, name):
    """The markup of one castaway's card."""
    marker = f'<span class="lb-pick-name">{name}'
    start = html.index(marker)
    end = html.find('<span class="lb-pick-name">', start + len(marker))
    return html[start : end if end != -1 else len(html)]


class TestEpisodeRows:
    def test_counts_are_per_episode_not_running_totals(self, league):
        """Boot has 5 confessionals then 3, not 5 then 8."""
        from app.routes import _episode_rows

        rows = _episode_rows(league.boot)
        conf = [
            dict((c["label"], c["value"]) for c in r["cells"])["Conf"] for r in rows
        ]

        assert conf == [5, 3]

    def test_rows_stop_at_the_elimination_episode(self, league):
        """episode_stats runs to episode 5 for everyone; Boot left in 2."""
        from app.routes import _episode_rows

        rows = _episode_rows(league.boot)

        assert [r["episode"] for r in rows] == [1, 2]
        assert rows[-1]["final"] is True

    def test_a_castaway_still_in_the_game_keeps_every_episode(self, league):
        from app.routes import _episode_rows

        rows = _episode_rows(league.runner)

        assert [r["episode"] for r in rows] == [1, 2, 3, 4, 5]
        assert not any(r["final"] for r in rows)

    def test_a_castaway_with_no_activity_gets_no_table(self, league):
        """All-zero rows are a blank table, not information."""
        from app.routes import _episode_rows

        assert _episode_rows(league.quiet) == []

    def test_heat_is_scaled_per_column_to_this_castaway(self, league):
        from app.routes import _episode_rows

        rows = _episode_rows(league.runner)
        conf = [dict((c["label"], c["heat"]) for c in r["cells"])["Conf"] for r in rows]

        assert max(conf) == 1.0  # the castaway's own best episode
        assert conf[0] == pytest.approx(0.5)  # 2 confessionals against a best of 4

    def test_the_winner_is_not_described_as_voted_out(self, league):
        """elimination_episode is where a game ended, not where a vote landed.

        The winner, both runners-up and the fire loser all share the finale's
        elimination_episode without anyone voting them out.
        """
        from app.routes import _episode_rows

        rows = _episode_rows(league.champ)

        # The marker still belongs on their last row; the claim it makes must
        # be true for a winner, so it says the game ended rather than why.
        assert rows[-1]["final"] is True
        html = _page(league)
        assert "voted out this episode" not in html
        assert "last episode in the game" in html

    def test_as_of_truncates_the_table(self, league):
        from app.routes import _episode_rows

        rows = _episode_rows(league.runner, as_of_episode=2)

        assert [r["episode"] for r in rows] == [1, 2]


class TestSheetMarkup:
    def test_the_sheet_has_a_hero_and_three_panels(self, league):
        card = _card(_page(league), "Runner")

        assert 'class="lb-sheet-hero"' in card
        assert 'class="lb-sheet-name">Runner Up<' in card
        for panel in ("summary", "journey", "episodes"):
            assert f'data-panel="{panel}"' in card

    def test_tab_strip_starts_hidden_so_it_never_appears_without_script(self, league):
        card = _card(_page(league), "Runner")

        assert re.search(r'class="lb-sheet-tabs"[^>]*\shidden', card)

    def test_tab_and_panel_ids_are_unique_across_the_page(self, league):
        """Two players can hold the same castaway, so the name is not enough."""
        html = _page(league)
        ids = re.findall(r'id="((?:tab|panel)-[^"]+)"', html)

        assert len(ids) == len(set(ids))

    def test_every_tab_points_at_a_panel_that_exists(self, league):
        html = _page(league)
        panel_ids = set(re.findall(r'id="(panel-[^"]+)"', html))

        for controls in re.findall(r'aria-controls="([^"]+)"', html):
            if controls.startswith("panel-"):
                assert controls in panel_ids

    def test_the_semicolon_occupation_separator_is_not_shown(self, league):
        """survivoR joins a returning player's occupations with ';'."""
        html = _page(league)

        assert "Waiter;Photographer" not in html
        assert "Waiter, Photographer" in html
