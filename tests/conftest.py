"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def refreshes_finish_before_the_response(monkeypatch):
    """Wait for each admin refresh's thread before the request returns.

    The admin's Refresh button and season creation start the survivoR
    refresh in a thread of its own (app/refresh_jobs.py) and answer at once.
    A test that posts one and then checks what the refresh did would race
    it, and a thread left running would touch a database the test has
    already removed. The thread is still a real one, with its own app
    context and database session; this only joins it before returning.

    Returns the real starter, for a test that needs the thread to outlive
    the request (tests/test_admin_refresh.py).
    """
    from app import refresh_jobs

    real_start = refresh_jobs._start_thread

    def start_and_wait(target):
        thread = real_start(target)
        thread.join(timeout=60)
        assert not thread.is_alive(), "the refresh thread did not finish"
        return thread

    monkeypatch.setattr(refresh_jobs, "_start_thread", start_and_wait)
    return real_start
