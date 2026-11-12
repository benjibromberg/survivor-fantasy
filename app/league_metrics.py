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
- compare(columns) is what the Scoring changes and scoring analysis pages
  show: each metric in METRICS for each config, with the better one marked.

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
from dataclasses import dataclass

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


# ── What the pages show ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Metric:
    """One row of the comparison: a key in evaluate_config's result.

    `better` is "higher", "lower", "band" (best inside `healthy`) or None
    (shown for information, never marked). `goodness` maps a value to how
    good it is, the way the analysis's composite score counts it; the
    highest is marked better.
    """

    key: str
    group: str
    label: str
    meaning: str
    fmt: str
    better: str | None = "higher"
    healthy: tuple | None = None
    goodness: object = None


def _same(v):
    return v


def _less(v):
    return -v


OVERALL = "Overall"
SUSPENSE = "Suspense and comebacks"
CLOSENESS = "How close it stays"
POINTS = "Where the points come from"

# The order the pages list them. Meanings and directions follow
# evaluate_config and the composite's weights in analyze_scoring.py.
METRICS = (
    Metric(
        "composite_unconstrained",
        OVERALL,
        "Overall simulation score",
        "The analysis's one number for a config: the metrics in the three "
        "groups below, weighted and added up, less a penalty for results that "
        "swing from draft to draft. Shown before any penalty for breaking the "
        "league's rules.",
        "num",
        goodness=_same,
    ),
    Metric(
        "orderings_ok",
        OVERALL,
        "Keeps the league's point-order rules",
        "Final Tribal over jury, individual over tribal immunity, idols over "
        "advantages: the rules listed on Scoring changes.",
        "share",
        goodness=_same,
    ),
    Metric(
        "placement_order_ok",
        OVERALL,
        "Finalists always score at least as much as 4th place",
        "Counting only points for how far a castaway went (tribals, jury, "
        "merge, placement, Final Tribal and fire), whoever wins fire.",
        "share",
        goodness=_same,
    ),
    Metric(
        "third_beats_second",
        OVERALL,
        "3rd place can outscore 2nd",
        "On points for how far a castaway went, in some fire outcome. Not a "
        "rule: a 3rd-place finalist lifted by winning fire is allowed.",
        "share",
        better=None,
    ),
    Metric(
        "suspense",
        SUSPENSE,
        "Share of the season the eventual winner isn't in 1st",
        "Checked after every boot. Higher means the result stays open longer.",
        "pct",
        goodness=_same,
    ),
    Metric(
        "comeback_rate",
        SUSPENSE,
        "The halfway leader goes on to lose",
        "How often the player leading at the halfway boot does not win.",
        "pct",
        goodness=_same,
    ),
    Metric(
        "rank_volatility",
        SUSPENSE,
        "How much the standings move from boot to boot",
        "Average places each player moves up or down after a boot.",
        "num",
        goodness=analyze_scoring._volatility_score,
    ),
    Metric(
        "blowout_rate",
        CLOSENESS,
        "Blowouts: 1st scores more than twice last",
        "Share of simulated drafts that end this way.",
        "pct",
        better="lower",
        goodness=_less,
    ),
    Metric(
        "late_game_gap",
        CLOSENESS,
        "Gap between 1st and 2nd at 75% of the season",
        "As a share of the leader's score.",
        "pct",
        better="lower",
        goodness=_less,
    ),
    Metric(
        "final_spread",
        CLOSENESS,
        "Gap between 1st and last at the end",
        "As a share of the winner's score.",
        "pct",
        better="lower",
        goodness=_less,
    ),
    Metric(
        "midpoint_competitive_pct",
        CLOSENESS,
        "Players still in it at halfway",
        "Share of players within 20% of the leader's score at the halfway boot.",
        "pct",
        goodness=_same,
    ),
    Metric(
        "finale_competitive_pct",
        CLOSENESS,
        "Players still in it at the finale",
        "Share of players within 20% of the leader's score when the finale starts.",
        "pct",
        goodness=_same,
    ),
    Metric(
        "draft_skill_correlation",
        POINTS,
        "How much a good draft predicts the final rank",
        "Rank correlation between how long a player's draft picks lasted and "
        "where the player finished: 1 means the draft settles the order, 0 "
        "means no link. The analysis counts higher as better.",
        "num",
        goodness=_same,
    ),
    Metric(
        "early_loser_avg_rank",
        POINTS,
        "Where players who lose a pick in the first three boots finish",
        "Average finishing place as a share of the way down the standings: "
        "1st of 4 is 25%, last is 100%. Lower means an early loss hurts less.",
        "pct",
        better="lower",
        goodness=_less,
    ),
    Metric(
        "longevity_share",
        POINTS,
        "Points from surviving tribals",
        "Share of all points that come from tribals survived. The analysis "
        "gives full credit anywhere from 50% to 80%, less outside it.",
        "pct",
        better="band",
        healthy=(0.5, 0.8),
        goodness=analyze_scoring._longevity_score,
    ),
    Metric(
        "non_draft_pct",
        POINTS,
        "Points from wildcards and replacements",
        "Share of all points from picks other than the draft. Below 10% the "
        "extra picks barely matter; past 35% the analysis gives no more credit.",
        "pct",
        better="band",
        healthy=(0.10, 0.35),
        goodness=analyze_scoring._non_draft_score,
    ),
)


def _pct(v):
    return f"{v * 100:.0f}%"


def format_value(fmt, value):
    """A metric value as the pages write it."""
    if value is None:
        return "n/a"
    if fmt == "pct":
        return _pct(value)
    if fmt == "share":
        # A share of the simulated seasons; all or none reads as yes or no
        if value >= 0.995:
            return "yes"
        if value <= 0.005:
            return "no"
        return f"{_pct(value)} of seasons"
    return f"{value:.2f}"


def direction_text(metric):
    if metric.better == "higher":
        return "Higher is better."
    if metric.better == "lower":
        return "Lower is better."
    if metric.better == "band":
        low, high = metric.healthy
        return f"Healthy from {_pct(low)} to {_pct(high)}."
    return "For information."


def _better_flags(metric, values):
    """Which values to mark better: the best, unless every value ties.

    Compared at the precision shown, so 57% beside 57% is a tie.
    """
    if metric.goodness is None or len(values) < 2 or None in values:
        return [False] * len(values)
    scores = [metric.goodness(round(v, 2)) for v in values]
    best = max(scores)
    if all(abs(s - best) < 1e-9 for s in scores):
        return [False] * len(values)
    return [abs(s - best) < 1e-9 for s in scores]


def _merge_columns(columns):
    """Drop a column whose config another already shows, noting it on that one."""
    merged = []
    for short, label, config in columns:
        effective = effective_config(config)
        same = next((c for c in merged if c["config"] == effective), None)
        if same is not None:
            same["also"].append(label)
            continue
        merged.append({"short": short, "label": label, "config": effective, "also": []})
    return merged


def _seasons_text(seasons):
    if seasons == list(range(seasons[0], seasons[-1] + 1)):
        return f"Seasons {seasons[0]}-{seasons[-1]}"
    return "Seasons " + ", ".join(str(n) for n in seasons)


def compare(columns):
    """The comparison both pages render, starting the work for any config missing.

    `columns` is [(short label, label, config)] in display order. Returns
    {"columns", "state", "groups", "basis", "retry_minutes"}: state is
    "ready" when every column has metrics (then groups holds the rows),
    "working" while a run is going, "failed" if one failed recently. Never
    runs the simulator.
    """
    shown = _merge_columns(columns)
    result = {
        "columns": shown,
        "groups": [],
        "basis": None,
        "retry_minutes": RETRY_AFTER_SECONDS // 60,
    }
    records = []
    for column in shown:
        key = cache_key(column["config"])
        records.append(_read_record(key, column["config"]))
    missing = [c["config"] for c, r in zip(shown, records, strict=True) if r is None]
    if missing:
        request_metrics(*missing)
        states = [status(config) for config in missing]
        result["state"] = "failed" if "failed" in states else "working"
        return result

    groups = []
    for metric in METRICS:
        values = [r["metrics"].get(metric.key) for r in records]
        flags = _better_flags(metric, values)
        row = {
            "label": metric.label,
            "meaning": metric.meaning,
            "direction": direction_text(metric),
            "cells": [
                {
                    "column": column["short"],
                    "text": format_value(metric.fmt, value),
                    "better": flag,
                }
                for column, value, flag in zip(shown, values, flags, strict=True)
            ],
        }
        if not groups or groups[-1]["label"] != metric.group:
            groups.append({"label": metric.group, "rows": []})
        groups[-1]["rows"].append(row)
    first = records[0]
    players = first.get("player_counts") or analyze_scoring.PLAYER_COUNTS
    result["basis"] = (
        f"Each worked out over the same {first.get('n_scenarios', 0):,} "
        f"simulated drafts of {_seasons_text(first['seasons'])}, with "
        f"{min(players)} to {max(players)} players, that the scoring analysis "
        "uses for every config it tests."
    )
    result["state"], result["groups"] = "ready", groups
    return result
