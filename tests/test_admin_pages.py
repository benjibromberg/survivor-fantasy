"""The admin pages other than a season's own: laid out by task, not as tables.

At 375px the old tables put each row's actions past the right edge, where
they could be reached only by scrolling the table sideways. These pin the
task layout (cards, folds, sections) and the routes and POST contracts the
pages drive.
"""

import importlib
import json
import re
import sys
from types import SimpleNamespace

import pytest

ADMIN_EMAIL = "admin@example.com"
PAT_EMAIL = "pat@example.com"
ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


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


def _version(key):
    from app.scoring.history import SCORING_VERSIONS

    return dict(next(v for v in SCORING_VERSIONS if v["key"] == key)["config"])


@pytest.fixture()
def league(client):
    """Season 60 (named, on Legacy) and 61 (active, Run 6 with one change).

    Pat plays 61 with a linked email; Sam plays 60 and 61.
    """
    from app.models import Pick, Season, Survivor, User

    c, db = client
    run6_changed = {**_version("run-6"), "jury_val": 4}
    s60 = Season(
        number=60,
        name="Heroes v Villains",
        num_players=4,
        num_episodes=4,
        scoring_config=json.dumps(_version("legacy")),
    )
    s61 = Season(
        number=61,
        name="Season 61",
        is_active=True,
        num_players=4,
        num_episodes=4,
        scoring_config=json.dumps(run6_changed),
    )
    db.session.add_all([s60, s61])
    db.session.flush()
    cast = {}
    for season in (s60, s61):
        for i, name in enumerate(("Ada", "Bo", "Cy", "Di")):
            cast[(season.number, name)] = Survivor(
                season_id=season.id,
                name=name,
                voted_out_order=1 if i == 0 else 0,
            )
    pat = User(username="pat", display_name="Pat", email=PAT_EMAIL)
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*cast.values(), pat, sam])
    db.session.flush()
    for user, season, name, kind in (
        (pat, s61, "Ada", "draft"),
        (pat, s61, "Bo", "wildcard"),
        (sam, s60, "Ada", "draft"),
        (sam, s61, "Cy", "draft"),
    ):
        db.session.add(
            Pick(
                user_id=user.id,
                season_id=season.id,
                survivor_id=cast[(season.number, name)].id,
                pick_type=kind,
                pick_order=1 if kind == "draft" else None,
            )
        )
    db.session.commit()
    return SimpleNamespace(c=c, db=db, s60=s60, s61=s61, pat=pat, sam=sam, cast=cast)


def _login(c, email=ADMIN_EMAIL):
    assert c.get("/login", headers={ACCESS_HEADER: email}).status_code == 302


def _flashes(c):
    with c.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _errors(c):
    return [msg for category, msg in _flashes(c) if category == "error"]


def _page(league, url):
    resp = league.c.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp.get_data(as_text=True)


def _inside_closed_fold(page, marker):
    """True when `marker` sits inside a <details> that is not rendered open."""
    before = page[: page.index(marker)]
    opened = before.rfind("<details")
    assert opened != -1, f"{marker} is not inside any fold"
    assert before.count("<details") > before.count("</details>"), marker
    tag = before[opened : before.index(">", opened) + 1]
    return " open" not in tag


def _card(page, marker):
    """The admin card (an <li class="admin-card...">) holding `marker`."""
    start = page.rindex('<li class="admin-card', 0, page.index(marker))
    return page[start : page.index("</li>", start)]


# ── The seasons list (/admin/seasons, the admin home) ────────────────────


class TestSeasonsList:
    def test_each_season_name_opens_its_admin_page(self, league):
        _login(league.c)

        page = _page(league, "/admin/seasons")

        for season in (league.s60, league.s61):
            link = f'href="/admin/season/{season.id}">{season.name}</a>'
            assert link in page

    def test_scoring_names_the_version_the_season_plays_under(self, league):
        _login(league.c)
        page = _page(league, "/admin/seasons")

        s61 = _card(page, f'href="/admin/season/{league.s61.id}"')
        s60 = _card(page, f'href="/admin/season/{league.s60.id}"')

        assert "<dd>Run 6<small>1 value changed</small></dd>" in s61
        assert "<dd>Legacy</dd>" in s60
        assert "Default" not in page and "Custom" not in page

    def test_facts_are_label_above_value_not_a_table(self, league):
        _login(league.c)
        page = _page(league, "/admin/seasons")

        s61 = _card(page, f'href="/admin/season/{league.s61.id}"')
        s60 = _card(page, f'href="/admin/season/{league.s60.id}"')

        assert "<table" not in page
        assert "<dt>Castaways</dt><dd>4</dd>" in s61
        assert "<dt>Status</dt><dd>Active</dd>" in s61
        assert "<dt>Status</dt><dd>Inactive</dd>" in s60
        # A named season shows its number, since the name does not
        assert "<dt>Season</dt><dd>60</dd>" in s60
        assert "<dt>Season</dt>" not in s61

    def test_each_card_reaches_picks_team_names_and_the_active_switch(self, league):
        _login(league.c)
        page = _page(league, "/admin/seasons")

        for season, switch in ((league.s61, "Deactivate"), (league.s60, "Make active")):
            card = _card(page, f'href="/admin/season/{season.id}"')
            assert f'href="/admin/picks/{season.id}"' in card
            assert f'href="/admin/players/{season.id}"' in card
            assert f'action="/admin/season/{season.id}/toggle-active"' in card
            assert f">{switch}<" in card

    def test_delete_is_on_the_season_page_not_the_list(self, league):
        _login(league.c)
        delete = f"/admin/season/{league.s60.id}/delete"

        assert delete not in _page(league, "/admin/seasons")
        assert delete in _page(league, f"/admin/season/{league.s60.id}")

    def test_create_a_season_is_folded_away(self, league):
        _login(league.c)

        page = _page(league, "/admin/seasons")

        assert 'name="number"' in page and 'name="fetch_images"' in page
        assert _inside_closed_fold(page, 'name="number"')

    def test_with_no_seasons_the_create_fold_starts_open(self, client):
        c, _db = client
        _login(c)

        page = c.get("/admin/seasons").get_data(as_text=True)

        assert not _inside_closed_fold(page, 'name="number"')

    def test_export_all_picks_and_players_stay_reachable(self, league):
        _login(league.c)

        page = _page(league, "/admin/seasons")

        assert 'action="/admin/export-all-picks"' in page
        assert 'href="/admin/players"' in page
        assert "<dt>Can log in</dt><dd>1</dd>" in page  # Pat

    def test_sections_come_in_task_order(self, league):
        _login(league.c)
        page = _page(league, "/admin/seasons")

        ids = ["seasons", "players", "backups"]
        found = [page.index(f'<section id="{i}"') for i in ids]

        assert found == sorted(found)

    def test_toggle_still_switches_the_active_season(self, league, monkeypatch):
        from app import schedule
        from app.models import Season

        monkeypatch.setattr(schedule, "sync_episode2_time", lambda s: None)
        monkeypatch.setattr(schedule, "sync_replacement_time", lambda s: None)
        _login(league.c)

        resp = league.c.post(f"/admin/season/{league.s60.id}/toggle-active")

        assert resp.headers["Location"].endswith("/admin/seasons")
        assert Season.get_active().id == league.s60.id
        assert not league.db.session.get(Season, league.s61.id).is_active


# ── The picks page (/admin/picks/<season_id>) ────────────────────────────


def _picks_of(user, season):
    from app.models import Pick

    return sorted(
        (p.survivor.name, p.pick_type)
        for p in Pick.query.filter_by(user_id=user.id, season_id=season.id)
    )


class TestPicksPage:
    def _url(self, league, season=None, **args):
        season = season or league.s61
        query = "&".join(f"{k}={v}" for k, v in args.items())
        return f"/admin/picks/{season.id}" + (f"?{query}" if query else "")

    def test_sections_come_in_task_order(self, league):
        _login(league.c)
        page = _page(league, self._url(league))

        found = [
            page.index(f'<section id="{i}"') for i in ("enter-pick", "player-picks")
        ]

        assert found == sorted(found)

    def test_enter_a_pick_asks_for_a_player_a_kind_and_a_castaway(self, league):
        _login(league.c)
        page = _page(league, self._url(league))

        form = page.split('<section id="enter-pick"', 1)[1].split("<details", 1)[0]

        assert f'action="/admin/picks/{league.s61.id}/add"' in form
        for field in ('name="user_id"', 'name="pick_type"', 'name="survivor_id"'):
            assert field in form
        for kind in ("draft", "wildcard", "pmr_d", "pmr_w"):
            assert f'<option value="{kind}">' in form

    def test_each_players_picks_read_by_name(self, league):
        _login(league.c)
        page = _page(league, self._url(league))

        pat = _card(page, f'id="player-{league.pat.id}"')

        assert "<dt>Draft</dt>" in pat and "<dd>Ada</dd>" in pat
        assert "<dt>Wildcard</dt><dd>Bo</dd>" in pat
        assert f"/admin/picks/{league.s61.id}?user={league.pat.id}#change-picks" in pat

    def test_the_editor_is_folded_away_until_a_player_is_chosen(self, league):
        _login(league.c)

        closed = _page(league, self._url(league))
        opened = _page(league, self._url(league, user=league.pat.id))

        assert _inside_closed_fold(closed, 'id="picks-form"')
        assert not _inside_closed_fold(opened, 'id="picks-form"')
        assert f'<option value="{league.pat.id}" selected' in opened

    def test_an_unknown_user_in_the_query_opens_nothing(self, league):
        _login(league.c)

        page = _page(league, self._url(league, user=424242))

        assert _inside_closed_fold(page, 'id="picks-form"')

    def test_saving_the_editor_returns_to_it_on_the_same_player(self, league):
        _login(league.c)

        resp = league.c.post(
            self._url(league),
            data={"user_id": league.sam.id, "draft": [league.cast[(61, "Di")].id]},
        )

        location = resp.headers["Location"]
        assert location.endswith(
            f"/admin/picks/{league.s61.id}?user={league.sam.id}#change-picks"
        )
        assert _picks_of(league.sam, league.s61) == [("Di", "draft")]

    def test_the_castaway_cards_still_fill_the_draft_grid(self, league):
        _login(league.c)

        page = _page(league, self._url(league))

        assert page.count('<label class="survivor-card') == 4
        assert 'class="survivor-grid"' in page

    def test_sole_survivor_shows_only_where_the_season_scores_it(self, league):
        _login(league.c)

        scored = _page(league, self._url(league))  # 61 plays Run 6
        unscored = _page(league, self._url(league, season=league.s60))  # Legacy

        assert '<select name="sole_survivor"' in scored
        assert '<option value="sole_survivor">' in scored
        assert '<select name="sole_survivor"' not in unscored
        assert '<option value="sole_survivor">' not in unscored

    def test_an_existing_sole_survivor_pick_shows_even_where_it_scores_nothing(
        self, league
    ):
        from app.models import SoleSurvivorPick

        league.db.session.add(
            SoleSurvivorPick(
                user_id=league.sam.id,
                season_id=league.s60.id,
                survivor_id=league.cast[(60, "Bo")].id,
                episode=2,
            )
        )
        league.db.session.commit()
        _login(league.c)

        page = _page(league, self._url(league, season=league.s60))

        sam = _card(page, f'id="player-{league.sam.id}"')
        assert "<dt>Sole Survivor</dt>" in sam
        assert "<dd>Bo<small>from episode 2</small></dd>" in sam

    def test_sole_survivor_delete_buttons_submit_their_own_form(self, league):
        """A form inside the editor's form is dropped by the HTML parser.

        The old page injected each Delete inside the editor, so pressing it
        submitted the editor and saved picks instead of deleting anything.
        """
        from app.models import SoleSurvivorPick

        pick = SoleSurvivorPick(
            user_id=league.pat.id,
            season_id=league.s61.id,
            survivor_id=league.cast[(61, "Cy")].id,
            episode=1,
        )
        league.db.session.add(pick)
        league.db.session.commit()
        _login(league.c)

        page = _page(league, self._url(league))
        editor = page.split('id="picks-form"', 1)[1].split("</form>", 1)[0]
        after = page.split('id="picks-form"', 1)[1].split("</form>", 1)[1]
        action = f"/admin/picks/{league.s61.id}/ss/{pick.id}/delete"

        assert f'form="ss-delete-{pick.id}"' in editor
        assert "<form" not in editor
        assert f'id="ss-delete-{pick.id}" action="{action}"' in after

        league.c.post(action)

        assert SoleSurvivorPick.query.count() == 0

    def test_empty_season_points_to_the_season_page(self, league):
        from app.models import Season

        empty = Season(number=62, name="Season 62", num_players=0)
        league.db.session.add(empty)
        league.db.session.commit()
        _login(league.c)

        page = _page(league, f"/admin/picks/{empty.id}")

        assert "No castaways yet" in page
        assert f'href="/admin/season/{empty.id}#season-data"' in page


class TestEnterAPick:
    def _add(self, league, user, kind, survivor, season=None, **extra):
        season = season or league.s61
        data = {
            "user_id": getattr(user, "id", user),
            "pick_type": kind,
            "survivor_id": getattr(survivor, "id", survivor),
            **extra,
        }
        return league.c.post(f"/admin/picks/{season.id}/add", data=data)

    def test_a_draft_pick_is_added_and_the_others_stay(self, league):
        from app.models import Pick

        _login(league.c)

        resp = self._add(league, league.pat, "draft", league.cast[(61, "Cy")])

        assert resp.headers["Location"].endswith(f"/admin/picks/{league.s61.id}")
        assert _picks_of(league.pat, league.s61) == [
            ("Ada", "draft"),
            ("Bo", "wildcard"),
            ("Cy", "draft"),
        ]
        added = Pick.query.filter_by(
            user_id=league.pat.id, survivor_id=league.cast[(61, "Cy")].id
        ).one()
        assert added.pick_order == 2  # after Ada

    def test_a_missing_wildcard_is_entered(self, league):
        _login(league.c)

        self._add(league, league.sam, "wildcard", league.cast[(61, "Di")])

        assert _picks_of(league.sam, league.s61) == [
            ("Cy", "draft"),
            ("Di", "wildcard"),
        ]

    def test_a_wildcard_replaces_the_one_the_player_had(self, league):
        _login(league.c)

        self._add(league, league.pat, "wildcard", league.cast[(61, "Di")])

        assert _picks_of(league.pat, league.s61) == [
            ("Ada", "draft"),
            ("Di", "wildcard"),
        ]
        assert any(
            "replacing Bo" in msg for cat, msg in _flashes(league.c) if cat == "success"
        )

    def test_replacements_are_entered_by_kind(self, league):
        _login(league.c)

        self._add(league, league.pat, "pmr_d", league.cast[(61, "Cy")])
        self._add(league, league.sam, "pmr_w", league.cast[(61, "Di")])

        assert ("Cy", "pmr_d") in _picks_of(league.pat, league.s61)
        assert ("Di", "pmr_w") in _picks_of(league.sam, league.s61)

    def test_a_castaway_from_another_season_is_refused(self, league):
        _login(league.c)

        self._add(league, league.sam, "wildcard", league.cast[(60, "Di")])

        assert _picks_of(league.sam, league.s61) == [("Cy", "draft")]
        assert _errors(league.c)

    def test_a_castaway_the_player_holds_as_another_pick_is_refused(self, league):
        _login(league.c)

        self._add(league, league.pat, "wildcard", league.cast[(61, "Ada")])

        assert _picks_of(league.pat, league.s61) == [
            ("Ada", "draft"),
            ("Bo", "wildcard"),
        ]
        assert any("as their draft pick" in msg for msg in _errors(league.c))

    def test_entering_a_pick_the_player_already_has_changes_nothing(self, league):
        _login(league.c)

        self._add(league, league.pat, "wildcard", league.cast[(61, "Bo")])

        assert _picks_of(league.pat, league.s61) == [
            ("Ada", "draft"),
            ("Bo", "wildcard"),
        ]
        assert not _errors(league.c)

    @pytest.mark.parametrize(
        "user, kind, survivor",
        [("nobody", "draft", "Cy"), ("sam", "captain", "Cy"), ("sam", "draft", None)],
    )
    def test_missing_or_unknown_fields_are_refused(self, league, user, kind, survivor):
        _login(league.c)
        user_id = league.sam.id if user == "sam" else 424242
        survivor_id = league.cast[(61, survivor)].id if survivor else ""

        self._add(league, user_id, kind, survivor_id)

        assert _picks_of(league.sam, league.s61) == [("Cy", "draft")]
        assert _errors(league.c)

    def test_a_sole_survivor_pick_needs_its_episode(self, league):
        from app.models import SoleSurvivorPick

        _login(league.c)

        self._add(league, league.sam, "sole_survivor", league.cast[(61, "Bo")])

        assert SoleSurvivorPick.query.count() == 0
        assert _errors(league.c)

    def test_a_sole_survivor_pick_is_set_from_its_episode(self, league):
        from app.models import SoleSurvivorPick

        _login(league.c)

        self._add(
            league, league.sam, "sole_survivor", league.cast[(61, "Bo")], ss_episode=3
        )
        self._add(
            league, league.sam, "sole_survivor", league.cast[(61, "Di")], ss_episode=3
        )

        pick = SoleSurvivorPick.query.one()
        assert (pick.survivor.name, pick.episode) == ("Di", 3)
        assert _picks_of(league.sam, league.s61) == [("Cy", "draft")]

    def test_a_player_cannot_enter_picks(self, league):
        _login(league.c, PAT_EMAIL)

        resp = self._add(league, league.pat, "draft", league.cast[(61, "Di")])

        assert resp.status_code == 302
        assert "/admin" not in resp.headers["Location"]
        assert _picks_of(league.pat, league.s61) == [
            ("Ada", "draft"),
            ("Bo", "wildcard"),
        ]


# ── Every page ───────────────────────────────────────────────────────────


def _admin_urls(league):
    return [
        "/admin/seasons",
        f"/admin/picks/{league.s61.id}",
    ]


class TestEveryPage:
    def test_every_post_form_carries_a_csrf_token(self, league):
        _login(league.c)

        for url in _admin_urls(league):
            forms = re.findall(r"<form[^>]*>.*?</form>", _page(league, url), re.S)
            posts = [f for f in forms if 'method="POST"' in f]
            assert posts, url
            missing = [f[:80] for f in posts if 'name="csrf_token"' not in f]
            assert not missing, f"{url}: {missing}"

    def test_one_h1_and_no_skipped_heading_levels(self, league):
        _login(league.c)

        for url in _admin_urls(league):
            levels = [int(n) for n in re.findall(r"<h([1-6])[ >]", _page(league, url))]
            assert levels.count(1) == 1, url
            assert all(b - a <= 1 for a, b in zip(levels, levels[1:], strict=False))

    def test_section_headings_use_the_site_heading_style(self, league):
        _login(league.c)

        for url in _admin_urls(league):
            page = _page(league, url)
            for tag in re.findall(r"<h[23][^>]*>", page):
                assert "section-title" in tag or "subsection-title" in tag, (url, tag)

    def test_no_admin_page_holds_a_table(self, league):
        """Every row's actions must be reachable without scrolling sideways."""
        _login(league.c)

        for url in _admin_urls(league):
            assert "<table" not in _page(league, url), url

    def test_a_player_cannot_open_them(self, league):
        _login(league.c, PAT_EMAIL)

        for url in _admin_urls(league):
            resp = league.c.get(url)
            assert resp.status_code == 302, url
            assert "/admin" not in resp.headers["Location"], url
