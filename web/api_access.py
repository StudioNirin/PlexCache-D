"""API key access and the trigger behind ``POST /api/run``.

External tools (Tautulli, Home Assistant, curl) can start a cache run, check
status and stop a run with an ``X-Api-Key`` header when the Web UI login is
enabled. The key is accepted for those three routes only; everything else
still needs a login session. With the login disabled the routes are open, as
they always were.

``RunTrigger`` adds a short cooldown so a burst of calls (a Tautulli stop event
fired twice, two people finishing at once) does not start a run per call.
Calls are never dropped: one that arrives during the cooldown or while a run is
in progress is merged into a single follow-up run that starts as soon as both
have passed, so every caller's latest state is picked up.

Kept free of app imports so tests can load it directly (several test modules
replace web.config with a MagicMock).
"""

import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

API_KEY_HEADER = "X-Api-Key"
DEFAULT_COOLDOWN_SECONDS = 30
MAX_COOLDOWN_SECONDS = 3600

# (method, path) pairs an API key may call. Anything else needs a session.
API_KEY_ROUTES = frozenset({
    ("POST", "/api/run"),
    ("GET", "/api/status"),
    ("POST", "/api/stop"),
})

# A queued follow-up that cannot start (maintenance or a CLI run holding the
# lock) is dropped after this long rather than firing hours later.
QUEUE_GIVE_UP_SECONDS = 3600


def is_api_key_route(method: str, path: str) -> bool:
    return (method.upper(), path.rstrip("/") or "/") in API_KEY_ROUTES


def api_key_matches(provided: Optional[str], stored: Optional[str]) -> bool:
    """Timing-safe comparison. An unset stored key never matches."""
    if not provided or not stored:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), stored.encode("utf-8"))


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def clamp_cooldown(value) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SECONDS
    return max(0, min(MAX_COOLDOWN_SECONDS, seconds))


@dataclass
class TriggerResult:
    status: str                    # "started" | "queued" | "blocked"
    message: str
    run_at: Optional[float] = None  # epoch seconds for a queued run, when known


class RunTrigger:
    """Start runs for API callers, merging calls inside the cooldown.

    Dependencies are injected so the logic can be tested without the app:
      start_run(dry_run, verbose) -> bool   start a run with source "api"
      is_busy() -> bool                     a cache run is in progress
      blocked_reason() -> str | None        maintenance / CLI run holding the lock
      cooldown_seconds() -> int             current setting
    """

    def __init__(self,
                 start_run: Callable[[bool, bool], bool],
                 is_busy: Callable[[], bool],
                 blocked_reason: Callable[[], Optional[str]],
                 cooldown_seconds: Callable[[], int],
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 poll_interval: float = 2.0,
                 start_worker: bool = True):
        self._start_run = start_run
        self._is_busy = is_busy
        self._blocked_reason = blocked_reason
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._poll_interval = poll_interval
        self._start_worker = start_worker
        self._lock = threading.Lock()
        self._last_start: Optional[float] = None
        self._pending: Optional[dict] = None     # {"dry_run", "verbose", "since"}
        self._worker: Optional[threading.Thread] = None

    # -- public -------------------------------------------------------------

    def request(self, dry_run: bool = False, verbose: bool = False) -> TriggerResult:
        with self._lock:
            reason = self._blocked_reason()
            if reason:
                return TriggerResult("blocked", reason)

            now = self._clock()
            ready_at = self._ready_at()
            if not self._is_busy() and now >= ready_at and self._pending is None:
                if self._start_run(dry_run, verbose):
                    self._last_start = now
                    return TriggerResult("started", self._started_message(dry_run, verbose))
                # Lost a race with another start; fall through and queue.

            self._merge_pending(dry_run, verbose, now)
            self._ensure_worker()
            return self._queued_result(now, ready_at)

    def cancel_pending(self) -> bool:
        """Drop a queued follow-up run. Returns True if one was queued."""
        with self._lock:
            had = self._pending is not None
            self._pending = None
            return had

    def pending_info(self) -> Optional[dict]:
        """For /api/status: whether a follow-up is queued and roughly when."""
        with self._lock:
            if self._pending is None:
                return None
            now = self._clock()
            run_at = None
            if not self._is_busy():
                run_at = self._wall_clock() + max(0.0, self._ready_at() - now)
            return {"queued": True, "run_at": run_at,
                    "dry_run": self._pending["dry_run"], "verbose": self._pending["verbose"]}

    def run_pending_once(self) -> Optional[str]:
        """Start the queued run if it can start now. Returns what happened.

        Called by the worker thread; tests call it directly.
        """
        with self._lock:
            if self._pending is None:
                return None
            now = self._clock()
            if now - self._pending["since"] > QUEUE_GIVE_UP_SECONDS:
                logging.warning("[API] Queued run dropped: could not start within %d minutes",
                                QUEUE_GIVE_UP_SECONDS // 60)
                self._pending = None
                return "dropped"
            if self._blocked_reason() or self._is_busy() or now < self._ready_at():
                return "waiting"
            pending = self._pending
            if self._start_run(pending["dry_run"], pending["verbose"]):
                self._last_start = now
                self._pending = None
                logging.info("[API] Started queued run")
                return "started"
            return "waiting"

    # -- internals ----------------------------------------------------------

    def _ready_at(self) -> float:
        if self._last_start is None:
            return float("-inf")
        return self._last_start + clamp_cooldown(self._cooldown_seconds())

    def _merge_pending(self, dry_run: bool, verbose: bool, now: float) -> None:
        if self._pending is None:
            self._pending = {"dry_run": dry_run, "verbose": verbose, "since": now}
        else:
            # A real run wins over a dry run; verbose if anyone asked for it.
            self._pending["dry_run"] = self._pending["dry_run"] and dry_run
            self._pending["verbose"] = self._pending["verbose"] or verbose

    def _queued_result(self, now: float, ready_at: float) -> TriggerResult:
        if self._is_busy():
            return TriggerResult("queued", "Run queued: starts when the current run finishes")
        wait = max(0.0, ready_at - now)
        return TriggerResult("queued", f"Run queued: starts in {int(wait + 0.999)}s",
                             run_at=self._wall_clock() + wait)

    @staticmethod
    def _started_message(dry_run: bool, verbose: bool) -> str:
        mode = [m for m, on in (("dry-run", dry_run), ("verbose", verbose)) if on]
        return "Run started" + (f" ({', '.join(mode)})" if mode else "")

    def _ensure_worker(self) -> None:
        if not self._start_worker:
            return
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._worker_loop, name="api-run-trigger", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            time.sleep(self._poll_interval)
            try:
                outcome = self.run_pending_once()
            except Exception:  # never let the worker die silently with a run queued
                logging.exception("[API] Queued run check failed")
                outcome = "waiting"
            if outcome in (None, "started", "dropped"):
                with self._lock:
                    if self._pending is None:
                        # Cleared under the lock, so a request() that queues a
                        # run after this point starts a fresh worker.
                        self._worker = None
                        return
