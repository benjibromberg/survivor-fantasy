"""The startup schema sync must survive several workers starting at once.

Gunicorn runs more than one worker and each calls create_app(), so each runs
create_all() and the column and index sync against the same database at the
same moment. These tests start real processes, because the race is between
processes and cannot be reproduced inside one.
"""

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKERS = 6

# One worker: import the app, report ready, wait for the starting gun, start.
WORKER = """
import os, sys, time
ready, go = sys.argv[1], sys.argv[2]
import app.scheduler
app.scheduler.init_scheduler = lambda _app: None
from app import create_app
open(ready, "w").close()
while not os.path.exists(go):
    time.sleep(0.001)
create_app()
"""


def _env(db_path):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["SECRET_KEY"] = "test-secret"
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _start_workers(db_path, tmp_path, count):
    """Start `count` workers at the same instant; return (returncode, stderr) each."""
    go = tmp_path / "go"
    procs = []
    for i in range(count):
        ready = tmp_path / f"ready-{i}"
        proc = subprocess.Popen(
            [sys.executable, "-c", WORKER, str(ready), str(go)],
            cwd=REPO_ROOT,
            env=_env(db_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        procs.append((proc, ready))

    deadline = time.monotonic() + 120
    while not all(ready.exists() for _proc, ready in procs):
        dead = [proc for proc, _ready in procs if proc.poll() is not None]
        assert not dead, f"worker died before starting: {dead[0].stderr.read()}"
        assert time.monotonic() < deadline, "workers did not become ready"
        time.sleep(0.01)

    go.touch()
    results = []
    for proc, _ready in procs:
        _out, err = proc.communicate(timeout=120)
        results.append((proc.returncode, err))
    return results


def _schema(db_path):
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute("select name from sqlite_master where type='table'")
        }
        indexes = {
            r[0]
            for r in conn.execute("select name from sqlite_master where type='index'")
        }
        user_cols = {r[1] for r in conn.execute("pragma table_info(user)")}
        season_cols = {r[1] for r in conn.execute("pragma table_info(season)")}
    finally:
        conn.close()
    return tables, indexes, user_cols, season_cols


def _assert_all_started(results):
    failures = [err for code, err in results if code != 0]
    assert not failures, (
        f"{len(failures)} of {len(results)} workers failed to start. "
        f"First failure:\n{failures[0][-2000:]}"
    )


def test_workers_starting_together_on_a_new_database(tmp_path):
    """First deploy: every worker runs create_all() at once."""
    db_path = tmp_path / "new.db"

    results = _start_workers(db_path, tmp_path, WORKERS)

    _assert_all_started(results)
    tables, indexes, user_cols, _season_cols = _schema(db_path)
    assert {"user", "season", "survivor", "pick", "team_name"} <= tables
    assert "ix_user_email" in indexes
    assert "email" in user_cols


def test_workers_starting_together_on_an_older_database(tmp_path):
    """Upgrade: every worker adds the missing columns, index and table at once."""
    db_path = tmp_path / "old.db"
    setup = tmp_path / "setup"
    setup.mkdir()
    _assert_all_started(_start_workers(db_path, setup, 1))

    # Put the schema back to what a deployment had before player login,
    # team names and the Episode 2 time existed
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DROP INDEX ix_user_email;
            ALTER TABLE user DROP COLUMN email;
            ALTER TABLE season DROP COLUMN episode2_starts_at;
            DROP TABLE team_name;
            """
        )
    finally:
        conn.close()
    tables, indexes, user_cols, season_cols = _schema(db_path)
    assert "team_name" not in tables and "ix_user_email" not in indexes
    assert "email" not in user_cols and "episode2_starts_at" not in season_cols

    results = _start_workers(db_path, tmp_path, WORKERS)

    _assert_all_started(results)
    tables, indexes, user_cols, season_cols = _schema(db_path)
    assert "team_name" in tables
    assert "ix_user_email" in indexes
    assert "email" in user_cols
    assert "episode2_starts_at" in season_cols
