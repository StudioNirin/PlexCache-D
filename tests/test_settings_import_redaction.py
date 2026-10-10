"""Settings Import/Export: redacted exports, import safety, validation.

An export "without sensitive data" replaces secrets with placeholders
("http://[REDACTED]:32400", blank tokens, anonymised users). Importing one,
even in Merge mode, wrote those placeholders over the real values: the Plex
token went blank (the app fell back to Setup) and the login password hash
became "[REDACTED]". Imports now keep the current installation's values for
everything a redacted export hides.
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ["fcntl", "pwd", "grp", 'apscheduler', 'apscheduler.schedulers',
             'apscheduler.schedulers.background', 'apscheduler.triggers',
             'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
             'plexapi', 'plexapi.server']:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    for name in ["web.config", "web.services", "web.services.settings_service", "web"]:
        if isinstance(sys.modules.get(name), MagicMock):
            del sys.modules[name]
    import web.config  # noqa: F401
    import web.services.settings_service  # noqa: F401


_force_real_modules()

ROOT = Path(__file__).resolve().parents[1]

CURRENT = {
    "PLEX_URL": "http://192.168.1.10:32400",
    "PLEX_TOKEN": "real-plex-token",
    "plexcache_client_id": "client-123",
    "remote_watchlist_rss_url": "https://rss.plex.tv/abcdef-guid",
    "webhook_url": "https://discord.com/api/webhooks/1/secret",
    "auth_enabled": True,
    "auth_password_username": "admin",
    "auth_password_hash": "hash-value",
    "auth_password_salt": "salt-value",
    "auth_admin_plex_id": 42,
    "auth_admin_username": "owner",
    "api_key": "api-key-value",
    "users": [
        {"title": "owner", "username": "owner", "is_admin": True, "is_local": True},
        {"title": "Alex", "username": "alex_plex", "is_local": False, "token": "alex-token", "uuid": "u-1"},
    ],
    "arr_instances": [
        {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": "sonarr-key", "enabled": True},
        {"name": "Radarr", "type": "radarr", "url": "http://radarr:7878", "api_key": "radarr-key", "enabled": True},
    ],
    "days_to_monitor": 30,
    "cache_limit": "75%",
}


@pytest.fixture
def svc(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps(CURRENT, indent=2), encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path), \
         patch("web.dependencies.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        service = SettingsService()
        with patch.object(service, "invalidate_plex_cache"):
            yield service


# -- export -------------------------------------------------------------------

def test_redacted_export_hides_secrets(svc):
    out = svc.export_settings(include_sensitive=False)
    text = json.dumps(out)
    for secret in ("real-plex-token", "192.168.1.10", "client-123", "abcdef-guid", "discord.com",
                   "hash-value", "salt-value", "api-key-value", "alex-token", "alex_plex",
                   "sonarr-key", "radarr-key"):
        assert secret not in text, secret
    assert "api_key" not in out
    assert out["_redacted_export"] is True


def test_full_export_keeps_everything_and_has_no_marker(svc):
    out = svc.export_settings(include_sensitive=True)
    assert out["api_key"] == "api-key-value" and out["PLEX_TOKEN"] == "real-plex-token"
    assert "_redacted_export" not in out


# -- import -------------------------------------------------------------------

@pytest.mark.parametrize("merge", [True, False])
def test_importing_a_redacted_export_keeps_current_secrets(svc, merge):
    shared = svc.export_settings(include_sensitive=False)
    shared["days_to_monitor"] = 45                      # an ordinary setting still applies
    result = svc.import_settings(shared, merge=merge)
    assert result["success"] and result["redacted"]

    raw = svc._load_raw()
    for key in ("PLEX_URL", "PLEX_TOKEN", "plexcache_client_id", "remote_watchlist_rss_url",
                "webhook_url", "auth_password_username", "auth_password_hash", "auth_password_salt",
                "auth_admin_plex_id", "auth_admin_username", "api_key", "users"):
        assert raw[key] == CURRENT[key], key
    assert [i["api_key"] for i in raw["arr_instances"]] == ["sonarr-key", "radarr-key"]
    assert raw["days_to_monitor"] == 45
    assert "_redacted_export" not in raw
    assert "[REDACTED]" not in json.dumps(raw)


def test_legacy_redacted_export_without_marker_is_recognised(svc):
    shared = svc.export_settings(include_sensitive=False)
    del shared["_redacted_export"]                      # exports made before the marker
    svc.import_settings(shared, merge=True)
    raw = svc._load_raw()
    assert raw["PLEX_TOKEN"] == "real-plex-token"
    assert raw["auth_password_hash"] == "hash-value"


def test_full_backup_round_trips_exactly(svc):
    backup = svc.export_settings(include_sensitive=True)
    svc.import_settings(dict(backup, days_to_monitor=99), merge=True)
    result = svc.import_settings(backup, merge=False)
    assert result["success"] and not result["redacted"]
    assert svc._load_raw() == CURRENT


def test_new_arr_instance_in_redacted_import_has_no_key(svc):
    shared = svc.export_settings(include_sensitive=False)
    shared["arr_instances"].append({"name": "Sonarr 4K", "type": "sonarr", "url": "http://sonarr4k:8989",
                                    "api_key": "", "enabled": True})
    svc.import_settings(shared, merge=True)
    keys = [i["api_key"] for i in svc._load_raw()["arr_instances"]]
    assert keys == ["sonarr-key", "radarr-key", ""]


# -- validation -----------------------------------------------------------------

def test_validation_of_a_full_backup_has_no_false_warnings(svc):
    report = svc.validate_import_settings(svc.export_settings(include_sensitive=True))
    assert report["valid"]
    joined = " ".join(report["warnings"])
    assert "Unknown settings" not in joined
    assert "cache_limit" not in joined                   # 75% is valid


def test_validation_explains_a_redacted_export(svc):
    report = svc.validate_import_settings(svc.export_settings(include_sensitive=False))
    joined = " ".join(report["warnings"])
    assert "exported without sensitive data" in joined
    assert "Missing PLEX_TOKEN" not in joined and "missing tokens" not in joined


def test_validation_flags_unreadable_sizes(svc):
    report = svc.validate_import_settings({"PLEX_URL": "u", "PLEX_TOKEN": "t", "cache_limit": "banana"})
    assert any(w.startswith("cache_limit:") for w in report["warnings"])


def test_every_real_settings_key_is_known():
    """Keys the engine reads or the app writes must not be reported as unknown."""
    from web.services.settings_service import KNOWN_SETTINGS_KEYS
    config_src = (ROOT / "core" / "config.py").read_text(encoding="utf-8")
    keys = set(re.findall(r"settings_data\.get\(['\"]([A-Za-z_]+)['\"]", config_src))
    for rel in ("web/services/settings_service.py", "web/services/auth_service.py",
                "web/services/scheduler_service.py", "web/routers/settings.py", "web/routers/setup.py",
                "core/config.py", "core/app.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        keys |= set(re.findall(
            r"(?:raw|settings|data|settings_data|save_data|self\.settings_data|current)"
            r"\[\s*['\"]([A-Za-z_]+)['\"]\s*\]\s*=", src))
    service_src = (ROOT / "web" / "services" / "settings_service.py").read_text(encoding="utf-8")
    keys |= set(re.findall(r'"\w+":\s*\("([A-Za-z_]+)",', service_src))   # save_* field mappings
    missing = sorted(keys - KNOWN_SETTINGS_KEYS)
    assert not missing, f"Add to KNOWN_SETTINGS_KEYS: {missing}"
