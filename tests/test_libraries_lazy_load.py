"""The Libraries tab when Plex is slow or down.

The tab asked Plex for the library list before drawing anything, so a slow
Plex held up the page, and an unreachable one cost a connection timeout (up to
10s) on every visit. Now:

- the page renders at once; when the list has to come from Plex, a skeleton
  stands in and GET /settings/libraries/cards loads the cards;
- a failed fetch is remembered for a minute, so pages that need the list do
  not each wait for another timeout;
- the last good list is kept (across restarts and the hourly refresh) and
  shown with a note that it is not current, why, and a Retry button.
"""

import json
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.modules.setdefault('fcntl', MagicMock())
for _mod in [
    'apscheduler', 'apscheduler.schedulers', 'apscheduler.schedulers.background',
    'apscheduler.triggers', 'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server',
]:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    for name in ["web.config", "web.routers", "web.routers.settings",
                 "web.services", "web.services.settings_service", "web"]:
        mod = sys.modules.get(name)
        if isinstance(mod, MagicMock):
            del sys.modules[name]
    import web  # noqa: F401
    import web.config  # noqa: F401
    import web.routers.settings  # noqa: F401
    import web.services.settings_service  # noqa: F401


_force_real_modules()

LIBS = [{"id": 1, "title": "Movies", "type": "movie", "type_label": "Movies", "locations": ["/data/Movies/"]},
        {"id": 2, "title": "TV Shows", "type": "show", "type_label": "TV Shows", "locations": ["/data/TV/"]}]


class _Section:
    def __init__(self, lib):
        self.key, self.title, self.type, self.locations = str(lib["id"]), lib["title"], lib["type"], lib["locations"]


def _plex_up():
    server = MagicMock()
    server.library.sections.return_value = [_Section(lib) for lib in LIBS]
    return MagicMock(return_value=server)


def _plex_down():
    return MagicMock(side_effect=ConnectionError("Connection refused"))


@pytest.fixture
def make_service(tmp_path):
    """A real SettingsService over temp files; cache_file seeds web_plex_cache.json."""
    created = []

    def make(configured=True, cache_file=None):
        settings_file = tmp_path / "plexcache_settings.json"
        settings = {"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t"} if configured else {}
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        if cache_file is not None:
            (tmp_path / "web_plex_cache.json").write_text(json.dumps(cache_file), encoding="utf-8")
        p1 = patch("web.services.settings_service.SETTINGS_FILE", settings_file)
        p2 = patch("web.services.settings_service.DATA_DIR", tmp_path)
        p3 = patch("web.services.settings_service.WEB_PLEX_CACHE_FILE", tmp_path / "web_plex_cache.json")
        for p in (p1, p2, p3):
            p.start()
            created.append(p)
        from web.services.settings_service import SettingsService
        return SettingsService()

    yield make
    for p in reversed(created):
        p.stop()


def _hours_ago(h):
    return (datetime.now() - timedelta(hours=h)).isoformat()


# ── Service ───────────────────────────────────────────────────────

def test_a_failed_fetch_is_remembered_for_a_minute(make_service):
    svc = make_service()
    down = _plex_down()
    with patch("plexapi.server.PlexServer", down):
        assert svc.get_plex_libraries() == []
        assert svc.get_plex_libraries() == []  # no second wait
        assert down.call_count == 1
        svc._plex_libraries_failed_at -= timedelta(seconds=61)
        svc.get_plex_libraries()
        assert down.call_count == 2  # tried again after the minute


def test_the_last_list_is_shown_as_not_current_when_plex_is_down(make_service):
    svc = make_service(cache_file={"cache_time": _hours_ago(2), "libraries_time": _hours_ago(2), "libraries": LIBS})
    with patch("plexapi.server.PlexServer", _plex_down()):
        status = svc.get_plex_libraries_status()
    assert status["libraries"] == LIBS  # kept across a restart although older than an hour
    assert status["stale"] is True
    assert 7100 < (datetime.now() - status["fetched_at"]).total_seconds() < 7300
    assert "Cannot connect to Plex server" in status["error"]


def test_a_successful_fetch_is_current_again(make_service):
    svc = make_service()
    with patch("plexapi.server.PlexServer", _plex_down()):
        assert svc.get_plex_libraries_status()["stale"] is True
    with patch("plexapi.server.PlexServer", _plex_up()):
        status = svc.get_plex_libraries_status(retry=True)  # Retry skips the minute's wait
    assert status["stale"] is False and status["error"] is None
    assert [lib["title"] for lib in status["libraries"]] == ["Movies", "TV Shows"]


def test_the_hourly_refresh_keeps_the_list_when_plex_is_down(make_service):
    """refresh_plex_cache invalidated first, so Plex being down at that moment
    threw the last good list away."""
    svc = make_service()
    with patch("plexapi.server.PlexServer", _plex_up()):
        svc.get_plex_libraries()
    with patch("plexapi.server.PlexServer", _plex_down()):
        svc.refresh_plex_cache()
        assert len(svc.get_plex_libraries()) == 2


def test_fetching_users_does_not_make_an_old_library_list_fresh(make_service):
    svc = make_service(cache_file={"cache_time": _hours_ago(2), "libraries_time": _hours_ago(2), "libraries": LIBS})
    svc._plex_cache_time = datetime.now()  # what a users fetch sets
    assert svc._plex_libraries_fresh() is False


def test_ready_means_no_plex_call_is_needed(make_service):
    fresh = make_service(cache_file={"cache_time": _hours_ago(0), "libraries_time": _hours_ago(0), "libraries": LIBS})
    assert fresh.plex_libraries_ready() is True
    old = make_service(cache_file={"cache_time": _hours_ago(2), "libraries_time": _hours_ago(2), "libraries": LIBS})
    assert old.plex_libraries_ready() is False  # has to ask Plex: lazy-load
    assert make_service(configured=False).plex_libraries_ready() is True  # nothing to ask


# ── Routes ────────────────────────────────────────────────────────

@pytest.fixture
def client_for():
    from web.routers import settings as settings_router

    def make(svc):
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        p = patch("web.routers.settings.get_settings_service", return_value=svc)
        p.start()
        return TestClient(app), p

    started = []

    def wrapped(svc):
        client, p = make(svc)
        started.append(p)
        return client

    yield wrapped
    for p in started:
        p.stop()


def test_page_renders_at_once_with_a_skeleton_when_plex_must_be_asked(make_service, client_for):
    svc = make_service(cache_file={"cache_time": _hours_ago(2), "libraries_time": _hours_ago(2), "libraries": LIBS})
    plex = _plex_down()
    with patch("plexapi.server.PlexServer", plex):
        page = client_for(svc).get("/settings/libraries").text
    assert plex.call_count == 0  # the page did not wait for Plex
    assert 'hx-get="/settings/libraries/cards" hx-trigger="load"' in page
    assert 'id="library-card-1"' not in page


def test_page_has_the_cards_when_the_list_is_fresh(make_service, client_for):
    svc = make_service(cache_file={"cache_time": _hours_ago(0), "libraries_time": _hours_ago(0), "libraries": LIBS})
    page = client_for(svc).get("/settings/libraries").text
    assert 'hx-get="/settings/libraries/cards" hx-trigger="load"' not in page
    assert 'id="library-card-1"' in page and "Couldn't reach Plex" not in page


def test_cards_say_the_list_is_not_current_and_offer_retry(make_service, client_for):
    svc = make_service(cache_file={"cache_time": _hours_ago(2), "libraries_time": _hours_ago(2), "libraries": LIBS})
    with patch("plexapi.server.PlexServer", _plex_down()):
        cards = client_for(svc).get("/settings/libraries/cards").text
    assert "Couldn't reach Plex." in cards and "library list from 2 hr ago" in cards
    assert 'hx-get="/settings/libraries/cards?retry=true"' in cards
    assert 'id="library-card-1"' in cards and 'id="library-card-2"' in cards


def test_cards_explain_when_there_is_no_list_at_all(make_service, client_for):
    svc = make_service()
    with patch("plexapi.server.PlexServer", _plex_down()):
        cards = client_for(svc).get("/settings/libraries/cards").text
    assert "so the library list can't be shown" in cards
    assert "?retry=true" in cards
