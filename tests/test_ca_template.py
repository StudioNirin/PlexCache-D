"""The Community Apps template (templates/plexcache-d.xml).

Jellyfin has no Web UI settings, so on Unraid its template fields are how it
gets set up. These tests keep those fields in step with the variables
core/config.py reads, and keep them optional so a Plex-only install is
unchanged.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ET.parse(ROOT / "templates" / "plexcache-d.xml").getroot()
CONFIG_SRC = (ROOT / "core" / "config.py").read_text(encoding="utf-8")


def _jellyfin_fields():
    return {c.get("Target"): c for c in TEMPLATE.findall("Config") if c.get("Target", "").startswith("JELLYFIN_")}


def test_every_jellyfin_variable_has_a_field():
    read_by_config = set(re.findall(r"os\.environ\.get\('(JELLYFIN_\w+)'\)", CONFIG_SRC))
    assert read_by_config, "core/config.py no longer reads JELLYFIN_* variables"
    assert set(_jellyfin_fields()) == read_by_config


def test_jellyfin_fields_are_optional_and_tucked_away():
    for target, field in _jellyfin_fields().items():
        assert field.get("Type") == "Variable", target
        assert field.get("Required") == "false", target
        assert field.get("Display") == "advanced", target
        assert not field.get("Default") and not (field.text or "").strip(), f"{target} must default to empty"


def test_the_api_key_is_masked():
    assert _jellyfin_fields()["JELLYFIN_API_KEY"].get("Mask") == "true"


def test_jellyfin_is_described_as_alongside_plex():
    overview = TEMPLATE.find("Overview").text
    requires = TEMPLATE.find("Requires").text
    assert "Plex Media Server must be accessible" in requires
    assert "alongside Plex" in overview and "alongside Plex" in requires
    assert "jellyfin" in TEMPLATE.find("ExtraSearchTerms").text.split()

