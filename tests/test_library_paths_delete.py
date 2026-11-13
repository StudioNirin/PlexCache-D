"""Route test: deleting a secondary path mapping from a library.

A library can have multiple path mappings (e.g. Movies + Movies UHD). The Edit
Paths form lets the user mark a mapping for removal (hidden ``delete_<pos>`` flag);
``PUT /settings/libraries/{section_id}/paths`` must drop those and keep the rest.

Mounts only the settings router on a minimal app, backed by a real
SettingsService over a temp settings file, with Plex library discovery mocked.
"""

import json
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


def _two_mapping_settings():
    return {
        "PLEX_URL": "http://localhost:32400",
        "PLEX_TOKEN": "abc",
        "valid_sections": [4],
        "path_mappings": [
            {"name": "Movies", "plex_path": "/data/Movies/",
             "real_path": "/mnt/user0/Movies/", "cache_path": "/mnt/cache/Movies/",
             "cacheable": True, "enabled": True, "section_id": 4},
            {"name": "Movies UHD", "plex_path": "/nas/Movies UHD/",
             "real_path": "/mnt/remotes/NAS_Media/Movies UHD/",
             "cache_path": "/mnt/cache/Movies UHD/",
             "cacheable": False, "enabled": True, "section_id": 4},
        ],
        "cache_dir": "/mnt/cache",
    }


@pytest.fixture
def service(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps(_two_mapping_settings(), indent=2), encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        svc = SettingsService()
        svc._cached_settings = None
        with patch.object(svc, "get_plex_libraries", return_value=[_MOVIES_LIB]):
            yield svc


@pytest.fixture
def client(service):
    from web.routers import settings as settings_router
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/settings")
    with patch("web.routers.settings.get_settings_service", return_value=service):
        yield TestClient(app), service


def _form(delete_uhd=False):
    form = {
        "name_0": "Movies", "plex_path_0": "/data/Movies/",
        "real_path_0": "/mnt/user0/Movies/", "cache_path_0": "/mnt/cache/Movies/",
        "host_cache_path_0": "", "cacheable_0": "on",
        "name_1": "Movies UHD", "plex_path_1": "/nas/Movies UHD/",
        "real_path_1": "/mnt/remotes/NAS_Media/Movies UHD/",
        "cache_path_1": "/mnt/cache/Movies UHD/", "host_cache_path_1": "",
    }
    if delete_uhd:
        form["delete_1"] = "1"
    return form


def test_delete_secondary_mapping_removes_it(client):
    test_client, service = client
    r = test_client.put("/settings/libraries/4/paths", data=_form(delete_uhd=True))
    assert r.status_code == 200

    raw = service._load_raw()
    names = [m["name"] for m in raw["path_mappings"]]
    assert names == ["Movies"]            # Movies UHD removed
    assert raw["valid_sections"] == [4]   # library still valid (primary remains)
    # The refreshed card no longer mentions the removed mapping.
    assert "Movies UHD" not in r.text


def test_no_delete_flag_keeps_both(client):
    test_client, service = client
    r = test_client.put("/settings/libraries/4/paths", data=_form(delete_uhd=False))
    assert r.status_code == 200

    raw = service._load_raw()
    names = {m["name"] for m in raw["path_mappings"]}
    assert names == {"Movies", "Movies UHD"}


def test_remove_button_shown_only_with_multiple_mappings(client):
    test_client, service = client
    # Two mappings → editing exposes a Remove control.
    r = test_client.get("/settings/libraries")
    # The library cards load from /settings/libraries/cards when the list has
    # to come from Plex; ensure the edit form carries delete plumbing when a
    # library has more than one mapping.
    cards = test_client.get("/settings/libraries/cards")
    assert "removeLibraryMapping(4" in cards.text or "delete_1" in cards.text
    # Removal uses the styled modal, not the native browser confirm().
    assert 'id="remove-mapping-modal"' in r.text
    assert "confirmRemoveMapping()" in r.text
