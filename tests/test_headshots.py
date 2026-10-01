"""Tests for castaway headshots: candidate URLs, mirroring, and serving."""

import importlib
import io
import logging
import os
import re
import sys

import pytest
import requests
from PIL import Image

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


# ── generate_season_images: download, resize, store ───────────────────────


def _jpeg_bytes(size=(440, 440), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, status_code=200, body=b""):
        self.status_code = status_code
        self._body = body

    def iter_content(self, chunk_size=1):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def remote(monkeypatch):
    """Fake the remote site: {url: bytes} served via app.data.requests.get."""
    monkeypatch.setattr("app.data.HEADSHOT_REQUEST_DELAY", 0)
    served = {}
    calls = []

    def fake_get(url, timeout=None, stream=False):
        calls.append(url)
        if url in served:
            body = served[url]
            if isinstance(body, Exception):
                raise body
            return _FakeResponse(200, body)
        return _FakeResponse(404)

    monkeypatch.setattr("app.data.requests.get", fake_get)
    served["calls"] = calls
    return served


class TestGenerateSeasonImages:
    def test_downloads_resizes_to_webp_and_sets_local_url(self, app, remote):
        from app.data import headshots_dir

        _, db = app
        season, (danny, thien_an, nobody) = _add_season(
            db, 51, ["Danny", "Thien An", "Nobody"]
        )
        remote[_url(51, "kilby")] = _jpeg_bytes()
        remote[_url(51, "thien%20an")] = _jpeg_bytes(color=(10, 200, 10))

        assert generate_season_images(season) == 2

        for surv in (danny, thien_an):
            assert re.fullmatch(r"/headshots/51/[0-9a-f]{16}\.webp", surv.image_url)
            path = os.path.join(headshots_dir(), *surv.image_url.split("/")[2:])
            with Image.open(path) as img:
                assert img.format == "WEBP"
                assert max(img.size) == 160
        assert danny.image_url != thien_an.image_url
        assert nobody.image_url is None

    def test_failed_fetch_leaves_image_url_none(self, app, remote, caplog):
        _, db = app
        season, (q,) = _add_season(db, 46, ["Q"])
        remote[_url(46, "q")] = requests.ConnectionError("boom")

        with caplog.at_level(logging.WARNING, logger="app.data"):
            assert generate_season_images(season) == 0

        assert q.image_url is None
        assert "boom" in caplog.text

    def test_request_failure_falls_through_to_next_candidate(self, app, remote):
        _, db = app
        season, (q,) = _add_season(db, 46, ["Q"])
        remote[_url(46, "q")] = requests.ConnectionError("boom")
        remote[_url(46, "%22q%22")] = _jpeg_bytes()

        assert generate_season_images(season) == 1
        assert q.image_url.startswith("/headshots/46/")

    def test_undecodable_image_is_skipped_not_fatal(self, app, remote, caplog):
        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = b"<html>not an image</html>"

        with caplog.at_level(logging.WARNING, logger="app.data"):
            assert generate_season_images(season) == 0

        assert rizo.image_url is None
        assert "unusable" in caplog.text

    def test_rerun_does_not_redownload(self, app, remote):
        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = _jpeg_bytes()

        generate_season_images(season)
        first_url = rizo.image_url
        calls_after_first = len(remote["calls"])

        assert generate_season_images(season) == 1
        assert len(remote["calls"]) == calls_after_first
        assert rizo.image_url == first_url

    def test_force_refetches_and_keeps_same_hashed_name(self, app, remote):
        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = _jpeg_bytes()

        generate_season_images(season)
        first_url = rizo.image_url
        calls_after_first = len(remote["calls"])

        generate_season_images(season, force=True)
        assert len(remote["calls"]) > calls_after_first
        assert rizo.image_url == first_url

    def test_missing_local_file_is_refetched(self, app, remote):
        from app.data import _local_headshot_path

        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = _jpeg_bytes()

        generate_season_images(season)
        os.remove(_local_headshot_path(rizo.image_url))
        generate_season_images(season)

        assert os.path.exists(_local_headshot_path(rizo.image_url))

    def test_remote_image_url_is_migrated_to_local(self, app, remote):
        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        rizo.image_url = _url(50, "rizo")
        remote[_url(50, "rizo")] = _jpeg_bytes()

        generate_season_images(season)

        assert rizo.image_url.startswith("/headshots/50/")

    def test_oversized_download_is_rejected(self, app, remote, monkeypatch):
        _, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = _jpeg_bytes()
        monkeypatch.setattr("app.data.HEADSHOT_MAX_BYTES", 10)

        assert generate_season_images(season) == 0
        assert rizo.image_url is None


# ── Serving route ─────────────────────────────────────────────────────────


class TestHeadshotRoute:
    def test_serves_file_with_immutable_cache_headers(self, app, remote):
        application, db = app
        season, (rizo,) = _add_season(db, 50, ["Rizo"])
        remote[_url(50, "rizo")] = _jpeg_bytes()
        generate_season_images(season)

        resp = application.test_client().get(rizo.image_url)

        assert resp.status_code == 200
        assert resp.mimetype == "image/webp"
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.data[:4] == b"RIFF"

    def test_missing_file_is_404(self, app):
        application, _ = app
        resp = application.test_client().get("/headshots/50/nope.webp")
        assert resp.status_code == 404

    def test_path_traversal_is_rejected(self, app, tmp_path):
        application, _ = app
        (tmp_path / "secret.txt").write_text("x")
        resp = application.test_client().get("/headshots/50/..%2F..%2Fsecret.txt")
        assert resp.status_code == 404


# ── seed.py: per-season wrapper ───────────────────────────────────────────


class TestSeedGenerateImageUrls:
    def test_prints_matched_over_total_per_season(self, app, monkeypatch, capsys):
        _, db = app
        import seed

        _add_season(db, 50, ["Rizo", "Savannah"])
        _add_season(db, 51, ["Danny", "Thien An", "Nobody"])
        found = {50: 2, 51: 1}
        monkeypatch.setattr(
            "app.data.generate_season_images",
            lambda season, force=False: found[season.number],
        )

        seed.generate_image_urls()

        out = capsys.readouterr().out
        assert "  Season 50: 2/2 images" in out
        assert "  Season 51: 1/3 images" in out
