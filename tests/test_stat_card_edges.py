"""
Stat cards only get a coloured left edge for problems (warning, danger).

An OK card already shows its state through its icon and value colour, so a
green edge made the card that needs no attention the loudest one on the page.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME_CSS = (ROOT / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")


def test_no_success_edge_rule():
    assert ".stat-card-success" not in THEME_CSS


def test_problem_edges_remain():
    assert ".stat-card-danger {" in THEME_CSS
    assert ".stat-card-warning {" in THEME_CSS


def test_templates_do_not_emit_the_success_edge():
    for path in (ROOT / "web" / "templates").rglob("*.html"):
        assert "stat-card-success" not in path.read_text(encoding="utf-8"), path
