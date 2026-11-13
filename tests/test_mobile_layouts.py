"""
Phone and narrow-screen layouts.

On a phone the Logs filter and search cards took most of the first screen, so
the log started below it. They now become one bar (search; Filters with a
summary of what is in effect; Refresh; Download), and the rest opens from
Filters. Long lines wrap by default on a phone, through a labelled switch.

Tab bars (the Settings tabs) wrapped onto three rows on a phone. They are now
one row that swipes, or drags with a mouse, with the current tab in view.

Header buttons are the page's own actions: Cached Files and the Priority
Report are tabs of one section, and Storage links to the files from its card.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "web" / "templates"


def _read(rel):
    return (TEMPLATES / rel).read_text(encoding="utf-8")


LOGS = _read("logs/viewer.html")
THEME = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _phone_blocks(css):
    """Every @media (max-width: 768px) block, concatenated."""
    out, i = [], 0
    while True:
        i = css.find("@media (max-width: 768px)", i)
        if i < 0:
            return "".join(out)
        depth, j = 0, css.index("{", i)
        for k in range(j, len(css)):
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            if depth == 0:
                out.append(css[i:k + 1])
                i = k + 1
                break


PHONE = _phone_blocks(THEME)


# ── Logs: one bar on a phone ──────────────────────────────────────

def test_controls_share_one_wrapper_with_a_filters_toggle():
    assert '<div class="log-controls" id="log-controls">' in LOGS
    toggle = re.search(r'<button[^>]*id="log-filters-toggle"[^>]*>', LOGS).group(0)
    assert 'aria-expanded="false"' in toggle and 'aria-controls="log-controls"' in toggle
    assert 'id="log-filters-summary"' in LOGS


def test_everything_but_search_and_actions_opens_from_filters():
    panel = re.findall(r'<[^>]+data-log-panel[^>]*>', LOGS)
    for cls in ("log-file-group", "log-lines-group", "log-level-group", "log-phase-group",
                "time-range-inputs", "log-regex-toggle", "log-case-toggle",
                "log-wrap-switch", "log-autoscroll-switch", "log-live-switch"):
        assert any(cls in tag for tag in panel), cls
    assert not any("log-actions" in tag or 'id="log-search"' in tag for tag in panel)


def test_phone_css_collapses_the_cards_into_one_grid():
    assert ".log-filters-toggle { display: none; }" in THEME  # desktop: no toggle
    assert ".log-controls:not(.is-open) [data-log-panel] { display: none; }" in PHONE
    assert re.search(r"\.log-controls > \.card,[^{]*\.log-controls \.log-options \{\s*display: contents;", PHONE)
    # Refresh and Download become icons on the Filters row
    assert re.search(r"\.log-controls \.log-actions > label,\s*\.log-controls \.log-actions \.btn-label \{ display: none; \}",
                     PHONE)


def test_closed_filters_say_what_is_in_effect():
    summary = LOGS[LOGS.index("function updateFilterSummary()"):LOGS.index("// ---- Init ----")]
    for part in ("els.levelFilter.value !== 'all'", "els.phaseFilter.value !== 'all'",
                 "els.timeFrom.value || els.timeTo.value", "classList.toggle('has-filters', active > 0)"):
        assert part in summary, part
    # A level badge click changes the filter without a change event
    badge = LOGS[LOGS.index("// Attach click handlers to filter by level"):]
    assert "updateFilterSummary();" in badge[:badge.index("savePreferences();") + 20]


def test_wrap_is_a_labelled_switch_that_defaults_on_for_phones():
    switch = re.search(r'<label class="switch log-wrap-switch"[^>]*>\s*<input type="checkbox" id="wrap-toggle">\s*'
                       r'<span>Wrap lines</span>', LOGS)
    assert switch, "Wrap lines should be a switch next to Auto-scroll and Live Updates"
    assert "function toggleWrap()" not in LOGS
    load = LOGS[LOGS.index("function loadPreferences()"):LOGS.index("// ---- Level Filtering")]
    # Only an explicit choice overrides the phone default: before the switch,
    # every save stored wrapLines: false whether or not wrap was touched
    assert "setWrap(wrapChosen ? !!prefs.wrapLines" in load
    assert "window.matchMedia(PHONE).matches" in load
    assert "wrapChosen: wrapChosen," in LOGS and "wrapChosen = true;" in LOGS


# ── Tab bars swipe on a phone ─────────────────────────────────────

def test_tabs_are_one_swipeable_row_on_a_phone():
    tabs = PHONE[PHONE.index(".tabs {"):PHONE.index("}", PHONE.index(".tabs {"))]
    for rule in ("flex-wrap: nowrap;", "overflow-x: auto;", "scrollbar-width: none;", "scroll-snap-type: x proximity;"):
        assert rule in tabs, rule
    assert ".tabs.can-scroll-left.can-scroll-right" in PHONE  # edges fade where more tabs wait


def test_current_tab_is_brought_into_view():
    block = APP_JS[APP_JS.index("function setUp(bar)"):]
    assert "bar.querySelector('.tab.active')" in block
    assert "bar.scrollLeft = active.offsetLeft - (bar.clientWidth - active.offsetWidth) / 2;" in block
    assert "classList.toggle('can-scroll-left'" in APP_JS and "classList.toggle('can-scroll-right'" in APP_JS


def test_tab_icons_are_centred_svgs():
    """Lucide renders <svg>; the icon sat on the text baseline at 24px."""
    assert re.search(r"\.tab \{\s*display: inline-flex;\s*align-items: center;", THEME)
    assert re.search(r"\.tab svg \{\s*width: 18px;\s*height: 18px;", THEME)


def test_tab_bar_drags_with_a_mouse():
    """A mouse cannot swipe: the bar drags, and a drag does not open the tab
    under the pointer when it ends. Touch keeps the native swipe."""
    drag = APP_JS[APP_JS.index("function dragToScroll(bar)"):APP_JS.index("function setUp(bar)")]
    assert "e.pointerType !== 'mouse'" in drag
    assert "Math.abs(dx) < 5" in drag  # a click is not a drag
    assert "bar.setPointerCapture(down.id)" in drag
    assert "if (dragged) { e.preventDefault(); e.stopPropagation();" in drag
    assert "dragToScroll(bar);" in APP_JS
    assert ".tabs.is-dragging { scroll-snap-type: none; }" in PHONE


# ── Header buttons are the page's own actions, not navigation ─────

def test_cached_files_and_priority_report_are_tabs_of_one_section():
    """Priority Report (on Cached Files) and Back to Cache (on the report) sat
    where other pages keep their own Refresh. They are now tabs."""
    tabs = _read("cache/partials/section_tabs.html")
    assert 'class="tabs"' in tabs and 'href="/cache"' in tabs and 'href="/cache/priorities"' in tabs
    for page, tab in (("cache/list.html", "files"), ("cache/priorities.html", "priorities")):
        html = _read(page)
        assert "action-buttons" not in html, page
        assert f"{{% with cache_tab = '{tab}' %}}{{% include \"cache/partials/section_tabs.html\" %}}{{% endwith %}}" in html
        assert "<h1>Cached Files</h1>" in html, page
    assert "Back to Cache" not in _read("cache/priorities.html")
    # The report is part of the Cached Files section in the sidebar too
    assert "page_title in ('Cached Files', 'Priority Report')" in _read("base.html")


def test_storage_links_to_the_files_from_its_cached_items_card():
    assert "action-buttons" not in _read("cache/drive.html")
    stats = _read("cache/partials/storage_stats.html")
    card = stats[stats.rindex("<a ", 0, stats.index("Cached Items")):stats.index("Cached Items")]
    assert card.startswith('<a href="/cache" class="stat-card stat-card-link"')  # the Dashboard's link-card pattern
