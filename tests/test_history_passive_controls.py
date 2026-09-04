"""Focused contracts for honest desktop History affordances."""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import render  # noqa: E402


def fixture():
    return json.loads((ROOT / "fixtures" / "sample-snapshot.json").read_text())


class HistoryPassiveControlsTests(unittest.TestCase):
    def test_snapshot_rail_is_a_passive_timeline_beside_the_real_pair_selectors(self):
        first = fixture()
        second = fixture()
        first["collected_at"] = "2026-08-05T08:00:00Z"
        second["collected_at"] = "2026-08-05T09:00:00Z"

        markup = render.render_history_workspace([first, second], None, None)

        self.assertIn("<ol class='snapshot-list' aria-label='Recorded snapshot timeline'>", markup)
        self.assertIn("<li class='snapshot is-selected' aria-current='true'>", markup)
        self.assertIn("data-desktop-history-a", markup)
        self.assertIn("data-desktop-history-b", markup)
        self.assertNotIn("name='history-snapshot'", markup)
        self.assertNotIn("<fieldset class='snapshot-list'", markup)
        self.assertNotIn("class='static-button'", markup)

    def test_change_markers_link_to_the_machine_readable_history(self):
        markup = render.render_change_markers(fixture(), None, None)

        self.assertIn("<a class='change-link' href='report.json'>", markup)
        self.assertIn("View full report data", markup)
        self.assertNotIn("<button class='change-link'", markup)
        self.assertNotIn("disabled", markup)


if __name__ == "__main__":
    unittest.main()
