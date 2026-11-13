"""Loading states: skeletons that show, in the shape of what they stand in for.

Recently Added asks Plex on every visit. Its placeholder was a short card with
"Loading recently added media..." and a spinner that drew as a thin line, and
the page jumped when the data landed. It now shows a skeleton of the loaded
page: the real headings and labels, with bars where the numbers, filters and
rows go.

Skeleton bars took their base colour from the card surface, so inside a card
they only showed while the shimmer passed. They are now a tint of the text
colour, which shows on any surface in either theme.

Pure text and arithmetic, no browser (as in tests/test_recently_added_css.py).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "web" / "templates"
THEME_CSS = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")

PAGE = (TEMPLATES / "recently_added" / "index.html").read_text(encoding="utf-8")
LIST = (TEMPLATES / "recently_added" / "partials" / "list.html").read_text(encoding="utf-8")
SKELETON = (TEMPLATES / "recently_added" / "partials" / "list_skeleton.html").read_text(encoding="utf-8")


# ── Recently Added ────────────────────────────────────────────────

def test_the_page_waits_on_a_skeleton_not_a_spinner():
    content = PAGE[PAGE.index('<div id="ra-content"'):]
    content = content[:content.index("</div>")]
    assert '{% include "recently_added/partials/list_skeleton.html" %}' in content
    assert "spinner" not in content and "Loading recently added media..." not in content


def _stat_headers(html):
    return re.findall(r'<div class="stat-card-header"><i data-lucide="([\w-]+)"></i> ([^<]+)</div>', html)


def _columns(html):
    thead = html[html.index("<thead>"):html.index("</thead>")]
    return re.findall(r"<th>([^<]+)</th>", thead)


def test_the_skeleton_has_the_loaded_page_headings():
    """Same stat cards, filter labels and columns, so nothing moves or renames
    itself when the list lands. Kept in step with list.html."""
    assert _stat_headers(SKELETON) == _stat_headers(LIST)
    assert len(_stat_headers(LIST)) == 3
    assert _columns(SKELETON) == _columns(LIST)
    loaded_labels = re.findall(r'<label for="ra-[\w-]+">([^<]+)</label>', LIST)
    assert "{% for label in (" + ", ".join(f"'{label}'" for label in loaded_labels) + ") %}" in SKELETON
    for label in ("on fast storage now", "not currently cached"):
        assert label in SKELETON and label in LIST


def test_the_skeleton_does_not_take_the_filters_ids():
    """#ra-content sends #ra-days-input with every load; a stand-in carrying
    that id would be sent in its place."""
    assert ' id="' not in SKELETON
    assert 'aria-busy="true"' in SKELETON


# ── Spinners ──────────────────────────────────────────────────────

def test_span_spinners_keep_their_size():
    """An inline <span> ignores width and height, so a span spinner outside a
    flex row drew only its border, as a thin vertical line."""
    rule = re.search(r"span\.spinner\s*\{([^}]*)\}", THEME_CSS)
    assert rule and "display: inline-block" in rule.group(1)


# ── Skeleton bars show on every surface ───────────────────────────

def _tokens(selector):
    body = THEME_CSS[THEME_CSS.index(selector):]
    body = body[:body.index("}")]
    return dict(re.findall(r"(--plex-[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", body))


def _rgb(value):
    return tuple(int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _skeleton_tint():
    rule = THEME_CSS[THEME_CSS.index(".skeleton {"):]
    rule = rule[:rule.index("}")]
    match = re.search(r"--skeleton-base:\s*color-mix\(in srgb, var\((--plex-[\w-]+)\) (\d+)%, transparent\)", rule)
    assert match, ".skeleton's base is not a tint of a theme token"
    assert "var(--skeleton-base) 25%" in rule
    return match.group(1), int(match.group(2)) / 100


@pytest.mark.parametrize("theme,selector", [("dark", ":root {"), ("light", '[data-theme="light"] {')])
@pytest.mark.parametrize("surface", ["--plex-bg-card", "--plex-bg-card-hover", "--plex-bg-hover"])
def test_skeleton_bars_show_on_every_surface(theme, selector, surface):
    tokens = _tokens(selector)
    token, alpha = _skeleton_tint()
    ink, ground = _rgb(tokens[token]), _rgb(tokens[surface])
    bar = tuple(round(i * alpha + g * (1 - alpha)) for i, g in zip(ink, ground))
    assert _contrast(bar, ground) >= 1.15, f"{theme} {surface}: {_contrast(bar, ground):.2f}"


def test_reduced_motion_stops_the_shimmer():
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.skeleton \{ animation: none; \}", THEME_CSS)
