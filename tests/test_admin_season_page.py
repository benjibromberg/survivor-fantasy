"""The season admin page: task sections, and opening a vote from a starting point.

Proposing used to mean loading a version into 25 boxes, editing two of them,
ticking Propose and saving. The Scoring vote form instead posts a starting
point (proposals.starting_points) plus any values typed over it, so a
recorded version, or the last proposal with its adjustments, goes to the
league exactly as recorded.
"""

import importlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta

import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"
CLOSES = datetime(2026, 11, 19, 0, 45)  # Wed Nov 18, 7:45 PM ET, naive UTC
CLOSES_FORM = "2026-11-18T19:45"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def clock(monkeypatch):
    """The league clock, two days before voting closes."""
    from app import wildcards

    now = {"t": CLOSES.replace(tzinfo=UTC) - timedelta(days=2)}
    monkeypatch.setattr(wildcards, "now_utc", lambda: now["t"])
    return lambda moment: now.__setitem__("t", moment)


@pytest.fixture()
def league(client, clock):
    """Season 50 (finished) and 51 (active, on Run 6), Pat with picks in 51."""
    from types import SimpleNamespace

    from app.models import Pick, Season, Survivor, User
    from app.scoring.history import SCORING_VERSIONS

    c, db = client
    run6 = next(v for v in SCORING_VERSIONS if v["key"] == "run-6")["config"]
    seasons = {}
    for number, active in ((50, False), (51, True)):
        season = Season(
            number=number,
            name=f"Season {number}",
            is_active=active,
            num_players=4,
            num_episodes=4,
            left_at_jury=3,
            n_finalists=3,
            scoring_config=json.dumps(dict(run6)),
        )
        db.session.add(season)
        db.session.flush()
        seasons[number] = season
    castaways = [
        Survivor(
            season_id=seasons[51].id,
            name=f"C{i}",
            castaway_id=f"US51{i:02d}",
            voted_out_order=0,
        )
        for i in range(4)
    ]
    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    db.session.add_all([*castaways, pat])
    db.session.flush()
    db.session.add(
        Pick(
            user_id=pat.id,
            season_id=seasons[51].id,
            survivor_id=castaways[0].id,
            pick_type="draft",
        )
    )
    db.session.commit()
    return SimpleNamespace(
        c=c, db=db, s50=seasons[50], s51=seasons[51], pat=pat, at=clock
    )


def _login(c, email=ADMIN_EMAIL):
    assert c.get("/login", headers={ACCESS_HEADER: email}).status_code == 302


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _errors(c):
    return [msg for category, msg in _flashes(c) if category == "error"]


def _version(key):
    from app.scoring.history import SCORING_VERSIONS

    return next(v for v in SCORING_VERSIONS if v["key"] == key)["config"]


def _effective(config):
    from app.scoring.classic import DEFAULT_CONFIG

    return {k: config.get(k, d) for k, d in DEFAULT_CONFIG.items()}


def _propose(league, start, closes=CLOSES_FORM, season=None, **adjust):
    data = {"propose_from": start, "proposal_closes_at": closes}
    data.update({f"adjust_{k}": v for k, v in adjust.items()})
    season = season or league.s51
    return league.c.post(f"/admin/season/{season.id}/settings", data=data)


def _open(season):
    from app import proposals

    return proposals.open_proposal(season)


def _page(league, season=None):
    season = season or league.s51
    resp = league.c.get(f"/admin/season/{season.id}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


# ── Proposing from a starting point ──────────────────────────────────────


class TestProposeFromAStartingPoint:
    def test_a_recorded_version_is_proposed_exactly(self, league):
        _login(league.c)
        stored = league.s51.scoring_config

        resp = _propose(league, "version:run-7")

        assert resp.status_code == 302
        proposal = _open(league.s51)
        assert proposal.get_config() == _effective(_version("run-7"))
        assert proposal.closes_at == CLOSES
        assert league.s51.scoring_config == stored  # a vote, not applied
        assert not _errors(league.c)

    def test_legacy_keeps_flat_tribals(self, league):
        """A Tribal Base of None is flat mode, which no number box can post."""
        _login(league.c)

        _propose(league, "version:legacy")

        config = _open(league.s51).get_config()
        assert config["tribal_base"] is None
        assert config["merge_val"] == 1 and config["first_val"] == 7

    def test_the_last_proposal_goes_back_with_its_adjustments(self, league):
        from app import proposals

        adjusted = {**_effective(_version("run-7")), "final_tribal_val": 4.0}
        adjusted["fire_win_val"] = 1.0
        assert proposals.propose(league.s51, adjusted, CLOSES) is None
        first = _open(league.s51)
        assert proposals.withdraw(league.s51) is None
        _login(league.c)

        _propose(league, f"proposal:{first.id}")

        again = _open(league.s51)
        assert again.id != first.id
        assert again.get_config() == adjusted

    def test_a_typed_value_replaces_the_starting_points(self, league):
        _login(league.c)

        _propose(league, "version:run-7", final_tribal_val="4", jury_val="")

        config = _open(league.s51).get_config()
        assert config["final_tribal_val"] == 4
        assert config["jury_val"] == _version("run-7")["jury_val"]  # blank kept

    def test_the_current_scoring_can_be_the_start(self, league):
        _login(league.c)

        _propose(league, "current", idol_play_val="2.5")

        config = _open(league.s51).get_config()
        assert config == {**_effective(_version("run-6")), "idol_play_val": 2.5}

    def test_proposing_replaces_the_open_proposal(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        first = _open(league.s51)
        _login(league.c)

        _propose(league, f"proposal:{first.id}", fire_win_val="0.5")

        assert first.status == "withdrawn"
        assert _open(league.s51).get_config()["fire_win_val"] == 0.5


class TestProposeFromIsChecked:
    """Anything the page does not offer is refused, and nothing changes."""

    @pytest.mark.parametrize(
        "start", ["", "version:run-99", "run-7", "proposal:", "proposal:abc", "x"]
    )
    def test_an_unknown_starting_point_is_refused(self, league, start):
        _login(league.c)
        stored = league.s51.scoring_config

        resp = _propose(league, start)

        assert resp.status_code == 302
        assert _open(league.s51) is None
        assert league.s51.scoring_config == stored
        assert _errors(league.c)

    def test_another_seasons_proposal_is_refused(self, league):
        from app import proposals

        proposals.propose(league.s50, _effective(_version("legacy")), CLOSES)
        theirs = _open(league.s50)
        _login(league.c)

        _propose(league, f"proposal:{theirs.id}")

        assert _open(league.s51) is None
        assert _open(league.s50).id == theirs.id  # untouched
        assert _errors(league.c)

    def test_a_superseded_proposal_is_refused(self, league):
        """A form left open while another proposal was made must not propose
        something the page no longer shows."""
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        old = _open(league.s51)
        proposals.propose(league.s51, _effective(_version("legacy")), CLOSES)
        current = _open(league.s51)
        _login(league.c)

        _propose(league, f"proposal:{old.id}")

        assert _open(league.s51).id == current.id
        assert _errors(league.c)

    @pytest.mark.parametrize("bad", ["abc", "nan", "inf"])
    def test_a_typed_value_that_is_not_a_number_is_refused(self, league, bad):
        _login(league.c)

        _propose(league, "version:run-7", jury_val=bad)

        assert _open(league.s51) is None
        assert _errors(league.c)

    @pytest.mark.parametrize("closes", ["", "not a time", "2026-11-01T19:45"])
    def test_voting_has_to_close_in_the_future(self, league, closes):
        _login(league.c)

        _propose(league, "version:run-7", closes=closes)

        assert _open(league.s51) is None
        assert _errors(league.c)

    def test_a_player_cannot_propose(self, league):
        _login(league.c, PAT_EMAIL)

        _propose(league, "version:run-7")

        assert _open(league.s51) is None


class TestStartingPoints:
    def test_recorded_versions_newest_first_then_the_current_scoring(self, league):
        from app import proposals
        from app.scoring.history import SCORING_VERSIONS

        points, default = proposals.starting_points(league.s51)

        keys = [p["key"] for p in points]
        assert keys == [
            *(f"version:{v['key']}" for v in reversed(SCORING_VERSIONS)),
            "current",
        ]
        assert default == f"version:{SCORING_VERSIONS[-1]['key']}"

    def test_the_last_proposal_comes_first_and_is_chosen(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        proposals.withdraw(league.s51)
        latest = proposals.latest_proposal(league.s51)

        points, default = proposals.starting_points(league.s51)

        assert points[0]["key"] == default == f"proposal:{latest.id}"
        assert "withdrawn" in points[0]["label"]
        assert "Run 7" in points[0]["label"]

    def test_an_adopted_proposal_is_listed_but_not_chosen(self, league):
        from app import proposals
        from app.scoring.history import SCORING_VERSIONS

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        proposals.adopt(league.s51)

        points, default = proposals.starting_points(league.s51)

        assert points[0]["key"].startswith("proposal:")
        assert default == f"version:{SCORING_VERSIONS[-1]['key']}"

    def test_each_counts_the_values_it_would_change(self, league):
        from app import proposals

        points, _ = proposals.starting_points(league.s51)
        by_key = {p["key"]: p for p in points}

        assert by_key["version:run-6"]["changes"] == 0  # what 51 plays under
        assert by_key["current"]["changes"] == 0
        assert by_key["version:run-7"]["changes"] > 0


# ── The page ─────────────────────────────────────────────────────────────


class TestTaskSections:
    def test_sections_come_in_task_order(self, league):
        _login(league.c)

        page = _page(league)

        ids = ["scoring-vote", "pick-windows", "scoring", "season-data", "danger-zone"]
        found = [page.index(f'<section id="{i}"') for i in ids]
        assert found == sorted(found)

    def test_one_h1_and_no_skipped_heading_levels(self, league):
        _login(league.c)

        levels = [int(n) for n in re.findall(r"<h([1-6])[ >]", _page(league))]

        assert levels.count(1) == 1
        assert all(b - a <= 1 for a, b in zip(levels, levels[1:], strict=False))

    def test_every_post_form_carries_a_csrf_token(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        _login(league.c)

        forms = re.findall(r"<form[^>]*>.*?</form>", _page(league), re.S)

        posts = [f for f in forms if 'method="POST"' in f]
        assert len(posts) >= 8
        assert all('name="csrf_token"' in f for f in posts)

    def test_the_page_is_for_the_admin_only(self, league):
        _login(league.c, PAT_EMAIL)

        resp = league.c.get(f"/admin/season/{league.s51.id}")

        assert resp.status_code == 302
        assert "propose_from" not in resp.get_data(as_text=True)


class TestScoringVoteSection:
    def _vote_section(self, page):
        return page.split('<section id="scoring-vote"', 1)[1].split(
            '<section id="pick-windows"', 1
        )[0]

    def test_without_a_proposal_it_offers_the_form_and_no_adopt(self, league):
        _login(league.c)

        section = self._vote_section(_page(league))

        assert "No vote is open." in section
        assert 'name="propose_from"' in section
        assert 'value="version:run-7" selected' in section
        assert "Adopt proposal" not in section
        assert "Withdraw proposal" not in section

    def test_an_open_proposal_shows_its_tally_and_both_buttons(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        _login(league.c)

        section = self._vote_section(_page(league))

        assert "Wed Nov 18, 7:45 PM ET" in section
        assert "0 of 1" in section  # Pat is the one voter
        adopt = f"/admin/season/{league.s51.id}/proposal/adopt"
        withdraw = f"/admin/season/{league.s51.id}/proposal/withdraw"
        assert adopt in section and withdraw in section
        assert ">Adopt proposal</button>" in section
        assert ">Withdraw proposal</button>" in section

    def test_adopt_and_withdraw_are_not_inside_a_fold(self, league):
        """They are pressed by automation, which cannot open a fold first."""
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        _login(league.c)
        page = _page(league)

        for text in (">Adopt proposal<", ">Withdraw proposal<"):
            before = page[: page.index(text)]
            assert before.count("<details") == before.count("</details>"), text

    def test_the_form_is_folded_away_while_a_proposal_is_open(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        _login(league.c)
        section = self._vote_section(_page(league))

        before = section[: section.index('name="propose_from"')]
        assert before.count("<details") > before.count("</details>")
        assert "Replace it with a new proposal" in section

    def test_a_withdrawn_proposals_future_deadline_is_offered(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        proposals.withdraw(league.s51)
        _login(league.c)

        section = self._vote_section(_page(league))

        assert f'value="{CLOSES_FORM}"' in section
        assert "The last proposal was withdrawn" in section

    def test_a_past_deadline_is_not_offered(self, league):
        from app import proposals

        proposals.propose(league.s51, _effective(_version("run-7")), CLOSES)
        proposals.withdraw(league.s51)
        league.at(CLOSES.replace(tzinfo=UTC) + timedelta(hours=1))
        _login(league.c)

        section = self._vote_section(_page(league))

        assert f'value="{CLOSES_FORM}"' not in section

    def test_adjust_boxes_show_the_starting_points_values(self, league):
        _login(league.c)
        page = _page(league)

        box = re.search(r'<input[^>]*id="adjust_final_tribal_val"[^>]*>', page)
        assert box and 'placeholder="5"' in box.group(0)  # Run 7's value
        assert 'value="' not in box.group(0)  # blank: keeps the start's value
        # Season 51 plays Run 6 (2): the difference is marked, and the box for
        # a value both share is not
        assert re.search(r'data-now-for="final_tribal_val"[^>]*>Now 2<', page)
        assert re.search(r'data-now-for="jury_val"[^>]* hidden>', page)


class TestOtherSections:
    def test_scoring_names_the_version_and_its_differences(self, league):
        cfg = {**_version("run-6"), "jury_val": 4}
        league.s51.scoring_config = json.dumps(cfg)
        league.db.session.commit()
        _login(league.c)

        section = _page(league).split('<section id="scoring"', 1)[1]
        section = section.split('<section id="season-data"', 1)[0]

        assert "<strong>Run 6</strong>, with 1 value changed" in section
        assert "Jury Bonus" in section

    def test_the_direct_form_writes_switches_as_numbers(self, league):
        """True in a number box renders empty and saves as the default."""
        cfg = {**_version("run-6"), "replacement_deduction": False}
        league.s51.scoring_config = json.dumps(cfg)
        league.db.session.commit()
        _login(league.c)

        box = re.search(
            r'<input[^>]*id="scoring_replacement_deduction"[^>]*>', _page(league)
        )

        assert 'value="0"' in box.group(0)

    def test_a_published_cast_starts_folded(self, league):
        _login(league.c)
        page = _page(league)

        fold = page.split('<section id="season-data"', 1)[1].split("<summary>", 1)[0]
        assert "<details" in fold and " open" not in fold
        assert "not automatic yet" not in page  # only relevant to a hand cast

    def test_an_empty_season_opens_the_cast_form(self, league):
        _login(league.c)

        page = _page(league, league.s50)

        fold = page.split('<section id="season-data"', 1)[1].split("<summary>", 1)[0]
        assert "<details" in fold and " open" in fold
        assert "not automatic yet" in page
