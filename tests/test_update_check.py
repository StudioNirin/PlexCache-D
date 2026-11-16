"""The page footer is gone; the sidebar version links to its release notes and
says when a newer release is out (web/update_check.py, issue #243).

The footer showed "PlexCache-D Web UI v0.3.0", the web package's internal
version, which never changed and read as a different version from the one in
the sidebar. GitHub is asked for the latest release by a scheduler job; a page
only reads the last answer, so a slow GitHub never delays it.
"""

import json
import re
from pathlib import Path

import pytest

from web import update_check

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_state():
    update_check.clear()
    yield
    update_check.clear()


def _release(tag, **extra):
    return lambda: {"tag_name": tag, "html_url": f"{update_check.REPO_URL}/releases/tag/{tag}", **extra}


# -- versions -------------------------------------------------------------------

@pytest.mark.parametrize("text,parsed", [
    ("v3.3.0", (3, 3, 0)), ("3.3.0", (3, 3, 0)), ("3.10", (3, 10, 0)), (" v4.0.1 ", (4, 0, 1)),
    ("v3.4.0-beta1", None), ("beta-3.0", None), ("", None), (None, None),
])
def test_parse_version(text, parsed):
    assert update_check.parse_version(text) == parsed


# -- the check --------------------------------------------------------------------

def test_a_newer_release_is_remembered():
    found = update_check.check_for_update("3.3.0", fetch=_release("v3.3.1"))
    assert found == {"version": "3.3.1", "url": f"{update_check.REPO_URL}/releases/tag/v3.3.1"}
    assert update_check.get_available_update() == found


@pytest.mark.parametrize("tag", ["v3.3.0", "v3.2.9", "not-a-version"])
def test_same_older_or_unreadable_release_is_not_an_update(tag):
    assert update_check.check_for_update("3.3.0", fetch=_release(tag)) is None
    assert update_check.get_available_update() is None


@pytest.mark.parametrize("flag", ["prerelease", "draft"])
def test_pre_releases_and_drafts_are_not_offered(flag):
    assert update_check.check_for_update("3.3.0", fetch=_release("v3.4.0", **{flag: True})) is None


def test_a_failed_check_keeps_the_last_answer():
    update_check.check_for_update("3.3.0", fetch=_release("v3.3.1"))

    def offline():
        raise OSError("no route to host")

    assert update_check.check_for_update("3.3.0", fetch=offline)["version"] == "3.3.1"
    assert update_check.get_available_update()["version"] == "3.3.1"


def test_installing_the_update_clears_the_notice():
    update_check.check_for_update("3.3.0", fetch=_release("v3.3.1"))
    update_check.check_for_update("3.3.1", fetch=_release("v3.3.1"))
    assert update_check.get_available_update() is None


def test_callers_get_a_copy():
    update_check.check_for_update("3.3.0", fetch=_release("v3.3.1"))
    update_check.get_available_update()["version"] = "changed"
    assert update_check.get_available_update()["version"] == "3.3.1"


def test_the_request_carries_nothing_about_the_install(monkeypatch):
    import requests
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v3.3.0"}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    update_check.fetch_latest_release()
    assert seen["url"] == "https://api.github.com/repos/StudioNirin/PlexCache-D/releases/latest"
    assert set(seen["headers"]) == {"Accept", "User-Agent"}
    assert re.fullmatch(r"PlexCache-D/[\d.]+", seen["headers"]["User-Agent"])
    assert "params" not in seen and seen["timeout"] == update_check.REQUEST_TIMEOUT


# -- the setting ------------------------------------------------------------------

@pytest.mark.parametrize("content,enabled", [
    (None, True), ("{}", True), ('{"update_check": true}', True), ('{"update_check": false}', False),
    ("not json", True),
])
def test_the_setting_is_on_unless_turned_off(tmp_path, content, enabled):
    settings = tmp_path / "plexcache_settings.json"
    if content is not None:
        settings.write_text(content, encoding="utf-8")
    assert update_check.is_enabled(settings) is enabled


def test_the_setting_is_a_known_key_with_a_search_entry():
    service = (ROOT / "web" / "services" / "settings_service.py").read_text(encoding="utf-8")
    keys = service[service.index("KNOWN_SETTINGS_KEYS"):]
    assert '"update_check"' in keys[:keys.index("})")]
    assert '"setting_id": "update_check"' in (ROOT / "web" / "settings_search_index.py").read_text(encoding="utf-8")
    notifications = (ROOT / "web" / "templates" / "settings" / "notifications.html").read_text(encoding="utf-8")
    assert 'data-setting-id="update_check"' in notifications and 'name="update_check" value="true"' in notifications


def test_the_scheduler_checks_only_when_the_setting_is_on(tmp_path, monkeypatch):
    pytest.importorskip("apscheduler")
    from web.services import scheduler_service
    settings = tmp_path / "plexcache_settings.json"
    monkeypatch.setattr(scheduler_service, "SETTINGS_FILE", settings)
    calls = []
    monkeypatch.setattr(update_check, "check_for_update", lambda: calls.append("check"))
    service = scheduler_service.SchedulerService.__new__(scheduler_service.SchedulerService)

    settings.write_text('{"update_check": true}', encoding="utf-8")
    service._check_for_update()
    assert calls == ["check"]

    update_check._available = {"version": "9.9.9", "url": "x"}
    settings.write_text('{"update_check": false}', encoding="utf-8")
    service._check_for_update()
    assert calls == ["check"] and update_check.get_available_update() is None


# -- where the version links -------------------------------------------------------

SHA = "e426fa91a4d6d6055c1056c4a050847ed7c8fe60"


@pytest.mark.parametrize("test_build,commit,url", [
    (False, SHA, "/releases/tag/v3.3.0"),
    (False, None, "/releases/tag/v3.3.0"),
    (True, SHA, f"/commit/{SHA}"),
    (True, "unknown", "/releases/tag/v3.3.0"),
    (True, None, "/releases/tag/v3.3.0"),
])
def test_version_url(test_build, commit, url):
    assert update_check.version_url("3.3.0", test_build, commit) == update_check.REPO_URL + url


# -- the page ---------------------------------------------------------------------

def test_the_footer_is_gone():
    assert "<footer" not in BASE and "web_version" not in BASE
    for css in ("plex-theme.css", "custom.css"):
        text = re.sub(r"/\*.*?\*/", "", (ROOT / "web" / "static" / "css" / css).read_text(encoding="utf-8"), flags=re.S)
        assert not re.search(r"(^|[\s,}\]])footer(\s*[{,]|\s+[.>\w])", text), f"{css} still styles <footer>"


def test_the_sidebar_links_the_version_and_shows_an_update():
    brand = BASE[BASE.index('<div class="sidebar-brand">'):BASE.index('<ul class="sidebar-nav">')]
    assert '<a href="{{ version_url }}" target="_blank" rel="noopener"' in brand
    assert "{% set update = available_update() %}" in BASE
    assert 'class="sidebar-update" href="{{ update.url }}"' in brand
    assert "has-update" in brand
    # Phones hide the sidebar's brand row, so the header carries it there
    header = BASE[BASE.index('<header class="mobile-header">'):BASE.index("</header>")]
    assert 'class="mobile-update" href="{{ update.url }}"' in header
    assert BASE.index("{% set update = available_update() %}") < BASE.index('<header class="mobile-header">')
