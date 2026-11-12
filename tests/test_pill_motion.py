"""
Structure of the operation pill's motion engine (static/js/pill-motion.js) and
the markup and CSS it works with. These run everywhere; the motion itself is
measured in a real browser by tests/test_pill_motion_browser.py, and the spring
maths by tests/test_pill_spring_node.py.

Guided by Apple's Live Activities / Dynamic Island design guidance: one spring
per change, shared elements travel, everything else contracts into / extends
out of the bubble, alerts expand the pill, reduced motion is honoured.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = (ROOT / "web" / "static" / "js" / "pill-motion.js").read_text(encoding="utf-8")
BANNER = (ROOT / "web" / "templates" / "components" / "global_operation_banner.html").read_text(encoding="utf-8")
BASE = (ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")
CUSTOM_CSS = (ROOT / "web" / "static" / "css" / "custom.css").read_text(encoding="utf-8")
THEME_CSS = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")

PUBLIC_API = ("diGetExpandState", "diSetExpandState", "diSmoothSetState", "diMinimize", "diAlert",
              "diDismiss", "diTriggerRun", "diHoverIn", "diHoverOut", "diPillClick",
              "bannerToggleShow", "showBannerWarning")


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def _media_block(css, query):
    i = css.index(f"@media ({query})")
    depth, j = 0, css.index("{", i)
    for k in range(j, len(css)):
        depth += {"{": 1, "}": -1}.get(css[k], 0)
        if depth == 0:
            return css[i:k + 1]
    raise AssertionError("unclosed @media block")


# ── The engine is a module, loaded once ───────────────────────────

def test_engine_is_loaded_once_from_base():
    """The banner template is re-swapped by every poll; code living in it was
    redefined every couple of seconds and its listeners piled up."""
    assert "<script src=\"{{ static_url('js/pill-motion.js') }}\"></script>" in BASE


def test_pages_put_back_the_previous_pill():
    """Navigating replayed the pill's entrance on every page: the pill is
    saved on pagehide and restored right after the banner element is parsed."""
    banner_div = BASE.index('<div id="global-operation-banner"')
    restore = BASE.index("PillMotion.restoreBanner()")
    assert banner_div < restore < BASE.index("{% block content %}")
    assert "window.addEventListener('pagehide', _diSaveBanner);" in ENGINE
    save = _between(ENGINE, "function _diSaveBanner(", "function restoreBanner(")
    assert "querySelectorAll('script')" in save  # a stand-in, not a re-run of the state's scripts
    assert "if (oldPill.hasAttribute('data-di-placeholder')) return;" in ENGINE  # always replaced
    assert "#global-operation-banner[data-di-continued] .di-pill { animation: none !important; }" in CUSTOM_CSS
    assert "target.removeAttribute('data-di-continued')" in ENGINE  # a real state change flashes again


def test_template_holds_no_engine_code():
    for fn in ("function _diDoFlip(", "function _diMorphStart(", "function diSmoothSetState(",
               "function _diSpring(", "function diTriggerRun(", "addEventListener('htmx:"):
        assert fn not in BANNER, fn


def test_engine_exports_its_public_api_and_hides_the_rest():
    assert ENGINE.lstrip().startswith("/*") and "(function () {" in ENGINE
    for name in PUBLIC_API:
        assert f"window.{name} = {name};" in ENGINE, name
    assert "window.PillMotion = { config: cfg," in ENGINE
    assert "window._diDoFlip" not in ENGINE and "window._diMorphStart" not in ENGINE


def test_markup_only_calls_the_public_api():
    called = set(re.findall(r'on(?:click|mouseenter|mouseleave)="[^"]*?\b(di[A-Z]\w+|bannerToggleShow)\(', BANNER))
    defined_in_template = set(re.findall(r"^function (\w+)\(", BANNER, re.M))
    assert called <= set(PUBLIC_API) | defined_in_template, called - set(PUBLIC_API) - defined_in_template


def test_config_defaults():
    cfg = _between(ENGINE, "var cfg = {", "};")
    bounce = float(re.search(r"bounce: ([0-9.]+),", cfg).group(1))
    assert 0.15 <= bounce <= 0.35  # Apple: ~0.3 clearly bouncy, above ~0.4 exaggerated
    for key in ("blur:", "contract: true", "expandMs: 380", "collapseMs: 280", "alertHoldMs:"):
        assert key in cfg, key


def test_listeners_are_bound_at_load_not_per_swap():
    uses = re.findall(r"document\.addEventListener\('htmx:(\w+)'", ENGINE)
    assert sorted(set(uses)) == ["afterSettle", "afterSwap", "beforeSwap", "responseError", "sendError"]


# ── Reduced motion ────────────────────────────────────────────────

def test_reduced_motion_skips_the_flip():
    smooth = _between(ENGINE, "function diSmoothSetState(", "function _diViewFor(")
    assert "if (_diReducedMotion()) { diSetExpandState(state); return; }" in smooth
    assert "_diReducedMotion()" in _between(ENGINE, "function _diSnapshotPill(", "function _diHoldShape(")


def test_reduced_motion_css_covers_pill_and_sidebar():
    pill = _media_block(CUSTOM_CSS, "prefers-reduced-motion: reduce")
    for sel in (".di-pill", ".di-content-enter", ".di-progress-fill", ".di-attention"):
        assert sel in pill, sel
    assert ".di-spinner" not in pill  # the spinner is the only sign a run is going
    sidebar = _media_block(THEME_CSS, "prefers-reduced-motion: reduce")
    for sel in (".sidebar", ".main-content", ".sidebar-label"):
        assert sel in sidebar, sel


# ── Markup the engine relies on ───────────────────────────────────

def test_every_icon_circle_and_label_is_shared():
    """Every pill takes part in the morph; the icon circle lines up by its glyph."""
    icons = re.findall(r'<div class="di-icon"[^>]*>', BANNER)
    assert icons and all('data-di-shared="icon"' in i for i in icons), icons
    assert not re.search(r'class="di-icon" data-di-shared="icon"[^>]*><i (?!data-di-anchor)(?![^>]*data-di-anchor)', BANNER)
    labels = re.findall(r'<span class="di-label"[^>]*>[^<]*', BANNER)
    unshared = [l for l in labels if "data-di-shared" not in l]
    assert unshared == ['<span class="di-label">PlexCache Ready'], unshared  # the Ready title is extra content
    spinners = re.findall(r'<div class="di-spinner"[^>]*>', BANNER)
    assert spinners and all('data-di-shared="spinner"' in s for s in spinners), spinners


def test_spinner_rotates_on_transform_so_translate_composes():
    """Shared elements move on translate/scale; a transform-based spin applies first."""
    assert "@keyframes diSpin { to { transform: rotate(360deg); } }" in CUSTOM_CSS
    start = _between(ENGINE, "function _diMorphStart(", "function _diMorphCleanup(")
    assert "el.style.translate" in start and not re.search(r"p\.(to|from)\.style\.transform\s*=", start)


def test_live_numbers_and_bars_are_tagged():
    for key in ("files-compact", "files", "bytes", "elapsed", "eta", "countdown"):
        assert f'data-di-num="{key}"' in BANNER, key
    assert BANNER.count('data-di-bar="fill"') == 2 and BANNER.count('data-di-bar="pill"') == 2


def test_dismiss_and_run_go_through_the_engine():
    """So they collapse first and the next state reshapes the bubble."""
    assert "fetch('/api/dismiss-operation'" not in BANNER and "fetch('/maintenance/dismiss-action'" not in BANNER
    assert BANNER.count("diDismiss('/api/dismiss-operation')") == 3
    assert BANNER.count("diDismiss('/maintenance/dismiss-action')") == 3
    assert "pill.style.transform = 'translateY(-20px)'" not in BANNER


# ── Alerts ────────────────────────────────────────────────────────

def test_errors_alert_and_are_not_auto_dismissed():
    block = _between(BANNER, "// A clean run auto-dismisses after 10s", "// Handle \"stuck\" expanded state")
    alert = block.index("if (errorCount > 0)")
    dismiss = block.index("dismissCompletionBanner()")
    assert "diAlert('.di-error-badge')" in block[alert:dismiss]
    assert block.index("} else {", alert) < dismiss  # the 10s dismiss is only for clean runs


def test_failure_alerts_once():
    failed = _between(BANNER, 'data-state="op-failed"', "{% elif maint_status is defined and maint_status.queue_countdown")
    assert "plexcache_last_failure_shown" in failed and "diAlert('.di-label')" in failed


# ── Concentric idle pills, round buttons, glow during a state morph ──

def test_idle_pills_are_concentric():
    assert re.search(r"#idle-ready-pill,\s*#idle-countdown-pill\s*\{\s*padding: 0 8px 0 12px;", CUSTOM_CSS)
    assert "#idle-ready-pill .di-pill__compact::after" in CUSTOM_CSS
    expanded = _between(CUSTOM_CSS, '#global-operation-banner[data-di-expanded="tier2"] #idle-ready-pill', "}")
    assert "padding: 0.85rem;" in expanded and "border-radius: calc(0.85rem + 14px);" in expanded
    assert ".di-idle-actions .di-btn { min-height: 28px; }" in CUSTOM_CSS


def test_pill_buttons_are_round_and_run_now_matches_run():
    btn = re.search(r"^\.di-btn \{[^}]*\}", CUSTOM_CSS, re.M).group(0)
    assert "border-radius: 999px;" in btn
    assert '.di-btn[data-di-shared="run"] svg { fill: currentColor; }' in CUSTOM_CSS


def test_glow_waits_during_a_state_morph():
    assert ".di-pill.di-morphing::after { opacity: 0; }" in CUSTOM_CSS
    assert "pill.classList.add('di-morphing')" in _between(ENGINE, "function _diStateMorph(", "function diTriggerRun(")


def test_crossfades_go_through_a_blur():
    fade_in = _between(CUSTOM_CSS, "@keyframes diFadeIn {", ".di-content-enter {")
    assert "blur(3px)" in fade_in
    assert re.search(r"\.di-content-enter \{ animation: diFadeIn [0-9.]+s ease-out both; \}", CUSTOM_CSS)
