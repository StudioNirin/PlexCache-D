"""Badge icon sizing and alignment.

Lucide replaces <i data-lucide> with <svg>, so `.badge i` never applied and
badge icons rendered at 24px. That made icon badges taller, and with baseline
alignment a text-only badge beside one (Recently Added: "Returns when watched"
+ "OnDeck") sat lower.
"""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")


def _rule(selector):
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, selector
    return match.group(1)


def test_badge_icons_are_sized_as_svg():
    body = _rule(".badge svg")
    assert "width: 12px" in body and "height: 12px" in body
    assert not re.search(r"(?m)^\.badge i\s*\{", CSS)


def test_badges_align_by_middle():
    assert "vertical-align: middle" in _rule(".badge")
