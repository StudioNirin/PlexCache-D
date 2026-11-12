"""Custom path mappings (not linked to a Plex library) on Settings → Libraries.

Delete used to target #path-mappings, which only existed on the retired Paths
page, so htmx dropped the request. Add targeted a container that was only
rendered once a custom mapping existed. Cards address mappings by list index,
which shifts whenever a mapping is deleted, so they also send the paths they
were rendered with and the server refuses a stale card.

Mounts only the settings router on a minimal app, backed by a real
SettingsService over a temp settings file, with Plex library discovery mocked.
"""

import html
import json
import re
import sys
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

_MOVIES_LIB = {
    "id": 4, "title": "Movies", "type": "movie", "type_label": "Movies",
    "locations": ["/data/Movies/"],
}


def _mapping(name, plex, real, section_id=None):
    m = {"name": name, "plex_path": plex, "real_path": real,
         "cache_path": "/mnt/cache" + real[len("/mnt/user"):],
         "cacheable": True, "enabled": True}
    if section_id is not None:
        m["section_id"] = section_id
    return m


MOVIES = _mapping("Movies", "/data/Movies/", "/mnt/user/Movies/", section_id=4)
MOVIES_UHD = _mapping("Movies UHD", "/data/UHD/", "/mnt/user/UHD/", section_id=4)
MUSIC = _mapping("Music", "/data/Music/", "/mnt/user/Music/")
AUDIOBOOKS = _mapping("Audiobooks", "/data/Books/", "/mnt/user/Books/")


@pytest.fixture
def make_client(tmp_path):
    def make(mappings):
        settings_file = tmp_path / "plexcache_settings.json"
        settings_file.write_text(json.dumps({
            "PLEX_URL": "http://localhost:32400", "PLEX_TOKEN": "abc",
            "valid_sections": [4], "path_mappings": mappings, "cache_dir": "/mnt/cache",
        }, indent=2), encoding="utf-8")
        return settings_file

    patches = []

    def client(mappings):
        settings_file = make(mappings)
        p1 = patch("web.services.settings_service.SETTINGS_FILE", settings_file)
        p2 = patch("web.services.settings_service.DATA_DIR", tmp_path)
        p1.start(); p2.start(); patches.extend([p1, p2])
        from web.services.settings_service import SettingsService
        svc = SettingsService()
        svc._cached_settings = None
        p3 = patch.object(svc, "get_plex_libraries", return_value=[_MOVIES_LIB])
        p3.start(); patches.append(p3)
        from web.routers import settings as settings_router
        p4 = patch("web.routers.settings.get_settings_service", return_value=svc)
        p4.start(); patches.append(p4)
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        return TestClient(app), svc

    yield client
    for p in reversed(patches):
        p.stop()


def _names(svc):
    return [m["name"] for m in svc._load_raw()["path_mappings"]]


def _expected(m):
    return {"expected_plex_path": m["plex_path"], "expected_real_path": m["real_path"]}


def _edit_form(m, **changes):
    form = {"name": m["name"], "plex_path": m["plex_path"], "real_path": m["real_path"],
            "cache_path": m["cache_path"], "cacheable": "on", "enabled": "on", **_expected(m)}
    form.update(changes)
    return form


# -- page wiring ------------------------------------------------------------

def test_every_hx_target_on_the_libraries_page_exists(make_client):
    """The Delete button pointed at an element this page never renders."""
    client, _ = make_client([MOVIES, MOVIES_UHD, MUSIC])
    page = client.get("/settings/libraries").text
    # The alert container lives in settings/index.html's layout.
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    targets = set(re.findall(r'hx-target="#([^"]+)"', page))
    assert targets, "expected hx-target attributes on the page"
    missing = sorted(t for t in targets if t not in ids)
    assert not missing, missing


def test_delete_url_carries_the_expected_paths(make_client):
    """In the URL, not hx-vals: htmx 1.9 sends DELETE values in the body."""
    kids = _mapping("Kids & Family", "/data/Kids & Family/", "/mnt/user/Kids/")
    client, svc = make_client([MOVIES, MUSIC, kids])
    page = client.get("/settings/libraries").text

    def delete_url(index):
        found = re.search(r'hx-delete="(/settings/paths/%d\?[^"]+)"' % index, page)
        return html.unescape(found.group(1))   # what the browser sends

    # The page's own URL deletes the right mapping, '&' in the path included.
    r = client.delete(delete_url(2))
    assert r.status_code == 200
    assert _names(svc) == ["Movies", "Music"]

    # Music's card is now stale-safe too: after removing Music elsewhere,
    # its old URL is refused instead of deleting whatever sits at index 1.
    svc.delete_path_mapping(1)
    svc.add_path_mapping(AUDIOBOOKS)
    r = client.delete(delete_url(1))
    assert "changed since this page loaded" in r.text
    assert _names(svc) == ["Movies", "Audiobooks"]


def test_custom_section_rendered_even_when_empty(make_client):
    client, _ = make_client([MOVIES])
    page = client.get("/settings/libraries").text
    assert 'id="custom-mappings"' in page
    assert 'hx-target="#custom-mappings"' in page          # the Add form
    assert 'id="orphan-mappings"' not in page              # no heading or list without cards


# -- add --------------------------------------------------------------------

def test_add_first_custom_mapping_returns_the_section(make_client):
    client, svc = make_client([MOVIES])
    r = client.post("/settings/paths", data={
        "name": "Music", "plex_path": "/data/Music/", "real_path": "/mnt/user/Music/",
        "cache_path": "/mnt/cache/Music/", "cacheable": "on", "enabled": "on"})
    assert r.status_code == 200
    assert r.text.lstrip().startswith('<div id="custom-mappings"')
    assert "Custom Mappings" in r.text and "Music" in r.text
    assert 'hx-delete="/settings/paths/1?' in r.text
    assert _names(svc) == ["Movies", "Music"]


# -- delete -----------------------------------------------------------------

def test_delete_custom_mapping_rerenders_with_fresh_indices(make_client):
    client, svc = make_client([MUSIC, MOVIES, AUDIOBOOKS])
    r = client.delete("/settings/paths/0", params=_expected(MUSIC))
    assert r.status_code == 200
    assert _names(svc) == ["Movies", "Audiobooks"]
    assert "Music" not in r.text
    # Audiobooks moved from index 2 to 1; the re-rendered card says so.
    assert 'hx-delete="/settings/paths/1?' in r.text
    assert 'hx-delete="/settings/paths/2?' not in r.text
    assert 'id="custom-mappings"' in r.text


def test_delete_last_custom_mapping_leaves_an_empty_section(make_client):
    client, svc = make_client([MOVIES, MUSIC])
    r = client.delete("/settings/paths/1", params=_expected(MUSIC))
    assert _names(svc) == ["Movies"]
    assert 'id="custom-mappings"' in r.text and 'id="orphan-mappings"' not in r.text


def test_stale_delete_is_refused(make_client):
    """Card rendered for Audiobooks at index 2, but the list has since shifted."""
    client, svc = make_client([MOVIES, AUDIOBOOKS])
    r = client.delete("/settings/paths/1", params=_expected(MUSIC))
    assert _names(svc) == ["Movies", "Audiobooks"]
    assert r.headers["HX-Retarget"] == "#settings-alert-container"
    assert "changed since this page loaded" in r.text

    r = client.delete("/settings/paths/5", params=_expected(AUDIOBOOKS))
    assert _names(svc) == ["Movies", "Audiobooks"]
    assert "changed since this page loaded" in r.text


# -- edit -------------------------------------------------------------------

def test_edit_custom_mapping(make_client):
    client, svc = make_client([MOVIES, MUSIC])
    r = client.put("/settings/paths/1", data=_edit_form(MUSIC, name="Music FLAC"))
    assert r.status_code == 200
    assert _names(svc) == ["Movies", "Music FLAC"]


def test_stale_edit_is_refused(make_client):
    """Without the check this would overwrite Audiobooks with Music's form."""
    client, svc = make_client([MOVIES, AUDIOBOOKS])
    r = client.put("/settings/paths/1", data=_edit_form(MUSIC, name="Music FLAC"))
    assert _names(svc) == ["Movies", "Audiobooks"]
    assert r.headers["HX-Retarget"] == "#settings-alert-container"


# -- library card save that removes a mapping --------------------------------

def test_library_save_with_removal_refreshes_custom_cards(make_client):
    client, svc = make_client([MOVIES, MOVIES_UHD, MUSIC])
    form = {
        "name_0": "Movies", "plex_path_0": MOVIES["plex_path"], "real_path_0": MOVIES["real_path"],
        "cache_path_0": MOVIES["cache_path"], "host_cache_path_0": "", "cacheable_0": "on",
        "name_1": "Movies UHD", "plex_path_1": MOVIES_UHD["plex_path"],
        "real_path_1": MOVIES_UHD["real_path"], "cache_path_1": MOVIES_UHD["cache_path"],
        "host_cache_path_1": "", "delete_1": "1",
    }
    r = client.put("/settings/libraries/4/paths", data=form)
    assert _names(svc) == ["Movies", "Music"]
    assert 'id="library-card-4"' in r.text
    # Music moved from index 2 to 1; its card is replaced out-of-band.
    assert '<div id="custom-mappings" hx-swap-oob="true">' in r.text
    assert 'hx-delete="/settings/paths/1?' in r.text


def test_library_save_without_removal_leaves_custom_cards_alone(make_client):
    client, _ = make_client([MOVIES, MUSIC])
    form = {"name_0": "Movies", "plex_path_0": MOVIES["plex_path"], "real_path_0": MOVIES["real_path"],
            "cache_path_0": MOVIES["cache_path"], "host_cache_path_0": "", "cacheable_0": "on"}
    r = client.put("/settings/libraries/4/paths", data=form)
    assert "hx-swap-oob" not in r.text
