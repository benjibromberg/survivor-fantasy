"""The Scoring changes page, the scoring-version log, and hiding Sole Survivor."""

import importlib
import json
import re
import sys

import pytest

ADMIN_EMAIL = "admin@example.com"

# A season-51-style config that keeps every league rule (app/scoring/rules.py)
NEW = {
    "final_tribal_val": 4,
    "second_val": 3.0,
    "third_val": 1.5,
    "fire_win_val": 1,
    "individual_immunity_val": 0.5,
    "tribal_immunity_val": 0.25,
    "idol_play_val": 1.5,
    "idol_found_val": 0.75,
    "advantage_play_val": 0.5,
    "advantage_found_val": 0.25,
    "sole_survivor_val": 0,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
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


def _season(db, number, config, active=False, finished=True):
    """Eight castaways; the winner out last. In-progress seasons stop at 4 boots."""
    from app.models import Season, Survivor

    season = Season(
        number=number,
        name=f"Season {number}",
        is_active=active,
        num_players=8,
        num_episodes=8,
        left_at_jury=5,
        n_finalists=3,
        merge_episode_num=4,
        scoring_config=json.dumps(config),
    )
    db.session.add(season)
    db.session.flush()
    out = 8 if finished else 4
    survivors = []
    for i in range(8):
        order = i + 1 if i < out else 0
        survivors.append(
            Survivor(
                season_id=season.id,
                name=f"S{number}C{i + 1}",
                voted_out_order=order,
                elimination_episode=min(order, 6) if order else None,
                day_voted_out=(order if order <= 5 else 6) if order else None,
                made_jury=4 <= order <= 5,
                individual_immunity_wins=1 if i == 7 else 0,
                tribal_immunity_wins=2 if i in (5, 6) else 0,
            )
        )
    db.session.add_all(survivors)
    db.session.flush()
    return season, survivors


@pytest.fixture()
def league(client):
    """Season 49 (legacy), 50 (run 6) finished; 51 active on the new config."""
    c, db = client
    from app.models import Pick, User
    from app.routes import _compare_cache
    from app.scoring.classic import DEFAULT_CONFIG, LEGACY_CONFIG
    from app.scoring.history import SCORING_VERSIONS

    run6 = next(v for v in SCORING_VERSIONS if v["key"] == "run-6")["config"]
    s49, c49 = _season(db, 49, dict(LEGACY_CONFIG))
    s50, c50 = _season(db, 50, dict(run6))
    s51, c51 = _season(db, 51, {**DEFAULT_CONFIG, **NEW}, active=True, finished=False)

    pat = User(username="pat", display_name="Pat", email="pat@example.com")
    sam = User(username="sam", display_name="Sam", email="sam@example.com")
    fan = User(username="fan", display_name="Fan", email="fan@example.com")
    db.session.add_all([pat, sam, fan])
    db.session.flush()

    def draft(user, survivors, idxs):
        for i in idxs:
            db.session.add(
                Pick(
                    user_id=user.id,
                    season_id=survivors[i].season_id,
                    survivor_id=survivors[i].id,
                    pick_type="draft",
                )
            )

    for survivors in (c49, c50, c51):
        draft(pat, survivors, (7, 0))  # the winner and the first boot
        draft(sam, survivors, (5, 6))  # 3rd and 2nd place
    db.session.commit()
    _compare_cache.clear()

    class League:
        pass

    league = League()
    league.c, league.db = c, db
    league.s49, league.s50, league.s51 = s49, s50, s51
    league.pat, league.sam, league.fan = pat, sam, fan
    return league


def _page(league, season=None):
    season = season or league.s51
    resp = league.c.get(f"/league-settings/{season.id}/changes")
    assert resp.status_code == 200
    return resp.data.decode()


def _board(season, config=None):
    from app.routes import _build_leaderboard

    entries, _ = _build_leaderboard(season, scoring_config=config)
    return {e["user"].username: round(e["total_points"], 2) for e in entries}


class TestLeaderboardWithAnotherConfig:
    def test_scores_with_the_given_config_without_saving_it(self, league):
        from app.scoring.classic import LEGACY_CONFIG

        stored = league.s51.scoring_config

        own = _board(league.s51)
        legacy = _board(league.s51, dict(LEGACY_CONFIG))

        assert own != legacy
        assert league.s51.scoring_config == stored
        assert _board(league.s51) == own


class TestWhatChanged:
    def test_lists_changed_values_against_the_previous_season(self, league):
        page = _page(league)

        assert "Season 50" in page
        assert "Final Tribal Bonus" in page
        assert "Individual Immunity Win" in page

    def test_leaves_out_values_that_did_not_change(self, league):
        page = _page(league)
        changed = page.split('id="what-changed"', 1)[1].split("</section>")[0]

        assert "Jury Bonus" not in changed

    def test_shows_each_rule_and_whether_it_holds(self, league):
        page = _page(league)
        rules = page.split('id="rules"', 1)[1].split("</section>")[0]

        assert rules.count("ls-rule-holds") == 3
        assert "ls-rule-broken" not in rules

    def test_rules_that_break_are_marked(self, league):
        page = _page(league, league.s50)
        rules = page.split('id="rules"', 1)[1].split("</section>")[0]

        assert "ls-rule-broken" in rules


class TestStandingsBothWays:
    def test_current_standings_under_old_and_new_scoring(self, league):
        page = _page(league)
        table = page.split('id="standings"', 1)[1].split("</section>")[0]
        old = _board(league.s51, league.s50.get_scoring_config())
        new = _board(league.s51)

        for user in ("pat", "sam"):
            assert f"{old[user]:.1f}" in table
            assert f"{new[user]:.1f}" in table

    def test_past_seasons_are_replayed_under_the_new_scoring(self, league):
        page = _page(league)
        past = page.split('id="past-seasons"', 1)[1].split("</section>")[0]

        assert "Season 50" in past and "Season 49" in past
        assert "Pat" in past or "Sam" in past

    def test_replays_are_cached(self, league, monkeypatch):
        from app import routes

        _page(league)
        calls = []
        real = routes._build_leaderboard
        monkeypatch.setattr(
            routes,
            "_build_leaderboard",
            lambda *a, **k: calls.append(1) or real(*a, **k),
        )

        _page(league)

        assert calls == []


class TestHistory:
    def test_each_season_names_its_scoring_version(self, league):
        page = _page(league)
        history = page.split('id="history"', 1)[1].split("</section>")[0]

        assert re.search(r"Season 49.*Legacy", history, re.S)
        assert re.search(r"Season 50.*Run 6", history, re.S)

    def test_describe_config_reports_changes_from_the_closest_version(self):
        from app.scoring.history import SCORING_VERSIONS, describe_config

        run6 = next(v for v in SCORING_VERSIONS if v["key"] == "run-6")
        version, diffs = describe_config({**run6["config"], "fire_win_val": 1})

        assert version is run6
        assert diffs == {"fire_win_val": (2, 1)}

    def test_versions_are_frozen_copies(self):
        from app.scoring.classic import DEFAULT_CONFIG
        from app.scoring.history import SCORING_VERSIONS

        for version in SCORING_VERSIONS:
            assert version["config"] is not DEFAULT_CONFIG


class TestNoPreviousSeason:
    def test_the_first_season_has_nothing_to_compare(self, league):
        page = _page(league, league.s49)

        assert "No earlier season to compare with" in page


class TestSoleSurvivorHiddenWhenOff:
    def test_hub_omits_it_for_a_season_without_it(self, league):
        resp = league.c.get(f"/league-settings/{league.s51.id}")

        assert b"/sole-survivor" not in resp.data
        assert b"Sole Survivor</dt>" not in resp.data

    def test_hub_keeps_it_for_a_season_that_scored_it(self, league):
        resp = league.c.get(f"/league-settings/{league.s50.id}")

        assert b"/sole-survivor" in resp.data

    def test_its_page_is_gone_for_a_season_without_it(self, league):
        resp = league.c.get(f"/league-settings/{league.s51.id}/sole-survivor")

        assert resp.status_code == 404


class TestEarlierRunsOnTheAnalysisPage:
    def test_lists_run_6_with_a_working_link_to_its_data(self, league):
        page = league.c.get("/scoring-analysis").data.decode()
        runs = page.split('id="earlier-runs"', 1)[1].split("</details>")[0]

        assert "Run 6" in runs and "103,648" in runs
        # Static URLs carry a content version (?v=...)
        link = re.search(r'href="([^"]*run-6-2026-03-13\.json[^"]*)"', runs)
        assert link, "no link to the archived run 6 data"
        assert league.c.get(link.group(1)).status_code == 200


class TestRunSeven:
    def test_run_7_keeps_the_league_rules(self):
        from app.scoring.history import SCORING_VERSIONS
        from app.scoring.rules import satisfies_orderings

        run7 = next(v for v in SCORING_VERSIONS if v["key"] == "run-7")

        assert satisfies_orderings(run7["config"])
        assert run7["config"]["sole_survivor_val"] == 0

    def test_run_7_matches_the_published_analysis(self):
        """The registry's frozen config is the run's recommendation, value for value."""
        from pathlib import Path

        from app.scoring.classic import DEFAULT_CONFIG
        from app.scoring.history import SCORING_VERSIONS

        run7 = next(v for v in SCORING_VERSIONS if v["key"] == "run-7")
        published = json.loads(
            (
                Path(__file__).parent.parent / "app/static/scoring_analysis.json"
            ).read_text()
        )

        assert published["total_configs"] == run7["configs_tested"]
        for key, value in published["recommended"].items():
            if key in DEFAULT_CONFIG:
                assert run7["config"][key] == value, key

    def test_the_analysis_page_lists_both_runs_newest_first(self, league):
        page = league.c.get("/scoring-analysis").data.decode()
        runs = page.split('id="earlier-runs"', 1)[1].split("</details>")[0]

        assert runs.index("Run 7") < runs.index("Run 6")
        assert "103,179" in runs


# ── Proposals and the league vote ──────────────────────────────────────────

from datetime import UTC, datetime, timedelta  # noqa: E402

ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"
CLOSES = datetime(2026, 11, 19, 0, 45)  # Wed Nov 18, 7:45 PM ET, naive UTC


def _login(c, email):
    assert c.get("/login", headers={ACCESS_HEADER: email}).status_code == 302


@pytest.fixture()
def proposal(league, monkeypatch):
    """Season 51 on the current scoring, with a proposal open for a vote."""
    from app import proposals
    from app.scoring.classic import DEFAULT_CONFIG

    league.s51.scoring_config = json.dumps(dict(DEFAULT_CONFIG))
    league.s51.proposed_scoring_config = json.dumps({**DEFAULT_CONFIG, **NEW})
    league.s51.proposal_closes_at = CLOSES
    league.db.session.commit()
    clock = {"now": CLOSES.replace(tzinfo=UTC) - timedelta(days=2)}
    monkeypatch.setattr(proposals, "now_utc", lambda: clock["now"])
    league.at = lambda moment: clock.__setitem__("now", moment)
    return league


def _vote(league, email, choice, comment=""):
    _login(league.c, email)
    resp = league.c.post(
        f"/league-settings/{league.s51.id}/vote",
        data={"choice": choice, "comment": comment},
    )
    league.c.get("/logout")
    return resp


def _votes(league):
    from app.models import ScoringVote

    return {
        v.user.username: (v.choice, v.comment)
        for v in ScoringVote.query.filter_by(season_id=league.s51.id)
    }


class TestProposalPage:
    def test_compares_current_scoring_with_the_proposal(self, proposal):
        page = _page(proposal)

        assert "Proposed scoring for Season 51" in page
        assert "Wed Nov 18, 7:45 PM ET" in page
        changed = page.split('id="what-changed"', 1)[1].split("</section>")[0]
        assert "Final Tribal Bonus" in changed

    def test_standings_are_current_against_proposed(self, proposal):
        page = _page(proposal)
        table = page.split('id="standings"', 1)[1].split("</section>")[0]
        current = _board(proposal.s51)
        proposed = _board(proposal.s51, proposal.s51.get_proposed_config())

        for user in ("pat", "sam"):
            assert f"{current[user]:.1f}" in table
            assert f"{proposed[user]:.1f}" in table

    def test_the_season_keeps_its_own_scoring_until_adopted(self, proposal):
        from app.scoring.classic import DEFAULT_CONFIG

        _page(proposal)

        assert proposal.s51.get_scoring_config() == DEFAULT_CONFIG


class TestVoting:
    def test_a_player_votes_and_can_change_it(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _vote(proposal, "pat@example.com", "next")

        assert _votes(proposal) == {"pat": ("next", "")}

    def test_votes_and_comments_show_with_names(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _vote(proposal, "sam@example.com", "keep")

        page = _page(proposal)
        votes = page.split('id="vote"', 1)[1].split("</section>")[0]
        assert "2 of 2 players have voted" in votes
        assert "Pat" in votes and "Love it" in votes and "Sam" in votes

    @pytest.mark.parametrize("choice", ["", "maybe", "NOW"])
    def test_an_unknown_choice_is_refused(self, proposal, choice):
        _vote(proposal, "pat@example.com", choice)

        assert _votes(proposal) == {}

    def test_someone_without_picks_cannot_vote(self, proposal):
        _vote(proposal, "fan@example.com", "now")

        assert _votes(proposal) == {}

    def test_signing_in_is_required(self, proposal):
        resp = proposal.c.post(
            f"/league-settings/{proposal.s51.id}/vote", data={"choice": "now"}
        )

        assert resp.status_code == 302
        assert _votes(proposal) == {}

    def test_voting_closes_at_the_deadline(self, proposal):
        proposal.at(CLOSES.replace(tzinfo=UTC))
        _vote(proposal, "pat@example.com", "now")

        assert _votes(proposal) == {}

    def test_a_long_comment_is_cut_to_the_limit(self, proposal):
        from app.proposals import COMMENT_MAX_LENGTH

        _vote(proposal, "pat@example.com", "now", "x" * (COMMENT_MAX_LENGTH + 50))

        assert len(_votes(proposal)["pat"][1]) == COMMENT_MAX_LENGTH

    def test_no_vote_without_a_proposal(self, league):
        _vote(league, "pat@example.com", "now")

        assert _votes(league) == {}


class TestAdminProposal:
    def _settings(self, league, **extra):
        from app.scoring.classic import DEFAULT_CONFIG

        data = {f"scoring_{k}": v for k, v in {**DEFAULT_CONFIG, **NEW}.items()}
        data = {k: ("1" if v is True else v) for k, v in data.items()}
        data.update(extra)
        _login(league.c, ADMIN_EMAIL)
        return league.c.post(f"/admin/season/{league.s51.id}/settings", data=data)

    def test_proposing_saves_the_config_beside_the_season(self, league):
        from app.scoring.classic import DEFAULT_CONFIG

        league.s51.scoring_config = json.dumps(dict(DEFAULT_CONFIG))
        league.db.session.commit()

        self._settings(league, propose="on", proposal_closes_at="2026-11-18T19:45")

        assert league.s51.get_scoring_config() == DEFAULT_CONFIG
        assert league.s51.get_proposed_config()["final_tribal_val"] == 4
        assert league.s51.get_proposed_config()["advantage_found_val"] == 0.25
        assert league.s51.proposal_closes_at == CLOSES

    def test_adopting_applies_it_and_keeps_the_votes(self, proposal):
        _vote(proposal, "pat@example.com", "now")
        _login(proposal.c, ADMIN_EMAIL)

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")

        assert proposal.s51.get_scoring_config()["final_tribal_val"] == 4
        assert proposal.s51.proposed_scoring_config is None
        assert _votes(proposal) == {"pat": ("now", "")}

    def test_withdrawing_clears_it(self, proposal):
        _login(proposal.c, ADMIN_EMAIL)

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/withdraw")

        assert proposal.s51.proposed_scoring_config is None
        assert proposal.s51.proposal_closes_at is None

    def test_a_player_cannot_adopt(self, proposal):
        _login(proposal.c, "pat@example.com")

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")

        assert proposal.s51.proposed_scoring_config is not None

    def test_after_adoption_the_page_keeps_the_vote_record(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _login(proposal.c, ADMIN_EMAIL)
        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")
        proposal.c.get("/logout")

        page = _page(proposal)

        assert "Love it" in page
        assert "Proposed scoring" not in page

    def test_scoring_inputs_accept_values_finer_than_a_quarter(self, league):
        _login(league.c, ADMIN_EMAIL)

        page = league.c.get(f"/admin/season/{league.s51.id}").data.decode()

        assert 'step="0.25"' not in page
