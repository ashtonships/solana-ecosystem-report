"""Terminology tooltip acceptance: definitions render on the evidence
cards with hover/focus affordance, and unknown terms pass through."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import terms


class TestTerms(unittest.TestCase):
    def test_known_term_gets_tooltip_affordance(self):
        out = terms.term("nakamoto coefficient", "validators for 33⅓%")
        self.assertIn("term-tip", out)
        self.assertIn("tabindex='0'", out)
        self.assertIn("role='tooltip'", out)
        self.assertIn("collude", out)
        self.assertIn("validators for 33⅓%", out)

    def test_unknown_term_passes_through_escaped(self):
        out = terms.term("not a term")
        self.assertNotIn("term-tip", out)
        self.assertEqual(out, "not a term")

    def test_definitions_exist_for_judge_facing_vocabulary(self):
        for name in ("vote-account state", "current validators",
                     "delinquent validator", "activated stake",
                     "nakamoto coefficient", "commission", "skip rate",
                     "measured", "sampled", "provider-reported", "recorded",
                     "derived"):
            self.assertIn(name, terms.TERM_DEFINITIONS, name)


if __name__ == "__main__":
    unittest.main()
