"""Tell the Web UI when a newer PlexCache-D release is out.

The scheduler asks GitHub for the latest release every 12 hours
(web/services/scheduler_service.py). Pages only read the last answer, so a slow
or unreachable GitHub never delays a page. The only thing sent is a request
for the public release list, with "PlexCache-D/<version>" as the user agent.
Turned off with the update_check setting (Settings > Notifications).

Lives outside web/services, like web/static_assets.py: base.html reads it
through template globals set in web/config.py.
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

REPO_URL = "https://github.com/StudioNirin/PlexCache-D"
LATEST_RELEASE_API = "https://api.github.com/repos/StudioNirin/PlexCache-D/releases/latest"
CHECK_INTERVAL_HOURS = 12
REQUEST_TIMEOUT = 10

_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
_COMMIT = re.compile(r"[0-9a-f]{40}")

_lock = threading.Lock()
_available: Optional[Dict[str, str]] = None


def parse_version(text: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """'v3.3.0' or '3.3' -> (3, 3, 0). None for anything else, pre-releases included."""
    match = _VERSION.fullmatch((text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def is_enabled(settings_file: Path) -> bool:
    """The update_check setting, on unless it is set to false."""
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            return json.load(f).get("update_check", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def fetch_latest_release() -> dict:
    """GitHub's latest full release (drafts and pre-releases are left out by GitHub)."""
    import requests
    from core import __version__

    response = requests.get(LATEST_RELEASE_API, timeout=REQUEST_TIMEOUT, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PlexCache-D/{__version__}",
    })
    response.raise_for_status()
    return response.json()


def check_for_update(current_version: Optional[str] = None,
                     fetch: Optional[Callable[[], dict]] = None) -> Optional[Dict[str, str]]:
    """Ask GitHub once and remember the answer: the newer release, or None.

    A failed request keeps the previous answer and logs at DEBUG, so an install
    without internet access is not warned about it twice a day.
    """
    global _available
    if current_version is None:
        from core import __version__ as current_version
    current = parse_version(current_version)
    try:
        release = (fetch or fetch_latest_release)()
    except Exception as e:
        logging.debug(f"Update check failed: {e}")
        return get_available_update()

    tag = str(release.get("tag_name", ""))
    latest = parse_version(tag)
    newer = None
    if latest and current and latest > current and not release.get("prerelease") and not release.get("draft"):
        newer = {
            "version": ".".join(str(part) for part in latest),
            "url": release.get("html_url") or f"{REPO_URL}/releases/tag/{tag}",
        }
    with _lock:
        _available = newer
    if newer:
        logging.info(f"PlexCache-D {newer['version']} is available: {newer['url']}")
    return dict(newer) if newer else None


def check_in_background() -> None:
    """Run a check now without waiting for it (after the setting is turned on)."""
    threading.Thread(target=check_for_update, name="update-check", daemon=True).start()


def get_available_update() -> Optional[Dict[str, str]]:
    """The newer release found by the last check, or None. Never makes a request."""
    with _lock:
        return dict(_available) if _available else None


def clear() -> None:
    """Forget the last answer (the setting was turned off)."""
    global _available
    with _lock:
        _available = None


def version_url(version: str, test_build: bool, commit: Optional[str]) -> str:
    """Where the sidebar version links to.

    A test build (:dev or a pre-release) links to the commit it was built from,
    since its version number may not have a release yet. Anything else links to
    the release notes for its version.
    """
    if test_build and commit and _COMMIT.fullmatch(commit):
        return f"{REPO_URL}/commit/{commit}"
    return f"{REPO_URL}/releases/tag/v{version}"
