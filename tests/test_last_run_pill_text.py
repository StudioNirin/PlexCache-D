"""
Text for the idle Ready pill (no schedule set): how long ago the last run was
and what it did, from data/last_run.txt and the last run summary.
"""

import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

if "fcntl" not in sys.modules:
    sys.modules["fcntl"] = MagicMock()

from core.activity import last_run_pill_text  # noqa: E402


def _ago(**delta):
    return (datetime.now() - timedelta(**delta)).isoformat()


def test_never_run():
    assert last_run_pill_text(None) == {"compact": "Not run yet", "detail": "Not run yet"}


def test_unreadable_timestamp_reads_as_never_run():
    assert last_run_pill_text("not a date")["compact"] == "Not run yet"


def test_age_without_summary():
    text = last_run_pill_text(_ago(hours=2, minutes=5))
    assert text == {"compact": "Ran 2 hr ago", "detail": "Last run 2 hr ago"}


def test_summary_details():
    summary = {"status": "completed", "files_cached": 12, "bytes_cached": 38.2 * 1024 ** 3,
               "files_restored": 3, "error_count": 2, "dry_run": False}
    text = last_run_pill_text(_ago(minutes=5, seconds=10), summary)
    assert text["compact"] == "Ran 5 min ago"
    assert text["detail"] == "Last run 5 min ago · 12 cached (38.20 GB) · 3 restored · 2 errors"


def test_failed_dry_run_and_single_error():
    summary = {"status": "failed", "error_count": 1, "dry_run": True}
    detail = last_run_pill_text(_ago(days=3, hours=1), summary)["detail"]
    assert detail == "Last run 3 days ago · failed · 1 error · dry run"


def test_nothing_moved_lists_only_the_age():
    summary = {"status": "completed", "files_cached": 0, "files_restored": 0, "error_count": 0}
    assert last_run_pill_text(_ago(seconds=20), summary)["detail"] == "Last run just now"
