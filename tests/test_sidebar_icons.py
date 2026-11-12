"""
Sidebar icons keep their size and position when the sidebar is collapsed.

Lucide replaces every <i data-lucide> with an <svg> when the page loads, so
rules that only target `i` never reach the rendered icon. The sidebar's size
rules target the svg as well, with flex-shrink: 0. Collapsed items keep left
alignment and take a left inset that centres the 24px icon in the 60px rail,
so the icons glide with the width animation instead of snapping to the middle.

Labels stay in the layout in both states and never wrap: the sidebar's edge
slides over them while it animates, instead of the text popping in at 60px and
wrapping onto two lines until the sidebar is wide enough.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME_CSS = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")


def _rule(selector_fragment):
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        selectors = [s.strip() for s in re.sub(r"/\*.*?\*/", "", selector, flags=re.S).split(",")]
        if selector_fragment in selectors:
            return dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", body))
    raise AssertionError(f"no rule for {selector_fragment}")


def test_icon_rules_reach_the_rendered_svg():
    for selector in (".sidebar-nav li a svg", ".sidebar-logout-btn svg", ".sidebar-collapse-btn svg"):
        rule = _rule(selector)
        assert rule.get("flex-shrink") == "0", selector
        assert rule.get("width") == "24px" and rule.get("height") == "24px", selector


COLLAPSED_ITEMS = (".sidebar.collapsed .sidebar-nav li a",
                   ".sidebar.collapsed .sidebar-logout-btn",
                   ".sidebar.collapsed .sidebar-collapse-btn")


def _root_var(name):
    match = re.search(rf"{re.escape(name)}:\s*([^;]+);", THEME_CSS)
    assert match, name
    return match.group(1).strip()


def test_collapsed_items_inset_the_icon_instead_of_centring():
    """Centring snapped the icons to the middle of the still-wide sidebar the
    moment a collapse started. A left inset animates with the width instead."""
    for selector in COLLAPSED_ITEMS:
        rule = _rule(selector)
        padding = rule["padding"].split()
        assert padding[1] == "0" and padding[3] == "var(--sidebar-item-inset-collapsed)", (selector, padding)
        assert rule.get("justify-content") != "center", selector


def test_collapsed_inset_centres_the_icon_in_the_rail():
    """Sidebar border 1px + list padding 0.5rem + item border 1px + inset puts
    the 24px icon's centre on the middle of the 59px visible rail."""
    rail = float(_root_var("--sidebar-collapsed").removesuffix("px"))
    assert _root_var("--sidebar-item-inset-collapsed") == \
        "calc((var(--sidebar-collapsed) - 1px - 1rem - 2px - 24px) / 2)"
    inset = (rail - 1 - 16 - 2 - 24) / 2
    icon_centre = 8 + 1 + inset + 12
    assert icon_centre == (rail - 1) / 2


def test_collapse_icon_flips_when_collapsed():
    assert "rotate(180deg)" in _rule(".sidebar.collapsed .sidebar-collapse-btn svg")["transform"]


def test_collapsed_items_keep_expanded_height():
    """Collapsing only hides the labels: same vertical padding, no thicker border."""
    expanded = _rule(".sidebar-nav li a")["padding"].split()[0]
    collapsed = _rule(".sidebar.collapsed .sidebar-nav li a")
    assert collapsed["padding"].split()[0] == expanded
    assert "border-bottom" not in collapsed and "border-bottom-width" not in collapsed


def test_collapsed_active_item_looks_like_expanded():
    """No collapsed-only active marker: the active item keeps the expanded look."""
    for selector, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        selectors = [s.strip() for s in re.sub(r"/\*.*?\*/", "", selector, flags=re.S).split(",")]
        assert not any(".sidebar.collapsed .sidebar-nav li a.active" in s for s in selectors), selectors


def test_labels_stay_in_layout_and_never_wrap():
    label = _rule(".sidebar-label")
    assert label.get("white-space") == "nowrap"
    assert label.get("flex-shrink") == "0"
    assert _rule(".sidebar.collapsed .sidebar-label").get("visibility") == "hidden"
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        if ".sidebar.collapsed" in selector and "display: none" in body.replace(":none", ": none"):
            assert "span" not in selector and "sidebar-label" not in selector, selector


def test_every_item_label_has_the_label_class():
    nav = BASE_HTML[BASE_HTML.index('<ul class="sidebar-nav">'):BASE_HTML.index("</nav>")]
    assert "<span>" not in nav
    assert nav.count('class="sidebar-label"') >= 9  # 7 nav links, Sign Out, Collapse


def test_collapsed_rules_also_apply_before_the_script_runs():
    """The <head> script sets .sidebar-collapsed-init before the sidebar
    exists. Each collapsed rule a transition animates must match it as well,
    or every load of a collapsed sidebar animates labels and icons."""
    for selector in COLLAPSED_ITEMS + (".sidebar.collapsed .sidebar-brand",
                                       ".sidebar.collapsed .sidebar-label",
                                       ".sidebar.collapsed .sidebar-brand-text",
                                       ".sidebar.collapsed .sidebar-collapse-btn svg"):
        init = selector.replace(".sidebar.collapsed", ".sidebar-collapsed-init .sidebar")
        assert _rule(selector) == _rule(init), selector


def test_collapsed_brand_keeps_its_height():
    """The brand text is hidden in place, not removed, so the nav does not move up."""
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        selectors = [s.strip() for s in re.sub(r"/\*.*?\*/", "", selector, flags=re.S).split(",")]
        if ".sidebar.collapsed .sidebar-brand-text" in selectors and "@media" not in selector:
            assert "display: none" not in body.replace(":none", ": none")
    assert _rule(".sidebar.collapsed .sidebar-brand-text").get("visibility") == "hidden"
