"""Tests for the optional Jellyfin OnDeck source (core/jellyfin_api.py)."""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions', 'requests',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.jellyfin_api import ActiveSessions, JellyfinManager


MAPPINGS = {"/media/en/": "/mnt/user/plex-media/", "/media/": "/data/"}


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.0000000Z')


def _episode(item_id, season, episode, path, runtime_min=45, series_id="s1", last_played=None):
    return {
        "Id": item_id, "Type": "Episode", "Name": f"Ep {season}x{episode}",
        "SeriesId": series_id, "SeriesName": "Show",
        "ParentIndexNumber": season, "IndexNumber": episode,
        "RunTimeTicks": runtime_min * 60 * 10_000_000,
        "MediaSources": [{"Path": path}],
        "UserData": {"LastPlayedDate": last_played} if last_played else {},
    }


class FakeSession:
    """Routes GET requests to canned JSON by path; raises for paths mapped to an exception."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        path = url.split("http://jf", 1)[1]
        self.calls.append((path, params))
        result = self.routes[path]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(params)
        response = MagicMock()
        response.json.return_value = result
        return response


def _manager(routes, **kwargs):
    return JellyfinManager("http://jf/", "key", path_mappings=MAPPINGS, session=FakeSession(routes), **kwargs)


def _routes(resume=None, next_up=None, episodes=None, users=None):
    return {
        "/Users": users if users is not None else [{"Id": "u1", "Name": "roger"}],
        "/UserItems/Resume": {"Items": resume or []},
        "/Shows/NextUp": {"Items": next_up or []},
        "/Shows/s1/Episodes": {"Items": episodes or []},
    }


class TestPathTranslation:
    def test_longest_prefix_wins(self):
        m = _manager({})
        assert m.to_plex_path("/media/en/Movies/a.mkv") == "/mnt/user/plex-media/Movies/a.mkv"
        assert m.to_plex_path("/media/de/Movies/a.mkv") == "/data/de/Movies/a.mkv"

    def test_unmapped_path_is_dropped(self):
        m = _manager({})
        assert m.to_plex_path("/other/a.mkv") is None
        assert m._item_paths({"Path": "/other/a.mkv"}) == []

    def test_all_versions_and_path_fallback(self):
        m = _manager({})
        item = {"MediaSources": [{"Path": "/media/en/a.mkv"}, {"Path": "/media/en/a-4k.mkv"}]}
        assert m._item_paths(item) == ["/mnt/user/plex-media/a.mkv", "/mnt/user/plex-media/a-4k.mkv"]
        assert m._item_paths({"Path": "/media/en/b.mkv"}) == ["/mnt/user/plex-media/b.mkv"]

    def test_trailing_slashes_are_normalized(self):
        m = JellyfinManager("http://jf", "key", path_mappings={"/media": "/mnt/user/plex-media/"},
                            session=FakeSession({}))
        assert m.to_plex_path("/media/a.mkv") == "/mnt/user/plex-media/a.mkv"
        m = JellyfinManager("http://jf", "key", path_mappings={"/media/": "/mnt/user/plex-media"},
                            session=FakeSession({}))
        assert m.to_plex_path("/media/a.mkv") == "/mnt/user/plex-media/a.mkv"

    def test_prefix_matches_whole_folder_only(self):
        m = JellyfinManager("http://jf", "key", path_mappings={"/media": "/data"}, session=FakeSession({}))
        assert m.to_plex_path("/media2/a.mkv") is None
        assert m.to_plex_path("/media/a.mkv") == "/data/a.mkv"

    def test_unmapped_path_logged_at_info_once_per_library(self, caplog):
        m = _manager({})
        with caplog.at_level(logging.INFO):
            m._item_paths({"Path": "/other/TV/Show/e1.mkv"})
            m._item_paths({"Path": "/other/TV/Show/e2.mkv"})
            m._item_paths({"Path": "/other/Movies/a.mkv"})
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert infos == [
            "[JELLYFIN] Skipping unmapped path /other/TV/ - add it to jellyfin_path_mappings",
            "[JELLYFIN] Skipping unmapped path /other/Movies/ - add it to jellyfin_path_mappings",
        ]

    def test_auth_header(self):
        m = _manager({})
        assert m.session.headers["Authorization"] == 'MediaBrowser Client="PlexCache-D", Token="key"'


class TestOnDeck:
    def test_movie_and_episode_with_prefetch(self):
        movie = {"Id": "m1", "Type": "Movie", "Name": "Film", "Path": "/media/en/Movies/film.mkv",
                 "UserData": {"LastPlayedDate": _ago(1)}}
        current = _episode("e2", 1, 2, "/media/en/TV/s01e02.mkv")
        episodes = [_episode(f"e{i}", 1, i, f"/media/en/TV/s01e0{i}.mkv") for i in range(1, 6)]
        m = _manager(_routes(resume=[movie], next_up=[current], episodes=episodes))

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=2)

        assert [(i.file_path, i.is_current_ondeck) for i in items] == [
            ("/mnt/user/plex-media/Movies/film.mkv", True),
            ("/mnt/user/plex-media/TV/s01e02.mkv", True),
            ("/mnt/user/plex-media/TV/s01e03.mkv", False),
            ("/mnt/user/plex-media/TV/s01e04.mkv", False),
        ]
        assert all(i.username == "roger" for i in items)
        assert all(i.source == "jellyfin" for i in items)
        assert items[1].episode_info == {"show": "Show", "season": 1, "episode": 2}
        assert items[3].episode_info == {"show": "Show", "season": 1, "episode": 4}
        assert m.is_ondeck_data_complete()

    def test_prefetch_minimum_minutes_uses_runtime(self):
        current = _episode("e1", 1, 1, "/media/en/TV/e1.mkv")
        episodes = [_episode(f"e{i}", 1, i, f"/media/en/TV/e{i}.mkv", runtime_min=20) for i in range(1, 8)]
        m = _manager(_routes(next_up=[current], episodes=episodes))

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=1, prefetch_minimum_minutes=60)

        # 3 x 20 min needed to cover 60 minutes
        assert len([i for i in items if not i.is_current_ondeck]) == 3

    def test_item_in_resume_and_next_up_counted_once(self):
        current = _episode("e1", 1, 1, "/media/en/TV/e1.mkv", last_played=_ago(1))
        m = _manager(_routes(resume=[current], next_up=[current], episodes=[current]))

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert [i.file_path for i in items] == ["/mnt/user/plex-media/TV/e1.mkv"]

    def test_stale_resume_item_is_skipped(self):
        movie = {"Id": "m1", "Type": "Movie", "Path": "/media/en/old.mkv",
                 "UserData": {"LastPlayedDate": _ago(90)}}
        m = _manager(_routes(resume=[movie]))

        assert m.get_on_deck_media(days_to_monitor=30, number_episodes=1) == []

    def test_next_up_cutoff_sent_to_server(self):
        m = _manager(_routes())
        m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        params = next(p for path, p in m.session.calls if path == "/Shows/NextUp")
        cutoff = datetime.strptime(params["nextUpDateCutoff"], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - cutoff) - timedelta(days=30)) < timedelta(minutes=1)

    def test_skip_users(self):
        movie = {"Id": "m1", "Type": "Movie", "Path": "/media/en/a.mkv"}
        users = [{"Id": "u1", "Name": "roger"}, {"Id": "u2", "Name": "kid"}]
        m = _manager(_routes(resume=[movie], users=users), skip_users=["kid"])

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert [i.username for i in items] == ["roger"]
        assert [p["userId"] for path, p in m.session.calls if path == "/UserItems/Resume"] == ["u1"]

    @pytest.mark.critical
    def test_user_list_failure_marks_incomplete(self):
        m = _manager({"/Users": RuntimeError("down")})

        assert m.get_on_deck_media(days_to_monitor=30, number_episodes=1) == []
        assert not m.is_ondeck_data_complete()

    @pytest.mark.critical
    def test_single_user_failure_marks_incomplete(self):
        routes = _routes()
        routes["/UserItems/Resume"] = RuntimeError("timeout")
        m = _manager(routes)

        m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert not m.is_ondeck_data_complete()

    @pytest.mark.critical
    def test_failed_item_keeps_the_rest_of_the_user(self):
        movie = {"Id": "m1", "Type": "Movie", "Path": "/media/en/a.mkv"}
        current = _episode("e1", 1, 1, "/media/en/TV/e1.mkv")
        routes = _routes(resume=[movie], next_up=[current])
        routes["/Shows/s1/Episodes"] = RuntimeError("timeout")
        m = _manager(routes)

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert [i.file_path for i in items] == ["/mnt/user/plex-media/a.mkv", "/mnt/user/plex-media/TV/e1.mkv"]
        assert not m.is_ondeck_data_complete()

    @pytest.mark.critical
    def test_failed_list_keeps_the_other_list(self):
        current = _episode("e1", 1, 1, "/media/en/TV/e1.mkv")
        routes = _routes(next_up=[current], episodes=[current])
        routes["/UserItems/Resume"] = RuntimeError("timeout")
        m = _manager(routes)

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert [i.file_path for i in items] == ["/mnt/user/plex-media/TV/e1.mkv"]
        assert not m.is_ondeck_data_complete()

    def test_episode_list_starts_at_current_episode(self):
        current = _episode("e7", 1, 7, "/media/en/TV/e7.mkv")
        m = _manager(_routes(next_up=[current], episodes=[current]))

        m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        params = next(p for path, p in m.session.calls if path == "/Shows/s1/Episodes")
        assert params["startItemId"] == "e7"

    def test_episode_list_retried_without_start_item(self):
        # Alternate version as current item: Jellyfin <= 10.11 returns an empty list for startItemId
        current = _episode("e2-alt", 1, 2, "/media/en/TV/s01e02-4k.mkv")
        episodes = [_episode(f"e{i}", 1, i, f"/media/en/TV/s01e0{i}.mkv") for i in range(1, 5)]
        routes = _routes(next_up=[current])
        routes["/Shows/s1/Episodes"] = lambda params: {"Items": [] if "startItemId" in params else episodes}
        m = _manager(routes)

        items = m.get_on_deck_media(days_to_monitor=30, number_episodes=2)

        assert [i.file_path for i in items if not i.is_current_ondeck] == [
            "/mnt/user/plex-media/TV/s01e03.mkv", "/mnt/user/plex-media/TV/s01e04.mkv"]
        assert [("startItemId" in p) for path, p in m.session.calls if path == "/Shows/s1/Episodes"] == [True, False]
        assert m.is_ondeck_data_complete()

    def test_complete_flag_resets_on_next_run(self):
        routes = _routes()
        routes["/Users"] = RuntimeError("down")
        m = _manager(routes)
        m.get_on_deck_media(days_to_monitor=30, number_episodes=1)
        routes["/Users"] = [{"Id": "u1", "Name": "roger"}]

        m.get_on_deck_media(days_to_monitor=30, number_episodes=1)

        assert m.is_ondeck_data_complete()


def _favorite_routes(favorites, seasons=None, season_episodes=None, users=None):
    """/Items serves the favorites list or, with parentId, one season's unplayed episodes."""
    season_episodes = season_episodes or {}

    def items(params):
        if params.get("isFavorite") == "true":
            return {"Items": favorites}
        return {"Items": season_episodes[params["parentId"]][:params["limit"]]}

    return {
        "/Users": users if users is not None else [{"Id": "u1", "Name": "roger"}],
        "/Items": items,
        "/Shows/s1/Seasons": {"Items": seasons or []},
    }


class TestFavorites:
    def test_unplayed_movie(self):
        movie = {"Id": "m1", "Type": "Movie", "Name": "Film", "MediaSources": [{"Path": "/media/en/Movies/film.mkv"}]}
        m = _manager(_favorite_routes([movie]))

        assert m.get_favorite_media(episodes_per_show=3) == [
            ("/mnt/user/plex-media/Movies/film.mkv", "roger", None, None, None, "movie")]
        params = next(p for path, p in m.session.calls if path == "/Items")
        assert (params["isFavorite"], params["isPlayed"]) == ("true", "false")
        assert m.is_favorites_data_complete()

    def test_series_first_unplayed_episodes_without_specials(self):
        series = {"Id": "s1", "Type": "Series", "Name": "Show"}
        seasons = [{"Id": "sea2", "IndexNumber": 2}, {"Id": "sea0", "IndexNumber": 0}, {"Id": "sea1", "IndexNumber": 1}]
        season_episodes = {
            "sea0": [_episode("x1", 0, 1, "/media/en/TV/s00e01.mkv")],
            "sea1": [_episode("e5", 1, 5, "/media/en/TV/s01e05.mkv"), _episode("e6", 1, 6, "/media/en/TV/s01e06.mkv")],
            "sea2": [_episode(f"f{i}", 2, i, f"/media/en/TV/s02e0{i}.mkv") for i in range(1, 5)],
        }
        m = _manager(_favorite_routes([series], seasons, season_episodes))

        items = m.get_favorite_media(episodes_per_show=3)

        assert [(i[0], i[3], i[5]) for i in items] == [
            ("/mnt/user/plex-media/TV/s01e05.mkv", {"show": "Show", "season": 1, "episode": 5}, "episode"),
            ("/mnt/user/plex-media/TV/s01e06.mkv", {"show": "Show", "season": 1, "episode": 6}, "episode"),
            ("/mnt/user/plex-media/TV/s02e01.mkv", {"show": "Show", "season": 2, "episode": 1}, "episode"),
        ]
        season_calls = [p for path, p in m.session.calls if path == "/Items" and "parentId" in p]
        assert [(p["parentId"], p["limit"]) for p in season_calls] == [("sea1", 3), ("sea2", 1)]
        assert all(p["isPlayed"] == "false" for p in season_calls)

    def test_skip_users(self):
        movie = {"Id": "m1", "Type": "Movie", "Path": "/media/en/a.mkv"}
        users = [{"Id": "u1", "Name": "roger"}, {"Id": "u2", "Name": "kid"}]
        m = _manager(_favorite_routes([movie], users=users), skip_users=["kid"])

        assert [i[1] for i in m.get_favorite_media(episodes_per_show=1)] == ["roger"]

    @pytest.mark.critical
    def test_failed_series_keeps_other_favorites(self):
        movie = {"Id": "m1", "Type": "Movie", "Path": "/media/en/a.mkv"}
        series = {"Id": "s1", "Type": "Series", "Name": "Show"}
        routes = _favorite_routes([series, movie])
        routes["/Shows/s1/Seasons"] = RuntimeError("timeout")
        m = _manager(routes)

        assert [i[0] for i in m.get_favorite_media(episodes_per_show=1)] == ["/mnt/user/plex-media/a.mkv"]
        assert not m.is_favorites_data_complete()

    @pytest.mark.critical
    def test_failed_favorites_list_marks_incomplete(self):
        routes = _favorite_routes([])
        routes["/Items"] = RuntimeError("down")
        m = _manager(routes)

        assert m.get_favorite_media(episodes_per_show=1) == []
        assert not m.is_favorites_data_complete()
        # OnDeck completeness is tracked separately
        assert m.is_ondeck_data_complete()

    @pytest.mark.critical
    def test_user_list_failure_marks_incomplete(self):
        m = _manager({"/Users": RuntimeError("down")})

        assert m.get_favorite_media(episodes_per_show=1) == []
        assert not m.is_favorites_data_complete()


class TestFavoritesInWatchlist:
    """PlexCacheApp._process_watchlist feeds Jellyfin favorites into the watchlist tracker."""

    def _app(self, favorites, favorites_toggle=True, expired=()):
        from core.app import PlexCacheApp
        app = object.__new__(PlexCacheApp)
        app.config_manager = MagicMock()
        app.config_manager.cache.watchlist_retention_days = 30
        app.config_manager.cache.watchlist_episodes = 3
        app.config_manager.cache.remote_watchlist_toggle = False
        app.config_manager.cache.cache_associated_files = "none"
        app.config_manager.plex.per_user_watchlist_days = {}
        app.config_manager.plex.users = []
        app.config_manager.jellyfin.favorites_toggle = favorites_toggle
        app.plex_manager = MagicMock()
        app.plex_manager.get_watchlist_media.return_value = []
        app.jellyfin_manager = MagicMock()
        app.jellyfin_manager.get_favorite_media.return_value = favorites
        app.watchlist_tracker = MagicMock()
        app.watchlist_tracker.is_expired.side_effect = lambda path, days, username=None: path in expired
        app.file_path_modifier = MagicMock()
        app.file_path_modifier.modify_file_paths.side_effect = lambda paths: list(paths)
        app._stop_requested = False
        app.media_info_map = {}
        app.sibling_map = {}
        app.files_to_skip = []
        return app

    def test_favorites_added_and_tracked(self):
        favorites = [("/data/film.mkv", "roger", None, None, None, "movie"),
                     ("/data/e1.mkv", "roger", None, {"show": "Show", "season": 1, "episode": 1}, None, "episode")]
        app = self._app(favorites)

        result = app._process_watchlist()

        assert {"/data/film.mkv", "/data/e1.mkv"} <= result
        app.jellyfin_manager.get_favorite_media.assert_called_once_with(3)
        app.watchlist_tracker.update_entry.assert_any_call(
            "/data/film.mkv", "roger", None, rating_key=None, media_type="movie", source="jellyfin")
        assert app.media_info_map["/data/e1.mkv"]["media_type"] == "episode"

    def test_expired_favorite_skipped(self):
        app = self._app([("/data/old.mkv", "roger", None, None, None, "movie")], expired={"/data/old.mkv"})

        assert "/data/old.mkv" not in app._process_watchlist()

    def test_toggle_off_does_not_fetch(self):
        app = self._app([], favorites_toggle=False)

        app._process_watchlist()

        app.jellyfin_manager.get_favorite_media.assert_not_called()


class TestActiveSessions:
    def test_path_from_now_playing_item(self):
        sessions = [{"UserId": "u1", "NowPlayingItem": {"Id": "m1", "Path": "/media/en/a.mkv"}}, {"UserId": "u2"}]
        m = _manager({"/Sessions": sessions})

        assert m.get_active_sessions() == ActiveSessions(1, ["/mnt/user/plex-media/a.mkv"])

    def test_fetches_item_when_path_missing(self):
        sessions = [{"UserId": "u1", "NowPlayingItem": {"Id": "m1"}}]
        m = _manager({"/Sessions": sessions, "/Items": {"Items": [{"Path": "/media/en/a.mkv"}]}})

        assert m.get_active_sessions() == ActiveSessions(1, ["/mnt/user/plex-media/a.mkv"])
        assert m.session.calls[-1][1]["userId"] == "u1"

    @pytest.mark.critical
    def test_unmapped_session_still_counts(self):
        sessions = [{"UserId": "u1", "NowPlayingItem": {"Id": "m1", "Path": "/other/a.mkv"}}]
        m = _manager({"/Sessions": sessions})

        assert m.get_active_sessions() == ActiveSessions(1, [])
        # The path was there, just unmapped: no extra /Items lookup
        assert [path for path, _ in m.session.calls] == ["/Sessions"]

    def test_failure_returns_empty(self):
        m = _manager({"/Sessions": RuntimeError("down")})

        assert m.get_active_sessions() == ActiveSessions(0, [])


class TestCheckActiveSessions:
    """PlexCacheApp._check_active_sessions with Plex and Jellyfin sessions."""

    def _app(self, plex_sessions, jellyfin_sessions, exit_if_active=False):
        from core.app import PlexCacheApp
        app = object.__new__(PlexCacheApp)
        app.config_manager = MagicMock()
        app.config_manager.exit_if_active_session = exit_if_active
        app.plex_manager = MagicMock()
        app.plex_manager.get_active_sessions.return_value = plex_sessions
        if jellyfin_sessions is None:
            app.jellyfin_manager = None
        else:
            app.jellyfin_manager = MagicMock()
            app.jellyfin_manager.get_active_sessions.return_value = jellyfin_sessions
        app.file_path_modifier = MagicMock()
        app.file_path_modifier.modify_file_paths.side_effect = lambda paths: [p.replace("/data", "/mnt") for p in paths]
        app.files_to_skip = []
        app._get_media_path_from_session = lambda session: session
        return app

    @pytest.mark.parametrize("plex, jellyfin, expected", [
        (["/data/p.mkv"], None, "Plex"),
        (["/data/p.mkv"], ActiveSessions(0, []), "Plex"),
        ([], ActiveSessions(1, ["/data/j.mkv"]), "Jellyfin"),
        (["/data/p.mkv"], ActiveSessions(1, ["/data/j.mkv"]), "Plex and Jellyfin"),
    ])
    def test_exit_message_names_server(self, plex, jellyfin, expected):
        app = self._app(plex, jellyfin, exit_if_active=True)

        with pytest.raises(SystemExit) as exc:
            app._check_active_sessions()

        assert str(exc.value) == f"There is an active session on {expected}. Exiting..."

    @pytest.mark.critical
    def test_unmapped_jellyfin_session_exits(self):
        app = self._app([], ActiveSessions(1, []), exit_if_active=True)

        with pytest.raises(SystemExit):
            app._check_active_sessions()

    def test_no_sessions_does_nothing(self):
        app = self._app([], ActiveSessions(0, []), exit_if_active=True)

        app._check_active_sessions()

        assert app.files_to_skip == []

    def test_jellyfin_only_skips_are_logged(self, caplog):
        app = self._app([], ActiveSessions(1, ["/data/j.mkv"]))

        with caplog.at_level(logging.INFO):
            app._check_active_sessions()

        assert app.files_to_skip == ["/mnt/j.mkv"]
        assert "[FILTER] Skipped 1 active session(s)" in caplog.text

    def test_both_servers_skipped(self):
        app = self._app(["/data/p.mkv"], ActiveSessions(1, ["/data/j.mkv"]))

        app._check_active_sessions()

        assert app.files_to_skip == ["/mnt/p.mkv", "/mnt/j.mkv"]


class TestConfig:
    def _load(self, tmp_path, monkeypatch, settings, env=None):
        from core.config import ConfigManager
        for key in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_PATH_MAPPINGS", "JELLYFIN_SKIP_USERS",
                    "JELLYFIN_FAVORITES_TOGGLE"):
            monkeypatch.delenv(key, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        cm = ConfigManager(str(tmp_path / "settings.json"))
        cm.settings_data = settings
        cm._load_jellyfin_config()
        return cm.jellyfin

    def test_disabled_by_default(self, tmp_path, monkeypatch):
        assert not self._load(tmp_path, monkeypatch, {}).enabled

    def test_settings_file(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, {
            "jellyfin_url": "http://jf", "jellyfin_api_key": "k",
            "jellyfin_path_mappings": MAPPINGS, "jellyfin_skip_users": ["kid"],
        })
        assert cfg.enabled
        assert cfg.path_mappings == MAPPINGS
        assert cfg.skip_users == ["kid"]

    def test_env_overrides_settings(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_url": "http://file", "jellyfin_api_key": "file"},
                         env={"JELLYFIN_URL": "http://env", "JELLYFIN_API_KEY": "env"})
        assert (cfg.url, cfg.api_key) == ("http://env", "env")

    def test_env_mappings_and_skip_users(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_skip_users": ["file"]}, env={
            "JELLYFIN_PATH_MAPPINGS": '{"/media/en/": "/mnt/user/plex-media/"}',
            "JELLYFIN_SKIP_USERS": "kid, guest",
        })
        assert cfg.path_mappings == {"/media/en/": "/mnt/user/plex-media/"}
        assert cfg.skip_users == ["kid", "guest"]

    @pytest.mark.parametrize("env_value", ["not json", "[]", '"/media"'])
    def test_invalid_env_mappings_keep_settings(self, tmp_path, monkeypatch, env_value):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_path_mappings": MAPPINGS},
                         env={"JELLYFIN_PATH_MAPPINGS": env_value})
        assert cfg.path_mappings == MAPPINGS

    @pytest.mark.parametrize("value", [["/media"], "/media", 5])
    def test_non_object_settings_mappings_are_ignored(self, tmp_path, monkeypatch, value):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_url": "http://jf", "jellyfin_api_key": "k",
                                                 "jellyfin_path_mappings": value})
        assert cfg.path_mappings == {}
        # The manager must still start with what is left
        JellyfinManager(cfg.url, cfg.api_key, path_mappings=cfg.path_mappings, session=FakeSession({}))

    def test_favorites_toggle(self, tmp_path, monkeypatch):
        assert not self._load(tmp_path, monkeypatch, {}).favorites_toggle
        assert self._load(tmp_path, monkeypatch, {"jellyfin_favorites_toggle": True}).favorites_toggle
        assert not self._load(tmp_path, monkeypatch, {"jellyfin_favorites_toggle": True},
                              env={"JELLYFIN_FAVORITES_TOGGLE": "false"}).favorites_toggle
        assert self._load(tmp_path, monkeypatch, {}, env={"JELLYFIN_FAVORITES_TOGGLE": "true"}).favorites_toggle

    @pytest.mark.parametrize("value, expected", [
        ("guest,kid", ["guest", "kid"]),
        (" guest , kid ,", ["guest", "kid"]),
        ("guest", ["guest"]),
        (["guest", " kid "], ["guest", "kid"]),
    ])
    def test_skip_users_string_or_list(self, tmp_path, monkeypatch, value, expected):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_skip_users": value})
        assert cfg.skip_users == expected

    def test_invalid_mapping_entries_are_dropped(self, tmp_path, monkeypatch):
        cfg = self._load(tmp_path, monkeypatch, {"jellyfin_path_mappings": {
            "/media/": "/data/", "/bad/": 5, "": "/data/"}})
        assert cfg.path_mappings == {"/media/": "/data/"}


class TestExportRedaction:
    @pytest.mark.critical
    def test_jellyfin_secrets_redacted_in_export(self):
        from web.services.settings_service import SettingsService
        svc = object.__new__(SettingsService)
        svc._load_raw = lambda: {"jellyfin_url": "http://192.168.1.5:8096", "jellyfin_api_key": "secret",
                                 "jellyfin_path_mappings": MAPPINGS}
        svc._read_pinned_tracker_file = lambda: {}

        redacted = svc.export_settings(include_sensitive=False)
        full = svc.export_settings(include_sensitive=True)

        assert redacted["jellyfin_api_key"] == ""
        assert redacted["jellyfin_url"] == "http://[REDACTED]:8096"
        assert redacted["jellyfin_path_mappings"] == MAPPINGS
        assert full["jellyfin_api_key"] == "secret"
