"""Setup wizard path suggestions and checks (#246).

A reporter installed from Community Apps, skipped the optional path mapping
section and ended up with the cache in a different share from the media
(/mnt/user/Media/... cached to /mnt/cache/plexcache/...). Plex couldn't see the
cached copies, and turning on "Create symlinks" to patch that left links
behind.

Two things let that through:
- The wizard only recognised Plex paths starting with lowercase /data/ or
  /media/. Their Plex paths start with /Media/, so it filled the real and
  cache paths with the Plex path itself.
- Nothing checked the result. The share check only ran in Settings and the
  run log, after setup.

Now the suggestion comes from the folders that exist under /mnt/user, the cache
path follows the real path, and step 3 stops to show any mapping whose real
path is missing or whose cache path is in another share.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("fcntl", "pwd", "grp"):
    sys.modules.setdefault(_mod, MagicMock())
for _mod in ['apscheduler', 'apscheduler.schedulers', 'apscheduler.schedulers.background',
             'apscheduler.triggers', 'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
             'plexapi', 'plexapi.server']:
    sys.modules.setdefault(_mod, MagicMock())


def _force_real_modules():
    for name in list(sys.modules):
        if (name == "web" or name.startswith("web.")) and isinstance(sys.modules[name], MagicMock):
            del sys.modules[name]
    import web.config  # noqa: F401


_force_real_modules()

from core.system_utils import suggest_mapping_paths  # noqa: E402


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@pytest.fixture
def shares(tmp_path):
    root = tmp_path / "user"
    root.mkdir()
    return root


class TestSuggestMappingPaths:

    def test_share_mounted_under_its_own_name(self, shares):
        """The #246 layout: Plex mounts /mnt/user/Media as /Media."""
        (shares / "Media" / "Movies").mkdir(parents=True)
        real, cache = suggest_mapping_paths("/Media/Movies", "/mnt/cache", str(shares))
        assert _same(real, str(shares / "Media" / "Movies"))
        assert cache == "/mnt/cache/Media/Movies/"
        assert real.endswith("/")

    def test_whole_user_folder_mounted_as_data(self, shares):
        (shares / "Movies").mkdir()
        real, cache = suggest_mapping_paths("/data/Movies/", "/mnt/cache/", str(shares))
        assert _same(real, str(shares / "Movies"))
        assert cache == "/mnt/cache/Movies/"

    def test_share_named_data(self, shares):
        """A share called "data" mounted as /data. The old prefix swap dropped the share."""
        (shares / "data" / "media" / "movies").mkdir(parents=True)
        real, cache = suggest_mapping_paths("/data/media/movies/", "/mnt/cache", str(shares))
        assert _same(real, str(shares / "data" / "media" / "movies"))
        assert cache == "/mnt/cache/data/media/movies/"

    def test_case_differs_from_the_share(self, shares):
        """Plex mounts the Media share as lowercase /media."""
        (shares / "Media" / "Movies").mkdir(parents=True)
        real, cache = suggest_mapping_paths("/media/Movies/", "/mnt/cache", str(shares))
        assert _same(real, str(shares / "Media" / "Movies"))
        assert os.path.normcase(cache) == os.path.normcase("/mnt/cache/Media/Movies/")

    @pytest.mark.skipif(os.name == "nt", reason="Plex paths are POSIX; a drive letter can't stand in")
    def test_plex_already_uses_user_share_paths(self, shares):
        (shares / "Media" / "TV").mkdir(parents=True)
        plex = str(shares / "Media" / "TV").replace("\\", "/")
        root = str(shares).replace("\\", "/")
        real, cache = suggest_mapping_paths(plex, "/mnt/cache", root)
        assert _same(real, plex)
        assert cache == "/mnt/cache/Media/TV/"

    def test_cache_path_always_follows_the_real_path(self, shares):
        (shares / "Media" / "Anime").mkdir(parents=True)
        real, cache = suggest_mapping_paths("/Media/Anime", "/mnt/cache_media", str(shares))
        assert cache == "/mnt/cache_media/Media/Anime/"

    def test_falls_back_to_prefix_swap_without_user_shares(self, tmp_path):
        missing = str(tmp_path / "no-shares")
        real, cache = suggest_mapping_paths("/media/Movies/", "/mnt/cache", missing)
        assert real == missing + "/Movies/"
        assert cache == "/mnt/cache/Movies/"

    def test_unknown_layout_without_user_shares_is_left_alone(self, tmp_path):
        real, cache = suggest_mapping_paths("/Volumes/Films/", "/mnt/cache", str(tmp_path / "x"))
        assert real == "/Volumes/Films/"


@pytest.fixture
def wizard(tmp_path, shares):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({}, indent=2), encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        from web.routers import setup as setup_router
        svc = SettingsService()
        app = FastAPI()
        app.include_router(setup_router.router)
        setup_router._setup_state.clear()
        setup_router._setup_state["_cached_libraries"] = [
            {"id": 1, "title": "Movies", "type": "movie", "type_label": "Movies",
             "locations": ["/Media/Movies"]},
        ]
        with patch("web.routers.setup.get_settings_service", return_value=svc), \
             patch("web.routers.setup.USER_SHARES_ROOT", str(shares)):
            yield TestClient(app), setup_router
        setup_router._setup_state.clear()


def _manual(real_path, cache_path, name="Movies"):
    return {
        "mapping_name_0": name,
        "mapping_plex_path_0": "/Media/Movies/",
        "mapping_real_path_0": real_path,
        "mapping_cache_path_0": cache_path,
        "mapping_cacheable_0": "on",
    }


class TestStep3:

    def test_skipped_mappings_are_filled_in_from_the_shares(self, wizard, shares):
        client, setup_router = wizard
        (shares / "Media" / "Movies").mkdir(parents=True)
        r = client.post("/setup/step3", data={"libraries": "1", "library_cacheable_1": "on",
                                              "cache_dir": "/mnt/cache"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/setup?step=4"
        mapping = setup_router._setup_state["path_mappings"][0]
        assert _same(mapping["real_path"], str(shares / "Media" / "Movies"))
        assert mapping["cache_path"] == "/mnt/cache/Media/Movies/"
        assert mapping["host_cache_path"] == "/mnt/cache/Media/Movies/"

    def test_cache_in_another_share_stops_on_step3(self, wizard, tmp_path):
        """The reporter's mapping. User shares aren't mounted here, so only the share check runs."""
        client, setup_router = wizard
        with patch("web.routers.setup.USER_SHARES_ROOT", str(tmp_path / "not-mounted")):
            r = client.post("/setup/step3", data={
                "libraries": "1", "cache_dir": "/mnt/cache",
                **_manual("/mnt/user/Media/Movies/", "/mnt/cache/plexcache/Movies/"),
            }, follow_redirects=False)
        assert r.status_code == 200
        assert 'id="path-problems"' in r.text
        assert "plexcache" in r.text and "different share" in r.text
        assert 'name="confirm_paths"' in r.text
        # The rows come back filled in, so they can be corrected.
        assert 'value="/mnt/cache/plexcache/Movies/"' in r.text

    def test_confirming_continues_with_the_paths_as_entered(self, wizard, tmp_path):
        client, setup_router = wizard
        with patch("web.routers.setup.USER_SHARES_ROOT", str(tmp_path / "not-mounted")):
            r = client.post("/setup/step3", data={
                "libraries": "1", "cache_dir": "/mnt/cache", "confirm_paths": "on",
                **_manual("/mnt/user/Media/Movies/", "/mnt/cache/plexcache/Movies/"),
            }, follow_redirects=False)
        assert r.status_code == 303
        assert setup_router._setup_state["path_mappings"][0]["cache_path"] == "/mnt/cache/plexcache/Movies/"

    def test_missing_real_path_stops_on_step3(self, wizard, shares):
        client, _ = wizard
        r = client.post("/setup/step3", data={
            "libraries": "1", "cache_dir": "/mnt/cache",
            **_manual(str(shares / "Nope" / "Movies"), "/mnt/cache/Nope/Movies/"),
        }, follow_redirects=False)
        assert r.status_code == 200
        assert "doesn&#39;t exist" in r.text or "doesn't exist" in r.text

    def test_correct_manual_mapping_goes_straight_through(self, wizard, shares):
        client, _ = wizard
        (shares / "Media" / "Movies").mkdir(parents=True)
        r = client.post("/setup/step3", data={
            "libraries": "1", "cache_dir": "/mnt/cache",
            **_manual(str(shares / "Media" / "Movies"), "/mnt/cache/Media/Movies/"),
        }, follow_redirects=False)
        assert r.status_code == 303

    def test_mapping_name_field_appears_once(self, wizard, tmp_path):
        """A hidden copy of the name came first in the form, so renaming a mapping did nothing."""
        client, setup_router = wizard
        with patch("web.routers.setup.USER_SHARES_ROOT", str(tmp_path / "not-mounted")):
            r = client.post("/setup/step3", data={
                "libraries": "1", "cache_dir": "/mnt/cache",
                **_manual("/mnt/user/Media/Movies/", "/mnt/cache/plexcache/Movies/"),
            }, follow_redirects=False)
        assert r.text.count('name="mapping_name_0"') == 1


class TestSymlinkToggleOnUnraid:

    @pytest.fixture
    def cache_page(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import web.config as web_config

        def render(is_unraid, use_symlinks):
            settings_file = tmp_path / "plexcache_settings.json"
            settings_file.write_text(json.dumps({"PLEX_URL": "http://x:32400", "PLEX_TOKEN": "t",
                                                 "use_symlinks": use_symlinks}, indent=2),
                                     encoding="utf-8")
            with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
                 patch("web.services.settings_service.DATA_DIR", tmp_path), \
                 patch.dict(web_config.templates.env.globals, {"is_unraid": is_unraid}):
                from web.services.settings_service import SettingsService
                from web.routers import settings as settings_router
                app = FastAPI()
                app.include_router(settings_router.router, prefix="/settings")
                with patch("web.routers.settings.get_settings_service",
                           return_value=SettingsService()):
                    r = TestClient(app).get("/settings/cache")
            assert r.status_code == 200
            start = r.text.index('data-setting-id="use_symlinks"')
            return r.text[start:start + 1500]

        return render

    def test_off_on_unraid_is_disabled(self, cache_page):
        block = cache_page(is_unraid=True, use_symlinks=False)
        assert "disabled" in block.split("</label>")[0]
        assert "Not used on Unraid" in block

    def test_on_on_unraid_stays_editable_and_warns(self, cache_page):
        block = cache_page(is_unraid=True, use_symlinks=True)
        assert "disabled" not in block.split("</label>")[0]
        assert "Unraid\n                doesn&#39;t need it" in block or "doesn't need it" in block

    def test_other_systems_unchanged(self, cache_page):
        block = cache_page(is_unraid=False, use_symlinks=False)
        assert "disabled" not in block.split("</label>")[0]
        assert "For non-Unraid systems" in block
