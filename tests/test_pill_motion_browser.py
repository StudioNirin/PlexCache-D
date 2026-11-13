"""
The operation pill's motion, measured in a real browser (Playwright + Chrome
or Chromium). Each test loads a minimal page with the real CSS, htmx, lucide
and static/js/pill-motion.js; /api/operation-banner answers with the real
banner template rendered for whatever state the test sets. Animations are
slowed and stepped (paused at chosen times) so the measurements do not depend
on timing.

Skipped unless Playwright is installed (pip install playwright; then either
`playwright install chromium` or an installed Google Chrome, which is tried
first).
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from jinja2 import Environment, FileSystemLoader  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
TEMPLATES = ROOT / "web" / "templates"
BASE_URL = "http://pill.test/"

HARNESS = """<!doctype html><html data-theme="dark"><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/plex-theme.css"><link rel="stylesheet" href="/static/css/custom.css">
<script src="/static/js/vendor/htmx.min.js"></script><script src="/static/js/vendor/lucide.min.js"></script>
<script src="/static/js/pill-motion.js"></script></head>
<body><div class="app-container"><main class="main-content" style="margin-left:0"><div class="content-wrapper">
<div class="page-header"><div class="page-title"><h1>Dashboard</h1></div>
<div id="global-operation-banner" hx-get="/api/operation-banner" hx-trigger="load" hx-swap="innerHTML"></div>
<script>if (window.PillMotion) PillMotion.restoreBanner();</script>
</div></div></main></div></body></html>"""

_env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
for _name in ("truncate_filename", "format_bytes", "format_time", "format_datetime"):
    _env.filters[_name] = lambda value, *args, **kwargs: value

IDLE_MAINT = {"state": "idle", "is_running": False, "queue_count": 0, "queue": [],
              "queue_paused": False, "queue_countdown": False}


def idle_ready():
    return {"status": {"state": "idle"}, "maint_status": IDLE_MAINT,
            "idle_info": {"compact": "Ran 2 hr ago", "detail": "Last run 2 hr ago · 12 cached (38.20 GB)"}}


def running(done=43, percent=60, bytes_done="24.10", phase="caching"):
    return {"status": {
        "state": "running", "is_running": True, "phase": phase,
        "current_phase_display": "Restoring..." if phase == "restoring" else "Caching...",
        "dry_run": False, "external": False, "error_count": 0, "total_files": 199,
        "completed_files": done, "progress_percent": percent, "has_byte_progress": True,
        "bytes_display": f"{bytes_done} GB / 40.20 GB", "elapsed_display": "6m 12s", "eta_display": "4m",
        "last_completed_file": "Show - S01E04.mkv", "active_files": [], "recent_files": [], "recent_logs": []},
        "maint_status": IDLE_MAINT}


def completed(errors=0, stamp="2026-11-12T20:00:00"):
    return {"status": {
        "state": "completed", "is_running": False, "completed_at": stamp, "duration_seconds": 95,
        "duration_display": "1m 35s", "files_cached": 12, "files_restored": 3,
        "bytes_cached_display": "38.20 GB", "bytes_restored_display": "4.10 GB", "error_count": errors,
        "error_messages": ["Failed to copy a file"] * errors, "dry_run": False, "was_stopped": False,
        "external": False, "recent_files": []}, "maint_status": IDLE_MAINT}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        last = None
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                b = p.chromium.launch(headless=True, **kwargs)
                break
            except Exception as e:  # noqa: BLE001 - try the next way to get a browser
                last = e
        else:
            pytest.skip(f"no Chromium available: {last}")
        yield b
        b.close()


class Pill:
    """A page holding the banner, and helpers to drive and measure it."""

    def __init__(self, page, state):
        self.page, self.state = page, state

    def js(self, script, arg=None):
        return self.page.evaluate(script, arg)

    def load(self, ctx):
        self.state["ctx"] = ctx
        self.page.goto(BASE_URL)
        self.page.wait_for_function("() => document.querySelector('#global-operation-banner .di-pill')")
        # Wait out the first-load slide-in (it moves the pill 20px), however
        # long a loaded machine takes, before anything is measured
        self.page.wait_for_function("""() => { const p = document.querySelector('#global-operation-banner .di-pill');
            return p && !p.getAnimations().some(a => a.animationName === 'diSlideDown'); }""")
        self.page.wait_for_timeout(100)
        self.js("() => { window.diHoverIn = function(){}; window.diHoverOut = function(){}; }")

    def switch(self, ctx):
        """The next poll brings a new state"""
        self.state["ctx"] = ctx
        self.js("""() => new Promise(done => {
            document.addEventListener('htmx:afterSettle', () => done(), { once: true });
            htmx.ajax('GET', '/api/operation-banner', { target: '#global-operation-banner', swap: 'innerHTML' });
        })""")

    def slow(self, factor):
        self.js("f => { const c = PillMotion.config; c.expandMs = 380 * f; c.collapseMs = 280 * f; }", factor)

    def freeze(self):
        """Pause every animation in the banner; returns the longest duration (ms)."""
        return self.js("""() => {
            window.__anims = document.getAnimations().filter(a => isFinite(a.effect.getComputedTiming().duration)
                && a.effect.target && a.effect.target.closest && a.effect.target.closest('#global-operation-banner'));
            __anims.forEach(a => a.pause());
            return Math.max(0, ...__anims.map(a => a.effect.getComputedTiming().duration));
        }""")

    def at(self, ms):
        self.js("ms => __anims.forEach(a => { a.currentTime = Math.min(ms, a.effect.getComputedTiming().duration); })", ms)

    def rect(self, selector):
        return self.js("""s => { const e = document.querySelector('#global-operation-banner ' + s);
            if (!e) return null; const r = e.getBoundingClientRect();
            return { x: r.left, y: r.top, w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2 }; }""",
                       selector)

    def settle(self):
        self.js("""() => { document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} });
            const o = document.getElementById('global-operation-banner'); if (o._flipCleanup) o._flipCleanup(); }""")
        self.page.wait_for_timeout(50)

    def leftovers(self):
        return self.js("""() => { const o = document.getElementById('global-operation-banner'); const p = o.querySelector('.di-pill');
            return o.querySelectorAll('.di-pill [style*="position: absolute"], .di-pill [style*="translate"], .di-pill > [aria-hidden="true"]').length
                + (p && p.style.height ? 1 : 0) + (o._flipCleanup ? 1 : 0); }""")

    def expand_state(self):
        return self.js("() => diGetExpandState()")


@pytest.fixture
def pill(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    state = {"ctx": idle_ready(), "dismissed": 0}

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/":
            return route.fulfill(body=state.get("page", HARNESS), content_type="text/html")
        if path.startswith("/static/"):
            f = STATIC / path[len("/static/"):]
            if f.is_file():
                kind = "text/css" if f.suffix == ".css" else "application/javascript"
                return route.fulfill(body=f.read_bytes(), content_type=kind)
        if path == "/api/operation-banner":
            time.sleep(state.get("latency", 0))  # a real server takes a moment; the harness would not
            html = _env.get_template("components/global_operation_banner.html").render(**state["ctx"])
            return route.fulfill(body=html, content_type="text/html")
        if path in ("/api/dismiss-operation", "/maintenance/dismiss-action"):
            state["dismissed"] += 1
            state["ctx"] = idle_ready()
            return route.fulfill(body=json.dumps({"ok": True}), content_type="application/json")
        return route.fulfill(status=404, body="")

    page.route("**/*", handle)
    yield Pill(page, state)
    context.close()


SHARED_EXPANDED = {"icon": '.di-pill__expanded [data-di-shared="icon"] [data-di-anchor]',
                   "label": '.di-pill__expanded [data-di-shared="label"]',
                   "run": '.di-pill__expanded [data-di-shared="run"]'}
SHARED_COMPACT = {"icon": '.di-pill__compact [data-di-shared="icon"]',
                  "label": '.di-pill__compact [data-di-shared="label"]',
                  "run": '.di-pill__compact [data-di-shared="run"]'}


def _final(pill, state, selectors):
    pill.js("s => diSetExpandState(s)", state)
    pill.page.wait_for_timeout(50)
    return {k: pill.rect(s) for k, s in selectors.items()}


def test_expand_shared_elements_travel_from_their_counterparts(pill):
    pill.load(idle_ready())
    home = _final(pill, "tier2", SHARED_EXPANDED)
    pill.js("() => diSetExpandState('false')")
    start = {k: pill.rect(s) for k, s in SHARED_COMPACT.items()}
    pill.slow(60)
    pill.js("() => diSmoothSetState('tier2')")
    total = pill.freeze()
    pill.at(0)
    for k, s in SHARED_EXPANDED.items():
        r = pill.rect(s)
        assert abs(r["x"] - start[k]["x"]) < 1 and abs(r["cy"] - start[k]["cy"]) < 1, (k, r, start[k])
    path = {k: [] for k in SHARED_EXPANDED}
    for i in range(51):
        pill.at(total * i / 50)
        for k, s in SHARED_EXPANDED.items():
            path[k].append(pill.rect(s))
    for k, pts in path.items():
        travel = abs(home[k]["x"] - start[k]["x"]) + abs(home[k]["cy"] - start[k]["cy"])
        past = max(max(0, (p["x"] - home[k]["x"]) * (1 if home[k]["x"] > start[k]["x"] else -1)) for p in pts)
        assert travel > 20, k
        assert past <= 0.05 * travel + 1, (k, past, travel)  # one gentle spring, no big swing
        assert abs(pts[-1]["x"] - home[k]["x"]) < 0.5 and abs(pts[-1]["cy"] - home[k]["cy"]) < 0.5, k
    pill.settle()
    assert pill.leftovers() == 0


def test_unshared_content_extends_out_of_and_contracts_into_the_bubble(pill):
    """Dry Run is not shared: it blooms out of the compact pill and is drawn back
    in, instead of holding still while the bubble's edge slides over it."""
    pill.load(idle_ready())
    home = _final(pill, "tier2", {"dry": ".di-idle-actions .di-btn--secondary"})["dry"]
    pill.js("() => diSetExpandState('false')")
    pill.slow(60)
    pill.js("() => diSmoothSetState('tier2')")
    pill.freeze()
    pill.at(0)
    arriving = pill.rect(".di-idle-actions .di-btn--secondary")
    assert abs(arriving["cx"] - home["cx"]) + abs(arriving["cy"] - home["cy"]) > 40  # starts inside the compact pill
    pill.settle()
    pill.js("() => diSmoothSetState('false')")
    total = pill.freeze()
    pill.at(total * 0.3)
    leaving = pill.rect(".di-idle-actions .di-btn--secondary")
    assert leaving["cy"] < home["cy"] - 10 and leaving["cx"] < home["cx"] - 10  # drawn up and in
    pill.settle()
    assert pill.leftovers() == 0 and pill.expand_state() == "false"


def test_interrupted_expand_turns_round_without_a_snap(pill):
    pill.load(idle_ready())
    pill.slow(40)
    pill.js("() => diSmoothSetState('tier2')")
    pill.page.wait_for_timeout(1500)
    heights = pill.js("""() => new Promise(done => {
        const p = document.querySelector('#global-operation-banner .di-pill'); const hs = [p.getBoundingClientRect().height];
        diSmoothSetState('false');
        let n = 0; (function f() { hs.push(p.getBoundingClientRect().height); if (++n < 20) requestAnimationFrame(f); else done(hs); })();
    })""")
    assert heights[0] > 45  # it was well into the expand
    jumps = [abs(b - a) for a, b in zip(heights, heights[1:])]
    assert max(jumps) < 3, jumps  # carries on from where it was
    assert pill.expand_state() == "false"  # already heading back
    pill.settle()
    assert pill.js("() => document.getElementById('global-operation-banner').getAttribute('data-di-expanded')") == "false"
    assert pill.leftovers() == 0


def test_state_change_reshapes_the_bubble(pill):
    pill.load(idle_ready())
    before = pill.rect(".di-pill")
    pill.slow(60)
    pill.switch(running())
    info = pill.js("""() => { const p = document.querySelector('#global-operation-banner .di-pill');
        const clone = p.querySelector(':scope > [aria-hidden="true"]');
        return { state: p.getAttribute('data-state'), morphing: !!document.getElementById('global-operation-banner')._flipCleanup,
                 clone: clone ? clone.className : null, entrance: p.classList.contains('di-pill--entrance'),
                 transition: p.style.transition.indexOf('height') !== -1 }; }""")
    assert info == {"state": "op-running-caching", "morphing": True, "clone": "di-pill--idle",
                    "entrance": False, "transition": True}, info
    pill.freeze()
    pill.at(0)
    start = pill.rect(".di-pill")
    assert abs(start["w"] - before["w"]) < 1 and abs(start["h"] - before["h"]) < 1  # starts as the old pill
    pill.settle()
    assert pill.leftovers() == 0


@pytest.mark.parametrize("expanded", [False, True], ids=["compact", "expanded"])
def test_phase_change_reshapes_instead_of_resetting(pill, expanded):
    """Caching -> Restoring is a new pill state: compact or open, the bubble
    reshapes (shared icon and label travel) and never replays an entrance."""
    pill.load(running())
    if expanded:
        pill.js("() => diSetExpandState('tier2')")
        pill.page.wait_for_timeout(100)
    before = pill.rect(".di-pill")
    pill.slow(60)
    pill.switch(running(phase="restoring"))
    info = pill.js("""() => { const p = document.querySelector('#global-operation-banner .di-pill');
        return { state: p.getAttribute('data-state'), morphing: !!document.getElementById('global-operation-banner')._flipCleanup,
                 entrance: p.classList.contains('di-pill--entrance'), expand: diGetExpandState(),
                 label: (p.querySelector('[aria-hidden="true"] [data-di-shared="label"]') || {}).textContent }; }""")
    assert info["state"] == "op-running-evicting" and info["morphing"] and not info["entrance"], info
    assert info["expand"] == ("tier2" if expanded else "false")
    assert info["label"].strip().startswith("Caching"), info  # the old label is still there, on its way out
    pill.freeze()
    pill.at(0)
    start = pill.rect(".di-pill")
    assert abs(start["w"] - before["w"]) < 1 and abs(start["h"] - before["h"]) < 1
    pill.settle()
    assert pill.leftovers() == 0


def header_page(title_width, buttons=True, css=""):
    """A page laid out like base.html: theme toggle, banner, then a page header
    whose title is `title_width` px wide, placed by PillMotion.placeIn. At the
    1280px viewport the header runs from x=32 to 1232, centred on 632."""
    actions = '<div class="action-buttons"><button class="btn btn-secondary">Refresh</button></div>' if buttons else ""
    return HARNESS.replace(
        '<div class="page-header"><div class="page-title"><h1>Dashboard</h1></div>\n'
        '<div id="global-operation-banner"',
        '<div class="top-controls"><button class="theme-toggle" style="width:36px;height:36px"></button></div>\n'
        '<div id="global-operation-banner"').replace(
        '<script>if (window.PillMotion) PillMotion.restoreBanner();</script>\n</div>',
        '<script>if (window.PillMotion) PillMotion.restoreBanner();</script>\n'
        f'<div class="page-header"><div class="page-title" style="width:{title_width}px;height:60px"><h1>Recently Added</h1></div>'
        f'{actions}</div>\n'
        "<script>PillMotion.placeIn(document.querySelector('.content-wrapper > .page-header'));</script>"
    ).replace("</head>", f"<style>{css}</style></head>")


def _box(pill, selector):
    return pill.js("""s => { const r = document.querySelector(s).getBoundingClientRect();
        return { left: r.left, right: r.right, cx: r.left + r.width / 2 }; }""", selector)


def _in_flow(pill):
    return pill.js("() => document.getElementById('global-operation-banner').classList.contains('di-flow')")


def test_pill_stays_centred_when_the_title_leaves_room(pill):
    pill.state["page"] = header_page(200)
    pill.load(running())
    assert not _in_flow(pill)
    assert abs(pill.rect(".di-pill")["cx"] - 632) < 1


def test_pill_slides_clear_of_a_wide_title_instead_of_dropping(pill):
    """Recently Added: the centred pill reached 13px into the subtitle's
    clearance, and the whole pill dropped below the header row."""
    pill.state["page"] = header_page(450)
    pill.load(running())
    assert not _in_flow(pill)
    title, actions, p = _box(pill, ".page-title"), _box(pill, ".action-buttons"), pill.rect(".di-pill")
    assert abs(p["x"] - (title["right"] + 16)) < 1  # only as far as it has to
    assert p["x"] + p["w"] <= actions["left"] - 16


def test_pill_drops_below_only_without_room_and_comes_back(pill):
    pill.state["page"] = header_page(900)
    pill.load(running())
    assert _in_flow(pill)
    pill.js("() => { document.querySelector('.page-title').style.width = '450px'; }")
    pill.page.wait_for_function("() => !document.getElementById('global-operation-banner').classList.contains('di-flow')")
    pill.page.wait_for_timeout(50)
    assert abs(pill.rect(".di-pill")["x"] - (_box(pill, ".page-title")["right"] + 16)) < 1


def test_widening_state_slides_on_the_reshape_spring(pill):
    """Ready fits centred; Caching is wider and has to slide. It reshapes and
    slides together, from where it was, instead of jumping on the next poll."""
    pill.state["page"] = header_page(450)
    pill.load(idle_ready())
    before = pill.rect(".di-pill")
    assert abs(before["cx"] - 632) < 1
    pill.slow(20)
    pill.switch(running())
    total = pill.freeze()
    pill.at(0)
    start = pill.rect(".di-pill")
    assert abs(start["x"] - before["x"]) < 1 and abs(start["w"] - before["w"]) < 1
    target = _box(pill, ".page-title")["right"] + 16
    lefts = []
    for i in range(41):
        pill.at(total * i / 40)
        lefts.append(pill.rect(".di-pill")["x"])
    assert all(b <= a + 0.5 for a, b in zip(lefts, lefts[1:12]))  # one movement, from the start
    assert min(lefts) > target - 0.05 * (before["x"] - target) - 1  # the spring's small overshoot, no more
    assert abs(lefts[-1] - target) < 1
    pill.settle()
    assert pill.leftovers() == 0
    assert pill.js("() => document.getElementById('global-operation-banner').style.transition") == ""


def test_phone_pill_fits_the_screen(pill):
    """A finished run with errors ran off the right of a 360px screen: the
    header is a wrapping column there, as wide as the pill's full content. The
    run summary shortens instead; the label and the error badge stay whole."""
    pill.page.set_viewport_size({"width": 360, "height": 740})
    pill.state["page"] = header_page(200)
    pill.load(completed(errors=2))
    pill.js("() => diSetExpandState('false')")
    pill.page.wait_for_timeout(100)
    assert _in_flow(pill)
    p = pill.rect(".di-pill")
    assert p["x"] >= 0 and p["x"] + p["w"] <= 360 - 16
    clipped = pill.js("""() => { const e = document.querySelector('#global-operation-banner .di-pill__compact .di-pill__left');
        return e.scrollWidth - e.clientWidth; }""")
    assert clipped <= 0
    summary = pill.js("""() => { const e = document.querySelector('#global-operation-banner .di-pill__compact .di-metric--muted');
        return [e.textContent, e.scrollWidth > e.clientWidth]; }""")
    assert summary == ["12 cached · 3 restored", True]  # still there, cut short with an ellipsis


def test_phone_header_reads_title_pill_then_actions(pill):
    """On a phone the header is a column. It follows the Dashboard: title, the
    pill, then the page's own actions as a small row on the right. The actions
    (Priority Report, View All Files, Refresh) used to sit between the title
    and the pill."""
    pill.page.set_viewport_size({"width": 390, "height": 844})
    pill.state["page"] = header_page(200)
    pill.load(running())
    title, p = pill.js("""() => { const b = r => { r = r.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; };
        return [b(document.querySelector('.page-title')), b(document.querySelector('#global-operation-banner .di-pill'))]; }""")
    actions = pill.js("""() => { const r = document.querySelector('.action-buttons').getBoundingClientRect();
        const h = document.querySelector('.page-header').getBoundingClientRect();
        return { top: r.top, right: r.right, headerRight: h.right,
                 font: getComputedStyle(document.querySelector('.action-buttons .btn')).fontSize }; }""")
    assert title["bottom"] <= p["top"] < p["bottom"] < actions["top"]
    assert abs(actions["right"] - actions["headerRight"]) < 1
    assert actions["font"] == "12.8px"  # .btn-sm, like the Dashboard's Refresh


def test_opening_keeps_clear_of_the_toggle_and_closing_returns(pill):
    """Slid far to the right, the open pill (here 620px) would reach under the
    theme toggle: it moves back as it opens, and closing returns it."""
    pill.state["page"] = header_page(700, buttons=False, css=(
        '#global-operation-banner[data-di-expanded="tier2"] .di-pill { min-width: 620px; }'))
    pill.load(running())
    closed = pill.rect(".di-pill")
    assert abs(closed["x"] - (_box(pill, ".page-title")["right"] + 16)) < 1
    toggle = _box(pill, ".top-controls")
    pill.slow(20)
    pill.js("() => diSmoothSetState('tier2')")
    pill.at(pill.freeze())  # where the spring takes it, not a correction after it settles
    opened = pill.rect(".di-pill")
    assert opened["w"] >= 619 and opened["x"] + opened["w"] <= toggle["left"] - 16 + 1
    assert opened["x"] + opened["w"] >= toggle["left"] - 16 - 1  # moved no further than it had to
    pill.settle()
    pill.js("() => diSmoothSetState('false')")
    pill.at(pill.freeze())
    assert abs(pill.rect(".di-pill")["x"] - closed["x"]) < 1
    pill.settle()
    assert pill.leftovers() == 0


FRAMES = """
window.__frames = []; const t0 = performance.now();
(function f() { const o = document.getElementById('global-operation-banner'); const p = o && o.querySelector('.di-pill');
  if (p) { const r = p.getBoundingClientRect();
    window.__frames.push({ t: performance.now() - t0, state: p.getAttribute('data-state'),
      restored: p.hasAttribute('data-di-placeholder'), top: r.top, opacity: +getComputedStyle(p).opacity,
      anims: p.getAnimations().map(a => a.animationName).filter(Boolean),
      morph: !!p.querySelector(':scope > div[aria-hidden]') }); }
  if (performance.now() - t0 < 1000) requestAnimationFrame(f); })();
"""


@pytest.mark.parametrize("make", [running, completed, idle_ready], ids=["running", "completed", "ready"])
def test_navigating_keeps_the_pill_in_place(pill, make):
    """A new page shows the previous page's pill from its first frame, in
    place, and the first poll replaces it without an entrance or a flash."""
    pill.load(make())
    top = pill.rect(".di-pill")["y"]
    pill.page.add_init_script(FRAMES)
    pill.state["latency"] = 0.3
    pill.page.goto(BASE_URL)  # leaving the page saves the pill (pagehide)
    pill.page.wait_for_timeout(1100)
    frames = pill.js("() => window.__frames")
    assert frames and frames[0]["restored"], frames[:2]  # there from the first frame with a pill
    assert any(not f["restored"] for f in frames)  # and replaced by the live one
    for f in frames:
        assert abs(f["top"] - top) < 0.5 and f["opacity"] == 1 and not f["anims"], f


def test_state_change_across_a_navigation_morphs(pill):
    pill.load(running())
    pill.page.add_init_script(FRAMES)
    pill.state["ctx"] = completed()  # the run finished while the page was changing
    pill.state["latency"] = 0.3
    pill.page.goto(BASE_URL)
    pill.page.wait_for_timeout(1100)
    frames = pill.js("() => window.__frames")
    assert frames[0]["restored"] and frames[0]["state"] == "op-running-caching"
    assert any(f["morph"] and f["state"] == "op-completed" for f in frames)  # reshaped from the old pill
    assert not any("diSlideDown" in f["anims"] for f in frames)


def test_dismissing_reshapes_into_the_ready_pill(pill):
    pill.load(completed())
    pill.page.wait_for_timeout(300)
    pill.js("() => dismissCompletionBanner()")
    pill.page.wait_for_function("() => document.getElementById('global-operation-banner')._flipCleanup")
    assert pill.state["dismissed"] == 1
    pill.settle()
    assert pill.js("() => document.querySelector('#global-operation-banner .di-pill').id") == "idle-ready-pill"
    assert pill.leftovers() == 0


def test_alert_expands_once_and_pulses_the_cause(pill):
    pill.load(completed(errors=2))
    pill.page.wait_for_function("() => diGetExpandState() === 'tier2'", timeout=3000)
    pill.page.wait_for_function(
        "() => document.querySelector('#global-operation-banner .di-pill__expanded .di-error-badge.di-attention')",
        timeout=3000)
    pill.settle()
    pill.js("() => diSetExpandState('false')")
    pill.page.reload()
    pill.page.wait_for_timeout(1200)
    assert pill.expand_state() == "false"  # the same event does not alert again


def test_numbers_roll_only_what_changed_and_bars_glide(pill):
    pill.load(running(done=43, percent=60, bytes_done="24.10"))
    pill.js("() => diSetExpandState('tier2')")
    width_before = pill.rect('[data-di-bar="fill"]')["w"]
    pill.switch(running(done=44, percent=66, bytes_done="26.50"))
    rolled = pill.js("""() => Array.from(document.querySelectorAll('#global-operation-banner .di-roll-char'))
        .map(s => s.parentElement.getAttribute('data-di-num') + '=' + s.textContent)""")
    assert "files-compact=4" in rolled and "bytes=6.5" in rolled, rolled  # "43" -> "44", "24.10" -> "26.50"
    width_now = pill.rect('[data-di-bar="fill"]')["w"]
    assert abs(width_now - width_before) < 2  # the bar starts where it was and glides
    pill.page.wait_for_timeout(700)
    assert pill.rect('[data-di-bar="fill"]')["w"] > width_before + 5


def test_polls_do_not_pile_up_listeners(pill):
    pill.load(running())
    counts = pill.js("""async () => {
        let calls = 0; const orig = lucide.createIcons; lucide.createIcons = function() { calls++; return orig.apply(this, arguments); };
        const per = [];
        for (let i = 0; i < 4; i++) {
            const before = calls;
            await new Promise(done => {
                document.addEventListener('htmx:afterSettle', () => done(), { once: true });
                htmx.ajax('GET', '/api/operation-banner', { target: '#global-operation-banner', swap: 'innerHTML' });
            });
            per.push(calls - before);
        }
        lucide.createIcons = orig; return per; }""")
    assert len(set(counts)) == 1, counts


def test_reduced_motion_changes_state_without_animating(pill):
    pill.page.emulate_media(reduced_motion="reduce")
    pill.load(idle_ready())
    pill.js("() => diSmoothSetState('tier2')")
    assert pill.expand_state() == "tier2"
    assert pill.leftovers() == 0
    assert pill.js("() => getComputedStyle(document.querySelector('#global-operation-banner .di-pill')).transitionDuration") \
        in ("0s", "0s, 0s")


def test_phone_width_morph_lines_up(browser):
    context = browser.new_context(viewport={"width": 360, "height": 740})
    page = context.new_page()
    state = {"ctx": idle_ready(), "dismissed": 0}

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/":
            return route.fulfill(body=HARNESS, content_type="text/html")
        if path.startswith("/static/"):
            f = STATIC / path[len("/static/"):]
            kind = "text/css" if f.suffix == ".css" else "application/javascript"
            return route.fulfill(body=f.read_bytes(), content_type=kind)
        if path == "/api/operation-banner":
            return route.fulfill(body=_env.get_template("components/global_operation_banner.html").render(**state["ctx"]),
                                 content_type="text/html")
        return route.fulfill(status=404, body="")

    page.route("**/*", handle)
    p = Pill(page, state)
    p.load(idle_ready())
    # The fit check in base.html (not in this harness) puts narrow screens in fit mode
    p.js("() => document.getElementById('global-operation-banner').classList.add('di-flow')")
    start = {k: p.rect(s) for k, s in SHARED_COMPACT.items()}
    p.slow(60)
    p.js("() => diSmoothSetState('tier2')")
    p.freeze()
    p.at(0)
    for k, s in SHARED_EXPANDED.items():
        r = p.rect(s)
        assert abs(r["x"] - start[k]["x"]) < 1 and abs(r["cy"] - start[k]["cy"]) < 1, k
    p.settle()
    assert p.leftovers() == 0
    assert p.rect(".di-pill")["w"] <= 360
    context.close()
