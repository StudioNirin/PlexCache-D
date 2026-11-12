"""Settings saves found wrong during a full UI pass.

- Users: the admin's OnDeck/Watchlist toggles render disabled (always
  included), and a disabled checkbox is never submitted, so every save wrote
  skip_ondeck/skip_watchlist = True for the admin.
- Users: a failed Sync replaced the users table with the error.
- Integrations: Add appended a card next to the "none configured" placeholder.
- Schedule: an invalid cron expression saved with "success", then failed when
  the job was created, leaving a schedule that showed Enabled but never ran.
- Import: "Settings merged with successfully".
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

for _mod in ["fcntl", "pwd", "grp", 'apscheduler', 'apscheduler.schedulers',
             'apscheduler.schedulers.background', 'apscheduler.triggers',
             'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
             'plexapi', 'plexapi.server']:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    for name in ["web.config", "web.routers", "web.routers.settings", "web.routers.api",
                 "web.services", "web.services.settings_service", "web"]:
        if isinstance(sys.modules.get(name), MagicMock):
            del sys.modules[name]
    import importlib
    import web.config
    import web.routers.settings
    import web.routers.api
    # A router imported earlier, while web.config was a mock, keeps that mock
    # as its `templates`; reload it against the real one.
    for router in (web.routers.settings, web.routers.api):
        if router.templates is not web.config.templates:
            importlib.reload(router)


_force_real_modules()

USERS = [
    {"title": "owner", "is_admin": True, "is_local": True},
    {"title": "Alex", "is_admin": False, "is_local": True},
]


@pytest.fixture
def client(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({
        "PLEX_URL": "http://x:32400", "PLEX_TOKEN": "t", "users": USERS, "arr_instances": [],
    }, indent=2), encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        from web.routers import settings as settings_router, api as api_router
        svc = SettingsService()
        scheduler = MagicMock()
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        app.include_router(api_router.router, prefix="/api")
        with patch("web.routers.settings.get_settings_service", return_value=svc), \
             patch("web.routers.api.get_scheduler_service", return_value=scheduler):
            yield TestClient(app), svc, scheduler


def _users(svc):
    return {u["title"]: u for u in svc._load_raw()["users"]}


def test_saving_users_keeps_the_admin_included(client):
    test_client, svc, _ = client
    # The browser sends nothing for the admin's disabled toggles.
    test_client.put("/settings/users", data={"users_toggle": "on", "include_ondeck_Alex": "on"})
    users = _users(svc)
    assert users["owner"]["skip_ondeck"] is False and users["owner"]["skip_watchlist"] is False
    assert users["Alex"]["skip_ondeck"] is False and users["Alex"]["skip_watchlist"] is True


def test_failed_sync_keeps_the_users_table(client):
    test_client, svc, _ = client
    with patch.object(svc, "sync_users_from_plex", return_value={"success": False, "error": "Plex unreachable"}):
        r = test_client.post("/settings/users/sync")
    assert "Sync failed: Plex unreachable" in r.text
    assert "alert-error" in r.text
    assert "owner" in r.text and "Alex" in r.text        # the table is still there


def test_adding_an_instance_rerenders_the_list(client):
    test_client, svc, _ = client
    r = test_client.post("/settings/integrations/instances",
                         data={"name": "Sonarr", "arr_type": "sonarr", "url": "http://s:8989",
                               "api_key": "k", "enabled": "on"})
    assert "No Sonarr/Radarr instances configured" not in r.text
    assert "Delete instance &#39;Sonarr&#39;" in r.text or "Delete instance 'Sonarr'" in r.text
    r = test_client.post("/settings/integrations/instances",
                         data={"name": "Radarr", "arr_type": "radarr", "url": "http://r:7878", "api_key": "k"})
    assert "/settings/integrations/instances/0" in r.text and "/settings/integrations/instances/1" in r.text


def test_invalid_cron_is_not_saved(client):
    test_client, _, scheduler = client
    scheduler.validate_cron.return_value = {"valid": False, "message": "Wrong number of fields; got 4, expected 5",
                                            "next_runs": []}
    r = test_client.post("/api/settings/schedule", data={"enabled": "on", "schedule_type": "cron",
                                                         "cron_expression": "99 * * *"})
    assert "Not saved." in r.text and "Wrong number of fields" in r.text
    scheduler.update_config.assert_not_called()


def test_valid_cron_is_saved(client):
    test_client, _, scheduler = client
    scheduler.validate_cron.return_value = {"valid": True, "message": "Valid cron expression", "next_runs": []}
    scheduler.update_config.return_value = {"success": True}
    r = test_client.post("/api/settings/schedule", data={"enabled": "on", "schedule_type": "cron",
                                                         "cron_expression": "15 3 * * 1-5"})
    assert "saved successfully" in r.text
    scheduler.update_config.assert_called_once()


def test_interval_schedule_skips_cron_validation(client):
    test_client, _, scheduler = client
    scheduler.update_config.return_value = {"success": True}
    test_client.post("/api/settings/schedule", data={"enabled": "on", "schedule_type": "interval",
                                                     "interval_hours": "6", "cron_expression": "garbage"})
    scheduler.validate_cron.assert_not_called()
    scheduler.update_config.assert_called_once()


def test_import_message_wording(client):
    test_client, _, _ = client
    backup = json.dumps({"PLEX_URL": "http://x:32400", "PLEX_TOKEN": "t", "days_to_monitor": 7})
    r = test_client.post("/settings/import-export/import", data={"import_mode": "merge"},
                         files={"settings_file": ("backup.json", backup, "application/json")})
    assert "Settings merged successfully" in r.text
