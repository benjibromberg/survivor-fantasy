"""Markup checks for the standings: team rows, torches, and openable castaway cards.

Open state on phones, the remembered state and the sticky row are CSS and
inline script, and are checked in a browser, not here.
"""

import importlib
import re
import sys
from types import SimpleNamespace

import pytest

PLAYER_EMAIL = "pat@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB. The scheduler is stubbed."""
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
    """Active season after two boots. Pat has three draft picks, one of them out."""
    from app.models import Pick, Season, Survivor, User

    c, db = client
    season = Season(number=99, name="Season 99", is_active=True, num_players=6)
    db.session.add(season)
    db.session.flush()

    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=i + 1 if i < 2 else 0,
            castaway_id=f"US9{i}",
            version_season="US99",
        )
        for i in range(6)
    ]
    pat = User(username="pat", display_name="Pat", email=PLAYER_EMAIL)
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*survivors, pat, sam])
    db.session.flush()

    def draft(user, surv):
        return Pick(
            user_id=user.id,
            season_id=season.id,
            survivor_id=surv.id,
            pick_type="draft",
        )

    db.session.add_all(
        [
            draft(pat, survivors[0]),  # voted out first
            draft(pat, survivors[2]),
            draft(pat, survivors[3]),
            draft(sam, survivors[4]),
        ]
    )
    db.session.commit()
    return SimpleNamespace(c=c, season=season, pat=pat, sam=sam)


def _page(league, login=False):
    if login:
        league.c.get("/login", headers={ACCESS_HEADER: PLAYER_EMAIL})
    resp = league.c.get(f"/leaderboard/{league.season.id}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


TEAM_START = re.compile(
    r'<details class="leaderboard-entry lb-team[^"]*"\s*data-team="(\d+)"'
)

STANDINGS_START = re.compile(r'<div class="lb-standings[^"]*">')


def _standings_start(html):
    """Index of the standings container, tolerant of extra classes on it."""
    m = STANDINGS_START.search(html)
    assert m, "no standings container in the page"
    return m.start()


def _team(html, user_id):
    """One team's markup: from its <details> to the next team's, or the end of the standings."""
    starts = list(TEAM_START.finditer(html))
    for i, m in enumerate(starts):
        if m.group(1) == str(user_id):
            end = (
                starts[i + 1].start()
                if i + 1 < len(starts)
                else _standings_start(html)
                + html[_standings_start(html) :].index("\n</div>\n<script>")
            )
            return html[m.start() : end]
    raise AssertionError(f"no team row for user {user_id}")


def _mine_attr(markup):
    """The data-mine attribute on an opening tag, not the word in a script."""
    return re.search(r"\sdata-mine[\s>]", markup) is not None


class TestTeamRows:
    def test_each_team_is_an_openable_row(self, league):
        html = _page(league)

        assert len(re.findall(r'<details class="leaderboard-entry lb-team', html)) == 2
        assert html.count('<summary class="lb-row">') == 2

    def test_row_carries_rank_name_and_points(self, league):
        team = _team(_page(league), league.pat.id)
        row = team[: team.index("</summary>")]

        assert '<span class="lb-name">Pat</span>' in row
        assert 'class="lb-points"' in row
        assert 'class="lb-rank"' in row

    def test_torches_count_picks_still_in_the_game(self, league):
        row = _team(_page(league), league.pat.id)

        assert 'aria-label="2 of 3 picks still in the game"' in row
        assert row.count("lb-torch-lit") == 2
        assert row.count("lb-torch-out") == 1
        assert ">2/3</span>" in row

    def test_your_own_team_is_marked_for_the_phone_default(self, league):
        html = _page(league, login=True)

        assert _mine_attr(_team(html, league.pat.id)[:300])
        assert not _mine_attr(_team(html, league.sam.id)[:300])

    def test_no_team_is_marked_when_logged_out(self, league):
        html = _page(league)

        assert not any(_mine_attr(m.group(0) + " ") for m in TEAM_START.finditer(html))
        start = _standings_start(html)
        assert not _mine_attr(html[start : html.index("<script>", start)])

    def test_the_win_column_goes_when_there_is_no_win_percentage(
        self, league, monkeypatch
    ):
        """Timeline views skip win % as too expensive, so its column goes too.

        The season is still in progress, so `is_finished` does not cover this
        case and the column would otherwise be reserved but empty. The
        probabilities are stubbed so both branches are exercised whatever the
        predictor makes of this fixture.
        """
        pcts = {league.pat.id: 60.0, league.sam.id: 40.0}
        monkeypatch.setattr(
            "app.routes.calculate_win_probabilities",
            lambda season: (
                {uid: {"win_pct": v} for uid, v in pcts.items()},
                {uid: {"win_pct": v} for uid, v in pcts.items()},
                1,
                True,
                {},
            ),
        )

        live = _page(league)
        assert "no-win" not in STANDINGS_START.search(live).group(0)
        assert 'class="lb-head-win"' in live

        # as_of short-circuits the predictor entirely, so the stub is not used.
        resp = league.c.get(f"/leaderboard/{league.season.id}?as_of=1")
        assert resp.status_code == 200
        timeline = resp.get_data(as_text=True)

        assert "no-win" in STANDINGS_START.search(timeline).group(0)
        assert "is-finished" not in STANDINGS_START.search(timeline).group(0)
        assert 'class="lb-head-win"' not in timeline

    def test_detail_switches_are_gone(self, league):
        html = _page(league)

        for removed in ("pts-toggle", "stats-toggle", "bio-toggle", "journey-toggle"):
            assert removed not in html
        assert 'data-roster="open"' in html and 'data-roster="close"' in html


class TestCastawayCards:
    def test_each_card_opens_to_its_own_detail(self, league):
        team = _team(_page(league), league.pat.id)

        cards = re.findall(r'<details class="lb-pick[ "]', team)
        assert len(cards) == 3
        assert team.count('<summary class="lb-pick-summary"') == 3
        assert team.count('<div class="lb-pick-more">') == 3

    def test_detail_holds_breakdown_and_the_stats_link(self, league):
        team = _team(_page(league), league.pat.id)
        more = team[team.index('<div class="lb-pick-more">') :]

        assert 'class="lb-pick-pts-detail"' in more
        assert 'class="lb-pick-link"' in more
        assert "https://survivorstatsdb.com/castaway?id=US99US9" in more

    def test_summary_holds_no_block_elements(self, league):
        """<summary> allows phrasing content only, so the card face uses spans."""
        team = _team(_page(league), league.pat.id)

        for summary in re.findall(
            r'<summary class="lb-pick-summary".*?</summary>', team, re.S
        ):
            assert "<div" not in summary

    def test_my_team_cards_stay_plain_links(self, league):
        league.c.get("/login", headers={ACCESS_HEADER: PLAYER_EMAIL})
        html = league.c.get(f"/my-team/{league.season.id}").get_data(as_text=True)

        assert '<details class="lb-pick' not in html
        assert html.count('class="lb-pick ') + html.count('class="lb-pick"') >= 3
