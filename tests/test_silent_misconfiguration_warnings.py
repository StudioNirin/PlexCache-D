"""Configurations that quietly do nothing should say so.

Two cases where a fully configured feature was inert and the log looked
identical to a healthy run:

* Both eviction entry points size themselves against ``cache_limit``. With no
  limit set they returned immediately, so "smart" eviction plus a
  ``plexcache_quota`` evicted nothing, forever, silently.
* Caching renames the original to ``.plexcached``. Off Unraid, without a
  merged view of the two paths, Plex stops finding the file unless
  ``use_symlinks`` is on, and that setting defaults to False.
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

GB = 1024 ** 3


def _app(eviction_mode="smart", cache_limit_bytes=0):
    from core.app import PlexCacheApp

    app = object.__new__(PlexCacheApp)
    app.config_manager = MagicMock()
    app.config_manager.cache.cache_eviction_mode = eviction_mode
    app.config_manager.cache.cache_eviction_threshold_percent = 90
    app.config_manager.cache.eviction_min_priority = 60
    app.config_manager.cache.cache_drive_size_bytes = 0
    app.config_manager.paths.cache_dir = "/mnt/cache/media"
    app.config_manager.paths.path_mappings = []
    app._warned_eviction_needs_limit = False
    app._get_effective_cache_limit = MagicMock(return_value=(cache_limit_bytes, ""))
    return app


class TestEvictionWithoutCacheLimit:

    def test_filter_path_warns(self, caplog):
        app = _app()
        with caplog.at_level(logging.WARNING):
            result = app._filter_low_priority_files(["/mnt/cache/a.mkv"], {})

        assert result == ["/mnt/cache/a.mkv"], "files still pass through"
        assert "no Cache Limit is configured" in caplog.text
        assert "smart" in caplog.text

    def test_eviction_path_warns(self, caplog):
        app = _app()
        with caplog.at_level(logging.WARNING):
            evicted, freed = app._run_smart_eviction()

        assert (evicted, freed) == (0, 0)
        assert "no Cache Limit is configured" in caplog.text

    def test_warns_once_per_run(self, caplog):
        """Both entry points run every cycle; one warning is enough."""
        app = _app()
        with caplog.at_level(logging.WARNING):
            app._filter_low_priority_files([], {})
            app._run_smart_eviction()
            app._filter_low_priority_files([], {})

        assert caplog.text.count("no Cache Limit is configured") == 1

    def test_silent_when_eviction_is_off(self, caplog):
        """Not a misconfiguration, so nothing to say."""
        app = _app(eviction_mode="none")
        with caplog.at_level(logging.WARNING):
            app._filter_low_priority_files([], {})
            app._run_smart_eviction()

        assert "Cache Limit" not in caplog.text

    def test_silent_when_a_limit_is_set(self, caplog):
        app = _app(cache_limit_bytes=500 * GB)
        with caplog.at_level(logging.WARNING), \
             patch("core.app.get_disk_usage", side_effect=OSError("no disk")):
            app._filter_low_priority_files([], {})

        assert "no Cache Limit is configured" not in caplog.text


def _symlink_app(is_unraid, use_symlinks, with_mapping=True):
    from core.app import PlexCacheApp

    app = object.__new__(PlexCacheApp)
    app.system_detector = MagicMock()
    app.system_detector.is_unraid = is_unraid
    app.config_manager = MagicMock()
    app.config_manager.cache.use_symlinks = use_symlinks

    if with_mapping:
        mapping = MagicMock(enabled=True, cacheable=True, cache_path="/mnt/cache/media")
        app.config_manager.paths.path_mappings = [mapping]
    else:
        app.config_manager.paths.path_mappings = []
        app.config_manager.paths.cache_dir = ""
    return app


class TestSymlinkAdvisory:

    def test_warns_off_unraid_with_symlinks_off(self, caplog):
        with caplog.at_level(logging.WARNING):
            _symlink_app(is_unraid=False, use_symlinks=False)._advise_on_symlinks()

        assert "Create symlinks" in caplog.text

    def test_silent_on_unraid(self, caplog):
        """The FUSE union keeps the original path resolving."""
        with caplog.at_level(logging.WARNING):
            _symlink_app(is_unraid=True, use_symlinks=False)._advise_on_symlinks()

        assert caplog.text == ""

    def test_warns_on_unraid_with_symlinks_on(self, caplog):
        """On Unraid the option only patches over a cache path in the wrong share (#246)."""
        with caplog.at_level(logging.WARNING):
            _symlink_app(is_unraid=True, use_symlinks=True)._advise_on_symlinks()

        assert "Unraid doesn't need it" in caplog.text
        assert "same share" in caplog.text

    def test_silent_when_symlinks_are_on(self, caplog):
        with caplog.at_level(logging.WARNING):
            _symlink_app(is_unraid=False, use_symlinks=True)._advise_on_symlinks()

        assert caplog.text == ""

    def test_silent_with_nothing_cacheable(self, caplog):
        """No cacheable path means nothing gets renamed."""
        with caplog.at_level(logging.WARNING):
            _symlink_app(is_unraid=False, use_symlinks=False,
                         with_mapping=False)._advise_on_symlinks()

        assert caplog.text == ""
