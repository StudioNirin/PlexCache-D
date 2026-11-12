"""
The pill's FLIP collapse lands on the compact pill's real height.

_diDoFlip measures the end state by clearing its inline overrides and reading
the CSS. It used to force height: 'auto' for that read. The expanded states
are height: auto in CSS anyway, but the compact pill is a fixed 38px (idle) or
44px, and 'auto' measured only its content row (22px). Every collapse then
shrank the pill past its real size and snapped back down at cleanup, which
read as a bounce.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNER = (ROOT / "web" / "static" / "js" / "pill-motion.js").read_text(encoding="utf-8")  # the engine
CUSTOM_CSS = (ROOT / "web" / "static" / "css" / "custom.css").read_text(encoding="utf-8")


def _measure_step():
    flip = BANNER[BANNER.index("function _diDoFlip("):]
    return flip[flip.index("/* 4. Measure target"):flip.index("var endHeight")]


def test_measure_reads_the_css_height():
    step = _measure_step()
    assert "pill.style.height = '';" in step
    assert not re.search(r"pill\.style\.height\s*=\s*['\"]auto['\"]", step)


def test_width_is_animated_not_just_min_width():
    """Animating only min-width let the incoming content's natural width resize
    the pill in the first frame (the idle pill jumped from 202px to 311px)."""
    flip = BANNER[BANNER.index("function _diDoFlip("):BANNER.index("function diMinimize(")]
    props = re.search(r"var animProps = \[([^\]]*)\]", flip).group(1)
    assert "'width'" in props and "'min-width'" not in props
    assert "pill.style.width = startWidth + 'px';" in flip
    assert "width: endWidth + 'px'" in flip  # the end of the shape table, applied on the spring
    assert "pill.style.minWidth = '0px';" in flip


def test_content_is_held_at_its_final_width_and_released():
    flip = BANNER[BANNER.index("function _diDoFlip("):BANNER.index("function diMinimize(")]
    assert "content.style.width = content.offsetWidth + 'px';" in flip
    cleanup = flip[flip.index("function cleanup()"):]
    for prop in ("width", "flexShrink", "marginLeft", "marginRight"):
        assert f"content.style.{prop} = '';" in cleanup, prop


def test_compact_pills_have_fixed_heights():
    """The collapse target depends on these: a compact pill that sized to its
    content would need the FLIP to measure differently."""
    base = CUSTOM_CSS[CUSTOM_CSS.index(".di-pill {"):]
    assert "height: 44px;" in base[:base.index("}")]
    idle = CUSTOM_CSS[CUSTOM_CSS.index(".di-pill--idle {"):]
    assert "height: 38px;" in idle[:idle.index("}")]
