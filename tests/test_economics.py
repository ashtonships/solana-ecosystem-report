"""Offline tests for the economic-indicator transforms.

No network. Each test feeds the shape the real API returns — captured from live
responses — to a pure function. The central property under test is that a
failed third-party source degrades to `available: false` rather than to zero.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import economics  # noqa: E402

DAY = 86_400


class TestTvl(unittest.TestCase):
    def series(self, days=30, tvl=4_000_000_000):
        base = 1_785_888_000
        return [{"date": base - (days - i) * DAY, "tvl": tvl + i * 1_000_000} for i in range(days)]

    def test_uses_the_newest_point(self):
        summary = economics.summarize_tvl(self.series())
        self.assertTrue(summary["available"])
        self.assertEqual(summary["tvl_usd"], 4_029_000_000.0)

    def test_computes_a_seven_day_change(self):
        series = [
            {"date": 1_785_888_000 - 7 * DAY, "tvl": 4_000_000_000},
            {"date": 1_785_888_000, "tvl": 4_400_000_000},
        ]
        self.assertEqual(economics.summarize_tvl(series)["change_7d_pct"], 10.0)

    def test_short_history_reports_no_change_rather_than_zero(self):
        # "We don't have 7 days of history" is not "TVL didn't move".
        series = [{"date": 1_785_888_000, "tvl": 4_000_000_000}]
        summary = economics.summarize_tvl(series)
        self.assertTrue(summary["available"])
        self.assertIsNone(summary["change_7d_pct"])

    def test_unsorted_input_still_finds_the_latest(self):
        series = list(reversed(self.series()))
        self.assertEqual(economics.summarize_tvl(series)["tvl_usd"], 4_029_000_000.0)

    def test_failed_source_is_unavailable_not_zero(self):
        for bad in (None, [], "nope", {}, [{"no": "fields"}]):
            self.assertFalse(economics.summarize_tvl(bad)["available"])


class TestPrice(unittest.TestCase):
    def live_shape(self):
        return {"solana": {
            "usd": 73.97, "usd_market_cap": 43000225604.74,
            "usd_24h_vol": 1524788499.32, "usd_24h_change": 1.2,
        }}

    def test_extracts_every_field(self):
        summary = economics.summarize_price(self.live_shape())
        self.assertEqual(summary["price_usd"], 73.97)
        self.assertEqual(summary["change_24h_pct"], 1.2)
        self.assertEqual(summary["market_cap_usd"], 43000225604.74)

    def test_partial_response_keeps_price_and_nulls_the_rest(self):
        summary = economics.summarize_price({"solana": {"usd": 73.97}})
        self.assertTrue(summary["available"])
        self.assertIsNone(summary["market_cap_usd"])
        self.assertIsNone(summary["change_24h_pct"])

    def test_failed_source_is_unavailable(self):
        for bad in (None, {}, {"solana": {}}, {"bitcoin": {"usd": 1}}, "nope"):
            self.assertFalse(economics.summarize_price(bad)["available"])


class TestStablecoins(unittest.TestCase):
    def chains(self):
        return [
            {"name": "Ethereum", "gecko_id": "ethereum",
             "totalCirculatingUSD": {"peggedUSD": 100_000_000_000}},
            {"name": "Solana", "gecko_id": "solana",
             "totalCirculatingUSD": {"peggedUSD": 15_820_740_037.79, "peggedEUR": 58_012_441.39}},
        ]

    def test_finds_solana_among_all_chains(self):
        summary = economics.summarize_stablecoins(self.chains())
        self.assertEqual(summary["stablecoin_usd"], 15_820_740_037.79)

    def test_separates_non_usd_pegs_from_the_headline(self):
        # The headline says "USD stablecoins"; EUR/JPY pegs must not inflate it.
        self.assertEqual(economics.summarize_stablecoins(self.chains())["non_usd_pegged_usd"],
                         58_012_441.39)

    def test_missing_solana_row_is_unavailable(self):
        self.assertFalse(economics.summarize_stablecoins([
            {"name": "Ethereum", "gecko_id": "ethereum", "totalCirculatingUSD": {"peggedUSD": 1}},
        ])["available"])

    def test_failed_source_is_unavailable(self):
        for bad in (None, {}, "nope", []):
            self.assertFalse(economics.summarize_stablecoins(bad)["available"])


class TestDex(unittest.TestCase):
    def test_extracts_volume_and_change(self):
        summary = economics.summarize_dex({
            "total24h": 1_738_979_711.93, "total7d": 11_188_344_624.98, "change_1d": 1.54,
        })
        self.assertEqual(summary["volume_24h_usd"], 1_738_979_711.93)
        self.assertEqual(summary["change_1d_pct"], 1.54)

    def test_failed_source_is_unavailable(self):
        for bad in (None, {}, {"total24h": "not a number"}, "nope"):
            self.assertFalse(economics.summarize_dex(bad)["available"])


class TestBuildEconomics(unittest.TestCase):
    def test_one_failed_source_does_not_take_out_the_others(self):
        # The property that matters: DeFiLlama going down must not blank the price.
        result = economics.build_economics({
            "price": {"solana": {"usd": 73.97}},
            "tvl": None,           # simulated outage
            "stablecoins": None,   # simulated outage
            "dex": None,           # simulated outage
        })
        self.assertTrue(result["available"])
        self.assertTrue(result["price"]["available"])
        self.assertFalse(result["tvl"]["available"])
        self.assertEqual(
            {n for n, s in result["sources"].items() if not s["available"]},
            {"tvl", "stablecoins", "dex"},
        )

    def test_total_outage_is_unavailable_not_a_page_of_zeros(self):
        result = economics.build_economics({k: None for k in economics.SOURCES})
        self.assertFalse(result["available"])
        for part in ("price", "tvl", "stablecoins", "dex"):
            self.assertFalse(result[part]["available"])
            # Crucially: no zero-valued figure anywhere to be mistaken for data.
            self.assertNotIn("tvl_usd", result["tvl"])
            self.assertNotIn("price_usd", result["price"])

    def test_records_that_no_source_needs_a_key(self):
        result = economics.build_economics({k: None for k in economics.SOURCES})
        self.assertFalse(result["requires_api_key"])

    def test_every_source_url_is_reported_for_reproducibility(self):
        result = economics.build_economics({k: None for k in economics.SOURCES})
        for name, source in result["sources"].items():
            self.assertEqual(source["url"], economics.SOURCES[name])


if __name__ == "__main__":
    unittest.main()
