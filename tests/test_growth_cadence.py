"""Focused contracts for independently scheduled growth slices."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import growth  # noqa: E402


class GrowthCadenceTests(unittest.TestCase):
    def previous_growth(self):
        return {
            "available": True,
            "requires_api_key": False,
            "tokenized_equities": {"available": True, "marker": "tokens"},
            "selected_usd_stablecoins": {"coverage_numerator": 4},
            "daily_active_addresses": {"history_available": True, "marker": "old"},
            "daily_fee_payers": {"history_available": False},
            "sources": {
                "registry": {"available": True, "marker": "registry"},
                "activity_benchmark": {"available": True, "marker": "old-source"},
            },
        }

    def test_provider_refresh_preserves_the_exact_token_slice(self):
        prior = self.previous_growth()
        before = deepcopy(prior)
        provider = {
            "daily_active_addresses": {"history_available": True, "marker": "new"},
            "daily_fee_payers": {"history_available": False, "marker": "new"},
            "source": {"available": True, "marker": "new-source"},
        }
        with patch.object(growth, "_provider_benchmarks", return_value=provider):
            refreshed = growth.refresh_growth_providers(prior)

        self.assertEqual(prior, before)
        self.assertEqual(refreshed["tokenized_equities"], before["tokenized_equities"])
        self.assertEqual(refreshed["selected_usd_stablecoins"],
                         before["selected_usd_stablecoins"])
        self.assertEqual(refreshed["sources"]["registry"],
                         before["sources"]["registry"])
        self.assertEqual(refreshed["daily_active_addresses"]["marker"], "new")
        self.assertEqual(refreshed["sources"]["activity_benchmark"]["marker"],
                         "new-source")

    def test_token_refresh_can_reuse_provider_slice_without_fetching_it(self):
        prior = self.previous_growth()
        stablecoins = growth.summarize_selected_usd_stablecoin_supplies({})
        with patch.object(growth, "fetch_xstocks_registry", return_value={
            "products": [], "coverage_complete": True,
        }), patch.object(
            growth, "fetch_selected_usd_stablecoin_supplies",
            return_value=stablecoins,
        ), patch.object(growth, "fetch_json") as provider_fetch:
            refreshed, _ = growth.collect_growth(
                "https://api.mainnet.solana.com",
                with_providers=False, previous_growth=prior,
            )

        provider_fetch.assert_not_called()
        self.assertEqual(refreshed["daily_active_addresses"],
                         prior["daily_active_addresses"])
        self.assertEqual(refreshed["daily_fee_payers"], prior["daily_fee_payers"])
        self.assertEqual(refreshed["sources"]["activity_benchmark"],
                         prior["sources"]["activity_benchmark"])


if __name__ == "__main__":
    unittest.main()
