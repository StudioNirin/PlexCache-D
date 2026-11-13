"""
Phone and narrow-screen layouts.

On a phone the Logs filter and search cards took most of the first screen, so
the log started below it. They now become one bar (search; Filters with a
summary of what is in effect; Refresh; Download), and the rest opens from
Filters. Long lines wrap by default on a phone, through a labelled switch.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "web" / "templates"


def _read(rel):
    return (TEMPLATES / rel).read_text(encoding="utf-8")


LOGS = _read("logs/viewer.html")
THEME = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")


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
