"""Markup checks for headings and disclosure buttons.

These cover what the server renders. Focus rings, the reduced-motion rules and
the Escape handling live in CSS and inline script, and are checked in a
browser, not here.
"""

import importlib
import re
import sys

import pytest


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
def finished_season(client):
    """A three-castaway season that is over, with two players, plus a second season."""
    from app.models import Pick, Season, Survivor, User

    c, db = client
    season = Season(
        number=99,
        name="Season 99",
        is_active=True,
        num_players=3,
        num_episodes=3,
        left_at_jury=3,
        n_finalists=3,
    )
    db.session.add_all([season, Season(number=98, name="Season 98", num_players=3)])
    db.session.flush()

    survivors = [
        Survivor(season_id=season.id, name=f"Castaway{i + 1}", voted_out_order=i + 1)
        for i in range(3)
    ]
    players = [
        User(username="pat", display_name="Pat"),
        User(username="sam", display_name="Sam"),
    ]
    db.session.add_all([*survivors, *players])
    db.session.flush()
    db.session.add_all(
        [
            Pick(
                user_id=players[0].id,
                season_id=season.id,
                survivor_id=survivors[2].id,
                pick_type="draft",
            ),
            Pick(
                user_id=players[1].id,
                season_id=season.id,
                survivor_id=survivors[0].id,
                pick_type="draft",
            ),
        ]
    )
    db.session.commit()
    return c, season


def _tag(html, element_id):
    match = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', html)
    assert match, f"no <button id={element_id}> on the page"
    return match.group(0)


def test_finished_season_has_one_h1(finished_season):
    """The finale banner's winner name is not a second page heading."""
    c, season = finished_season

    html = c.get(f"/leaderboard/{season.id}").get_data(as_text=True)

    assert "finale-banner" in html, "fixture did not render the finale banner"
    assert re.findall(r"<h1[ >]", html) == ["<h1>"]
    assert '<p class="finale-winner-name">Pat</p>' in html


def test_season_button_declares_what_it_opens(finished_season):
    c, season = finished_season

    tag = _tag(
        c.get(f"/leaderboard/{season.id}").get_data(as_text=True), "seasons-trigger"
    )

    assert 'aria-expanded="false"' in tag
    assert 'aria-controls="seasons-dropdown"' in tag
    assert 'type="button"' in tag


def test_contents_button_declares_what_it_opens(finished_season):
    c, _season = finished_season

    html = c.get("/rules").get_data(as_text=True)
    tag = _tag(html, "toc-toggle")

    assert 'aria-expanded="false"' in tag
    assert 'aria-controls="page-toc"' in tag
    assert 'id="page-toc"' in html
