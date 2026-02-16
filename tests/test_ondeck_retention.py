"""Tests for OnDeck retention feature.

Verifies that OnDeckTracker preserves first_seen across runs,
cleanup_unseen() removes stale entries, and is_expired() correctly
expires items based on ondeck_retention_days.
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Mock fcntl for Windows compatibility
sys.modules['fcntl'] = MagicMock()

# Mock apscheduler
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.file_operations import OnDeckTracker


@pytest.fixture
def tracker(tmp_path):
    """Create a fresh OnDeckTracker with a temp file."""
    return OnDeckTracker(str(tmp_path / "ondeck_tracker.json"))


class TestPrepareForRun:
    """Tests for prepare_for_run() replacing clear_for_run()."""

    def test_preserves_first_seen(self, tracker):
        """first_seen survives across prepare/update cycles."""
        tracker.update_entry("/media/movie.mkv", "Alice")
        entry_before = tracker.get_entry("/media/movie.mkv")
        original_first_seen = entry_before['first_seen']

        # Simulate a new run
        tracker.prepare_for_run()
        tracker.update_entry("/media/movie.mkv", "Alice")

        entry_after = tracker.get_entry("/media/movie.mkv")
        assert entry_after['first_seen'] == original_first_seen

    def test_clears_per_run_fields(self, tracker):
        """users, ondeck_users, episode_info reset on prepare_for_run()."""
        tracker.update_entry(
            "/media/show/s01e01.mkv", "Bob",
            episode_info={"show": "Foundation", "season": 1, "episode": 1},
            is_current_ondeck=True
        )

        entry = tracker.get_entry("/media/show/s01e01.mkv")
        assert entry['users'] == ["Bob"]
        assert entry['ondeck_users'] == ["Bob"]
        assert entry['episode_info'] is not None

        # Prepare for new run — per-run fields should be cleared
        tracker.prepare_for_run()

        entry = tracker.get_entry("/media/show/s01e01.mkv")
        assert entry['users'] == []
        assert entry['ondeck_users'] == []
        assert 'episode_info' not in entry
        # first_seen and last_seen preserved
        assert 'first_seen' in entry
        assert 'last_seen' in entry


class TestCleanupUnseen:
    """Tests for cleanup_unseen() removing entries not refreshed this run."""

    def test_removes_stale_entries(self, tracker):
        """Entries not refreshed during the run are removed."""
        tracker.update_entry("/media/movie1.mkv", "Alice")
        tracker.update_entry("/media/movie2.mkv", "Bob")

        # New run — only refresh movie1
        tracker.prepare_for_run()
        tracker.update_entry("/media/movie1.mkv", "Alice")
        removed = tracker.cleanup_unseen()

        assert removed == 1
        assert tracker.get_entry("/media/movie1.mkv") is not None
        assert tracker.get_entry("/media/movie2.mkv") is None

    def test_keeps_refreshed_entries(self, tracker):
        """Entries refreshed this run are kept."""
        tracker.update_entry("/media/movie1.mkv", "Alice")
        tracker.update_entry("/media/movie2.mkv", "Bob")
        tracker.update_entry("/media/movie3.mkv", "Carol")

        tracker.prepare_for_run()
        tracker.update_entry("/media/movie1.mkv", "Alice")
        tracker.update_entry("/media/movie2.mkv", "Bob")
        tracker.update_entry("/media/movie3.mkv", "Carol")
        removed = tracker.cleanup_unseen()

        assert removed == 0
        assert tracker.get_entry("/media/movie1.mkv") is not None
        assert tracker.get_entry("/media/movie2.mkv") is not None
        assert tracker.get_entry("/media/movie3.mkv") is not None


class TestIsExpired:
    """Tests for is_expired() OnDeck retention check."""

    def test_returns_true_when_old(self, tracker):
        """Item older than retention_days expires."""
        tracker.update_entry("/media/old_movie.mkv", "Alice")

        # Backdate first_seen to 10 days ago
        entry = tracker._data["/media/old_movie.mkv"]
        entry['first_seen'] = (datetime.now() - timedelta(days=10)).isoformat()
        tracker._save()

        assert tracker.is_expired("/media/old_movie.mkv", retention_days=7) is True

    def test_returns_false_when_fresh(self, tracker):
        """Item within retention_days doesn't expire."""
        tracker.update_entry("/media/fresh_movie.mkv", "Alice")

        # first_seen is now — well within 7 days
        assert tracker.is_expired("/media/fresh_movie.mkv", retention_days=7) is False

    def test_disabled_when_zero(self, tracker):
        """retention_days=0 never expires."""
        tracker.update_entry("/media/movie.mkv", "Alice")

        # Backdate first_seen way in the past
        entry = tracker._data["/media/movie.mkv"]
        entry['first_seen'] = (datetime.now() - timedelta(days=365)).isoformat()
        tracker._save()

        assert tracker.is_expired("/media/movie.mkv", retention_days=0) is False

    def test_returns_false_for_unknown_entry(self, tracker):
        """Unknown file path returns False (conservative)."""
        assert tracker.is_expired("/media/unknown.mkv", retention_days=7) is False

    def test_returns_false_when_no_first_seen(self, tracker):
        """Entry without first_seen returns False (conservative)."""
        tracker._data["/media/no_ts.mkv"] = {"users": ["Alice"], "last_seen": datetime.now().isoformat()}
        tracker._save()

        assert tracker.is_expired("/media/no_ts.mkv", retention_days=7) is False


class TestIntegration:
    """Integration tests for OnDeck retention in the caching workflow."""

    def test_expired_items_not_in_ondeck_items(self, tracker):
        """Expired items are excluded from the ondeck list (simulated _process_media flow)."""
        # Simulate run 1: add items
        tracker.prepare_for_run()
        tracker.update_entry("/media/old.mkv", "Alice")
        tracker.update_entry("/media/new.mkv", "Bob")

        # Backdate old.mkv to 20 days ago
        tracker._data["/media/old.mkv"]['first_seen'] = (
            datetime.now() - timedelta(days=20)
        ).isoformat()
        tracker._save()

        # Simulate run 2
        tracker.prepare_for_run()
        tracker.update_entry("/media/old.mkv", "Alice")
        tracker.update_entry("/media/new.mkv", "Bob")

        # Filter like _process_media does
        modified_ondeck = ["/media/old.mkv", "/media/new.mkv"]
        retention_days = 14
        expired = {p for p in modified_ondeck if tracker.is_expired(p, retention_days)}
        filtered = [p for p in modified_ondeck if p not in expired]

        assert "/media/old.mkv" not in filtered
        assert "/media/new.mkv" in filtered
        assert len(expired) == 1

    def test_expired_items_eligible_for_move_back(self, tracker):
        """Expired items not in ondeck_items means they're eligible for move-back."""
        tracker.prepare_for_run()
        tracker.update_entry("/media/expired.mkv", "Alice")

        # Backdate
        tracker._data["/media/expired.mkv"]['first_seen'] = (
            datetime.now() - timedelta(days=30)
        ).isoformat()
        tracker._save()

        # Simulate the filtering
        modified_ondeck = ["/media/expired.mkv"]
        retention_days = 14
        expired = {p for p in modified_ondeck if tracker.is_expired(p, retention_days)}
        ondeck_items = set(p for p in modified_ondeck if p not in expired)

        # Expired item is NOT in ondeck_items, so move-back logic won't skip it
        assert "/media/expired.mkv" not in ondeck_items

    def test_first_seen_accumulates_across_runs(self, tracker):
        """Simulate multiple prepare/update cycles — first_seen never resets."""
        tracker.prepare_for_run()
        tracker.update_entry("/media/movie.mkv", "Alice")
        original_first_seen = tracker.get_entry("/media/movie.mkv")['first_seen']

        # Simulate 5 more runs
        for _ in range(5):
            tracker.prepare_for_run()
            tracker.update_entry("/media/movie.mkv", "Alice")
            tracker.cleanup_unseen()

        final_entry = tracker.get_entry("/media/movie.mkv")
        assert final_entry['first_seen'] == original_first_seen
        # last_seen should be updated each run
        assert final_entry['last_seen'] >= original_first_seen
