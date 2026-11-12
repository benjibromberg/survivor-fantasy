"""Working out a scoring config's simulator metrics in the background.

The simulator itself never runs here: tests/conftest.py replaces the
subprocess with a fake (the `simulator` fixture), except in the one test at
the end that checks the real thing against the published analysis.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

METRICS = {
    "composite": 6.5,
    "composite_unconstrained": 6.5,
    "orderings_ok": 1.0,
    "placement_order_ok": 1.0,
    "suspense": 0.57,
}


def _lm():
    from app import league_metrics

    return league_metrics


def _config(**changes):
    from app.scoring.classic import DEFAULT_CONFIG

    return {**DEFAULT_CONFIG, **changes}


def _record(config, metrics=None, **changes):
    lm = _lm()
    record = {
        "config": lm.effective_config(config),
        **lm._standard_inputs(),
        "n_scenarios": 3150,
        "seconds": 4.5,
        "metrics": dict(metrics or METRICS),
    }
    record.update(changes)
    return record


def _seed(config, record=None):
    """Put a record in the cache, where the real run would write it."""
    lm = _lm()
    path = Path(lm._path(lm.cache_key(config), ".json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record if record is not None else _record(config)))
    return path


def _jobs(command):
    """[(config, out path)] from a --metrics-for command line."""
    pairs = []
    for i, arg in enumerate(command):
        if arg == "--metrics-for":
            assert command[i + 2] == "--metrics-out"
            pairs.append((json.loads(command[i + 1]), command[i + 3]))
    return pairs


def _write_records(command):
    """What a successful run leaves behind: one record per config."""
    lm = _lm()
    for config, out in _jobs(command):
        Path(out).write_text(json.dumps(_record(config)))
        assert out == lm._path(lm.cache_key(config), ".json")


def _lock(config):
    lm = _lm()
    return Path(lm._path(lm.cache_key(config), ".lock"))


def _age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


class TestCacheKey:
    def test_an_empty_config_is_the_default(self):
        from app.scoring.classic import DEFAULT_CONFIG

        lm = _lm()

        assert lm.cache_key({}) == lm.cache_key(dict(DEFAULT_CONFIG))

    def test_a_whole_number_saved_as_a_float_is_the_same_config(self):
        lm = _lm()

        assert lm.cache_key(_config(jury_val=3)) == lm.cache_key(_config(jury_val=3.0))

    def test_a_changed_value_is_a_different_config(self):
        lm = _lm()

        assert lm.cache_key(_config(fire_win_val=1)) != lm.cache_key(_config())

    def test_bumping_the_version_starts_again(self, monkeypatch):
        lm = _lm()
        before = lm.cache_key({})

        monkeypatch.setattr(lm, "METRICS_VERSION", lm.METRICS_VERSION + 1)

        assert lm.cache_key({}) != before

    def test_other_scenarios_are_a_different_entry(self, monkeypatch):
        import analyze_scoring

        lm = _lm()
        before = lm.cache_key({})

        monkeypatch.setattr(analyze_scoring, "DRAFTS_PER_PLAYER_COUNT", 3)

        assert lm.cache_key({}) != before


class TestReading:
    def test_nothing_cached_is_none(self):
        lm = _lm()

        assert lm.metrics_for(_config()) is None
        assert lm.status(_config()) == "missing"

    def test_a_cached_config_reads_back(self):
        lm = _lm()
        _seed(_config())

        assert lm.metrics_for(_config()) == METRICS
        assert lm.metrics_for({}) == METRICS
        assert lm.status(_config()) == "ready"

    def test_a_record_for_another_config_is_not_used(self):
        lm = _lm()
        _seed(_config(), _record(_config(fire_win_val=1)))

        assert lm.metrics_for(_config()) is None

    def test_a_record_from_other_scenarios_is_not_used(self):
        lm = _lm()
        _seed(_config(), _record(_config(), drafts_per=3))

        assert lm.metrics_for(_config()) is None

    def test_an_unreadable_record_is_none(self):
        lm = _lm()
        _seed(_config()).write_text("{not json")

        assert lm.metrics_for(_config()) is None


class TestStarting:
    def test_one_process_works_out_every_missing_config(self, simulator):
        lm = _lm()
        proposal = _config(fire_win_val=1)

        started = lm.request_metrics(proposal, {})

        ((command, kwargs),) = simulator.commands
        assert command[:2] == [sys.executable, str(ROOT / "analyze_scoring.py")]
        assert [c for c, _ in _jobs(command)] == [
            lm.effective_config(proposal),
            lm.effective_config({}),
        ]
        assert started == [lm.cache_key(proposal), lm.cache_key({})]
        assert kwargs["cwd"] == str(ROOT)
        assert kwargs["stdin"] is subprocess.DEVNULL

    def test_a_cached_config_is_not_started_again(self, simulator):
        lm = _lm()
        _seed(_config())

        lm.request_metrics(_config(), _config(fire_win_val=1))

        ((command, _),) = simulator.commands
        assert [c for c, _ in _jobs(command)] == [
            lm.effective_config(_config(fire_win_val=1))
        ]

    def test_nothing_to_do_starts_nothing(self, simulator):
        lm = _lm()
        _seed(_config())

        assert lm.request_metrics(_config(), {}) == []
        assert simulator.commands == []

    def test_the_same_config_twice_is_one_job(self, simulator):
        lm = _lm()

        lm.request_metrics({}, _config())

        ((command, _),) = simulator.commands
        assert len(_jobs(command)) == 1

    def test_it_returns_while_the_run_is_still_going(self, simulator):
        lm = _lm()
        simulator.hold()

        assert lm.request_metrics(_config()) == [lm.cache_key(_config())]
        assert lm.status(_config()) == "working"
        assert _lock(_config()).exists()

    def test_a_second_request_while_it_runs_starts_nothing(self, simulator):
        lm = _lm()
        simulator.hold()
        lm.request_metrics(_config())

        assert lm.request_metrics(_config()) == []
        assert len(simulator.commands) == 1

    def test_another_workers_lock_holds_it_off(self, simulator):
        lm = _lm()
        _lock(_config()).parent.mkdir(parents=True, exist_ok=True)
        _lock(_config()).write_text("4242-another-worker")

        assert lm.request_metrics(_config()) == []
        assert simulator.commands == []
        assert lm.status(_config()) == "working"

    def test_a_stale_lock_is_taken_over(self, simulator):
        lm = _lm()
        lock = _lock(_config())
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("4242-a-worker-that-died")
        _age(lock, lm.LOCK_STALE_SECONDS + 1)

        assert lm.request_metrics(_config()) == [lm.cache_key(_config())]
        assert len(simulator.commands) == 1


class TestFinishing:
    def test_a_finished_run_is_waited_on_and_its_lock_removed(self, simulator):
        lm = _lm()
        simulator.on_exit = _write_records

        lm.request_metrics(_config(), _config(fire_win_val=1))
        simulator.settle()

        assert simulator.processes[0].returncode == 0  # reaped, not a zombie
        assert not _lock(_config()).exists()
        assert not _lock(_config(fire_win_val=1)).exists()
        assert lm.metrics_for(_config()) == METRICS
        assert lm.status(_config(fire_win_val=1)) == "ready"

    def test_a_failed_run_is_not_retried_at_once(self, simulator):
        lm = _lm()
        simulator.code = 1

        lm.request_metrics(_config())
        simulator.settle()

        assert not _lock(_config()).exists()
        assert lm.status(_config()) == "failed"
        assert lm.request_metrics(_config()) == []
        assert len(simulator.commands) == 1

    def test_a_failed_config_is_tried_again_later(self, simulator):
        lm = _lm()
        simulator.code = 1
        lm.request_metrics(_config())
        simulator.settle()
        failed = Path(lm._path(lm.cache_key(_config()), ".failed"))

        _age(failed, lm.RETRY_AFTER_SECONDS + 1)

        assert lm.request_metrics(_config()) == [lm.cache_key(_config())]

    def test_a_run_that_writes_nothing_counts_as_failed(self, simulator):
        lm = _lm()

        lm.request_metrics(_config())
        simulator.settle()

        assert lm.status(_config()) == "failed"

    def test_a_run_that_overstays_is_killed(self, simulator, monkeypatch):
        lm = _lm()
        monkeypatch.setattr(lm, "RUN_TIMEOUT_SECONDS", 0.05)
        simulator.hold()

        lm.request_metrics(_config())
        simulator.settle()

        assert simulator.processes[0].killed
        assert not _lock(_config()).exists()
        assert lm.status(_config()) == "failed"

    def test_a_process_that_cannot_start_does_not_raise(self, simulator, monkeypatch):
        lm = _lm()

        def broken(*args, **kwargs):
            raise OSError("no such interpreter")

        monkeypatch.setattr(lm, "_popen", broken)

        assert lm.request_metrics(_config()) == []
        assert not _lock(_config()).exists()
        assert lm.status(_config()) == "failed"


# A second gunicorn worker: a separate Python process sharing the cache folder,
# starting runs through a recorder instead of the simulator.
_OTHER_WORKER = """
import json, sys
from app import league_metrics
from app.scoring.classic import DEFAULT_CONFIG
config = {**DEFAULT_CONFIG, **json.loads(sys.argv[1])}
started = []
def record(command, **kwargs):
    started.append(command)
    raise OSError("recorded, not run")
league_metrics._popen = record
league_metrics.request_metrics(config)
print(len(started))
"""


def _other_worker(changes):
    result = subprocess.run(
        [sys.executable, "-c", _OTHER_WORKER, json.dumps(changes)],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return int(result.stdout.strip().splitlines()[-1])


class TestTwoWorkers:
    def test_another_process_does_not_start_a_config_that_is_running(self, simulator):
        lm = _lm()
        simulator.hold()
        lm.request_metrics(_config())

        assert _other_worker({}) == 0
        assert _other_worker({"fire_win_val": 1}) == 1


# ── The real simulator, against the published analysis ─────────────────────


def _data_file():
    import analyze_scoring

    return analyze_scoring.SURVIVOR_DATA_FILE


@pytest.mark.skipif(
    not os.path.exists(_data_file()),
    reason="survivoR.xlsx is not in the data directory (set DATABASE_URL)",
)
def test_run_7_works_out_to_the_published_metrics(simulator, monkeypatch):
    """The whole path: lock, subprocess, simulator, record, read-back.

    Run 7's config is the newest export's top10[0]; the export rounds every
    metric to three places.
    """
    from app.scoring.history import SCORING_VERSIONS

    lm = _lm()
    # The child finds survivoR.xlsx through DATABASE_URL, as production does;
    # point it at the file this test checked, so it never downloads one.
    folder = os.path.dirname(os.path.abspath(_data_file()))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{folder}/unused.db")
    monkeypatch.setattr(lm, "_popen", subprocess.Popen)
    run7 = next(v for v in SCORING_VERSIONS if v["key"] == "run-7")["config"]
    published = json.loads((ROOT / "app/static/scoring_analysis.json").read_text())

    assert lm.request_metrics(run7) == [lm.cache_key(run7)]
    simulator.settle(timeout=180)  # the thread that waits for the process

    metrics = lm.metrics_for(run7)
    assert metrics is not None, Path(lm._path(lm.cache_key(run7), ".log")).read_text()
    for key, value in published["top10"][0]["metrics"].items():
        assert metrics[key] == pytest.approx(value, abs=5e-4 + 1e-9), key
