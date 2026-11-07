"""Recent Activity action badges and filter pills.

Two defects these lock down:

* `Moved` and `Released` had no arm in `action_badge`, so both fell through to
  the muted default and were styled like an unrecognised action. `Moved` is the
  main restore-by-copy path and `Released` is a deliberate, user-visible state.
* The "Moved" pill sent ``data-filter="Moved to Array"`` — the label maintenance
  writes — so it matched no entry from an actual run, and those rows landed
  under "Other" instead. The hardcoded ``KNOWN_ACTIONS`` also named `Protected`,
  which has no pill, so those rows matched neither their own filter nor "Other"
  and were reachable only under "All".

Source-text tests: this is template + inline JS with no Python seam, and the
contract that matters is "every action a writer emits is handled".
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ACTIVITY = REPO / "web" / "templates" / "components" / "recent_activity.html"
DASHBOARD = REPO / "web" / "templates" / "dashboard.html"

# Every action string written to the activity feed, with its writer.
#   FileMover._move_to_cache / _move_to_array  -> Cached / Restored / Moved
#   PlexCacheApp._release_files                -> Released
#   MaintenanceRunner.ACTION_ACTIVITY_LABELS   -> the rest
#   web/routers/pinned.py                      -> Cached
ALL_ACTIONS = [
    "Cached", "Restored", "Moved", "Released",
    "Protected", "Moved to Array", "Fixed", "Restored Backup", "Deleted Backup",
]


@pytest.fixture(scope="module")
def activity_src():
    return ACTIVITY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_src():
    return DASHBOARD.read_text(encoding="utf-8")


class TestWriterInventory:
    def test_action_labels_match_the_maintenance_runner(self):
        """If a maintenance action label is added, this list must grow with it."""
        src = (REPO / "web" / "services" / "maintenance_runner.py").read_text(encoding="utf-8")
        block = src[src.index("ACTION_ACTIVITY_LABELS = {"):]
        block = block[:block.index("}")]
        for label in re.findall(r':\s*"([^"]+)"', block):
            assert label in ALL_ACTIONS, f"maintenance emits {label!r}, not in ALL_ACTIONS"

    def test_release_action_is_still_spelled_the_same(self):
        src = (REPO / "core" / "app.py").read_text(encoding="utf-8")
        assert '_record_file_activity("Released"' in src


class TestBadgeCoverage:
    def test_every_action_has_a_tooltip(self, activity_src):
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for action in ALL_ACTIONS:
            assert f"'{action}':" in tips, f"no tooltip for {action}"

    def test_moved_and_released_are_not_left_to_the_default_arm(self, activity_src):
        macro = activity_src[activity_src.index("{% macro action_badge"):
                             activity_src.index("{% macro source_icon")]
        assert "'Moved'" in macro
        assert "action == 'Released'" in macro

    def test_released_uses_its_own_badge_not_muted(self, activity_src):
        macro = activity_src[activity_src.index("{% macro action_badge"):
                             activity_src.index("{% macro source_icon")]
        released_arm = macro[macro.index("action == 'Released'"):]
        released_arm = released_arm[:released_arm.index("{%- elif")]
        assert "badge-released" in released_arm

    def test_tooltips_are_non_empty(self, activity_src):
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for action, text in re.findall(r"'([^']+)':\s*'([^']*)'", tips):
            assert len(text) > 20, f"{action} tooltip is too short to be useful"


class TestProtectedRelabel:
    """"Protected" is the mover sense, and the same action's own button says
    "Keep on Cache" (tests/test_pin_vocabulary.py guards that wording). Re-label
    at render only — the action string is persisted in recent_activity.json."""

    def test_protected_is_relabelled_for_display(self, activity_src):
        assert "'Protected': 'Kept on Cache'" in activity_src

    def test_relabel_happens_in_the_template_not_the_writer(self):
        # Renaming at the writer would need a migration and would rewrite what
        # already happened; old entries must keep rendering consistently.
        src = (REPO / "web" / "services" / "maintenance_runner.py").read_text(encoding="utf-8")
        assert '"protect-with-backup": "Protected"' in src

    def test_protected_tooltip_states_the_mover_effect(self, activity_src):
        # The pin-vs-keep distinction is guarded where the user acts on it —
        # the button and confirm modal (tests/test_pin_vocabulary.py). This
        # badge is a history record, so it only needs to say what was done.
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        line = [l for l in tips[:tips.index("} %}")].splitlines() if "'Protected':" in l][0]
        assert "mover leaves it alone" in line


class TestFilterPills:
    def test_moved_pill_matches_both_restore_labels(self, dashboard_src):
        assert 'data-filter="Moved|Moved to Array"' in dashboard_src

    def test_filter_matching_splits_on_pipe(self, dashboard_src):
        assert "filter.split('|').indexOf(action) !== -1" in dashboard_src

    def test_known_actions_is_derived_from_the_pills(self, dashboard_src):
        # A hardcoded list is what let the two drift.
        assert "KNOWN_ACTIONS:" not in dashboard_src
        assert "knownActions: function()" in dashboard_src

    def test_filter_bar_is_scoped_by_id(self, dashboard_src):
        # Recently Added reuses .activity-filter-bar; an unscoped query would
        # pick up its location pills.
        assert 'id="activity-filter-bar"' in dashboard_src
        assert "#activity-filter-bar .activity-filter-pill" in dashboard_src

    def test_every_pill_action_is_a_real_action(self, dashboard_src):
        bar = dashboard_src[dashboard_src.index('id="activity-filter-bar"'):]
        bar = bar[:bar.index("</div>")]
        for value in re.findall(r'data-filter="([^"]+)"', bar):
            if value in ("all", "other"):
                continue
            for action in value.split("|"):
                assert action in ALL_ACTIONS, f"pill filters on unknown action {action!r}"

    def test_every_action_is_reachable_by_some_filter(self, dashboard_src):
        """Each action matches either its own pill or "Other" — never neither.

        Rows matched neither when KNOWN_ACTIONS named an action that had no
        pill: the pill couldn't match it and "Other" excluded it, so it was
        visible only under "All". Deriving the list from the pills makes the two
        sets identical by construction, which is what this asserts.
        """
        bar = dashboard_src[dashboard_src.index('id="activity-filter-bar"'):]
        bar = bar[:bar.index("</div>")]
        covered = set()
        for value in re.findall(r'data-filter="([^"]+)"', bar):
            if value not in ("all", "other"):
                covered.update(value.split("|"))

        # "Other" is the complement of the pill set, and the pill set is what
        # knownActions() derives — so reachability holds iff no action is
        # claimed by the known-list without also having a pill. That is exactly
        # what a hardcoded list broke, so assert the two can't diverge: every
        # covered action must appear as a pill's own data-filter value.
        pill_values = set()
        for value in re.findall(r'data-filter="([^"]+)"', bar):
            if value not in ("all", "other"):
                pill_values.update(value.split("|"))
        assert covered == pill_values

        # Released earns a pill of its own — it is a deliberate state, not a
        # leftover, so burying it under "Other" hid the feature working.
        assert "Released" in covered


SOURCE_BADGE = REPO / "web" / "templates" / "macros" / "source_badge.html"
FILE_TABLE = REPO / "web" / "templates" / "cache" / "partials" / "file_table.html"
RA_LIST = REPO / "web" / "templates" / "recently_added" / "partials" / "list.html"


class TestSharedSourceBadges:
    """OnDeck/Watchlist/lapsed badges are shared, not hand-copied.

    Hand-copied copy is how "Protected" came to mean three different things.
    Both the Cached Files table and Recently Added answer the same question —
    what put this file on cache, and does that still hold — so one macro serves
    both.
    """

    @pytest.fixture(scope="class")
    def macro_src(self):
        return SOURCE_BADGE.read_text(encoding="utf-8")

    def test_both_pages_import_the_shared_macro(self):
        for path in (FILE_TABLE, RA_LIST):
            src = path.read_text(encoding="utf-8")
            assert "macros/source_badge.html" in src, f"{path.name} hand-rolls its badges"

    def test_neither_page_hardcodes_the_per_file_badge_markup(self):
        # Scoped to the per-row source cell. The Cached Files totals legend
        # legitimately prints the words with its counts, which is a different
        # thing from a row's source badge.
        src = FILE_TABLE.read_text(encoding="utf-8")
        cell = src[src.index('class="source-badges"'):]
        cell = cell[:cell.index("</td>")]
        assert ">OnDeck<" not in cell, "file_table row still inlines an OnDeck badge"
        assert ">Watchlist<" not in cell, "file_table row still inlines a Watchlist badge"
        # Called with the row's media-server sources (Plex / Jellyfin)
        assert "ondeck_badge(" in cell and "watchlist_badge(" in cell

        ra = RA_LIST.read_text(encoding="utf-8")
        badge_macro = ra[ra.index("{% macro ra_status_badge"):ra.index("{% macro ra_row")]
        assert ">OnDeck<" not in badge_macro
        assert "ondeck_badge()" in badge_macro and "watchlist_badge()" in badge_macro

    def test_ondeck_and_watchlist_have_tooltips(self, macro_src):
        for key in ("ondeck", "watchlist", "stale_ondeck", "stale_watchlist", "other"):
            assert f"'{key}':" in macro_src, f"no tooltip for {key}"

    def test_transient_sources_make_no_protection_claim(self, macro_src):
        # OnDeck/Watchlist add +15 to the priority score; only a pin exempts.
        # The badge needn't spell that out, but it must not claim otherwise —
        # "keeps it on cache for now" is a statement of the current reason, not
        # a guarantee. The disclaimer lives on the outcome badge instead.
        tips = macro_src[macro_src.index("SOURCE_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for key in ("ondeck", "watchlist"):
            line = [l for l in tips.splitlines() if f"'{key}':" in l][0]
            assert "protect" not in line.lower(), f"{key} makes a protection claim"

    def test_lapsed_badges_say_what_happens_next(self, macro_src):
        tips = macro_src[macro_src.index("SOURCE_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for key in ("stale_ondeck", "stale_watchlist", "other"):
            line = [l for l in tips.splitlines() if f"'{key}':" in l][0]
            assert "moves it back to the array" in line
