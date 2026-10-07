"""
API key access through the real app: auth middleware + /api routes.

With the Web UI login on, an X-Api-Key header opens POST /api/run,
GET /api/status and POST /api/stop, and nothing else. With the login off the
routes stay open as before. Settings come from a temp settings file; the run
trigger and services behind the routes are faked.
"""

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("fcntl", "pwd", "grp"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))
for _mod in ["apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
             "apscheduler.triggers", "apscheduler.triggers.cron", "apscheduler.triggers.interval"]:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    """Several test modules park a MagicMock in sys.modules['web.config']; drop those."""
    for name in list(sys.modules):
        if (name == "web" or name.startswith("web.")) and isinstance(sys.modules[name], MagicMock):
            del sys.modules[name]
    import web.config  # noqa: F401
    import web.main  # noqa: F401


_force_real_modules()

from fastapi.testclient import TestClient  # noqa: E402

KEY = "test-key-0123456789abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def env(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"

    def write(**settings):
        settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    write(auth_enabled=True, api_key=KEY)

    from web.services import auth_service as auth_mod
    with patch.object(auth_mod, "SETTINGS_FILE", settings_file), \
         patch.object(auth_mod.AuthService, "SESSIONS_FILE", str(tmp_path / "sessions.json")):
        auth = auth_mod.AuthService()
        trigger = MagicMock()
        trigger.request.return_value = SimpleNamespace(status="started", message="Run started", run_at=None)
        trigger.cancel_pending.return_value = False
        trigger.pending_info.return_value = None
        runner = MagicMock(is_running=False)
        runner.stop_operation.return_value = True
        runner.get_status_dict.return_value = {"state": "idle"}
        settings_svc = MagicMock()
        settings_svc.check_plex_connection.return_value = True
        scheduler = MagicMock()
        scheduler.get_status.return_value = {"enabled": False}
        cache = MagicMock()
        cache.get_cache_stats.return_value = {}

        import web.main as main
        with patch.object(auth_mod, "get_auth_service", return_value=auth), \
             patch.object(main.setup, "is_setup_complete", return_value=True), \
             patch("web.routers.api.get_run_trigger", return_value=trigger), \
             patch("web.routers.api.get_operation_runner", return_value=runner), \
             patch("web.routers.api.get_settings_service", return_value=settings_svc), \
             patch("web.routers.api.get_scheduler_service", return_value=scheduler), \
             patch("web.routers.api.get_cache_service", return_value=cache):
            yield SimpleNamespace(client=TestClient(main.app), write=write, auth=auth,
                                  trigger=trigger, runner=runner)


# -- login on ---------------------------------------------------------------

def test_valid_key_starts_a_run(env):
    r = env.client.post("/api/run", headers={"X-Api-Key": KEY})
    assert r.status_code == 202
    assert r.json()["status"] == "started" and r.json()["success"] is True
    env.trigger.request.assert_called_once_with(dry_run=False, verbose=False)


def test_valid_key_opens_status_and_stop(env):
    assert env.client.get("/api/status", headers={"X-Api-Key": KEY}).status_code == 200
    r = env.client.post("/api/stop", headers={"X-Api-Key": KEY})
    assert r.status_code == 200 and r.json()["success"] is True
    env.runner.stop_operation.assert_called_once()
    env.trigger.cancel_pending.assert_called_once()


def test_wrong_key_is_rejected_with_json(env):
    r = env.client.post("/api/run", headers={"X-Api-Key": KEY + "x"})
    assert r.status_code == 401
    assert r.json() == {"success": False, "message": "Invalid API key"}
    env.trigger.request.assert_not_called()


def test_no_key_and_no_session_gets_401_json_not_a_login_redirect(env):
    r = env.client.post("/api/run", follow_redirects=False)
    assert r.status_code == 401
    assert "X-Api-Key" in r.json()["message"]


@pytest.mark.parametrize("method, path", [
    ("get", "/"),
    ("get", "/settings/security"),
    ("post", "/operations/run"),
    ("get", "/api/operation-banner"),
    ("post", "/settings/security/api-key"),
    ("get", "/api/run"),
])
def test_key_does_not_open_other_routes(env, method, path):
    r = getattr(env.client, method)(path, headers={"X-Api-Key": KEY}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login")


def test_session_still_works_on_api_routes(env):
    token = env.auth.create_session("1", "admin")
    env.client.cookies.set("plexcache_session", token)
    r = env.client.post("/api/run")
    assert r.status_code == 202


def test_no_key_configured_rejects_any_key(env):
    env.write(auth_enabled=True)
    r = env.client.post("/api/run", headers={"X-Api-Key": KEY})
    assert r.status_code == 401


def test_regenerated_key_replaces_the_old_one(env):
    env.write(auth_enabled=True, api_key="new-key-abcdefghijklmnopqrstuvwxyz0123456789")
    assert env.client.post("/api/run", headers={"X-Api-Key": KEY}).status_code == 401
    assert env.client.post("/api/run", headers={
        "X-Api-Key": "new-key-abcdefghijklmnopqrstuvwxyz0123456789"}).status_code == 202


# -- login off --------------------------------------------------------------

def test_login_off_leaves_routes_open_without_a_key(env):
    env.write(auth_enabled=False)
    assert env.client.post("/api/run").status_code == 202
    assert env.client.get("/api/status").status_code == 200


# -- responses --------------------------------------------------------------

def test_queued_and_blocked_status_codes(env):
    env.trigger.request.return_value = SimpleNamespace(
        status="queued", message="Run queued: starts in 20s", run_at=1_900_000_000.0)
    r = env.client.post("/api/run", headers={"X-Api-Key": KEY})
    assert r.status_code == 202
    assert r.json()["status"] == "queued" and r.json()["run_at"]

    env.trigger.request.return_value = SimpleNamespace(
        status="blocked", message="A maintenance action is in progress", run_at=None)
    r = env.client.post("/api/run", headers={"X-Api-Key": KEY})
    assert r.status_code == 409
    assert r.json()["success"] is False
    assert r.json()["message"] == "A maintenance action is in progress"


def test_run_passes_dry_run_and_verbose(env):
    env.client.post("/api/run?dry_run=true&verbose=true", headers={"X-Api-Key": KEY})
    env.trigger.request.assert_called_once_with(dry_run=True, verbose=True)


def test_stop_with_nothing_running_is_409(env):
    env.runner.stop_operation.return_value = False
    r = env.client.post("/api/stop", headers={"X-Api-Key": KEY})
    assert r.status_code == 409
    assert r.json()["message"] == "No cache run in progress"


def test_status_reports_queued_run(env):
    env.trigger.pending_info.return_value = {"queued": True, "run_at": 1_900_000_000.0,
                                             "dry_run": False, "verbose": False}
    body = env.client.get("/api/status", headers={"X-Api-Key": KEY}).json()
    assert body["queued_run"]["queued"] is True
    assert body["queued_run"]["run_at"]
