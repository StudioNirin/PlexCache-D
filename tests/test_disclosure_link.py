"""Settings > Libraries: "Add Custom Path Mapping" shows one marker, not two.

The <summary> had only inline colour and cursor, so the browser drew its own
triangle in front of the orange +, at Lucide's default 24px beside 14px text,
and the whole width of the card opened the form. `.disclosure-link` hides the
browser marker, sizes the icon to the text and turns the + into an x while
the form is open.

Pure text, no browser (as in tests/test_loading_skeletons.py).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "static" / "css" / "custom.css").read_text(encoding="utf-8")
LIBRARIES = (ROOT / "web" / "templates" / "settings" / "libraries.html").read_text(encoding="utf-8")


def _rule(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_add_mapping_uses_the_disclosure_link():
    details = re.search(r'<details class="disclosure-link" data-setting-id="path_mappings">\s*<summary>\s*'
                        r'<i data-lucide="plus"></i> Add Custom Path Mapping', LIBRARIES)
    assert details, "the Add Custom Path Mapping summary lost .disclosure-link or gained inline styles"


def test_the_browser_marker_is_hidden():
    assert "list-style: none" in _rule(".disclosure-link > summary")
    assert "display: none" in _rule(".disclosure-link > summary::-webkit-details-marker")


def test_the_label_not_the_row_opens_it():
    assert "width: fit-content" in _rule(".disclosure-link > summary")


def test_the_icon_is_sized_to_the_text_and_turns_into_an_x():
    icon = _rule(".disclosure-link > summary > svg")
    assert "width: 16px" in icon and "height: 16px" in icon
    assert "rotate: 45deg" in _rule(".disclosure-link[open] > summary > svg")


def test_keyboard_focus_and_reduced_motion():
    assert "outline: 2px solid var(--plex-orange)" in _rule(".disclosure-link > summary:focus-visible")
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.disclosure-link > summary > svg \{ transition: none; \}", CSS)
