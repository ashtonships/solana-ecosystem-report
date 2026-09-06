"""Offline regression checks for metric explanations and recorded inspection data."""

import re
import unittest
from copy import deepcopy
from unittest.mock import patch

import render
from tests import test_render as fixtures


class MetricInspectorTests(unittest.TestCase):
    def test_every_validator_card_has_its_own_named_explanation_in_both_layouts(self):
        snapshot = fixtures.TestRecoveredValidatorCharts.snapshot()
        ids = []
        for context in ("desktop", "mobile"):
            markup = render.render_validator_workbench(snapshot, context, history=[snapshot])
            cards = re.findall(r"<article\b[^>]*data-validator-metric-card.*?</article>", markup, re.DOTALL)
            self.assertEqual(len(cards), 7)
            for card in cards:
                self.assertEqual(card.count("data-metric-inspector>"), 1)
                self.assertIn("About this metric", card)
                self.assertIn("data-metric-inspector-picker", card)
                control_id = re.search(r"aria-controls='([^']+)'", card).group(1)
                self.assertIn(f"id='{control_id}' role='region'", card)
                ids.append(control_id)
        self.assertEqual(len(ids), len(set(ids)))

    def test_growth_cards_explain_scope_even_when_current_run_is_missing(self):
        snapshot = fixtures.load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {
            "available": True,
            "selected_usd_stablecoins": fixtures.selected_stablecoins_fixture(),
            "daily_active_addresses": {"available": False},
            "daily_fee_payers": {"available": False},
            "tokenized_equities": {
                "available": True,
                "registry_asset_count": 7,
                "eligible_asset_count": 7,
                "supply_coverage": {"coverage_numerator": 3, "coverage_denominator": 7},
                "volume": {"available": False},
                "assets": [],
            },
        }
        for context in ("desktop", "mobile"):
            markup = render.render_growth_workbench(snapshot, context)
            cards = re.findall(r"<article\b[^>]*data-growth-metric-card.*?</article>", markup, re.DOTALL)
            self.assertEqual(len(cards), 4)
            self.assertTrue(all(card.count("data-metric-inspector>") == 1 for card in cards))
            run = next(card for card in cards if "growth-run-component" in card)
            self.assertIn("Current supply-run coverage unavailable.", run)
            self.assertIn("successful supply queries divided by assets queried", run)
            market = next(card for card in cards if "growth-market-component" in card)
            self.assertIn("not total market activity", market)
            stable = next(card for card in cards if "growth-stablecoin-component" in card)
            self.assertIn("not a complete stablecoin market total", stable)

    def test_inspectable_history_points_are_actual_eligible_readings(self):
        snapshot = fixtures.TestRecoveredValidatorCharts.snapshot()
        earlier = deepcopy(snapshot)
        earlier["collected_at"] = "2026-08-05T10:00:00Z"
        earlier["validators"]["active_stake_sol"] = 12345
        snapshot["collected_at"] = "2026-08-05T11:00:00Z"
        snapshot["validators"]["active_stake_sol"] = 23456
        charts = dict(render.render_validator_evidence_cards(snapshot, [earlier, snapshot], None))
        history = charts["Active-stake history"]
        self.assertEqual(history.count("data-metric-sample"), 2)
        self.assertIn("2026-08-05T10:00:00Z: 12,345.0 SOL</title>", history)
        self.assertIn("2026-08-05T11:00:00Z: 23,456.0 SOL</title>", history)
        earlier["validators"]["active_stake_sol"] = None
        charts = dict(render.render_validator_evidence_cards(snapshot, [earlier, snapshot], None))
        self.assertEqual(charts["Active-stake history"].count("data-metric-sample"), 1)
        self.assertIn("Unavailable / no new observation", charts["Active-stake history"])

    def test_distribution_targets_keep_exact_counts_and_population_labels(self):
        snapshot = fixtures.TestRecoveredValidatorCharts.snapshot()
        charts = dict(render.render_validator_evidence_cards(snapshot, [snapshot], None))
        self.assertIn("skip rate: 1 production identities", charts["Skip-rate distribution"])
        self.assertIn("each bar counts production identities, not vote accounts", charts["Skip-rate distribution"])
        markup = render.render_validator_workbench(snapshot, "mobile")
        self.assertIn("0% commission: 1 current accounts", markup)
        self.assertIn("median is not weighted by stake", markup)

    def test_inspector_escapes_title_and_copy_and_retains_card_bindings(self):
        with patch.dict(render.METRIC_INSPECTOR_COPY, {"test": ("A < B", "A & B <script>")}):
            markup = render.add_metric_inspector("<article data-observation-id='retained'>evidence</article>", "test", "mobile")
        self.assertIn("data-observation-id='retained'", markup)
        self.assertIn("A &lt; B", markup)
        self.assertIn("A &amp; B &lt;script&gt;", markup)
        self.assertNotIn("<script>", markup)


if __name__ == "__main__":
    unittest.main()
