"""
API key access and the run trigger behind POST /api/run (web/api_access.py).

The trigger merges calls instead of dropping them: a call inside the cooldown,
or while a run is in progress, becomes one follow-up run. These tests drive it
with a fake clock and a fake runner, so every timing case is exact.
"""

import threading
import time

import pytest

from web.api_access import (
    API_KEY_ROUTES, DEFAULT_COOLDOWN_SECONDS, QUEUE_GIVE_UP_SECONDS, RunTrigger,
    api_key_matches, clamp_cooldown, generate_api_key, is_api_key_route,
)


# ---------------------------------------------------------------------------
# Key and route helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("POST", "/api/run"), ("GET", "/api/status"), ("POST", "/api/stop"),
    ("post", "/api/run/"),
])
def test_key_routes(method, path):
    assert is_api_key_route(method, path)


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/run"),            # wrong method
    ("POST", "/api/status"),
    ("POST", "/operations/run"),    # the Web UI route stays session-only
    ("POST", "/maintenance/run"),
    ("GET", "/settings/security"),
    ("POST", "/settings/security/api-key"),
    ("GET", "/api/runs"),
])
def test_everything_else_is_not_a_key_route(method, path):
    assert not is_api_key_route(method, path)


def test_only_run_status_stop_are_key_routes():
    assert API_KEY_ROUTES == {("POST", "/api/run"), ("GET", "/api/status"), ("POST", "/api/stop")}


def test_key_matching():
    key = generate_api_key()
    assert len(key) >= 40
    assert api_key_matches(key, key)
    assert not api_key_matches(key + "x", key)
    assert not api_key_matches("", key)
    assert not api_key_matches(None, key)
    # No key configured: nothing matches, including an empty header
    assert not api_key_matches("", "")
    assert not api_key_matches("anything", "")
    assert generate_api_key() != generate_api_key()


@pytest.mark.parametrize("raw, expected", [
    (30, 30), ("45", 45), ("12.7", 12), (0, 0), (-5, 0), (99999, 3600),
    (None, DEFAULT_COOLDOWN_SECONDS), ("abc", DEFAULT_COOLDOWN_SECONDS),
])
def test_clamp_cooldown(raw, expected):
    assert clamp_cooldown(raw) == expected


# ---------------------------------------------------------------------------
# RunTrigger
# ---------------------------------------------------------------------------

class FakeRunner:
    def __init__(self):
        self.busy = False
        self.blocked = None
        self.starts = []
        self.refuse_next = False

    def start(self, dry_run, verbose):
        if self.refuse_next:
            self.refuse_next = False
            return False
        self.starts.append((dry_run, verbose))
        return True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _trigger(runner, clock, cooldown=30):
    return RunTrigger(
        start_run=runner.start,
        is_busy=lambda: runner.busy,
        blocked_reason=lambda: runner.blocked,
        cooldown_seconds=lambda: cooldown,
        clock=clock,
        wall_clock=lambda: 50_000.0 + clock.now,
        start_worker=False,
    )


def test_first_call_starts_immediately():
    runner, clock = FakeRunner(), Clock()
    result = _trigger(runner, clock).request()
    assert result.status == "started"
    assert runner.starts == [(False, False)]


def test_call_inside_cooldown_is_queued_then_runs_once_ready():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request()
    clock.now += 10
    result = trig.request()
    assert result.status == "queued"
    assert "20s" in result.message
    assert result.run_at == pytest.approx(50_000.0 + 1030.0)
    assert len(runner.starts) == 1

    clock.now += 19
    assert trig.run_pending_once() == "waiting"
    clock.now += 1
    assert trig.run_pending_once() == "started"
    assert len(runner.starts) == 2
    assert trig.pending_info() is None


def test_burst_of_calls_merges_into_one_follow_up():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request()
    for _ in range(5):
        clock.now += 2
        assert trig.request().status == "queued"
    clock.now += 30
    assert trig.run_pending_once() == "started"
    assert len(runner.starts) == 2          # one immediate + one merged follow-up
    assert trig.run_pending_once() is None  # nothing left queued


def test_call_during_a_run_queues_until_it_finishes():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    runner.busy = True                      # e.g. the scheduled run is going
    result = trig.request()
    assert result.status == "queued"
    assert "current run finishes" in result.message
    assert runner.starts == []

    clock.now += 600
    assert trig.run_pending_once() == "waiting"   # still busy
    runner.busy = False
    assert trig.run_pending_once() == "started"   # no earlier API run, so no cooldown to wait for


def test_blocked_by_maintenance_or_cli_is_not_queued():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock)
    runner.blocked = "A maintenance action is in progress"
    result = trig.request()
    assert result.status == "blocked"
    assert result.message == "A maintenance action is in progress"
    assert trig.pending_info() is None
    assert runner.starts == []


def test_queued_run_waits_out_maintenance_that_starts_later():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request()
    trig.request()                          # queued behind the cooldown
    runner.blocked = "A maintenance action is in progress"
    clock.now += 60
    assert trig.run_pending_once() == "waiting"
    runner.blocked = None
    assert trig.run_pending_once() == "started"


def test_queued_run_is_dropped_if_it_cannot_start_for_an_hour():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock)
    runner.busy = True
    trig.request()
    clock.now += QUEUE_GIVE_UP_SECONDS + 1
    assert trig.run_pending_once() == "dropped"
    assert trig.pending_info() is None


def test_zero_cooldown_starts_every_call_when_idle():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=0)
    assert trig.request().status == "started"
    assert trig.request().status == "started"
    assert len(runner.starts) == 2


def test_merged_flags_prefer_a_real_run_and_keep_verbose():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request()
    trig.request(dry_run=True, verbose=False)
    trig.request(dry_run=False, verbose=True)
    trig.request(dry_run=True, verbose=False)
    info = trig.pending_info()
    assert info["dry_run"] is False and info["verbose"] is True
    clock.now += 30
    trig.run_pending_once()
    assert runner.starts[-1] == (False, True)


def test_all_dry_run_requests_stay_a_dry_run():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request(dry_run=True)
    trig.request(dry_run=True)
    clock.now += 30
    trig.run_pending_once()
    assert runner.starts == [(True, False), (True, False)]


def test_lost_start_race_queues_instead_of_failing():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock)
    runner.refuse_next = True               # another run grabbed the slot first
    assert trig.request().status == "queued"
    assert trig.run_pending_once() == "started"


def test_cancel_pending():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock)
    assert trig.cancel_pending() is False
    runner.busy = True
    trig.request()
    assert trig.cancel_pending() is True
    runner.busy = False
    assert trig.run_pending_once() is None
    assert runner.starts == []


def test_pending_info_reports_run_time_only_when_known():
    runner, clock = FakeRunner(), Clock()
    trig = _trigger(runner, clock, cooldown=30)
    trig.request()
    clock.now += 5
    trig.request()
    assert trig.pending_info()["run_at"] == pytest.approx(50_000.0 + 1030.0)
    runner.busy = True
    assert trig.pending_info()["run_at"] is None   # depends on when the run ends


def test_worker_thread_starts_the_queued_run():
    """End to end with the real worker thread and clock."""
    started = threading.Event()
    state = {"busy": True, "starts": 0}

    def start(dry_run, verbose):
        state["starts"] += 1
        started.set()
        return True

    trig = RunTrigger(start_run=start, is_busy=lambda: state["busy"],
                      blocked_reason=lambda: None, cooldown_seconds=lambda: 0,
                      poll_interval=0.01)
    assert trig.request().status == "queued"
    state["busy"] = False
    assert started.wait(2), "queued run never started"
    # The worker exits once the queue is empty; a new queued call starts a fresh one.
    deadline = time.time() + 2
    while trig._worker is not None and time.time() < deadline:
        time.sleep(0.01)
    assert trig._worker is None
    started.clear()
    state["busy"] = True
    assert trig.request().status == "queued"
    state["busy"] = False
    assert started.wait(2)
    assert state["starts"] == 2
