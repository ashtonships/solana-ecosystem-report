"""Offline tests for the economic-indicator transforms.

No network. Each test feeds a provider-shaped payload containing deliberately
synthetic values to a pure function. The central property under test is that a
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

    def test_retains_every_source_native_point_as_a_deduplicated_fact(self):
        raw = self.series(days=3)
        facts = economics.tvl_history_facts(
            [raw[1], raw[0], raw[1], raw[2]], "2026-08-25T22:00:00+00:00",
        )
        self.assertEqual(len(facts), 3)
        self.assertEqual([fact["value"] for fact in facts],
                         [point["tvl"] for point in raw])
        self.assertTrue(all(fact["basis"] == "recorded" for fact in facts))
        self.assertTrue(all(fact["event_time"] for fact in facts))

    def test_conflicting_duplicate_tvl_dates_fail_closed(self):
        raw = [
            {"date": 1_785_888_000, "tvl": 4_000_000_000},
            {"date": 1_785_888_000, "tvl": 4_100_000_000},
        ]
        with self.assertRaises(economics.facts.FactConflictError):
            economics.tvl_history_facts(raw, "2026-08-25T22:00:00+00:00")


class TestPrice(unittest.TestCase):
    def provider_shape(self):
        return {"solana": {
            "usd": 123.45, "usd_market_cap": 12_345_678.90,
            "usd_24h_vol": 1_234_567.80, "usd_24h_change": 1.25,
            "last_updated_at": 1_700_000_000,
        }}

    def test_extracts_every_field(self):
        summary = economics.summarize_price(self.provider_shape())
        self.assertEqual(summary["price_usd"], 123.45)
        self.assertEqual(summary["change_24h_pct"], 1.25)
        self.assertEqual(summary["market_cap_usd"], 12_345_678.90)
        self.assertEqual(summary["last_updated_at_unix"], 1_700_000_000)

    def test_price_freshness_is_explicit(self):
        raw = self.provider_shape()
        self.assertEqual(economics.summarize_price(raw, 1_700_000_100)["freshness"], "fresh")
        self.assertEqual(economics.summarize_price(raw, 1_700_001_000)["freshness"], "stale")

    def test_missing_price_timestamp_is_not_called_fresh(self):
        summary = economics.summarize_price({"solana": {"usd": 123.45}}, 1_700_000_000)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["freshness"], "missing")
        self.assertIsNone(summary["last_updated_at_unix"])

    def test_build_economics_preserves_price_freshness_in_source_metadata(self):
        summary = economics.build_economics({"price": self.provider_shape()}, 1_700_000_100)
        self.assertEqual(summary["price"]["freshness"], "fresh")
        self.assertEqual(summary["sources"]["price"]["freshness"], "fresh")
        self.assertEqual(summary["sources"]["price"]["last_updated_at_unix"], 1_700_000_000)

    def test_partial_response_keeps_price_and_nulls_the_rest(self):
        summary = economics.summarize_price({"solana": {"usd": 123.45}})
        self.assertTrue(summary["available"])
        self.assertIsNone(summary["market_cap_usd"])
        self.assertIsNone(summary["change_24h_pct"])

    def test_failed_source_is_unavailable(self):
        for bad in (None, {}, {"solana": {}}, {"bitcoin": {"usd": 1}}, "nope",
                    {"solana": {"usd": float("nan")}}, {"solana": {"usd": float("inf")}},
                    {"solana": {"usd": True}}):
            self.assertFalse(economics.summarize_price(bad)["available"])
            self.assertEqual(economics.summarize_price(bad)["freshness"], "unavailable")


class TestStablecoins(unittest.TestCase):
    def chains(self):
        return [
            {"name": "Ethereum", "gecko_id": "ethereum",
             "totalCirculatingUSD": {"peggedUSD": 98_765_432.10}},
            {"name": "Solana", "gecko_id": "solana",
             "totalCirculatingUSD": {"peggedUSD": 12_345_678.90, "peggedEUR": 123_456.70}},
        ]

    def test_finds_solana_among_all_chains(self):
        summary = economics.summarize_stablecoins(self.chains())
        self.assertEqual(summary["usd_pegged_circulating_usd"], 12_345_678.90)
        self.assertNotIn("stablecoin_usd", summary)
        self.assertEqual(summary["provider_field"], "totalCirculatingUSD.peggedUSD")
        self.assertEqual(summary["metric"], "USD-pegged circulating supply")
        self.assertIn("not all stablecoins", summary["scope"])

    def test_separates_non_usd_pegs_from_the_headline(self):
        # The headline says "USD stablecoins"; EUR/JPY pegs must not inflate it.
        self.assertEqual(economics.summarize_stablecoins(self.chains())["non_usd_pegged_usd"],
                         123_456.70)

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
            "total24h": 98_765_432.10, "total7d": 654_321_098.70, "change_1d": 1.25,
        })
        self.assertEqual(summary["volume_24h_usd"], 98_765_432.10)
        self.assertEqual(summary["change_1d_pct"], 1.25)
        self.assertTrue(summary["transport_complete"])
        self.assertFalse(summary["market_coverage"]["complete"])
        self.assertIn("RFQ", summary["market_coverage"]["exclusions"])
        self.assertIn("unindexed venues", summary["market_coverage"]["exclusions"])

    def test_failed_source_is_unavailable(self):
        for bad in (None, {}, {"total24h": "not a number"}, "nope",
                    {"total24h": float("nan")}, {"total24h": float("inf")}, {"total24h": True}):
            self.assertFalse(economics.summarize_dex(bad)["available"])


class TestProtocols(unittest.TestCase):
    def test_ranks_non_cex_protocols_by_solana_chain_tvl(self):
        summary = economics.summarize_protocols([
            {"id": "1", "name": "Global Giant", "slug": "global", "category": "Dexes",
             "tvl": 100_000_000_000, "chainTvls": {"Solana": 10_000_000}},
            {"id": "2", "name": "Solana Lender", "slug": "lender", "category": "Lending",
             "tvl": 1_000_000_000, "chainTvls": {"Solana": 900_000_000}},
            {"id": "3", "name": "Custodial Exchange", "slug": "cex", "category": "CEX",
             "tvl": 8_000_000_000, "chainTvls": {"Solana": 7_000_000_000}},
            {"id": "4", "name": "Other Chain", "slug": "other", "category": "Dexes",
             "tvl": 4_000_000_000, "chainTvls": {"Ethereum": 4_000_000_000}},
        ])
        self.assertTrue(summary["available"])
        self.assertEqual([row["name"] for row in summary["protocols"]], ["Solana Lender", "Global Giant"])
        self.assertEqual(summary["protocols"][0]["solana_tvl_usd"], 900_000_000.0)
        self.assertEqual(summary["scope"], "non-CEX protocols with reported Solana chain TVL")
        self.assertEqual(summary["excluded_categories"], ["CEX"])

    def test_prefers_provider_parent_aggregate_and_excludes_its_children(self):
        summary = economics.summarize_protocols([
            {"id": "parent#family", "name": "Family", "slug": "family",
             "category": "Lending", "chainTvls": {"Solana": 700}},
            {"id": "child-a", "name": "Family Lend", "slug": "family-lend",
             "parentProtocol": "parent#family", "category": "Lending",
             "chainTvls": {"Solana": 500}},
            {"id": "child-b", "name": "Family Vault", "slug": "family-vault",
             "parentProtocol": "parent#family", "category": "Yield",
             "chainTvls": {"Solana": 300}},
            {"id": "solo", "name": "Solo", "slug": "solo", "category": "Dexes",
             "chainTvls": {"Solana": 600}},
        ])
        self.assertEqual([row["name"] for row in summary["protocols"]], ["Family", "Solo"])
        family = summary["protocols"][0]
        self.assertEqual(family["provider_family_id"], "parent#family")
        self.assertEqual(family["provider_protocol_id"], "parent#family")
        self.assertEqual(family["ranking_basis"], "provider_parent_aggregate")
        self.assertEqual(summary["eligible_protocol_count"], 2)
        self.assertEqual(summary["excluded_child_protocol_count"], 2)

    def test_malformed_protocol_source_is_unavailable_not_empty_leaderboard(self):
        for bad in (None, {}, "nope", [], [{"name": "Missing chain TVL"}],
                    [{"name": "Bad", "category": "Dexes", "chainTvls": {"Solana": float("nan")}}]):
            self.assertFalse(economics.summarize_protocols(bad)["available"])


class TestBuildEconomics(unittest.TestCase):
    def test_one_failed_source_does_not_take_out_the_others(self):
        # The property that matters: DeFiLlama going down must not blank the price.
        result = economics.build_economics({
            "price": {"solana": {"usd": 123.45}},
            "tvl": None,           # simulated outage
            "stablecoins": None,   # simulated outage
            "dex": None,           # simulated outage
            "protocols": None,     # simulated outage
        })
        self.assertTrue(result["available"])
        self.assertTrue(result["price"]["available"])
        self.assertFalse(result["tvl"]["available"])
        self.assertEqual(
            {n for n, s in result["sources"].items() if not s["available"]},
            {"tvl", "stablecoins", "dex", "protocols"},
        )

    def test_total_outage_is_unavailable_not_a_page_of_zeros(self):
        result = economics.build_economics({k: None for k in economics.SOURCES})
        self.assertFalse(result["available"])
        for part in ("price", "tvl", "stablecoins", "dex", "protocols"):
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
