"""When a wildcard or a replacement starts to count, the same on every page.

survivoR records a same-day double boot as consecutive orders with the same
day and episode (season 51: Alexis 6 and Linnea 7, both day 10). The number
of boots then runs ahead of the number of elimination days, and CLAUDE.md's
tribal counting rule says same-day boots count once. The leaderboard counted
active picks by day; the compare page counted boots, so the two disagreed
when a pair took the boot count to the merge before the day count got there.
"""

import importlib
import json
import re
import sys

import pytest

from app.replacements import counts_yet


class TestCountsYet:
    def test_a_wildcard_counts_from_the_first_boot(self):
        assert not counts_yet("wildcard", 0, 3)
        assert counts_yet("wildcard", 1, 3)

    def test_a_replacement_counts_from_the_merge(self):
        for kind in ("pmr_w", "pmr_d"):
            assert not counts_yet(kind, 2, 3), kind
            assert counts_yet(kind, 3, 3), kind
            assert not counts_yet(kind, 9, None), kind

    def test_a_draft_always_counts(self):
        assert counts_yet("draft", 0, None)


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


def _season(db, number, boots):
    """Eight castaways, merge after 3 boots (left_at_jury 5), in progress.

    `boots` is (voted_out_order, day, episode) for C1, C2, ... in turn; the
    rest are still in. C4 holds points a replacement scores after the merge
    (advantages and an idol play). Pat drafts C8 and holds C4 as a draft
    replacement; Sam drafts C7 and holds C6 as a wildcard.
    """
    from app.models import Pick, Season, Survivor, User
    from app.scoring.classic import DEFAULT_CONFIG

    season = Season(
        number=number,
        name=f"Season {number}",
        num_players=8,
        num_episodes=13,
        left_at_jury=5,
        n_finalists=3,
        scoring_config=json.dumps(dict(DEFAULT_CONFIG)),
    )
    db.session.add(season)
    db.session.flush()
    cast = []
    for i in range(8):
        order, day, episode = boots[i] if i < len(boots) else (0, None, None)
        cast.append(
            Survivor(
                season_id=season.id,
                name=f"S{number}C{i + 1}",
                voted_out_order=order,
                day_voted_out=day,
                elimination_episode=episode,
                result=f"{order} voted out" if order else None,
                advantages_found=2 if i in (3, 5) else 0,
                idols_played=1 if i in (3, 5) else 0,
            )
        )
    users = {
        name: User.query.filter_by(username=name).first()
        or User(username=name, display_name=name.title())
        for name in ("pat", "sam")
    }
    db.session.add_all([*cast, *users.values()])
    db.session.flush()

    def pick(user, castaway, kind):
        return Pick(
            user_id=user.id,
            season_id=season.id,
            survivor_id=castaway.id,
            pick_type=kind,
        )

    db.session.add_all(
        [
            pick(users["pat"], cast[7], "draft"),
            pick(users["pat"], cast[3], "pmr_d"),
            pick(users["sam"], cast[6], "draft"),
            pick(users["sam"], cast[5], "wildcard"),
        ]
    )
    db.session.commit()
    return season, cast


# Two boots on day 5 in episode 2, consecutive orders: three boots, two days,
# and the merge comes after three boots, which is two elimination days.
PAIR_AT_THE_MERGE = [(1, 2, 1), (2, 5, 2), (3, 5, 2)]
# A double boot in episode 1, then the season stops: two boots on one day.
# The merge is still a boot away, so at least two days: one so far, one to come.
PAIR_BEFORE_THE_MERGE = [(1, 2, 1), (2, 2, 1)]
# No pair at all: boots and days agree, so nothing about the merge moves.
NO_PAIR = [(1, 2, 1), (2, 5, 2), (3, 8, 3)]
# Season 51's shape: the pair comes after the merge (orders 4 and 5, day 10).
PAIR_AFTER_THE_MERGE = [(1, 2, 1), (2, 5, 2), (3, 8, 3), (4, 10, 4), (5, 10, 4)]


def _leaderboard(season):
    from app.routes import _build_leaderboard

    entries, _ = _build_leaderboard(season)
    return {e["user"].username: e for e in entries}


def _compare(season):
    """The compare page's own-scoring preset: {username: (total, bar, line)}."""
    from app.routes import _build_compare_data

    data = _build_compare_data(season)
    (own,) = [c for c in data["comparisons"] if c["name"] == "Current Settings"]
    by_name = {e["user"].display_name: e for e in own["entries"]}
    bars = {row["name"]: sum(row["breakdown"].values()) for row in own["breakdown"]}
    lines = {row["name"]: row["points"] for row in own["datasets"]}
    return {
        e["user"].username: (e["total_points"], bars[name], lines[name])
        for name, e in by_name.items()
    }


def _leaderboard_lines(c, season):
    """{display name: progression points} from the leaderboard page."""
    page = c.get(f"/leaderboard/{season.id}").data.decode()
    data = json.loads(re.search(r"const progDatasets = (\[.*?\]);\n", page).group(1))
    return {row["name"]: row["points"] for row in data}


class TestPairAtTheMerge:
    def test_the_fixture_is_survivors_shape_and_reaches_the_merge(self, client):
        _, db = client
        season, cast = _season(db, 61, PAIR_AT_THE_MERGE)
        first, second = cast[1], cast[2]

        assert (first.voted_out_order, second.voted_out_order) == (2, 3)
        assert first.day_voted_out == second.day_voted_out
        assert first.elimination_episode == second.elimination_episode
        # Three boots are the merge, and they took two elimination days
        assert season.merge_threshold == 3
        assert max(s.voted_out_order for s in cast) == 3
        assert season.current_tribal_count == 2

    def test_the_merge_is_two_days_in(self, client):
        from app.replacements import scoring_start

        _, db = client
        season, _ = _season(db, 61, PAIR_AT_THE_MERGE)

        assert scoring_start(season) == (2, 2)

    def test_the_pair_no_longer_holds_the_replacement_back(self, client):
        """The merge has happened, so the replacement counts. It used to wait
        a day: the merge point was the boot count (3) and the day count is 2."""
        _, db = client
        season, _ = _season(db, 61, PAIR_AT_THE_MERGE)
        pat = _leaderboard(season)["pat"]

        assert "pmr_d" in {p["pick_type_raw"] for p in pat["picks"]}
        assert not counts_yet("pmr_d", season.current_tribal_count, 3)

    def test_the_compare_page_counts_picks_as_the_leaderboard_does(self, client):
        _, db = client
        season, _ = _season(db, 61, PAIR_AT_THE_MERGE)

        board, compare = _leaderboard(season), _compare(season)

        for name, entry in board.items():
            total, bar, line = compare[name]
            assert total == pytest.approx(entry["total_points"]), name
            assert bar == pytest.approx(entry["total_points"]), name
            assert line[-1] == pytest.approx(entry["total_points"]), name

    def test_the_leaderboard_line_ends_on_the_standings(self, client):
        c, db = client
        season, _ = _season(db, 61, PAIR_AT_THE_MERGE)

        lines, board = _leaderboard_lines(c, season), _leaderboard(season)

        for entry in board.values():
            name = entry["user"].display_name
            assert lines[name][-1] == pytest.approx(entry["total_points"]), name


class TestPairBeforeTheMerge:
    """Boots run ahead of days here and the merge is between them, so a page
    counting boots would count the replacement and the leaderboard would not."""

    def test_the_boot_count_reaches_the_merge_point_and_the_day_count_does_not(
        self, client
    ):
        from app.replacements import scoring_start
        from app.scoring import get_scoring_system

        _, db = client
        season, cast = _season(db, 64, PAIR_BEFORE_THE_MERGE)
        merge_from, _ = scoring_start(season)
        boots = max(s.voted_out_order for s in cast)
        scoring = get_scoring_system("Classic", season.get_scoring_config())
        held, _ = scoring.score_pick(cast[3], season, "pmr_d", {"advantages_found": 2})

        assert (boots, season.current_tribal_count, merge_from) == (2, 1, 2)
        assert held > 0
        assert counts_yet("pmr_d", boots, merge_from)
        assert not counts_yet("pmr_d", season.current_tribal_count, merge_from)
        pat = _leaderboard(season)["pat"]
        assert "pmr_d" not in {p["pick_type_raw"] for p in pat["picks"]}

    def test_the_compare_page_counts_picks_as_the_leaderboard_does(self, client):
        c, db = client
        season, _ = _season(db, 64, PAIR_BEFORE_THE_MERGE)

        board, compare = _leaderboard(season), _compare(season)
        lines = _leaderboard_lines(c, season)

        for name, entry in board.items():
            total, bar, line = compare[name]
            assert total == bar == pytest.approx(entry["total_points"]), name
            assert line[-1] == pytest.approx(entry["total_points"]), name
            display = entry["user"].display_name
            assert lines[display][-1] == pytest.approx(entry["total_points"])


class TestNoPair:
    def test_the_merge_point_is_the_boot_count_as_before(self, client):
        from app.replacements import scoring_start

        _, db = client
        season, _ = _season(db, 65, NO_PAIR)

        assert scoring_start(season) == (3, 3) == (season.merge_threshold, 3)
        assert "pmr_d" in {
            p["pick_type_raw"] for p in _leaderboard(season)["pat"]["picks"]
        }

    def test_before_the_merge_it_is_not_reached_early(self, client):
        """One boot of three: the merge point stays at three, as it was."""
        from app.replacements import scoring_start

        _, db = client
        season, _ = _season(db, 66, NO_PAIR[:1])

        assert scoring_start(season) == (3, None)
        assert "pmr_d" not in {
            p["pick_type_raw"] for p in _leaderboard(season)["pat"]["picks"]
        }


class TestWinOdds:
    def test_the_odds_count_picks_by_day_like_the_standings(
        self, client, monkeypatch, tmp_path
    ):
        """The win odds took the boot count; they now take the day count from
        before the simulation renumbers anyone, and the same merge point."""
        from app import predictions

        _, db = client
        monkeypatch.setattr(
            predictions, "SURVIVOR_DATA_FILE", str(tmp_path / "missing.xlsx")
        )
        predictions.clear_cache()
        season, _ = _season(db, 61, PAIR_AT_THE_MERGE)
        seen = set()
        real = predictions._score_all_users

        def spy(scoring, users, user_picks, surv_by_id, season_, tribals, start, *rest):
            seen.add((tribals, start))
            return real(
                scoring, users, user_picks, surv_by_id, season_, tribals, start, *rest
            )

        monkeypatch.setattr(predictions, "_score_all_users", spy)
        predictions.calculate_win_probabilities(season)
        predictions.clear_cache()

        assert seen == {(2, 2)}  # two days in, merge two days in: it counts


class TestPairAfterTheMerge:
    def test_both_pages_agree_and_the_replacement_counts(self, client):
        c, db = client
        season, _ = _season(db, 62, PAIR_AFTER_THE_MERGE)

        board, compare = _leaderboard(season), _compare(season)
        lines = _leaderboard_lines(c, season)

        assert season.current_tribal_count == 4
        assert "pmr_d" in {p["pick_type_raw"] for p in board["pat"]["picks"]}
        for name, entry in board.items():
            total, bar, line = compare[name]
            assert total == bar == pytest.approx(entry["total_points"]), name
            assert line[-1] == pytest.approx(entry["total_points"]), name
            display = entry["user"].display_name
            assert lines[display][-1] == pytest.approx(entry["total_points"])


class TestTwoCastawaysOnOneOrder:
    """A shape survivoR has never used, but might for an unordered pair: two
    castaways sharing an order and a day. The pages must not break on it."""

    @pytest.fixture()
    def shared(self, client):
        from app.models import Pick

        c, db = client
        _season(db, 60, PAIR_AT_THE_MERGE)  # the season before, for the replays
        season, cast = _season(db, 63, [(1, 2, 1), (2, 5, 2), (2, 5, 2)])
        pat = _leaderboard(season)["pat"]["user"]
        db.session.add_all(
            Pick(
                user_id=pat.id,
                season_id=season.id,
                survivor_id=cast[i].id,
                pick_type="draft",
            )
            for i in (1, 2)
        )
        db.session.commit()
        return c, season, cast

    def test_every_page_renders(self, shared):
        c, season, _ = shared

        for url in (
            f"/leaderboard/{season.id}",
            f"/compare/{season.id}",
            f"/league-settings/{season.id}/changes",
        ):
            assert c.get(url).status_code == 200, url

    def test_both_count_as_out_with_the_same_tribal_count(self, shared):
        _, season, cast = shared
        pat = _leaderboard(season)["pat"]

        out = {p["survivor"]: p["eliminated"] for p in pat["picks"]}
        assert out[cast[1].name] and out[cast[2].name]
        assert season.compute_tribals_survived(
            cast[1]
        ) == season.compute_tribals_survived(cast[2])
        assert season.current_tribal_count == 2
