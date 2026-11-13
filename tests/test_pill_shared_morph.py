"""
The idle pills morph between compact and expanded instead of swapping views.

Elements carrying the same data-di-shared key in both views (icon, label, Run
button) travel from one spot to the other while the rest of each view fades.
A key on only one side would have nothing to travel to.
"""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "web" / "templates"
BANNER = "components/global_operation_banner.html"
SOURCE = (TEMPLATES_DIR.parent / "static" / "js" / "pill-motion.js").read_text(encoding="utf-8")  # the engine


def _render(**ctx):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    for name in ("truncate_filename", "format_bytes", "format_time", "format_datetime"):
        env.filters[name] = lambda value, *args, **kwargs: value
    ctx.setdefault("status", {"state": "idle"})
    return env.get_template(BANNER).render(**ctx)


def _views(html, pill_id):
    pill = html[html.index(f'id="{pill_id}"'):]
    pill = pill[:pill.index("</script>")]
    compact = pill[pill.index('class="di-pill__compact"'):pill.index('class="di-pill__expanded"')]
    expanded = pill[pill.index('class="di-pill__expanded"'):]
    return compact, expanded


def _keys(fragment):
    return re.findall(r'data-di-shared="([^"]+)"', fragment)


@pytest.mark.parametrize("scheduler_status, pill_id", [
    ({"next_run_relative": "32m"}, "idle-countdown-pill"),
    (None, "idle-ready-pill"),
], ids=["countdown", "ready"])
def test_every_shared_key_has_one_partner(scheduler_status, pill_id):
    compact, expanded = _views(_render(scheduler_status=scheduler_status), pill_id)
    assert sorted(_keys(compact)) == sorted(_keys(expanded)) == ["icon", "label", "run"]


def test_countdown_label_is_the_shared_element():
    """The whole label is shared, so the extra words ("in 32m") travel with the
    shared ones instead of fading in at their final spot while the words are
    still on their way there."""
    _, expanded = _views(_render(scheduler_status={"next_run_relative": "32m"}), "idle-countdown-pill")
    assert '<span class="di-label" data-di-shared="label">Next run in 32m</span>' in expanded


def test_ready_pill_shows_the_last_run():
    """Without a schedule the compact pill says how long ago the last run was,
    and that line travels into the expanded detail line, under the title and
    above a link to set a schedule."""
    info = {"compact": "Ran 2 hr ago", "detail": "Last run 2 hr ago · 12 cached (38.20 GB)"}
    compact, expanded = _views(_render(scheduler_status=None, idle_info=info), "idle-ready-pill")
    assert 'data-di-num="last-run"' in compact and ">Ran 2 hr ago</span>" in compact
    assert 'data-di-shared="label" data-di-num="last-run-detail">Last run 2 hr ago · 12 cached (38.20 GB)</span>' in expanded
    assert '<span class="di-label">PlexCache Ready</span>' in expanded
    assert 'href="/settings/schedule#setting-schedule_enabled"' in expanded


def test_ready_pill_without_info_falls_back():
    compact, expanded = _views(_render(scheduler_status=None), "idle-ready-pill")
    assert ">PlexCache</span>" in compact
    assert ">Ready to run</span>" in expanded


@pytest.mark.parametrize("scheduler_status, pill_id", [
    ({"next_run_relative": "32m"}, "idle-countdown-pill"),
    (None, "idle-ready-pill"),
], ids=["countdown", "ready"])
def test_icon_circle_travels_with_the_icon(scheduler_status, pill_id):
    """The tinted circle is the shared element, lined up by the glyph inside
    it, so it moves with the icon instead of fading in at its final spot."""
    _, expanded = _views(_render(scheduler_status=scheduler_status), pill_id)
    circle = expanded[expanded.index('class="di-icon" data-di-shared="icon"'):]
    circle = circle[:circle.index("</div>")]
    assert "data-di-anchor" in circle


def test_engine_wires_the_morph_into_the_flip():
    flip = SOURCE[SOURCE.index("function _diDoFlip("):SOURCE.index("function diMinimize(")]
    assert flip.index("_diMorphCapture(") < flip.index("_diApplyExpandState(toState)")
    assert "_diMorphMeasureEnd(morph, pill)" in flip
    assert "_diMorphStart(morph, pill, perceived, spring," in flip
    cleanup = flip[flip.index("function cleanup()"):]
    assert "_diMorphCleanup(morph)" in cleanup


def test_morph_is_only_between_compact_and_expanded():
    capture = SOURCE[SOURCE.index("function _diMorphCapture("):SOURCE.index("function _diMorphMeasureEnd(")]
    assert "(fromState === 'false') === (toState === 'false')" in capture


def test_collapse_skips_the_exit_delay_when_morphing():
    """Every pill morphs back to compact; only closing the detail panel uses the exit fade."""
    smooth = SOURCE[SOURCE.index("function diSmoothSetState("):SOURCE.index("function _diViewFor(")]
    morph_branch = smooth[smooth.index("if (state === 'false') {"):]
    assert morph_branch.index("_diDoFlip(") < morph_branch.index("cfg.exitOverlapMs")


def test_morph_needs_no_shared_pair():
    """A pill without matching elements still contracts/extends instead of fading."""
    capture = SOURCE[SOURCE.index("function _diMorphCapture("):SOURCE.index("function _diMorphMeasureEnd(")]
    assert "if (!pairs.length) return null;" not in capture
