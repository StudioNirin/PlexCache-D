"""
The pill's springs, checked by running the engine (static/js/pill-motion.js)
in Node with a minimal stand-in for the browser.

PillMotion.spring(duration, bounce) follows SwiftUI's spring(duration:bounce:)
(WWDC23 "Animate with springs"): bounce sets the damping ratio (1 - bounce),
so a 0 bounce settles without overshoot and the overshoot grows with bounce.
The curve is handed to CSS as a linear() easing. Skipped when Node is absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "web" / "static" / "js" / "pill-motion.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
function load(supportsLinear) {
    const noop = () => {};
    const doc = { addEventListener: noop, getElementById: () => null, querySelector: () => null,
                  querySelectorAll: () => [], documentElement: {} };
    const win = { matchMedia: () => ({ matches: false }), addEventListener: noop };
    const ctx = { window: win, document: doc, setTimeout, clearTimeout, console,
                  CSS: { supports: () => supportsLinear } };
    win.CSS = ctx.CSS;
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    return win.PillMotion;
}
const PM = load(true);
const out = {};
for (const b of [0, 0.12, 0.25, 0.4]) {
    const s = PM.spring(380, b);
    const pts = s.easing.slice(7, -1).split(', ').map(Number);
    out[b] = { first: pts[0], last: pts[pts.length - 1], max: Math.max(...pts), total: s.total,
               n: pts.length, monotonic: pts.every((v, i) => i === 0 || v >= pts[i - 1] - 1e-9) };
}
out.cached = PM.spring(380, 0.25) === PM.spring(380, 0.25);
out.fallback = load(false).spring(380, 0.25);
out.config = PM.config;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run([NODE, "-e", HARNESS, str(ENGINE)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_curves_start_at_rest_and_end_on_target(result):
    for b in ("0", "0.12", "0.25", "0.4"):
        assert result[b]["first"] == 0 and result[b]["last"] == 1, b
        assert result[b]["n"] >= 40, b  # enough points for a smooth linear()


def test_no_bounce_means_no_overshoot(result):
    assert result["0"]["max"] <= 1.0001
    assert result["0"]["monotonic"]


def test_overshoot_matches_the_damping_ratio(result):
    """Peak overshoot of an underdamped spring: exp(-zeta*pi/sqrt(1-zeta^2)),
    zeta = 1 - bounce: 0.25 -> ~2.8%, 0.4 -> ~9.5%. The curve is sampled, so
    the sampled peak may sit slightly under the true one."""
    import math
    for b in (0.12, 0.25, 0.4):
        z = 1 - b
        peak = math.exp(-z * math.pi / math.sqrt(1 - z * z))
        assert peak * 0.85 <= result[str(b)]["max"] - 1 <= peak * 1.05, (b, result[str(b)]["max"])


def test_more_bounce_overshoots_more(result):
    assert result["0.12"]["max"] < result["0.25"]["max"] < result["0.4"]["max"]


def test_total_covers_the_settle(result):
    """The transition runs until the spring has settled, past the perceived duration."""
    for b in ("0", "0.25", "0.4"):
        assert result[b]["total"] > 380, b
    assert result["0.4"]["total"] > result["0.25"]["total"]  # more bounce, longer tail


def test_springs_are_cached(result):
    assert result["cached"] is True


def test_browsers_without_linear_get_an_ease_out(result):
    assert result["fallback"]["easing"].startswith("cubic-bezier(")
    assert result["fallback"]["total"] == 380


def test_default_config(result):
    assert result["config"]["bounce"] == 0.25
    assert result["config"]["contract"] is True
