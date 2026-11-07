"""Tests for showing which media server (Plex / Jellyfin) put a file on cache.

The OnDeck and Watchlist trackers record a `sources` list per entry; the
Cached Files table turns that into "OnDeck · Jellyfin" style badges, but only
once Jellyfin shows up in the trackers so Plex-only installs look unchanged.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from jinja2 import Environment, FileSystemLoader, select_autoescape

for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.file_operations import OnDeckTracker, WatchlistTracker
from core.plex_api import OnDeckItem
from web.services.cache_service import CacheService, cached_files_to_dicts, trackers_have_jellyfin


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "web" / "templates"


class TestOnDeckTrackerSources:
    def test_defaults_to_plex(self, tmp_path):
        tracker = OnDeckTracker(str(tmp_path / "ondeck.json"))
        tracker.update_entry("/m/film.mkv", "roger")

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["plex"]

    def test_both_servers_recorded_once(self, tmp_path):
        tracker = OnDeckTracker(str(tmp_path / "ondeck.json"))
        tracker.update_entry("/m/film.mkv", "roger", source="jellyfin")
        tracker.update_entry("/m/film.mkv", "anna", source="plex")
        tracker.update_entry("/m/film.mkv", "roger", source="jellyfin")

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["jellyfin", "plex"]

    def test_prepare_for_run_resets_sources(self, tmp_path):
        """OnDeck is rebuilt every run, so a server that dropped it must disappear."""
        tracker = OnDeckTracker(str(tmp_path / "ondeck.json"))
        tracker.update_entry("/m/film.mkv", "roger", source="plex")
        tracker.update_entry("/m/film.mkv", "roger", source="jellyfin")

        tracker.prepare_for_run()
        tracker.update_entry("/m/film.mkv", "roger", source="jellyfin")

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["jellyfin"]

    def test_plex_ondeck_item_defaults_to_plex(self):
        assert OnDeckItem(file_path="/m/film.mkv", username="roger").source == "plex"


class TestWatchlistTrackerSources:
    def test_new_and_existing_entries(self, tmp_path):
        tracker = WatchlistTracker(str(tmp_path / "watchlist.json"))
        tracker.update_entry("/m/film.mkv", "roger", None, source="jellyfin")
        assert tracker.get_entry("/m/film.mkv")["sources"] == ["jellyfin"]

        tracker.update_entry("/m/film.mkv", "anna", None)
        assert tracker.get_entry("/m/film.mkv")["sources"] == ["jellyfin", "plex"]


class TestCachedFileSources:
    def _build(self, tmp_path, ondeck, watchlist, show_sources=True):
        from conftest import create_test_file

        path = create_test_file(str(tmp_path / "Film.mkv"), size_bytes=100)
        service = object.__new__(CacheService)
        return service._build_cached_file(
            path,
            timestamps={path: {"cached_at": "2026-11-01T12:00:00", "source": "watchlist"}},
            ondeck=ondeck,
            watchlist=watchlist,
            settings={},
            pinned_cache_path_map={},
            pinned_cache_paths=set(),
            video_subtitles={},
            video_sidecars={},
            source_filter="all",
            search="",
            now=datetime(2026, 11, 7, 12, 0, 0),
            show_sources=show_sources,
        )

    def test_sources_in_display_order(self, tmp_path):
        f = self._build(
            tmp_path,
            ondeck={"/data/Film.mkv": {"users": ["roger"], "sources": ["jellyfin"]}},
            watchlist={"/data/Film.mkv": {"users": ["roger"], "sources": ["jellyfin", "plex"]}},
        )

        assert f.ondeck_sources == ["jellyfin"]
        assert f.watchlist_sources == ["plex", "jellyfin"]
        row = cached_files_to_dicts([f])[0]
        assert row["watchlist_sources"] == ["plex", "jellyfin"]

    def test_hidden_without_show_sources(self, tmp_path):
        f = self._build(
            tmp_path, ondeck={},
            watchlist={"/data/Film.mkv": {"users": ["roger"], "sources": ["plex"]}},
            show_sources=False,
        )

        assert f.is_watchlist
        assert f.watchlist_sources == []

    def test_legacy_entry_without_sources(self, tmp_path):
        f = self._build(tmp_path, ondeck={}, watchlist={"/data/Film.mkv": {"users": ["roger"]}})

        assert f.is_watchlist
        assert f.watchlist_sources == []

    def test_jellyfin_detection(self):
        assert not trackers_have_jellyfin({"/a.mkv": {"sources": ["plex"]}}, {"/b.mkv": {}})
        assert trackers_have_jellyfin({}, {"/b.mkv": {"sources": ["plex", "jellyfin"]}})


class TestSourceBadgeMacro:
    def _render(self, call):
        env = Environment(loader=FileSystemLoader(str(TEMPLATE_ROOT)),
                          autoescape=select_autoescape(["html"]))
        return env.from_string(
            '{% from "macros/source_badge.html" import ondeck_badge, watchlist_badge %}' + call
        ).render()

    def test_plain_badge_without_sources(self):
        html = self._render("{{ watchlist_badge() }}")
        assert ">Watchlist</span>" in html
        assert "Plex user&#39;s Watchlist" in html

    def test_single_server(self):
        html = self._render("{{ ondeck_badge(['jellyfin']) }}")
        assert ">OnDeck · Jellyfin</span>" in html
        assert "Jellyfin user&#39;s Continue Watching" in html

    def test_both_servers(self):
        html = self._render("{{ watchlist_badge(['plex', 'jellyfin']) }}")
        assert ">Watchlist · Plex + Jellyfin</span>" in html
        assert "On a Plex user&#39;s Watchlist and on a Jellyfin user&#39;s favorites" in html


class TestWatchlistPruneSources:
    """A server that stops reporting an item loses its label, unless its fetch failed."""

    def _tracker(self, tmp_path):
        tracker = WatchlistTracker(str(tmp_path / "watchlist.json"))
        tracker.update_entry("/m/film.mkv", "roger", None, source="plex")
        tracker.update_entry("/m/film.mkv", "roger", None, source="jellyfin")
        return tracker

    def test_unfavorited_but_still_on_plex_watchlist(self, tmp_path):
        tracker = self._tracker(tmp_path)

        tracker.prune_sources({"/m/film.mkv": {"plex"}}, {"plex", "jellyfin"})

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["plex"]

    def test_incomplete_fetch_keeps_its_source(self, tmp_path):
        tracker = self._tracker(tmp_path)

        tracker.prune_sources({"/m/film.mkv": {"plex"}}, {"plex"})

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["plex", "jellyfin"]

    def test_unseen_and_legacy_entries_untouched(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker._data["/m/legacy.mkv"] = {"users": ["roger"]}

        tracker.prune_sources({"/m/legacy.mkv": {"plex"}}, {"plex", "jellyfin"})

        assert tracker.get_entry("/m/film.mkv")["sources"] == ["plex", "jellyfin"]
        assert "sources" not in tracker.get_entry("/m/legacy.mkv")


class TestCompleteWatchlistSources:
    def _app(self, plex_complete=True, jellyfin=True, favorites=True, favorites_complete=True):
        from core.app import PlexCacheApp
        app = object.__new__(PlexCacheApp)
        app.config_manager = MagicMock()
        app.config_manager.jellyfin.favorites_toggle = favorites
        app.plex_manager = MagicMock()
        app.plex_manager.is_watchlist_data_complete.return_value = plex_complete
        app.jellyfin_manager = MagicMock() if jellyfin else None
        if jellyfin:
            app.jellyfin_manager.is_favorites_data_complete.return_value = favorites_complete
        return app

    def test_all_complete(self):
        assert self._app()._complete_watchlist_sources(rss_failed=False) == {"plex", "jellyfin"}

    def test_plex_incomplete_or_rss_failed(self):
        assert self._app(plex_complete=False)._complete_watchlist_sources(False) == {"jellyfin"}
        assert self._app()._complete_watchlist_sources(rss_failed=True) == {"jellyfin"}

    def test_jellyfin_favorites_failed(self):
        assert self._app(favorites_complete=False)._complete_watchlist_sources(False) == {"plex"}

    def test_jellyfin_off_or_favorites_off_counts_as_complete(self):
        assert "jellyfin" in self._app(jellyfin=False)._complete_watchlist_sources(False)
        assert "jellyfin" in self._app(favorites=False)._complete_watchlist_sources(False)
