"""Guards every test shares."""

import subprocess
import threading

import pytest


class _FakeProcess:
    def __init__(self, runs, command):
        self.runs = runs
        self.command = command
        self.pid = 0
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if not self.runs.gate.wait(timeout):
            raise subprocess.TimeoutExpired(self.command, timeout)
        if self.returncode is None:
            if self.runs.on_exit is not None:
                self.runs.on_exit(self.command)
            self.returncode = self.runs.code
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.runs.gate.set()


class SimulatorRuns:
    """Stands in for the simulator subprocess app/league_metrics.py starts.

    Each start is recorded in `commands` and returns a fake process that
    exits with `code` as soon as it is waited on, after calling
    `on_exit(command)` if set (to write records the way the real run would).
    `hold()` keeps every run going until `release()`.
    """

    def __init__(self):
        self.commands = []
        self.processes = []
        self.code = 0
        self.on_exit = None
        self.gate = threading.Event()
        self.gate.set()

    def popen(self, command, **kwargs):
        self.commands.append((command, kwargs))
        process = _FakeProcess(self, command)
        self.processes.append(process)
        return process

    def hold(self):
        self.gate.clear()

    def release(self):
        self.gate.set()

    def settle(self, timeout=5):
        """Wait for every waiting thread to finish with its run."""
        for thread in threading.enumerate():
            if thread.name == "league-metrics":
                thread.join(timeout=timeout)


@pytest.fixture(autouse=True)
def simulator(tmp_path_factory, monkeypatch):
    """No test runs the real simulator, and its cache stays out of the repo.

    Viewing the Scoring changes or analysis page, or opening a proposal,
    starts a background run of analyze_scoring.py (app/league_metrics.py).
    Here every start is a SimulatorRuns fake, and the cache is a fresh
    temporary folder. The one test that runs the real thing restores
    subprocess.Popen itself.
    """
    from app import league_metrics

    runs = SimulatorRuns()
    monkeypatch.setattr(league_metrics, "_popen", runs.popen)
    monkeypatch.setenv(
        "CONFIG_METRICS_DIR", str(tmp_path_factory.mktemp("config-metrics"))
    )
    yield runs
    runs.release()
    runs.settle()
