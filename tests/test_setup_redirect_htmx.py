"""Setup redirect for HTMX requests.

When setup becomes incomplete while a page is open (e.g. the Plex token is
cleared), the banner keeps polling /api/operation-banner. A plain redirect was
followed by the browser and htmx swapped the whole Setup page into the pill,
on top of the current page. HTMX requests now get HX-Redirect so the page
navigates, as auth_middleware already does for login.
"""

import sys
import types
from unittest.mock import MagicMock, patch

for _mod in ("fcntl", "pwd", "grp"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))
for _mod in ["apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
             "apscheduler.triggers", "apscheduler.triggers.cron", "apscheduler.triggers.interval"]:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    for name in list(sys.modules):
        if (name == "web" or name.startswith("web.")) and isinstance(sys.modules[name], MagicMock):
            del sys.modules[name]
    import web.config  # noqa: F401
    import web.main  # noqa: F401


_force_real_modules()

from fastapi.testclient import TestClient  # noqa: E402


def _client():
    import web.main as main
    return TestClient(main.app), main


def test_htmx_request_gets_hx_redirect_not_a_page():
    client, main = _client()
    with patch.object(main.setup, "is_setup_complete", return_value=False):
        r = client.get("/api/operation-banner", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/setup"
    assert r.text == ""


def test_normal_navigation_still_redirects():
    client, main = _client()
    with patch.object(main.setup, "is_setup_complete", return_value=False):
        r = client.get("/settings/cache", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/setup"
