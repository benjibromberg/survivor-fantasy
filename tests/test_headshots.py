"""Tests for castaway headshot URLs: candidate ordering and the HEAD-check loop."""

import importlib
import logging
import sys
from types import SimpleNamespace

import pytest
import requests

from app.data import generate_season_images, headshot_url_candidates

# ── Helpers ────────────────────────────────────────────────────────────────


def _url(season, site_name):
    """Build the expected headshot URL for a site name."""
    return f"https://www.fantasysurvivorgame.com/images/{season}/biopics/{site_name}BIO.jpg"


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if anything in this module reaches the network."""

    def _blocked(self, method, url, **kwargs):
        raise AssertionError(f"unexpected network request: {method} {url}")

    monkeypatch.setattr(requests.Session, "request", _blocked)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Flask app with a temp file-backed SQLite DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application, db
        db.session.remove()

    if "config" in sys.modules:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(sys.modules["config"])


def _add_season(db, number, names):
    """Create a season with one survivor per name; return (season, survivors)."""
    from app.models import Season, Survivor

    season = Season(number=number, name=f"Season {number}", num_players=len(names))
    db.session.add(season)
    db.session.flush()
    survivors = [
        Survivor(season_id=season.id, name=name, voted_out_order=0) for name in names
    ]
    db.session.add_all(survivors)
    db.session.commit()
    return season, survivors


# ── headshot_url_candidates: pure candidate ordering ──────────────────────


class TestHeadshotUrlCandidates:
    def test_first_name_rule(self):
        assert headshot_url_candidates(50, "Rizo")[0] == _url(50, "rizo")

    def test_season_override_comes_first(self):
        """Season 51 Danny is filed under his surname."""
        candidates = headshot_url_candidates(51, "Danny")
        assert candidates[0] == _url(51, "kilby")
        # The first-name rule is still offered as a fallback
        assert candidates[1] == _url(51, "danny")

    def test_season_override_does_not_leak_to_other_seasons(self):
        candidates = headshot_url_candidates(50, "Danny")
        assert candidates[0] == _url(50, "danny")
        assert _url(50, "kilby") not in candidates

    def test_multi_word_name_offers_url_encoded_full_name(self):
        """Thien An is filed under the full name, not the first word."""
        assert headshot_url_candidates(51, "Thien An") == [
            _url(51, "thien"),
            _url(51, "thien%20an"),
            _url(51, "%22thien%22"),
        ]

    def test_single_word_name_has_no_full_name_candidate(self):
        assert len(headshot_url_candidates(50, "Rizo")) == 2

    def test_global_override_single_word(self):
        assert headshot_url_candidates(41, "Jelinsky")[0] == _url(41, "david")

    def test_global_override_multi_word(self):
        assert headshot_url_candidates(45, "J. Maya")[0] == _url(45, "janani")

    def test_quoted_variant_still_offered(self):
        """Names like Q are filed wrapped in URL-encoded double quotes."""
        assert headshot_url_candidates(46, "Q") == [
            _url(46, "q"),
            _url(46, "%22q%22"),
        ]

    def test_no_duplicate_candidates(self):
        for season, name in [
            (51, "Danny"),
            (50, "Danny"),
            (51, "Thien An"),
            (45, "J. Maya"),
            (46, "Q"),
        ]:
            candidates = headshot_url_candidates(season, name)
            assert len(candidates) == len(set(candidates)), (season, name)

    def test_override_equal_to_first_name_is_not_repeated(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules["app.data"].SEASON_NAME_TO_SITE, 99, {"rizo": "rizo"}
        )
        assert headshot_url_candidates(99, "Rizo") == [
            _url(99, "rizo"),
            _url(99, "%22rizo%22"),
        ]

    def test_blank_name_has_no_candidates(self):
        assert headshot_url_candidates(50, "  ") == []


# ── generate_season_images: HEAD-check loop (requests.head monkeypatched) ─


class TestGenerateSeasonImages:
    def test_keeps_first_candidate_answering_200(self, app, monkeypatch):
        _, db = app
        season, (danny, thien_an, nobody) = _add_season(
            db, 51, ["Danny", "Thien An", "Nobody"]
        )
        live = {_url(51, "kilby"), _url(51, "thien%20an")}

        def fake_head(url, timeout):
            return SimpleNamespace(status_code=200 if url in live else 404)

        monkeypatch.setattr("app.data.requests.head", fake_head)

        assert generate_season_images(season) == 2
        assert danny.image_url == _url(51, "kilby")
        assert thien_an.image_url == _url(51, "thien%20an")
        assert nobody.image_url is None

    def test_request_failure_is_logged_and_next_candidate_tried(
        self, app, monkeypatch, caplog
    ):
        _, db = app
        season, (q,) = _add_season(db, 46, ["Q"])

        def fake_head(url, timeout):
            if url == _url(46, "q"):
                raise requests.ConnectionError("boom")
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("app.data.requests.head", fake_head)

        with caplog.at_level(logging.WARNING, logger="app.data"):
            assert generate_season_images(season) == 1

        assert q.image_url == _url(46, "%22q%22")
        assert _url(46, "q") in caplog.text
        assert "boom" in caplog.text
