"""Focused contracts for independently scheduled growth slices."""

import sys
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import growth  # noqa: E402
from tests.test_growth import cached_supply  # noqa: E402


def retained_supply_growth():
    observed_at = "2026-09-05T12:00:00+00:00"
    observations = {
        "mint-a": cached_supply(observed_at),
        "mint-b": cached_supply("2026-09-05T11:59:59+00:00"),
        "mint-c": cached_supply("2026-09-02T18:00:00+00:00"),
    }
    products = [{"solana_mint": mint, "symbol": mint, "name": mint, "slug": mint}
                for mint in observations]
    reference = int(datetime.fromisoformat(observed_at).timestamp())
    equities = growth.build_tokenized_equities(products, observations, observed_at_unix=reference)
    coverage = growth.summarize_supply_coverage(
        3, list(observations), {"observations": observations}, reference, 2, 1, True,
    )
    equities.update(
        supply_coverage=coverage, supply_queried_this_run_asset_count=2,
        supply_successful_this_run_asset_count=1, supply_failed_this_run_asset_count=1,
        supply_deadline_exhausted=True,
    )
    return {"tokenized_equities": equities, "sources": {
        "supply": dict(coverage, available=True, deadline_exhausted=True),
    }}


class GrowthCadenceTests(unittest.TestCase):
    def test_reused_supply_rechecks_six_hour_and_seventy_two_hour_boundaries(self):
        prior = retained_supply_growth()
        original = deepcopy(prior)
        for stamp, fresh, covered in (
            ("2026-09-05T18:00:00+00:00", 1, 3),
            ("2026-09-05T18:00:01+00:00", 0, 2),
        ):
            with self.subTest(stamp=stamp):
                result = growth.evaluate_supply_freshness(prior, stamp, reused_this_run=True)
                equities = result["tokenized_equities"]
                coverage = equities["supply_coverage"]
                self.assertEqual(equities["fresh_supply_asset_count"], fresh)
                self.assertEqual(equities["stale_supply_asset_count"], 3 - fresh)
                self.assertEqual(coverage["fresh_asset_count"], fresh)
                self.assertEqual(coverage["coverage_numerator"], covered)
                self.assertEqual(coverage["coverage_denominator"], 3)
                self.assertEqual(coverage["sweep_complete"], covered == 3)
                self.assertEqual(equities["supply_evaluated_at"], stamp)
                self.assertIs(equities["supply_reused_this_run"], True)
                self.assertEqual(equities["assets"], equities["all_assets"])
                for key in ("oldest_observation_at", "newest_observation_at", "observation_span_seconds"):
                    self.assertEqual(coverage[key], prior["tokenized_equities"]["supply_coverage"][key])
                for key in ("queried", "successful", "failed"):
                    self.assertEqual(equities[f"supply_{key}_this_run_asset_count"], 0)
                    self.assertEqual(coverage[f"{key}_this_run_asset_count"], 0)
                self.assertEqual(coverage["attempt_scope"], "current collection run")
                self.assertFalse(equities["supply_deadline_exhausted"])
                self.assertFalse(result["sources"]["supply"]["deadline_exhausted"])
                for key, value in coverage.items():
                    self.assertEqual(result["sources"]["supply"][key], value)
                for old, new in zip(prior["tokenized_equities"]["all_assets"], equities["all_assets"]):
                    self.assertEqual(
                        {k: v for k, v in old.items() if k not in ("supply_age_seconds", "supply_freshness")},
                        {k: v for k, v in new.items() if k not in ("supply_age_seconds", "supply_freshness")},
                    )
                    expected_age = int((datetime.fromisoformat(stamp) - datetime.fromisoformat(old["supply_collected_at"])).total_seconds())
                    self.assertEqual(new["supply_age_seconds"], expected_age)
        self.assertEqual(prior, original)

    def test_fresh_collection_projection_preserves_attempt_counts_and_deadline(self):
        prior = retained_supply_growth()
        result = growth.evaluate_supply_freshness(
            prior, "2026-09-05T18:00:01+00:00", reused_this_run=False,
        )
        equities = result["tokenized_equities"]
        self.assertIs(equities["supply_reused_this_run"], False)
        self.assertEqual(equities["fresh_supply_asset_count"], 0)
        for key in ("queried", "successful", "failed"):
            field = f"supply_{key}_this_run_asset_count"
            self.assertEqual(equities[field], prior["tokenized_equities"][field])
            self.assertEqual(equities["supply_coverage"][f"{key}_this_run_asset_count"], equities[field])
        self.assertTrue(equities["supply_deadline_exhausted"])
        self.assertTrue(result["sources"]["supply"]["deadline_exhausted"])

    def test_projection_keeps_missing_supply_unavailable_and_uses_all_registry_rows(self):
        prior = retained_supply_growth()
        equities = prior["tokenized_equities"]
        missing = growth.build_tokenized_equities(
            [{"solana_mint": "missing", "symbol": "missing"}], {},
            observed_at_unix=equities["observed_at_unix"],
        )["all_assets"][0]
        equities["all_assets"].append(missing)
        equities["assets"] = equities["all_assets"][:1]
        equities["registry_asset_count"] = 4
        result = growth.evaluate_supply_freshness(
            prior, "2026-09-05T18:00:01+00:00", reused_this_run=True,
        )["tokenized_equities"]
        self.assertEqual(result["supply_coverage"]["coverage_denominator"], 4)
        self.assertEqual(result["supply_observed_asset_count"], 3)
        self.assertEqual(next(row for row in result["all_assets"] if row["mint"] == "missing"), missing)

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
