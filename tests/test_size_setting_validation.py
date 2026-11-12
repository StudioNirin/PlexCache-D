"""Size settings (Cache Drive Size, Cache Limit, Min Free Space, PlexCache Quota).

The engine reads an unparseable size as "no limit", so the Cache tab saved
"banana" with a success message and the cap silently did nothing. A negative
size was worse: callers treat negative bytes as a percentage, so "-5GB" Min
Free Space became billions of percent and stopped all caching. The Settings
page now refuses both, and parse_size_bytes() never returns a negative.
"""

import sys
from unittest.mock import MagicMock

import pytest

for _mod in ("fcntl", "pwd", "grp"):
    sys.modules.setdefault(_mod, MagicMock())

from core.system_utils import parse_size_bytes, size_setting_error  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("500GB", 500 * 1024**3), ("1.5T", int(1.5 * 1024**4)), ("250", 250 * 1024**3),
    ("100MB", 100 * 1024**2), (" 2 tb ", 2 * 1024**4), ("", 0), ("0", 0),
    ("banana", 0), ("-5GB", 0), ("-1", 0),
])
def test_parse_size_bytes(text, expected):
    assert parse_size_bytes(text) == expected


@pytest.mark.parametrize("text", ["", "0", "500GB", "1.5t", "250", "2 TB", "100mb", "75%", "7.5%", "100%"])
def test_valid_sizes(text):
    assert size_setting_error(text) is None


@pytest.mark.parametrize("text", ["banana", "-5GB", "5 GiB", "150%", "0%", "-5%", "abc%", "GB"])
def test_invalid_sizes(text):
    assert size_setting_error(text)


def test_percent_only_where_allowed():
    assert size_setting_error("75%", allow_percent=False)
    assert size_setting_error("500GB", allow_percent=False) is None


# -- Cache tab save -----------------------------------------------------------

def _force_real_modules():
    for name in ["web.config", "web.routers", "web.routers.settings",
                 "web.services", "web.services.settings_service", "web"]:
        if isinstance(sys.modules.get(name), MagicMock):
            del sys.modules[name]
    import importlib
    import web.config
    import web.routers.settings
    # A router imported earlier, while web.config was a mock, keeps that mock
    # as its `templates`; reload it against the real one.
    if web.routers.settings.templates is not web.config.templates:
        importlib.reload(web.routers.settings)


for _mod in ['apscheduler', 'apscheduler.schedulers', 'apscheduler.schedulers.background',
             'apscheduler.triggers', 'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
             'plexapi', 'plexapi.server']:
    sys.modules.setdefault(_mod, MagicMock())
_force_real_modules()


@pytest.fixture
def client(tmp_path):
    import json
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({"PLEX_URL": "http://x:32400", "PLEX_TOKEN": "t",
                                         "cache_limit": "500GB"}, indent=2), encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        from web.routers import settings as settings_router
        svc = SettingsService()
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        with patch("web.routers.settings.get_settings_service", return_value=svc):
            yield TestClient(app), svc


def test_cache_save_refuses_unreadable_sizes(client):
    test_client, svc = client
    r = test_client.put("/settings/cache", data={"cache_limit": "banana", "min_free_space": "-5GB",
                                                 "cache_drive_size": "75%", "days_to_monitor": "30"})
    assert "Not saved." in r.text
    assert "Cache Limit" in r.text and "Min Free Space" in r.text and "Cache Drive Size" in r.text
    raw = svc._load_raw()
    assert raw["cache_limit"] == "500GB"          # unchanged
    assert "days_to_monitor" not in raw            # nothing from that form was saved


def test_cache_save_accepts_valid_sizes(client):
    test_client, svc = client
    r = test_client.put("/settings/cache", data={"cache_limit": "75%", "min_free_space": "50GB",
                                                 "plexcache_quota": "1.5T", "cache_drive_size": ""})
    assert "Not saved" not in r.text
    raw = svc._load_raw()
    assert (raw["cache_limit"], raw["min_free_space"], raw["plexcache_quota"]) == ("75%", "50GB", "1.5T")
