"""Sidebar badge text for the Docker image tag (the IMAGE_TAG build arg).

Lives outside web.config, like web/static_assets.py, because several test
modules replace web.config in sys.modules.
"""

import re

_VERSION = re.compile(r"v?\d+(?:\.\d+)*(?:-(?P<suffix>.+))?")


def tag_label(tag: str) -> str:
    """Short badge text for an image tag; an empty string means no badge.

    'dev' -> 'DEV' and 'v3.4.0-beta1' -> 'BETA 1'. 'latest' and a plain
    release version ('3.3.0' or 'v3.3.0') get no badge: the sidebar already
    shows the version, and a stable install should not look like a test build.
    """
    if not tag or tag == "latest":
        return ""
    version = _VERSION.fullmatch(tag)
    if version:
        suffix = version.group("suffix")
        return re.sub(r"(\D+)(\d+)$", r"\1 \2", suffix).upper() if suffix else ""
    return tag.upper()
