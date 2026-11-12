"""How the league would play under a scoring config: the simulator's metrics.

The scoring analysis (analyze_scoring.py) scores every config it tests on how
a season plays: suspense, comebacks, blowouts, how much a good draft decides
the result. Its export keeps those numbers only for its top configs, so a
season's own scoring or a hand-adjusted proposal had none. This module works
them out for any config the site shows, in the background.

- metrics_for(config) reads a config's metrics from the cache, or None.
- request_metrics(*configs) starts working out the ones not cached and not
  already running, in one subprocess (analyze_scoring.py --metrics-for), and
  returns at once. Nothing here runs the simulator inside a request.

The cache is one JSON file per config in <data dir>/config-metrics, named by
cache_key(). Starting a run takes a lock file per config, created with
O_EXCL, so two gunicorn workers never start the same config twice. A daemon
thread waits on the process, so it never lingers as a zombie, and then
releases the locks.
"""

import fcntl
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager, suppress

import analyze_scoring

from .data import _data_dir

# Every DEFAULT_CONFIG key, from the config where it has one: what scoring
# uses. A season stores `{}` and scores under DEFAULT_CONFIG, so `{}` and
# DEFAULT_CONFIG are one config here and share one cache entry. The same
# function the scoring versions log compares configs with.
from .scoring.history import _effective as effective_config

logger = logging.getLogger(__name__)

# Part of every cache key. Bump it when evaluate_config's metrics change, so
# every cached record is worked out again rather than shown stale.
METRICS_VERSION = 1

# A run takes about ten seconds (two configs, about fifteen). A lock older
# than this belongs to a run that died without removing it.
LOCK_STALE_SECONDS = 5 * 60
# A run still going after this is killed, so a run never outlives its lock.
RUN_TIMEOUT_SECONDS = 4 * 60
# After a failed run, the same config waits this long before another try, so
# a run that cannot succeed is not restarted by every page view.
RETRY_AFTER_SECONDS = 15 * 60

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "analyze_scoring.py")

# The one place a process is started. Tests replace it (tests/conftest.py).
_popen = subprocess.Popen


def _standard_inputs():
    """The scenarios every record is worked out on: the sweep's own."""
    return {
        "seasons": list(analyze_scoring.DEFAULT_SEASONS),
        "drafts_per": analyze_scoring.DRAFTS_PER_PLAYER_COUNT,
        "player_counts": list(analyze_scoring.PLAYER_COUNTS),
    }


def _hashable(value):
    # 1 and 1.0 score the same; the admin form saves floats
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return value


def cache_key(config):
    """The cache file name for a config: its effective values, the scenarios
    and METRICS_VERSION, hashed."""
    spec = {
        "config": {k: _hashable(v) for k, v in effective_config(config).items()},
        "version": METRICS_VERSION,
        **_standard_inputs(),
    }
    raw = json.dumps(spec, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _metrics_dir():
    """Resolved per call so it follows DATABASE_URL, like the headshots folder."""
    return os.environ.get("CONFIG_METRICS_DIR") or os.path.join(
        _data_dir(), "config-metrics"
    )


def _path(key, suffix):
    return os.path.join(_metrics_dir(), key + suffix)


def _read_record(key, effective):
    """The cached record for `key`, or None if missing, unreadable or not this config."""
    path = _path(key, ".json")
    try:
        with open(path) as f:
            record = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Unreadable scoring metrics record %s", path, exc_info=True)
        return None
    inputs = _standard_inputs()
    if (
        not isinstance(record, dict)
        or record.get("config") != effective
        or record.get("seasons") != inputs["seasons"]
        or record.get("drafts_per") != inputs["drafts_per"]
        or not isinstance(record.get("metrics"), dict)
    ):
        logger.warning("Scoring metrics record %s is not for its config", path)
        return None
    return record


def metrics_for(config):
    """A config's metrics from the cache (evaluate_config's dict), or None."""
    effective = effective_config(config)
    record = _read_record(cache_key(effective), effective)
    return record["metrics"] if record else None


def _age(path):
    """Seconds since `path` was written, or None if it does not exist."""
    try:
        return time.time() - os.path.getmtime(path)
    except FileNotFoundError:
        return None


def _running(key):
    age = _age(_path(key, ".lock"))
    return age is not None and age < LOCK_STALE_SECONDS


def _failed_recently(key):
    age = _age(_path(key, ".failed"))
    return age is not None and age < RETRY_AFTER_SECONDS


def status(config):
    """'ready', 'working' (a run holds its lock), 'failed' (recently), or 'missing'."""
    effective = effective_config(config)
    key = cache_key(effective)
    if _read_record(key, effective) is not None:
        return "ready"
    if _running(key):
        return "working"
    if _failed_recently(key):
        return "failed"
    return "missing"


@contextmanager
def _guard(folder):
    """Serialise lock checks across both web workers and their threads.

    O_EXCL alone keeps two starters off one free lock. The guard also covers
    taking over a stale lock, where removing it and creating a new one are two
    steps another worker could otherwise interleave with. Held for a few file
    operations, never for a run.
    """
    with open(os.path.join(folder, ".guard"), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _take_lock(key):
    """Create the config's lock file and return its token, or None if it is held.

    Call inside _guard. A lock older than LOCK_STALE_SECONDS is taken over.
    """
    path = _path(key, ".lock")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        if _running(key):
            return None
        logger.warning("Taking over a stale scoring metrics lock %s", path)
        with suppress(FileNotFoundError):
            os.remove(path)
        fd = os.open(path, flags, 0o644)
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def _release(key, token):
    """Remove the config's lock if it is still the one this run took."""
    path = _path(key, ".lock")
    with _guard(_metrics_dir()):
        try:
            with open(path) as f:
                held = f.read().strip()
        except FileNotFoundError:
            return
        if held == token:
            os.remove(path)


def _mark_failed(key, reason):
    with open(_path(key, ".failed"), "w") as f:
        f.write(f"{reason}\n")


def _clear_failed(key):
    with suppress(FileNotFoundError):
        os.remove(_path(key, ".failed"))


def _reap(proc, locks):
    """Wait for a run, so it never lingers as a zombie, then release its locks.

    Runs on a daemon thread. A config whose record did not appear is marked
    failed, which holds off another try for RETRY_AFTER_SECONDS.
    """
    try:
        try:
            code = proc.wait(timeout=RUN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = "timed out"
        for key in locks:
            if code == 0 and os.path.exists(_path(key, ".json")):
                _clear_failed(key)
            else:
                logger.error(
                    "Working out scoring metrics %s failed (%s); see %s",
                    key,
                    code,
                    _path(next(iter(locks)), ".log"),
                )
                _mark_failed(key, code)
    except Exception:
        logger.exception("Waiting on a scoring metrics run failed")
    finally:
        for key, token in locks.items():
            try:
                _release(key, token)
            except OSError:
                logger.exception("Could not release scoring metrics lock %s", key)


def _start(configs):
    folder = _metrics_dir()
    os.makedirs(folder, exist_ok=True)
    jobs, locks = {}, {}
    with _guard(folder):
        for config in configs:
            effective = effective_config(config)
            key = cache_key(effective)
            if (
                key in jobs
                or _read_record(key, effective) is not None
                or _failed_recently(key)
            ):
                continue
            token = _take_lock(key)
            if token is not None:
                jobs[key], locks[key] = effective, token
    if not jobs:
        return []

    command = [sys.executable, _SCRIPT]
    for key, effective in jobs.items():
        command += [
            "--metrics-for",
            json.dumps(effective, sort_keys=True),
            "--metrics-out",
            _path(key, ".json"),
        ]
    try:
        # The child keeps its own copy of the log's descriptor
        with open(_path(next(iter(jobs)), ".log"), "w") as log:
            proc = _popen(
                command,
                cwd=_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    except OSError:
        for key, token in locks.items():
            _mark_failed(key, "could not start")
            _release(key, token)
        raise
    threading.Thread(
        target=_reap, args=(proc, locks), name="league-metrics", daemon=True
    ).start()
    logger.info("Working out scoring metrics for %s", ", ".join(jobs))
    return list(jobs)


def request_metrics(*configs):
    """Start working out each config's metrics, unless cached, running or failed.

    Every config that needs it goes into one subprocess, which loads the data
    once and writes each record as it finishes. Returns at once with the keys
    it started, and never raises: a page view or a proposal must not fail
    because the background work could not start.
    """
    try:
        return _start(configs)
    except OSError:
        logger.exception("Could not start working out scoring metrics")
        return []
