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
    from app.routes import _changes_cache, _compare_cache
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
    _changes_cache.clear()

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


def _block(page, block_id):
    """The markup of one section, whether an open <section> or a <details> fold.

    Counts nesting, because an earlier season whose order changes holds a
    fold of its own inside the earlier-seasons fold.
    """
    head, rest = page.split(f'id="{block_id}"', 1)
    tag = re.match(r"\w+", head.rsplit("<", 1)[1]).group(0)
    depth = 1
    for match in re.finditer(rf"<(/?){tag}\b", rest):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return rest[: match.start()]
    raise AssertionError(f"no closing </{tag}> for #{block_id}")


def _season_row(block, label):
    """One earlier season's row in the earlier-seasons fold."""
    rows = re.split(r'<div class="ls-value">\s*<dt>(?=Season \d)', block)
    (row,) = [r for r in rows if r.startswith(label + "<")]
    return row


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
        changed = _block(page, "what-changed")

        assert "Jury Bonus" not in changed

    def test_shows_each_rule_and_whether_it_holds(self, league):
        page = _page(league)
        rules = _block(page, "rules")

        assert rules.count("ls-rule-holds") == 3
        assert "ls-rule-broken" not in rules

    def test_rules_that_break_are_marked(self, league):
        page = _page(league, league.s50)
        rules = _block(page, "rules")

        assert "ls-rule-broken" in rules


def _standings_list(page):
    """The current season's standings list, without the reasons fold under it."""
    return _block(page, "standings").split("<details", 1)[0]


class TestStandingsBothWays:
    def test_current_standings_under_old_and_new_scoring(self, league):
        """In the fold's number format (_pts), so the list and the fold under
        it never show one total two ways. It used to assert one decimal."""
        listed = _standings_list(_page(league))
        old = _board(league.s51, league.s50.get_scoring_config())
        new = _board(league.s51)

        for user in ("pat", "sam"):
            assert f"<dd>{_pts(new[user])} pts" in listed
            assert f"was {_pts(old[user])}," in listed

    def test_the_list_keeps_quarter_points(self, league):
        """One advantage: 0.25 under Season 51's scoring, 0.5 under Run 6.
        One decimal would show 2.75 as 2.8, beside 2.75 in the fold."""
        from app.models import Survivor

        star = Survivor.query.filter_by(season_id=league.s51.id, name="S51C8").one()
        star.advantages_found = 1
        league.db.session.commit()

        listed = _standings_list(_page(league))

        assert "<dd>2.75 pts <small>was 2.5, 2nd</small>" in listed
        assert "2.8 pts" not in listed

    def test_past_seasons_are_replayed_under_the_new_scoring(self, league):
        page = _page(league)
        past = _block(page, "past-seasons")

        assert "Season 50" in past and "Season 49" in past
        assert "Pat" in past or "Sam" in past


def _pts(v):
    """Points as the page writes them in a replay: two places, 44.0 not 44.00."""
    s = f"{v:.2f}"
    return s[:-1] if s.endswith("0") else s


def _ranked_board(season, config=None):
    """{username: (points, rank)}, best first, as the page ranks them."""
    board = _board(season, config)
    ordered = sorted(board, key=lambda u: board[u], reverse=True)
    return {u: (board[u], rank) for rank, u in enumerate(ordered, start=1)}


class TestReplayDetail:
    """Season 50 finishes Pat, Sam under its own scoring and Sam, Pat under
    Season 51's; Season 49 finishes the same way under both."""

    def _rows(self, league):
        past = _block(_page(league), "past-seasons")
        return _season_row(past, "Season 50"), _season_row(past, "Season 49")

    def test_the_fixture_has_one_season_that_changes_and_one_that_does_not(
        self, league
    ):
        new = league.s51.get_scoring_config()
        own50, new50 = _ranked_board(league.s50), _ranked_board(league.s50, new)
        own49, new49 = _ranked_board(league.s49), _ranked_board(league.s49, new)

        assert [own50[u][1] for u in ("pat", "sam")] != [
            new50[u][1] for u in ("pat", "sam")
        ]
        assert [own49[u][1] for u in ("pat", "sam")] == [
            new49[u][1] for u in ("pat", "sam")
        ]

    def test_a_changed_season_shows_every_players_totals_and_ranks_both_ways(
        self, league
    ):
        changed, _ = self._rows(league)
        own = _ranked_board(league.s50)
        new = _ranked_board(league.s50, league.s51.get_scoring_config())
        ordinal = {1: "1st", 2: "2nd"}

        for user, name in (("pat", "Pat"), ("sam", "Sam")):
            (old_points, old_rank), (points, rank) = own[user], new[user]
            assert f"{ordinal[rank]} &middot; {name}</dt>" in changed
            assert f"<dd>{_pts(points)} pts" in changed
            assert f"was {_pts(old_points)}, {ordinal[old_rank]}" in changed
        assert "up 1 place" in changed and "down 1 place" in changed

    def test_names_the_groups_that_moved_each_player_most(self, league):
        changed, _ = self._rows(league)
        sam, pat = changed.split("&middot; Pat</dt>")

        # Sam's 2nd and 3rd place picks: placement (+4.5) and Final Tribal
        # (+4.0) now score, and read as one reason, ahead of immunity (+1.0)
        assert "+8.5</span>" in sam
        assert sam.index("Final Tribal and placement") < sam.index("immunity wins")
        assert "finishing place" not in sam and "reaching Final Tribal" not in sam
        # Pat's winner gains Final Tribal (+2.0) and individual immunity (+0.5)
        assert pat.index("Final Tribal and placement") < pat.index("immunity wins")
        assert "+2.0</span>" in pat and "+0.5</span>" in pat

    def test_names_the_castaways_behind_the_changes(self, league):
        changed, _ = self._rows(league)
        sam, pat = changed.split("&middot; Pat</dt>")

        # Two castaways share Sam's Final Tribal and placement gain, so each
        # carries an amount: S50C7 +3 placement +2 Final Tribal, S50C6 +1.5 +2
        assert "S50C7&nbsp;+5.0, S50C6&nbsp;+3.5" in sam
        # One castaway is the whole of Pat's, so the amount is not repeated
        assert '<span class="ls-replay-who">S50C8</span>' in pat

    def test_the_detail_is_a_fold_that_starts_closed_everywhere(self, league):
        """It used to start open on wider screens; closed on every screen, the
        section is a short list of seasons and you open the one you want."""
        changed, _ = self._rows(league)

        tag = re.search(r"<details[^>]*>", changed).group(0)
        assert 'class="fold ls-replay"' in tag
        assert " open" not in tag and "data-mobile-collapsed" not in tag

    def test_the_fold_is_labelled_by_what_it_answers(self, league):
        changed, _ = self._rows(league)

        summary = re.search(r"<summary>(.*?)</summary>", changed, re.S).group(1)
        assert "Why the order changes" in summary
        # Every replay fold reads the same, so a screen reader also hears which
        assert '<span class="visually-hidden"> in Season 50</span>' in summary

    def test_a_named_season_is_still_read_out_by_number(self, league):
        """A season's title is the row label, but "in In the Hands of the
        Fans" does not read as a season; the hidden text uses the number."""
        league.s50.name = "In the Hands of the Fans"
        league.db.session.commit()

        past = _block(_page(league), "past-seasons")

        assert "<dt>In the Hands of the Fans</dt>" in past
        assert '<span class="visually-hidden"> in Season 50</span>' in past
        assert "in In the Hands" not in past

    def test_a_season_with_the_same_order_keeps_its_summary_and_gets_the_detail(
        self, league
    ):
        """It used to keep only its one-line summary; the league asked for the
        breakdown on every season, labelled for what it answers there."""
        _, same = self._rows(league)

        assert "Sam still wins" in same
        assert "Same finishing order" in same
        tag = re.search(r"<details[^>]*>", same).group(0)
        assert 'class="fold ls-replay"' in tag
        assert " open" not in tag and "data-mobile-collapsed" not in tag
        summary = re.search(r"<summary>(.*?)</summary>", same, re.S).group(1)
        assert "How the points change" in summary
        assert "Why the order changes" not in summary
        assert '<span class="visually-hidden"> in Season 49</span>' in summary

    def test_a_same_order_season_names_what_moved_each_player(self, league):
        _, same = self._rows(league)
        sam, pat = same.split("&middot; Pat</dt>")

        # Legacy's flat tribals and merge bonus give way to Final Tribal and
        # placement: Sam gains overall, Pat loses, and the order holds.
        assert "<dd>18.5 pts" in sam and "was 16.0, 1st &middot; +2.5 pts" in sam
        assert "S49C6&nbsp;+4.5, S49C7&nbsp;+4.0" in sam
        assert sam.index("Final Tribal and placement") < sam.index("surviving tribals")
        assert "everything else" in sam and "immunity wins&nbsp;+1.0" in sam
        assert "was 13.0, 2nd &middot; &minus;1.0 pts" in pat
        assert "up " not in pat and "down " not in pat

    def test_a_season_scored_the_same_both_ways_has_nothing_to_open(self, league):
        """Season 51 on Season 50's own scoring: Season 50 replays unchanged,
        while Season 49 (Legacy) still moves and keeps its fold."""
        from app.scoring.history import SCORING_VERSIONS

        run6 = next(v for v in SCORING_VERSIONS if v["key"] == "run-6")["config"]
        league.s51.scoring_config = json.dumps(dict(run6))
        league.db.session.commit()

        past = _block(_page(league), "past-seasons")
        unchanged, legacy = (
            _season_row(past, "Season 50"),
            _season_row(past, "Season 49"),
        )

        assert "Same finishing order" in unchanged
        assert "<details" not in unchanged and "ls-replay" not in unchanged
        assert 'class="fold ls-replay"' in legacy


def _standings_fold(page):
    """The reasons fold under the current season's standings, or None."""
    block = _block(page, "standings")
    return block.split("<details", 1)[1] if "<details" in block else None


class TestCurrentSeasonReasons:
    """Season 51, on Season 50's Run 6 and then its own scoring: immunity
    wins now score, so both players gain and the order holds."""

    def test_the_standings_get_a_fold_of_reasons_that_starts_closed(self, league):
        fold = _standings_fold(_page(league))

        tag = fold.split(">", 1)[0]
        assert 'class="fold ls-replay"' in tag
        assert " open" not in tag and "data-mobile-collapsed" not in tag
        summary = re.search(r"<summary>(.*?)</summary>", fold, re.S).group(1)
        assert "Why the points change" in summary
        assert '<span class="visually-hidden"> in Season 51</span>' in summary

    def test_every_detail_fold_starts_closed_and_the_sections_keep_theirs(self, league):
        """Only the inner .ls-replay folds changed: the section folds still
        start open, and closed on phones."""
        tags = re.findall(r"<details[^>]*>", _page(league))
        inner = [t for t in tags if "ls-replay" in t]
        outer = [t for t in tags if "ls-replay" not in t and 'class="fold"' in t]

        assert len(inner) == 3  # Season 51's standings, Seasons 50 and 49
        for tag in inner:
            assert " open" not in tag and "data-mobile-collapsed" not in tag, tag
        assert {re.search(r'id="([^"]+)"', t).group(1) for t in outer} >= {
            "rules",
            "past-seasons",
            "history",
        }
        for tag in outer:
            assert " open" in tag and "data-mobile-collapsed" in tag, tag

    def test_names_the_groups_and_castaways_behind_each_change(self, league):
        sam, pat = _standings_fold(_page(league)).split("&middot; Pat</dt>")

        assert "<dd>5.0 pts" in sam and "was 4.0, 1st &middot; +1.0 pts" in sam
        assert "immunity wins" in sam
        assert "S51C6&nbsp;+0.5, S51C7&nbsp;+0.5" in sam
        assert "was 2.0, 2nd &middot; +0.5 pts" in pat
        assert '<span class="ls-replay-who">S51C8</span>' in pat

    def test_says_currently_while_a_proposal_is_open(self, proposal):
        fold = _standings_fold(_page(proposal))

        assert "<small>currently " in fold
        assert "<small>was " not in fold

    def test_no_fold_when_the_season_scores_the_same_both_ways(self, league):
        """Season 51 on Season 50's scoring: the standings stay, nothing to open."""
        from app.scoring.history import SCORING_VERSIONS

        run6 = next(v for v in SCORING_VERSIONS if v["key"] == "run-6")["config"]
        league.s51.scoring_config = json.dumps(dict(run6))
        league.db.session.commit()

        page = _page(league)

        assert "standings, both ways" in _block(page, "standings")
        assert _standings_fold(page) is None


_SIGNED = r"(0|\+[\d.]+|&minus;[\d.]+)"


def _cents_of(printed):
    """A signed amount as the page prints it (+1.5, &minus;0.8, 0), in cents."""
    if printed == "0":
        return 0
    sign = -1 if printed.startswith("&minus;") else 1
    return sign * round(float(printed.lstrip("+").removeprefix("&minus;")) * 100)


def _printed_players(page):
    """(new, old, change, parts) in cents, as printed, for every replay player."""
    out = []
    bodies = re.findall(
        r'<dl class="ls-values ls-values-wide ls-replay-body">(.*?)</dl>', page, re.S
    )
    for body in bodies:
        for player in body.split('<div class="ls-value">')[1:]:
            new = re.search(r"<dd>([\d.]+) pts", player).group(1)
            old = re.search(r"<small>(?:was|currently) ([\d.]+),", player).group(1)
            moved = re.search(r"&middot; (same points|" + _SIGNED + " pts)", player)
            change = 0 if moved.group(1) == "same points" else _cents_of(moved.group(2))
            parts = re.findall(
                r'class="ls-replay-delta[^"]*">' + _SIGNED + "</span>", player
            )
            out.append(
                (
                    round(float(new) * 100),
                    round(float(old) * 100),
                    change,
                    [_cents_of(x) for x in parts],
                )
            )
    return out


class TestPrintedNumbersAddUp:
    def test_every_players_parts_add_up_to_the_printed_change(self, league):
        """A Season 51 wildcard puts Pat's parts in eighths: immunity +0.625,
        idols +0.375, advantages -0.125, from 3.25 to 4.125 (printed 4.12).
        Rounded one by one they print 0.88 against a change of 0.87."""
        from app.models import Pick, Survivor

        fifth = Survivor.query.filter_by(season_id=league.s51.id, name="S51C5").one()
        fifth.advantages_found = 1
        fifth.idols_found = 1
        fifth.tribal_immunity_wins = 1
        league.db.session.add(
            Pick(
                user_id=league.pat.id,
                season_id=league.s51.id,
                survivor_id=fifth.id,
                pick_type="wildcard",
            )
        )
        league.db.session.commit()

        players = _printed_players(_page(league))

        assert len(players) == 6  # two players each in Seasons 51, 50 and 49
        for new, old, change, parts in players:
            assert new - old == change, (new, old, change)
            if parts:
                assert sum(parts) == change, (parts, change)

    def test_no_remainder_line_when_nothing_is_left(self, league):
        """Sam's Season 50 change is all in its two listed groups."""
        past = _block(_page(league), "past-seasons")
        sam = _season_row(past, "Season 50").split("&middot; Pat</dt>")[0]

        assert "everything else" not in sam


class TestComponentTotalsOnRealEntries:
    def test_components_add_up_to_each_players_total(self, league):
        """Checked on _build_leaderboard's entries, with a wildcard in them."""
        from app.breakdowns import component_totals, pick_contributions
        from app.models import Pick, Survivor
        from app.routes import _build_leaderboard

        fifth = Survivor.query.filter_by(season_id=league.s50.id, name="S50C5").one()
        league.db.session.add(
            Pick(
                user_id=league.sam.id,
                season_id=league.s50.id,
                survivor_id=fifth.id,
                pick_type="wildcard",
            )
        )
        league.db.session.commit()

        entries, _ = _build_leaderboard(league.s50)

        for entry in entries:
            totals = component_totals(entry)
            assert sum(totals.values()) == pytest.approx(entry["total_points"])
        sam = next(e for e in entries if e["user"].username == "sam")
        (wild,) = [p for p in pick_contributions(sam) if p["pick_type"] == "Wildcard"]
        (raw,) = [p for p in sam["picks"] if p["pick_type_raw"] == "wildcard"]
        assert sum(wild["components"].values()) == pytest.approx(
            sum(raw["breakdown"].values()) / 2
        )


class TestHistory:
    def test_each_season_names_its_scoring_version(self, league):
        page = _page(league)
        history = _block(page, "history")

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
def clock(monkeypatch):
    """The league clock (wildcards.now_utc), two days before voting closes."""
    from app import wildcards

    now = {"t": CLOSES.replace(tzinfo=UTC) - timedelta(days=2)}
    monkeypatch.setattr(wildcards, "now_utc", lambda: now["t"])
    return lambda moment: now.__setitem__("t", moment)


@pytest.fixture()
def proposal(league, clock):
    """Season 51 on the current scoring, with a proposal open for a vote."""
    from app import proposals
    from app.scoring.classic import DEFAULT_CONFIG

    league.s51.scoring_config = json.dumps(dict(DEFAULT_CONFIG))
    league.db.session.commit()
    assert proposals.propose(league.s51, {**DEFAULT_CONFIG, **NEW}, CLOSES) is None
    league.at = clock
    return league


def _open(league):
    from app import proposals

    return proposals.open_proposal(league.s51)


def _vote(league, email, choice, comment=""):
    _login(league.c, email)
    resp = league.c.post(
        f"/league-settings/{league.s51.id}/vote",
        data={"choice": choice, "comment": comment},
    )
    league.c.get("/logout")
    return resp


def _votes(league, proposal=None):
    """{username: (choice, comment)} on a proposal (the open one by default)."""
    proposal = proposal or _open(league)
    if proposal is None:
        return {}
    return {v.user.username: (v.choice, v.comment) for v in proposal.votes}


class TestProposalPage:
    def test_compares_current_scoring_with_the_proposal(self, proposal):
        page = _page(proposal)

        assert "Proposed scoring for Season 51" in page
        assert "Wed Nov 18, 7:45 PM ET" in page
        assert "Final Tribal Bonus" in _block(page, "what-changed")

    def test_standings_are_current_against_proposed(self, proposal):
        listed = _standings_list(_page(proposal))
        current = _board(proposal.s51)
        proposed = _board(proposal.s51, _open(proposal).get_config())

        for user in ("pat", "sam"):
            assert f"currently {_pts(current[user])}," in listed
            assert f"<dd>{_pts(proposed[user])} pts" in listed

    def test_the_season_keeps_its_own_scoring_until_adopted(self, proposal):
        from app.scoring.classic import DEFAULT_CONFIG

        _page(proposal)

        assert proposal.s51.get_scoring_config() == DEFAULT_CONFIG

    def test_the_ballot_comes_before_the_long_sections(self, proposal):
        """Most players open this on a phone: the vote must not be at the bottom."""
        _login(proposal.c, "pat@example.com")
        page = _page(proposal)

        ballot = page.index('class="ls-vote-form"')
        for later in ('id="why"', 'id="rules"', 'id="past-seasons"', 'id="history"'):
            assert ballot < page.index(later), later

    def test_the_banner_links_straight_to_the_ballot(self, proposal):
        page = _page(proposal)

        assert 'class="ls-jump" href="#vote"' in page
        assert page.index('class="ls-jump"') < page.index('id="what-changed"')

    def test_no_jump_link_once_voting_closes(self, proposal):
        proposal.at(CLOSES.replace(tzinfo=UTC))

        assert 'class="ls-jump"' not in _page(proposal)

    def test_long_sections_start_closed_on_phones(self, proposal):
        page = _page(proposal)

        for block in ("why", "rules", "past-seasons", "history"):
            tag = page.split(f'id="{block}"', 1)[0].rsplit("<", 1)[1]
            assert tag.startswith("details") and "data-mobile-collapsed" in tag, block


class TestVoting:
    def test_a_player_votes_and_can_change_it(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _vote(proposal, "pat@example.com", "next")

        assert _votes(proposal) == {"pat": ("next", "")}

    def test_votes_and_comments_show_with_names(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _vote(proposal, "sam@example.com", "keep")

        votes = _block(_page(proposal), "vote")
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

    def test_a_new_proposal_starts_with_no_votes(self, proposal):
        """Votes belong to one proposal; a re-proposal must not inherit them."""
        from app import proposals
        from app.scoring.classic import DEFAULT_CONFIG

        _vote(proposal, "pat@example.com", "keep")
        first = _open(proposal)
        proposals.withdraw(proposal.s51)

        proposals.propose(
            proposal.s51, {**DEFAULT_CONFIG, **NEW, "fire_win_val": 0.5}, CLOSES
        )

        assert _votes(proposal) == {}
        assert _votes(proposal, first) == {"pat": ("keep", "")}
        _login(proposal.c, "pat@example.com")
        assert " checked" not in _block(_page(proposal), "vote")


class TestProposing:
    def test_a_closing_time_is_required(self, league, clock):
        from app import proposals

        assert proposals.propose(league.s51, dict(NEW), None) is not None
        assert proposals.open_proposal(league.s51) is None

    def test_the_closing_time_must_be_in_the_future(self, league, clock):
        from app import proposals

        clock(CLOSES.replace(tzinfo=UTC) + timedelta(minutes=1))

        assert proposals.propose(league.s51, dict(NEW), CLOSES) is not None
        assert proposals.open_proposal(league.s51) is None

    def test_proposing_again_replaces_the_open_one(self, proposal):
        from app import proposals
        from app.scoring.classic import DEFAULT_CONFIG

        first = _open(proposal)
        proposals.propose(
            proposal.s51, {**DEFAULT_CONFIG, **NEW, "fire_win_val": 0.5}, CLOSES
        )

        assert first.status == "withdrawn"
        assert _open(proposal).get_config()["fire_win_val"] == 0.5


class TestAdminProposal:
    def _settings(self, league, **extra):
        from app.scoring.classic import DEFAULT_CONFIG

        data = {f"scoring_{k}": v for k, v in {**DEFAULT_CONFIG, **NEW}.items()}
        data = {k: ("1" if v is True else v) for k, v in data.items()}
        data.update(extra)
        _login(league.c, ADMIN_EMAIL)
        return league.c.post(f"/admin/season/{league.s51.id}/settings", data=data)

    def test_proposing_saves_the_config_beside_the_season(self, league, clock):
        from app.scoring.classic import DEFAULT_CONFIG

        league.s51.scoring_config = json.dumps(dict(DEFAULT_CONFIG))
        league.db.session.commit()

        self._settings(league, propose="on", proposal_closes_at="2026-11-18T19:45")

        assert league.s51.get_scoring_config() == DEFAULT_CONFIG
        assert _open(league).get_config()["final_tribal_val"] == 4
        assert _open(league).get_config()["advantage_found_val"] == 0.25
        assert _open(league).closes_at == CLOSES

    def test_proposing_without_a_closing_time_is_refused(self, league, clock):
        self._settings(league, propose="on", proposal_closes_at="")

        assert _open(league) is None

    @pytest.mark.parametrize("bad", ["abc", "nan", "inf", "-inf"])
    def test_a_value_that_is_not_a_number_is_refused(self, league, bad):
        stored = league.s51.scoring_config

        resp = self._settings(league, scoring_jury_val=bad)

        assert resp.status_code == 302
        assert league.s51.scoring_config == stored

    def test_the_closing_time_is_pre_filled_from_the_open_proposal(self, proposal):
        _login(proposal.c, ADMIN_EMAIL)

        page = proposal.c.get(f"/admin/season/{proposal.s51.id}").data.decode()

        assert 'value="2026-11-18T19:45"' in page

    def test_adopting_applies_it_and_keeps_the_votes(self, proposal):
        _vote(proposal, "pat@example.com", "now")
        adopted = _open(proposal)
        _login(proposal.c, ADMIN_EMAIL)

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")

        assert proposal.s51.get_scoring_config()["final_tribal_val"] == 4
        assert _open(proposal) is None
        assert adopted.status == "adopted"
        assert _votes(proposal, adopted) == {"pat": ("now", "")}

    def test_withdrawing_closes_it_and_keeps_the_season_scoring(self, proposal):
        from app.scoring.classic import DEFAULT_CONFIG

        withdrawn = _open(proposal)
        _login(proposal.c, ADMIN_EMAIL)

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/withdraw")

        assert _open(proposal) is None
        assert withdrawn.status == "withdrawn"
        assert proposal.s51.get_scoring_config() == DEFAULT_CONFIG

    def test_a_player_cannot_adopt(self, proposal):
        _login(proposal.c, "pat@example.com")

        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")

        assert _open(proposal) is not None

    def test_after_adoption_the_page_keeps_the_vote_record(self, proposal):
        _vote(proposal, "pat@example.com", "now", "Love it")
        _login(proposal.c, ADMIN_EMAIL)
        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")
        proposal.c.get("/logout")

        page = _page(proposal)

        assert "Love it" in _block(page, "vote")
        assert "Proposed scoring" not in page

    def test_history_shows_the_switch_and_the_vote(self, proposal):
        """Adopting mid-season must not rewrite what the season played under before."""
        _vote(proposal, "pat@example.com", "now")
        _login(proposal.c, ADMIN_EMAIL)
        proposal.c.post(f"/admin/season/{proposal.s51.id}/proposal/adopt")
        proposal.c.get("/logout")

        history = _block(_page(proposal), "history")
        s51 = history.split("Season 51", 1)[1].split("Season 50", 1)[0]

        assert "Run 6" in s51 and "until" in s51
        assert "league vote" in s51

    def test_scoring_inputs_accept_values_finer_than_a_quarter(self, league):
        _login(league.c, ADMIN_EMAIL)

        page = league.c.get(f"/admin/season/{league.s51.id}").data.decode()

        assert 'step="0.25"' not in page


class TestReplayCache:
    def test_a_stat_change_without_a_boot_shows_within_the_cache_lifetime(
        self, league, monkeypatch
    ):
        """The replays expire: a refresh that only adds immunity wins must show."""
        from app import routes

        now = {"t": 1000.0}
        monkeypatch.setattr(routes, "_changes_cache_clock", lambda: now["t"])
        before = _block(_page(league), "standings")
        star = next(s for s in league.s51.survivors if s.name == "S51C8")
        star.individual_immunity_wins = 5
        league.db.session.commit()

        now["t"] += routes._CHANGES_CACHE_TTL + 1

        assert _block(_page(league), "standings") != before

    def test_a_cache_hit_builds_nothing(self, league, monkeypatch):
        from app import routes

        _page(league)
        calls = []
        for name in ("_build_leaderboard", "_compare_cache_key"):
            real = getattr(routes, name)
            monkeypatch.setattr(
                routes, name, lambda *a, _r=real, **k: calls.append(1) or _r(*a, **k)
            )

        _page(league)

        assert calls == []

    def test_the_cached_replays_hold_only_plain_values(self, league):
        """The cache outlives the request, so an ORM object in it would detach."""
        from app.routes import _changes_cache

        _page(league)

        def walk(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    assert isinstance(k, str)
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
            else:
                assert value is None or isinstance(value, (str, int, float)), value

        ((_stored, result),) = _changes_cache.values()
        assert any(row["players"] for row in result["past"])
        assert any(p["reasons"] for p in result["standings"])
        walk(result)


class TestHistoryGrouping:
    def test_seasons_on_the_same_version_share_one_row(self, league):
        from app.models import Season

        s48 = Season(
            number=48, name="Season 48", scoring_config=league.s49.scoring_config
        )
        league.db.session.add(s48)
        league.db.session.commit()

        history = _block(_page(league), "history")

        assert "Seasons 48 and 49" in history
        assert history.count("Legacy") == 1
