"""Static file URLs carry a content version, so a changed file gets a new URL."""

import hashlib
import importlib
import re
import sys

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


def _version_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


class TestStaticVersion:
    def test_pages_link_the_stylesheet_with_its_content_version(self, app):
        expected = _version_of(f"{app.static_folder}/style.css")

        page = app.test_client().get("/league-settings").data.decode()

        assert f"/static/style.css?v={expected}" in page

    def test_version_follows_the_content_not_the_file_date(self, app, tmp_path):
        """A rebuilt image rewrites every mtime; an unchanged file must keep its URL."""
        from flask import url_for

        static = tmp_path / "static"
        static.mkdir()
        (static / "a.css").write_text("body { color: red; }")
        app.static_folder = str(static)

        with app.test_request_context():
            first = url_for("static", filename="a.css")
            # Same bytes written again: a new mtime, the same content
            (static / "a.css").write_text("body { color: red; }")
            assert url_for("static", filename="a.css") == first

    def test_changed_content_gets_a_new_url_after_a_restart(self, app, tmp_path):
        from flask import url_for

        from app import static_versions

        static = tmp_path / "static"
        static.mkdir()
        (static / "a.css").write_text("body { color: red; }")
        app.static_folder = str(static)
        with app.test_request_context():
            before = url_for("static", filename="a.css")

        (static / "a.css").write_text("body { color: blue; }")
        static_versions.clear()  # what a container restart does
        with app.test_request_context():
            after = url_for("static", filename="a.css")

        assert before != after
        assert re.search(r"\?v=[0-9a-f]{12}$", after)

    def test_every_static_file_is_versioned(self, app):
        from flask import url_for

        expected = _version_of(f"{app.static_folder}/scoring_analysis.json")
        with app.test_request_context():
            url = url_for("static", filename="scoring_analysis.json")

        assert url.endswith(f"?v={expected}")

    def test_missing_file_gets_no_version(self, app):
        from flask import url_for

        with app.test_request_context():
            url = url_for("static", filename="does-not-exist.css")

        assert url == "/static/does-not-exist.css"

    def test_paths_outside_the_static_folder_are_never_read(self, app):
        from flask import url_for

        with app.test_request_context():
            url = url_for("static", filename="../config.py")

        assert "v=" not in url

    def test_versioned_url_still_serves_the_file(self, app):
        expected = _version_of(f"{app.static_folder}/style.css")

        resp = app.test_client().get(f"/static/style.css?v={expected}")

        assert resp.status_code == 200
        assert resp.data
